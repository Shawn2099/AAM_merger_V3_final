"""Regressions found by the 2026-10-23 pre-release verification.

Each test failed before its fix. They are grouped here because they are the
release-gate findings, not a new feature area.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import (
    DocType,
    Document,
    ExtractionStatus,
    LineItem,
    POSet,
    POSetStatus,
)
from app.models.base import Base
from app.services import extraction as extraction_module
from app.services.extraction import extract_document, populate_from_raw_dict
from app.services.ingestion import ingest_file
from app.services.locking import release_lock
from app.services.merge import merge_po_set
from app.services.quarantine import quarantine_document
from app.services.sanitizer import parse_quantity_scaled
from app.services.splitting import SplitError, split_combined


@pytest.fixture()
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c.paths.database_path = tmp_path / "release.db"
    c.paths.input_folder = tmp_path / "input"
    c.paths.output_folder = tmp_path / "output"
    c.paths.quarantine_folder = tmp_path / "quarantine"
    c.paths.stored_documents_folder = tmp_path / "stored"
    c.paths.combined_folder = tmp_path / "combined"
    c.paths.log_folder = tmp_path / "logs"
    for p in (
        c.paths.input_folder,
        c.paths.output_folder,
        c.paths.quarantine_folder,
        c.paths.stored_documents_folder,
        c.paths.combined_folder,
        c.paths.log_folder,
    ):
        p.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(c))
    return c


def _pdf(path: Path, widths: list[float]) -> Path:
    w = PdfWriter()
    for width in widths:
        w.add_blank_page(width=width, height=200)
    with open(path, "wb") as f:
        w.write(f)
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Quantity magnitude follows the product contract (was 10x looser)
# ---------------------------------------------------------------------------


def test_magnitude_guard_matches_the_1e12_contract():
    assert parse_quantity_scaled("1000000000000") == 1_000_000_000_000_000
    for over in ("1000000000001", "2000000000000", "9e12", "1e13"):
        with pytest.raises(ValueError):
            parse_quantity_scaled(over)


# ---------------------------------------------------------------------------
# PDF header may be preceded by up to 1024 bytes (BOM, CRLF, junk)
# ---------------------------------------------------------------------------


def test_a_pdf_with_a_bom_is_still_a_pdf(cfg, tmp_path):
    plain = _pdf(tmp_path / "plain.pdf", [100])
    bom_copy = cfg.paths.input_folder / "bom.pdf"
    bom_copy.write_bytes(b"\xef\xbb\xbf" + plain.read_bytes())

    doc = ingest_file(bom_copy, cfg)
    assert doc.sha256_hash == _sha(bom_copy)
    assert doc.stored_path


def test_an_html_page_named_pdf_is_still_refused(cfg):
    fake = cfg.paths.input_folder / "error.pdf"
    fake.write_bytes(b"<html><body>404</body></html>")
    with pytest.raises(ValueError):
        ingest_file(fake, cfg)
    assert any((cfg.paths.quarantine_folder / "_documents").glob("*.reason.txt"))


# ---------------------------------------------------------------------------
# A refused re-split must not leave the previous pass's children in input/
# ---------------------------------------------------------------------------


def test_stale_children_are_removed_even_when_the_re_split_is_refused(cfg, tmp_path):
    parent = _pdf(cfg.paths.combined_folder / "packet.pdf", [100, 200, 300])
    sha16 = _sha(parent)[:16]
    stale = [cfg.paths.input_folder / f"{sha16}_p{i}.pdf" for i in (1, 2, 3)]
    for path in stale:
        _pdf(path, [100])

    with pytest.raises(SplitError) as exc:
        split_combined(
            parent,
            {
                "page_count": 3,
                "components": [
                    {"doc_type": "PO", "page_start": 1, "page_end": 2},
                    {"doc_type": "DN", "page_start": 2, "page_end": 3},  # overlap
                ],
            },
            cfg,
        )
    assert exc.value.reason == "page_range_overlap"
    assert not any(p.exists() for p in stale), "abandoned children were left in input/"


# ---------------------------------------------------------------------------
# A parent whose split never completed is an ordinary document again
# ---------------------------------------------------------------------------


def test_a_failed_split_parent_read_as_po_is_no_longer_a_parent(cfg):
    eng = get_engine(cfg)
    stored = _pdf(cfg.paths.stored_documents_folder / "p.pdf", [100])
    with Session(eng) as s:
        doc = Document(
            sha256_hash="a" * 64,
            original_filename="p.pdf",
            stored_path=str(stored),
            doc_type=DocType.COMBINED,
            extraction_status=ExtractionStatus.valid,
            is_split_parent=True,
            split_completed_at=None,
        )
        s.add(doc)
        s.commit()
        populate_from_raw_dict(
            doc,
            {
                "document_type": "PO",
                "document_number": "PO-1",
                "po_no_raw": "PO-1",
                "line_items": [],
            },
            s,
            cfg,
        )
        s.commit()
        assert doc.is_split_parent is False


def test_a_re_read_with_no_po_detaches_the_document_from_its_set(cfg):
    eng = get_engine(cfg)
    stored = _pdf(cfg.paths.stored_documents_folder / "dn.pdf", [100])
    with Session(eng) as s:
        ps = POSet(po_no_normalized="OLD", status=POSetStatus.pending)
        s.add(ps)
        s.flush()
        doc = Document(
            sha256_hash="b" * 64,
            original_filename="dn.pdf",
            stored_path=str(stored),
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps.id,
            po_no_raw="OLD",
            po_no_normalized="OLD",
        )
        s.add(doc)
        s.commit()

        populate_from_raw_dict(
            doc,
            {"document_type": "DN", "po_no_raw": None, "document_number": "DN-9", "line_items": []},
            s,
            cfg,
        )
        s.commit()
        assert doc.po_no_normalized is None
        assert doc.po_set_id is None, (
            "a document with no PO reference must be held, not left attached"
        )


# ---------------------------------------------------------------------------
# A byte-identical duplicate of a COMBINED parent must not linger in input/
# ---------------------------------------------------------------------------


def test_a_renamed_duplicate_of_a_combined_parent_is_cleared(cfg, tmp_path):
    combined = _pdf(tmp_path / "packet-A.pdf", [100, 200, 300])
    first = cfg.paths.input_folder / "packet-A.pdf"
    second = cfg.paths.input_folder / "packet-B.pdf"
    first.write_bytes(combined.read_bytes())
    second.write_bytes(combined.read_bytes())

    doc = ingest_file(first, cfg)
    assert ingest_file(second, cfg).id == doc.id  # dedup: one row, first name

    def fake_vlm(stored_path, doc_type, cfg_):
        return {
            "document_type": "COMBINED",
            "page_count": 3,
            "components": [
                {"doc_type": "PO", "document_type": "PO", "page_start": 1, "page_end": 1},
                {"doc_type": "DN", "document_type": "DN", "page_start": 2, "page_end": 2},
                {"doc_type": "SI", "document_type": "SI", "page_start": 3, "page_end": 3},
            ],
            "line_items": [],
            "po_reference": None,
            "po_no_raw": None,
            "po_reference_ambiguous": False,
            "document_number": None,
        }

    original = extraction_module._call_vlm
    extraction_module._call_vlm = fake_vlm
    try:
        extract_document(doc.id, cfg)
    finally:
        extraction_module._call_vlm = original

    assert not second.exists(), "the renamed duplicate would be re-hashed on every sync"
    assert (cfg.paths.combined_folder / "packet-A.pdf").is_file()
    children = list(cfg.paths.input_folder.glob("*_p*.pdf"))
    assert len(children) == 3


# ---------------------------------------------------------------------------
# quarantine_document really removes the input copy (it says it does)
# ---------------------------------------------------------------------------


def test_quarantine_document_removes_only_the_matching_input_copy(cfg):
    source = _pdf(cfg.paths.input_folder / "broken.pdf", [100])
    doc = ingest_file(source, cfg)
    stored = Path(doc.stored_path)
    assert stored.is_file()

    # A same-named file with DIFFERENT bytes appears after ingest (batch export
    # reuses names): it must survive.
    source.write_bytes(b"%PDF-1.4\ncompletely different\n")
    quarantine_document(doc.id, cfg, reason="test")
    assert source.is_file(), "a same-named unrelated file must not be swept away"

    source.write_bytes(stored.read_bytes())
    quarantine_document(doc.id, cfg, reason="test")
    assert not source.exists(), "the dead file would be retried every night"


# ---------------------------------------------------------------------------
# A background writer must never re-status a delivered packet (lost update)
# ---------------------------------------------------------------------------


def test_a_failed_document_cannot_re_quarantine_a_merged_set(cfg):
    from datetime import UTC, datetime

    from app.flows.sync import _quarantine_broken_document

    eng = get_engine(cfg)
    stored = _pdf(cfg.paths.stored_documents_folder / "member.pdf", [100])
    with Session(eng) as s:
        ps = POSet(
            po_no_normalized="MERGED",
            status=POSetStatus.merged,
            merged_output_path=str(cfg.paths.output_folder / "MERGED.pdf"),
            merged_at=datetime.now(UTC),
        )
        s.add(ps)
        s.flush()
        doc = Document(
            sha256_hash="d" * 64,
            original_filename="member.pdf",
            stored_path=str(stored),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.failed,
            po_set_id=ps.id,
        )
        s.add(doc)
        s.commit()
        doc_id, set_id = doc.id, ps.id

    _quarantine_broken_document(doc_id, cfg, eng, None, set())

    with Session(eng) as s:
        assert s.get(POSet, set_id).status == POSetStatus.merged, (
            "a delivered set was overwritten by the document-failure writer"
        )


# ---------------------------------------------------------------------------
# Reconciling a previously quarantined set clears the stale sentence
# ---------------------------------------------------------------------------


def test_a_recovered_set_does_not_keep_its_quarantine_reason(cfg):
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = POSet(
            po_no_normalized="RECOVER",
            status=POSetStatus.pending,
            reconcile_reason=(
                "Quarantined: a document in this set failed to read, "
                "so the set could not be verified."
            ),
        )
        s.add(ps)
        s.flush()
        for i, (dtype, width) in enumerate(
            ((DocType.PO, 101), (DocType.DN, 201), (DocType.SI, 301))
        ):
            stored = _pdf(cfg.paths.stored_documents_folder / f"r{i}.pdf", [width])
            doc = Document(
                sha256_hash=f"r{i}".ljust(64, "c"),
                original_filename=f"r{i}.pdf",
                stored_path=str(stored),
                doc_type=dtype,
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps.id,
                po_no_raw="RECOVER",
                po_no_normalized="RECOVER",
                si_no="INV-RECOVER" if dtype == DocType.SI else None,
            )
            s.add(doc)
            s.flush()
            s.add(
                LineItem(
                    document_id=doc.id,
                    line_item_no="1",
                    description="ITEM",
                    quantity=1000,
                    unit_price=1000,
                )
            )
        s.commit()
        set_id = ps.id

    path = merge_po_set(set_id, cfg)
    assert path is not None and path.is_file()
    with Session(eng) as s:
        ps = s.get(POSet, set_id)
        assert ps.status == POSetStatus.merged
        assert "quarantined" not in (ps.reconcile_reason or "").lower()
        assert "could not be verified" not in (ps.reconcile_reason or "")


# ---------------------------------------------------------------------------
# Stale-lock cleanup must not clear a lock acquired after it looked
# ---------------------------------------------------------------------------


def test_stale_lock_release_cannot_clear_a_fresh_lock(cfg):
    from datetime import UTC, datetime, timedelta

    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = POSet(
            po_no_normalized="LOCK",
            status=POSetStatus.pending,
            locked_by_action="merge",
            locked_at=datetime.now(UTC) - timedelta(hours=1),
        )
        s.add(ps)
        s.commit()
        set_id = ps.id
        observed_action, observed_at = ps.locked_by_action, ps.locked_at

    # Another request acquires the same action's lock after the cleanup read.
    fresh = datetime.now(UTC)
    with Session(eng) as other:
        row = other.get(POSet, set_id)
        row.locked_at = fresh
        other.commit()

    with Session(eng) as s:
        stale_view = s.get(POSet, set_id)
        release_lock(stale_view, s, action=observed_action, observed_at=observed_at)

    with Session(eng) as s:
        row = s.get(POSet, set_id)
        assert row.locked_by_action == "merge", "the fresh lock was stomped by a stale cleanup"
