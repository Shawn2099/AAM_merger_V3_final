"""Regression tests for the 2026-10-23 audit — pipeline intake/extraction (P2-21..P2-25).

Every test here is new. The behaviour under test lives in:

* ``app/services/ingestion.py``    — magic + size gate on the input sweep
* ``app/services/extraction.py``   — per-read field reset, stale-claim reclaim,
                                     config-derived attempt cap
* ``app/services/splitting.py``    — all-SKIP decomposition, stale child cleanup
* ``app/api/routes/po_sets.py``    — redo_extract refuses during a sync / on merged

No network call is made: every extraction test monkeypatches ``_call_vlm`` or
asserts it was never reached.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core import limits
from app.core.config import load_config
from app.core.database import get_engine
from app.models import Base, DocType, Document, ExtractionStatus, POSet, POSetStatus

# --------------------------------------------------------------------------- helpers


@pytest.fixture()
def cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    base = tmp_path
    cfg.paths.input_folder = str(base / "input")
    cfg.paths.output_folder = str(base / "output")
    cfg.paths.quarantine_folder = str(base / "quarantine")
    cfg.paths.stored_documents_folder = str(base / "stored")
    cfg.paths.combined_folder = str(base / "combined")
    cfg.paths.log_folder = str(base / "logs")
    cfg.paths.database_path = str(base / "aam.db")
    for sub in ("input", "output", "quarantine", "stored", "combined", "logs"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(cfg))
    return cfg


def _pdf(path: Path, pages: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    with open(path, "wb") as f:
        writer.write(f)
    return path


def _pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _add_doc(cfg, **kwargs) -> int:
    stored = kwargs.pop("stored", None) or _pdf(
        Path(cfg.paths.stored_documents_folder) / (kwargs.get("original_filename") or "doc.pdf")
    )
    defaults = {
        "sha256_hash": kwargs.get("original_filename") or "sha",
        "original_filename": "doc.pdf",
        "stored_path": str(stored),
        "doc_type": DocType.UNKNOWN,
        "extraction_status": ExtractionStatus.pending,
    }
    defaults.update(kwargs)
    with Session(get_engine(cfg)) as s:
        doc = Document(**defaults)
        s.add(doc)
        s.commit()
        return doc.id


def _po_result(po_no: str = "4500043712") -> dict:
    return {
        "document_type": "PO",
        "document_number": po_no,
        "po_no_raw": None,
        "po_reference": None,
        "po_reference_ambiguous": False,
        "vendor_name": "ACME",
        "page_count": 1,
        "components": [],
        "line_items": [
            {
                "line_item_no": "1",
                "description": "WASHER",
                "quantity": "10",
                "dn_no": None,
            }
        ],
    }


def _session(cfg) -> Session:
    return Session(get_engine(cfg))


# ------------------------------------------------------- P2-21 redo_extract guard


def test_redo_extract_refuses_while_a_sync_is_running(cfg, monkeypatch):
    """Two VLM calls on one document would append duplicate line items."""
    import app.api.routes.po_sets as po_routes
    import app.services.sync_lock as sync_lock

    with _session(cfg) as s:
        ps = POSet(po_no_normalized="4500043712", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        doc = Document(
            sha256_hash="sync-race",
            original_filename="po.pdf",
            stored_path=str(_pdf(Path(cfg.paths.stored_documents_folder) / "po.pdf")),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            extraction_attempt_count=2,
            po_set_id=ps.id,
        )
        s.add(doc)
        s.commit()
        set_id, doc_id = ps.id, doc.id

    monkeypatch.setattr(po_routes, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(sync_lock, "_is_sync_running", lambda *a, **k: True)

    with pytest.raises(HTTPException) as excinfo:
        po_routes.redo_extract(set_id)

    assert excinfo.value.status_code == 409
    assert "sync" in excinfo.value.detail.lower()

    with _session(cfg) as s:
        doc = s.get(Document, doc_id)
        assert doc.extraction_status == ExtractionStatus.valid, "no reset while the sync reads it"
        assert doc.extraction_attempt_count == 2
        assert s.get(POSet, set_id).locked_by_action is None, "the refusal must not take the lock"


def test_redo_extract_on_a_merged_set_is_refused(cfg, monkeypatch):
    import app.api.routes.po_sets as po_routes

    with _session(cfg) as s:
        ps = POSet(
            po_no_normalized="MERGED-1",
            status=POSetStatus.merged,
            merged_output_path=str(Path(cfg.paths.output_folder) / "MERGED-1.pdf"),
        )
        s.add(ps)
        s.commit()
        doc = Document(
            sha256_hash="merged-doc",
            original_filename="si.pdf",
            stored_path=str(_pdf(Path(cfg.paths.stored_documents_folder) / "si.pdf")),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps.id,
        )
        s.add(doc)
        s.commit()
        set_id, doc_id = ps.id, doc.id

    monkeypatch.setattr(po_routes, "load_config", lambda *a, **k: cfg)

    with pytest.raises(HTTPException) as excinfo:
        po_routes.redo_extract(set_id)

    assert excinfo.value.status_code == 409
    assert "merged" in excinfo.value.detail.lower()
    with _session(cfg) as s:
        assert s.get(Document, doc_id).extraction_status == ExtractionStatus.valid
        assert s.get(POSet, set_id).status == POSetStatus.merged


# ------------------------------------------------------ P2-23 ingestion magic/size


def test_ingestion_rejects_a_non_pdf_and_leaves_a_reason(cfg):
    from app.services.ingestion import ingest_file

    src = Path(cfg.paths.input_folder) / "fake.pdf"
    src.write_bytes(b"<html><body>404 not found</body></html>")

    with pytest.raises(ValueError, match="%PDF-"):
        ingest_file(src, cfg)

    assert not src.exists(), "the rejected file must leave the input folder"
    reasons = list((Path(cfg.paths.quarantine_folder) / "_documents").glob("*.reason.txt"))
    assert len(reasons) == 1
    reason = reasons[0].read_text(encoding="utf-8")
    assert "ValueError" in reason and "%PDF-" in reason
    with _session(cfg) as s:
        (row,) = s.query(Document).all()  # R-03: listed on the Unclassified page as failed
        assert row.extraction_status == ExtractionStatus.failed and row.po_set_id is None
        assert "%PDF-" in (row.ingest_reject_reason or "")


def test_ingestion_rejects_a_file_over_the_size_cap(cfg, monkeypatch):
    from app.services.ingestion import ingest_file

    monkeypatch.setattr(limits, "MAX_UPLOAD_BYTES", 20)
    src = Path(cfg.paths.input_folder) / "big.pdf"
    src.write_bytes(b"%PDF-1.4\n" + b"0" * 200)

    with pytest.raises(ValueError, match="upload cap"):
        ingest_file(src, cfg)

    assert not src.exists()
    reasons = list((Path(cfg.paths.quarantine_folder) / "_documents").glob("*.reason.txt"))
    assert len(reasons) == 1
    assert "upload cap" in reasons[0].read_text(encoding="utf-8")
    with _session(cfg) as s:
        (row,) = s.query(Document).all()
        assert row.extraction_status == ExtractionStatus.failed
        assert "upload cap" in (row.ingest_reject_reason or "")


def test_ingestion_accepts_a_real_pdf(cfg):
    from app.services.ingestion import ingest_file

    src = Path(cfg.paths.input_folder) / "real.pdf"
    src.write_bytes(_pdf_bytes())

    doc = ingest_file(src, cfg)
    assert doc.extraction_status == ExtractionStatus.pending
    assert Path(doc.stored_path).exists()


def test_known_limitation_unreadable_input_file_stays_for_next_sync(cfg, monkeypatch):
    """R-05: a read error is treated as transient. The file stays in input for the next
    sync (no quarantine, no row); a permanently unreadable file is retried every night."""
    from app.services import ingestion

    src = Path(cfg.paths.input_folder) / "locked.pdf"
    src.write_bytes(b"%PDF-1.4\nlocked by another process")

    real_read_bytes = Path.read_bytes

    def _boom(self):
        if self == src:
            raise PermissionError("locked by another process")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _boom)

    with pytest.raises(PermissionError):
        ingestion.ingest_file(src, cfg)

    assert src.exists(), "a transient read error must leave the file for the next sync"
    assert not list((Path(cfg.paths.quarantine_folder) / "_documents").glob("*.reason.txt"))


# --------------------------------------------------- P2-25 config-derived attempt cap


def test_attempt_cap_follows_extraction_max_retries(cfg, monkeypatch):
    """max_retries=5 must actually allow a fourth and fifth attempt."""
    from app.services import extraction as ext

    cfg.extraction.max_retries = 5
    doc_id = _add_doc(
        cfg,
        sha256_hash="cap-five",
        original_filename="po.pdf",
        doc_type=DocType.PO,
        extraction_attempt_count=3,
    )

    calls: list[int] = []

    def fake_vlm(stored_path, doc_type, cfg):
        calls.append(1)
        return _po_result()

    monkeypatch.setattr(ext, "_call_vlm", fake_vlm)

    out = ext.extract_document(doc_id, cfg)
    assert calls, "a configured cap of 5 must not stop the document at 3 attempts"
    assert out.extraction_status == ExtractionStatus.valid
    assert out.extraction_attempt_count == 4


def test_default_attempt_cap_remains_three(cfg, monkeypatch):
    from app.services import extraction as ext

    cfg.extraction.max_retries = 3
    doc_id = _add_doc(
        cfg,
        sha256_hash="cap-three",
        original_filename="po.pdf",
        doc_type=DocType.PO,
        extraction_attempt_count=3,
    )

    def must_not_call(*args, **kwargs):
        raise AssertionError("the VLM must not be called at the cap")

    monkeypatch.setattr(ext, "_call_vlm", must_not_call)

    out = ext.extract_document(doc_id, cfg)
    assert out.extraction_status == ExtractionStatus.failed
    assert out.extraction_attempt_count == 3


# ---------------------------------------------------- P2-22 stale processing reclaim


def test_a_stale_processing_claim_is_reclaimed_and_read(cfg, monkeypatch):
    from app.services import extraction as ext

    stale = datetime.now(UTC) - timedelta(seconds=cfg.extraction.stale_processing_seconds + 60)
    doc_id = _add_doc(
        cfg,
        sha256_hash="stale-claim",
        original_filename="po.pdf",
        doc_type=DocType.PO,
        extraction_status=ExtractionStatus.processing,
        extraction_attempt_count=0,
        updated_at=stale,
    )

    calls: list[int] = []

    def fake_vlm(stored_path, doc_type, cfg):
        calls.append(1)
        return _po_result()

    monkeypatch.setattr(ext, "_call_vlm", fake_vlm)

    out = ext.extract_document(doc_id, cfg)
    assert calls, "the reclaimed document must be read again"
    assert out.extraction_status == ExtractionStatus.valid
    assert out.extraction_attempt_count == 1


def test_a_live_processing_claim_is_left_alone(cfg, monkeypatch):
    from app.services import extraction as ext

    doc_id = _add_doc(
        cfg,
        sha256_hash="live-claim",
        original_filename="po.pdf",
        doc_type=DocType.PO,
        extraction_status=ExtractionStatus.processing,
        extraction_attempt_count=0,
    )

    def must_not_call(*args, **kwargs):
        raise AssertionError("a live claim is not a dead one")

    monkeypatch.setattr(ext, "_call_vlm", must_not_call)

    out = ext.extract_document(doc_id, cfg)
    assert out.extraction_status == ExtractionStatus.processing
    assert out.extraction_attempt_count == 0


# --------------------------------------------- P1-7 successive populate_from_raw_dict


def test_a_second_read_clears_stale_numbers_and_ambiguity(cfg):
    from app.services import extraction as ext

    doc_id = _add_doc(
        cfg,
        sha256_hash="two-reads",
        original_filename="doc.pdf",
        doc_type=DocType.SI,
        extraction_status=ExtractionStatus.valid,
    )

    with _session(cfg) as s:
        doc = s.get(Document, doc_id)
        ext.populate_from_raw_dict(
            doc,
            {
                "document_type": "SI",
                "document_number": "INV-999",
                "po_no_raw": "111",
                "po_reference_ambiguous": True,
                "line_items": [{"line_item_no": "1", "description": "A", "quantity": "1"}],
            },
            s,
            cfg,
        )
        s.commit()
        assert doc.si_no == "INV-999"
        assert doc.invoice_no == "INV-999"
        assert doc.po_no_raw == "111"
        assert doc.po_reference_ambiguous is True

        # Second read: a DN carrying no PO reference and no ambiguity.
        ext.populate_from_raw_dict(
            doc,
            {
                "document_type": "DN",
                "document_number": "GDN-2",
                "po_no_raw": None,
                "po_reference_ambiguous": False,
                "line_items": [{"line_item_no": "1", "description": "A", "quantity": "1"}],
            },
            s,
            cfg,
        )
        s.commit()

        assert doc.si_no is None, "the old SI number must not survive the re-read"
        assert doc.invoice_no is None
        assert doc.dn_no == "GDN-2"
        assert doc.po_no_raw is None, "the old PO key must not survive the re-read"
        assert doc.po_no_normalized is None
        assert doc.po_reference_ambiguous is False, "sticky ambiguity must be cleared"


def test_a_second_read_preserves_an_operator_set_po(cfg):
    from app.services import extraction as ext

    doc_id = _add_doc(
        cfg,
        sha256_hash="manual-po",
        original_filename="doc.pdf",
        doc_type=DocType.DN,
        extraction_status=ExtractionStatus.valid,
        po_no_source="manual",
        po_no_raw="MAN-42",
        po_no_normalized="MAN42",
        po_reference_ambiguous=True,
    )

    with _session(cfg) as s:
        doc = s.get(Document, doc_id)
        ext.populate_from_raw_dict(
            doc,
            {
                "document_type": "DN",
                "document_number": "GDN-9",
                "po_no_raw": "999",
                "po_reference_ambiguous": True,
                "line_items": [],
            },
            s,
            cfg,
        )
        s.commit()

        assert doc.po_no_raw == "MAN-42", "a human placement must not be overwritten"
        assert doc.po_no_normalized == "MAN42"
        assert doc.dn_no == "GDN-9"
        # The human decision resolves the model's uncertainty.
        assert doc.po_reference_ambiguous is False


# ------------------------------------------------------------- P2-17 all-SKIP split


def test_an_all_skip_split_fails_and_quarantines_the_parent(cfg, monkeypatch):
    from app.services import extraction as ext

    stored = _pdf(Path(cfg.paths.stored_documents_folder) / "combined.pdf", pages=2)
    doc_id = _add_doc(
        cfg,
        sha256_hash="all-skip",
        original_filename="combined.pdf",
        stored=stored,
        doc_type=DocType.COMBINED,
    )

    result = {
        "document_type": "COMBINED",
        "page_count": 2,
        "components": [
            {"doc_type": "SKIP", "document_type": "SKIP", "page_start": 1, "page_end": 1},
            {"doc_type": "SKIP", "document_type": "SKIP", "page_start": 2, "page_end": 2},
        ],
        "line_items": [],
    }
    monkeypatch.setattr(ext, "_call_vlm", lambda *a, **k: result)

    out = ext.extract_document(doc_id, cfg)

    assert out.extraction_status == ExtractionStatus.failed
    assert out.is_split_parent is True
    assert out.split_completed_at is None, "no children were cut, so the split is incomplete"
    quarantine = list((Path(cfg.paths.quarantine_folder) / "_documents").rglob("QUARANTINE.txt"))
    assert len(quarantine) == 1, "an all-SKIP split must leave a quarantine trace"
    assert "no_document_pages" in quarantine[0].read_text(encoding="utf-8")


# ---------------------------------------------------------- P2-18 stale child files


def test_a_resplit_removes_stale_child_files(cfg, tmp_path):
    from app.services.splitting import split_combined

    parent = _pdf(tmp_path / "combined.pdf", pages=3)
    sha16 = hashlib.sha256(parent.read_bytes()).hexdigest()[:16]
    input_dir = Path(cfg.paths.input_folder)
    stale = input_dir / f"{sha16}_p9.pdf"
    stale.write_bytes(b"%PDF-1.4 stale child from an abandoned pass")

    response = {
        "page_count": 3,
        "components": [
            {"doc_type": "PO", "page_start": 1, "page_end": 1},
            {"doc_type": "DN", "page_start": 2, "page_end": 2},
            {"doc_type": "SI", "page_start": 3, "page_end": 3},
        ],
    }

    children = split_combined(parent, response, cfg)

    assert not stale.exists(), "a stale p9 from the abandoned decomposition must be removed"
    assert [p.name for p in children] == [
        f"{sha16}_p1.pdf",
        f"{sha16}_p2.pdf",
        f"{sha16}_p3.pdf",
    ]
    for child in children:
        assert child.exists() and child.read_bytes().startswith(b"%PDF-")
