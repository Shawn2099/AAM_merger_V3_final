"""Regressions for the high-priority audit fixes.

These are the invariants that are cheap to state and expensive to lose:
one open set per PO key, a quarantine folder per set (not per number), the
matrix using the engine's own aggregates, and the server normalizer being the
one the preview shows. Pure or tmp-DB only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
from app.models.base import Base
from app.services.grouping import get_or_create_po_set, group_document
from app.services.merge import MergeEmptyError, _si_numbers, _write_merged


def _cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "audit.db")
    cfg.paths.input_folder = str(tmp_path / "input")
    cfg.paths.output_folder = str(tmp_path / "output")
    cfg.paths.quarantine_folder = str(tmp_path / "quarantine")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    cfg.paths.log_folder = str(tmp_path / "logs")
    for p in (
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.log_folder,
    ):
        Path(p).mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    return cfg, eng


def _pdf(path: Path, pages: int = 1):
    from pypdf import PdfWriter

    path.parent.mkdir(parents=True, exist_ok=True)
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=100, height=200)
    w.write(str(path))
    return path


def _doc(s, cfg, ps_id, dtype, name, lines=(), **extra):
    p = _pdf(Path(cfg.paths.stored_documents_folder) / name)
    d = Document(
        sha256_hash=f"audit_{name}_{ps_id}",
        original_filename=name,
        stored_path=str(p),
        doc_type=DocType(dtype),
        extraction_status=ExtractionStatus.valid,
        po_set_id=ps_id,
        **extra,
    )
    s.add(d)
    s.flush()
    from app.models import LineItem

    for no, qty in lines:
        s.add(
            LineItem(
                document_id=d.id,
                line_item_no=no,
                description="Widget",
                quantity=qty,
                unit_price=1000,
            )
        )
    return d


def test_unique_open_set_index_refuses_a_second_open_set(tmp_path):
    _, eng = _cfg(tmp_path)
    with Session(eng) as s:
        s.add(POSet(po_no_normalized="PO-DUP", status=POSetStatus.pending))
        s.commit()
    with Session(eng) as s:
        s.add(POSet(po_no_normalized="PO-DUP", status=POSetStatus.quarantined))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


def test_get_or_create_po_set_is_idempotent(tmp_path):
    cfg, eng = _cfg(tmp_path)
    first = get_or_create_po_set("PO-1001", cfg)
    second = get_or_create_po_set("PO-1001", cfg)
    assert first.id == second.id
    with Session(eng) as s:
        assert s.query(POSet).count() == 1


def test_quarantine_folders_do_not_collide_when_a_po_number_is_reused(tmp_path):
    from app.services.quarantine import quarantine_copy

    cfg, eng = _cfg(tmp_path)
    with Session(eng) as s:
        old = POSet(po_no_normalized="PO-REUSE", status=POSetStatus.merged)
        new = POSet(po_no_normalized="PO-REUSE", status=POSetStatus.quarantined)
        s.add_all([old, new])
        s.commit()
        _doc(s, cfg, old.id, "PO", "old-po.pdf", [("1", 100)])
        _doc(s, cfg, new.id, "PO", "new-po.pdf", [("1", 100)])
        old_id, new_id = old.id, new.id
        s.commit()

    first = quarantine_copy(old_id, cfg, reason="old")
    second = quarantine_copy(new_id, cfg, reason="new")
    assert first != second
    assert (first / "QUARANTINE.txt").is_file()
    assert (second / "QUARANTINE.txt").is_file()
    assert (first / "old-po.pdf").is_file()
    assert (second / "new-po.pdf").is_file()


def test_matrix_uses_one_row_per_comparison_key():
    from app.api.routes.dashboard import build_matrix_rows
    from app.services.reconciliation import compare_po_set_lines

    po = [
        {
            "doc_type": "PO",
            "line_item_no": "1",
            "description": "A",
            "quantity": 40_000,
            "unit_price": 1000,
        },
        {
            "doc_type": "PO",
            "line_item_no": "1",
            "description": "A",
            "quantity": 60_000,
            "unit_price": 1000,
        },
    ]
    dn = [{"doc_type": "DN", "line_item_no": "1", "description": "A", "quantity": 100_000}]
    si = [{"doc_type": "SI", "line_item_no": "1", "description": "A", "quantity": 100_000}]
    comparison = compare_po_set_lines(po, dn, si, 85)
    rows = build_matrix_rows(po, dn, si, comparison)
    assert len(rows) == 1
    assert rows[0]["po_qty"] == 100
    assert rows[0]["verdict"] == "✅ Match"


def test_multiple_si_numbers_are_deterministic():
    si_1 = SimpleNamespace(id=1, doc_type="SI", si_no="INV-1", invoice_no="INV-1")
    si_2 = SimpleNamespace(id=2, doc_type="SI", si_no="INV-2", invoice_no="INV-2")
    ps = SimpleNamespace(documents=[si_2, si_1])
    assert _si_numbers(ps) == ["INV-1", "INV-2"]


def test_zero_page_packet_is_refused(tmp_path):
    src = _pdf(tmp_path / "empty.pdf", pages=0)
    doc = SimpleNamespace(stored_path=str(src), id=1, original_filename="empty.pdf")
    with pytest.raises(MergeEmptyError):
        _write_merged([doc], tmp_path / "out.pdf", allow_missing=False)
    assert not (tmp_path / "out.pdf").exists()


def test_ambiguous_dn_is_not_attached(tmp_path):
    cfg, _ = _cfg(tmp_path)
    doc = SimpleNamespace(
        doc_type=DocType.DN,
        po_no_normalized="PO-AMB",
        po_reference_ambiguous=True,
    )
    assert group_document(doc, cfg) == (None, "ambiguous_po_number")


def test_served_file_rejects_a_path_outside_its_root(tmp_path):
    from app.api.routes.dashboard import _served_file

    root = tmp_path / "stored"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4 x")
    with pytest.raises(HTTPException) as exc:
        _served_file(str(outside), root, "not found")
    assert exc.value.status_code == 404


def test_corrupt_source_writes_no_packet(tmp_path):
    from app.services.merge import MergeSourceError

    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not really a pdf")
    doc = SimpleNamespace(stored_path=str(bad), id=2, original_filename="bad.pdf")
    out = tmp_path / "out-corrupt.pdf"
    with pytest.raises(MergeSourceError):
        _write_merged([doc], out, allow_missing=False)
    assert not out.exists()
    assert not list(tmp_path.glob(".out-corrupt.*.tmp"))


def test_split_parent_is_not_in_the_holding_area(tmp_path):
    from app.api.routes.dashboard import _holding_area_query

    _, eng = _cfg(tmp_path)
    with Session(eng) as s:
        s.add(
            Document(
                sha256_hash="audit_split_parent",
                original_filename="combined.pdf",
                stored_path=str(tmp_path / "combined.pdf"),
                doc_type=DocType.UNKNOWN,
                extraction_status=ExtractionStatus.valid,
                po_set_id=None,
                is_split_parent=True,
            )
        )
        s.commit()
        assert _holding_area_query(s).count() == 0


def test_multiple_si_numbers_are_flagged_on_the_result(tmp_path):
    from app.services.reconciliation import reconcile_po_set

    cfg, eng = _cfg(tmp_path)
    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO-TWO-SI", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        _doc(s, cfg, ps.id, "PO", "po2.pdf", [("1", 100_000)])
        _doc(s, cfg, ps.id, "DN", "dn2.pdf", [("1", 100_000)])
        _doc(
            s,
            cfg,
            ps.id,
            "SI",
            "si2a.pdf",
            [("1", 50_000)],
            si_no="INV-A",
            invoice_no="INV-A",
        )
        _doc(
            s,
            cfg,
            ps.id,
            "SI",
            "si2b.pdf",
            [("1", 50_000)],
            si_no="INV-B",
            invoice_no="INV-B",
        )
        pid = ps.id
        s.commit()

    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "merged"
    messages = " ".join(flag.get("message", "") for flag in result.get("flags", []))
    assert "more than one invoice number" in messages
    assert "INV-A" in messages and "INV-B" in messages


def test_merge_clears_input_copies(tmp_path):
    from app.services.reconciliation import reconcile_po_set

    cfg, eng = _cfg(tmp_path)
    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO-INPUT", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        docs = [
            _doc(s, cfg, ps.id, "PO", "in-po.pdf", [("1", 100_000)]),
            _doc(s, cfg, ps.id, "DN", "in-dn.pdf", [("1", 100_000)]),
            _doc(
                s,
                cfg,
                ps.id,
                "SI",
                "in-si.pdf",
                [("1", 100_000)],
                si_no="INV-INPUT",
                invoice_no="INV-INPUT",
            ),
        ]
        pid = ps.id
        s.commit()
        for d in docs:
            Path(cfg.paths.input_folder, d.original_filename).write_bytes(
                Path(d.stored_path).read_bytes()
            )

    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "merged"
    assert list(Path(cfg.paths.input_folder).iterdir()) == []


def test_get_or_create_po_set_recovers_from_an_insert_race(tmp_path, monkeypatch):
    """Two workers both miss the SELECT; the loser joins the winner.

    The unique index raises IntegrityError on the second insert. The recovery
    path must roll back and re-select, not surface a 500 or create a duplicate
    open set.
    """
    cfg, eng = _cfg(tmp_path)
    with Session(eng) as s:
        existing = POSet(po_no_normalized="1002", status=POSetStatus.pending)
        s.add(existing)
        s.commit()
        existing_id = existing.id

    real_query = Session.query
    calls = {"n": 0}

    class _EmptyQuery:
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def first(self):
            return None

    def fake_query(self, *args, **kwargs):
        if args and args[0] is POSet and calls["n"] == 0:
            calls["n"] += 1
            return _EmptyQuery()
        return real_query(self, *args, **kwargs)

    monkeypatch.setattr(Session, "query", fake_query)
    got = get_or_create_po_set("PO-1002", cfg)
    assert got.id == existing_id
    with Session(eng) as s:
        assert s.query(POSet).count() == 1


def test_zero_page_sources_quarantine_as_unreadable(tmp_path):
    from app.services.reconciliation import reconcile_po_set

    cfg, eng = _cfg(tmp_path)
    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO-EMPTY-SRC", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        docs = [
            _doc(s, cfg, ps.id, "PO", "e-po.pdf", [("1", 100_000)]),
            _doc(s, cfg, ps.id, "DN", "e-dn.pdf", [("1", 100_000)]),
            _doc(
                s,
                cfg,
                ps.id,
                "SI",
                "e-si.pdf",
                [("1", 100_000)],
                si_no="INV-EMPTY",
                invoice_no="INV-EMPTY",
            ),
        ]
        pid = ps.id
        s.commit()
        for d in docs:
            _pdf(Path(d.stored_path), pages=0)

    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "quarantined"
    assert result["reason"] == "packet_source_unreadable"


def test_allow_missing_skips_a_corrupt_member(tmp_path):
    good = _pdf(tmp_path / "good.pdf")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not really a pdf")
    docs = [
        SimpleNamespace(stored_path=str(good), id=1, original_filename="good.pdf"),
        SimpleNamespace(stored_path=str(bad), id=2, original_filename="bad.pdf"),
    ]
    pages = _write_merged(docs, tmp_path / "out-skip.pdf", allow_missing=True)
    assert pages == 1
    assert (tmp_path / "out-skip.pdf").is_file()


def test_reconcile_restores_prior_status_when_merge_is_refused(tmp_path, monkeypatch):
    import app.services.reconciliation as rec

    cfg, eng = _cfg(tmp_path)
    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO-RESTORE", status=POSetStatus.mismatched)
        s.add(ps)
        s.commit()
        _doc(s, cfg, ps.id, "PO", "r-po.pdf", [("1", 100_000)])
        _doc(s, cfg, ps.id, "DN", "r-dn.pdf", [("1", 100_000)])
        _doc(
            s,
            cfg,
            ps.id,
            "SI",
            "r-si.pdf",
            [("1", 100_000)],
            si_no="INV-R",
            invoice_no="INV-R",
        )
        pid = ps.id
        s.commit()

    monkeypatch.setattr("app.services.merge.merge_po_set", lambda *a, **k: None)
    result = rec.reconcile_po_set(pid, cfg)
    assert result["status"] == "mismatched"
    with Session(eng) as s:
        assert s.get(POSet, pid).status == POSetStatus.mismatched


def test_quarantine_delete_keeps_a_same_named_file_with_different_bytes(tmp_path):
    import hashlib

    from app.services.quarantine import delete_quarantined

    cfg, eng = _cfg(tmp_path)
    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO-DEL-HASH", status=POSetStatus.quarantined)
        s.add(ps)
        s.commit()
        doc = _doc(s, cfg, ps.id, "PO", "shared-name.pdf", [("1", 100_000)])
        doc.sha256_hash = hashlib.sha256(Path(doc.stored_path).read_bytes()).hexdigest()
        pid = ps.id
        s.commit()

    incoming = Path(cfg.paths.input_folder) / "shared-name.pdf"
    incoming.write_bytes(b"%PDF-1.4 a different file with the same name")
    delete_quarantined(pid, cfg)
    assert incoming.exists(), "a hash mismatch must not be treated as the deleted document"


def test_price_parser_keeps_fractional_and_negative_rates():
    from app.services.sanitizer import parse_price_scaled

    assert parse_price_scaled("0.125") == 125
    assert parse_price_scaled("-250.00") == -250_000
    assert parse_price_scaled("1,234.50") == 1_234_500
    assert parse_price_scaled(None) == 0
    assert parse_price_scaled("") == 0
    assert parse_price_scaled("not a number") == 0


def test_force_merge_writes_a_truthful_reason(tmp_path):
    from app.services.merge import force_merge

    cfg, eng = _cfg(tmp_path)
    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO-FORCED", status=POSetStatus.mismatched)
        s.add(ps)
        s.commit()
        _doc(s, cfg, ps.id, "PO", "forced-po.pdf", [("1", 100_000)])
        pid = ps.id
        s.commit()

    out = force_merge(pid, cfg, justification="operator override for a customer delivery")
    assert out.is_file()
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        assert ps.status == POSetStatus.merged
        assert ps.reconcile_reason is not None
        assert "Force-merged" in ps.reconcile_reason


def test_foreign_key_and_open_set_indexes_exist(tmp_path):
    from sqlalchemy import inspect

    _, eng = _cfg(tmp_path)
    inspector = inspect(eng)
    doc_indexes = {i["name"] for i in inspector.get_indexes("documents")}
    line_indexes = {i["name"] for i in inspector.get_indexes("line_items")}
    audit_indexes = {i["name"] for i in inspector.get_indexes("audit_log")}
    set_indexes = {i["name"] for i in inspector.get_indexes("po_sets")}
    assert "ix_documents_po_set_id" in doc_indexes
    assert "ix_line_items_document_id" in line_indexes
    assert "ix_audit_log_po_set_id" in audit_indexes
    assert "uq_po_sets_open_po_no" in set_indexes


def test_age_seconds_treats_naive_db_timestamps_as_utc():
    from app.api.routes.dashboard import _age_seconds

    naive = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None)
    age = _age_seconds(naive)
    assert age is not None
    assert 3595 <= age <= 3605
    assert _age_seconds(None) is None
