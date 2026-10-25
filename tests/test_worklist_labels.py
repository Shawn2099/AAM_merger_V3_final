"""Batch 1 step 8: worklist labels on the dashboard.

Display only. The status of a set does not change: a short delivery stays
`mismatched`, and it is shown as "Waiting (part delivered)" only when every
disagreeing quantity is below the PO (decision D16).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
from app.models.base import Base


@pytest.fixture()
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c.paths.database_path = tmp_path / "worklist.db"
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


@pytest.fixture()
def client(cfg, monkeypatch):
    monkeypatch.setattr("app.api.routes.dashboard.load_config", lambda path=None: cfg)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _pdf(path: Path) -> Path:
    w = PdfWriter()
    w.add_blank_page(width=100, height=200)
    with open(path, "wb") as f:
        w.write(f)
    return path


def _make_set(cfg, po_key: str, status: POSetStatus, rows, reason: str | None = None) -> int:
    """rows: list of (doc_type, [(line_no, description, qty)])"""
    with Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized=po_key, status=status, reconcile_reason=reason)
        s.add(ps)
        s.flush()
        for i, (dtype, lines) in enumerate(rows):
            stored = _pdf(cfg.paths.stored_documents_folder / f"{po_key}-{i}.pdf")
            doc = Document(
                sha256_hash=f"{po_key}-{i}".ljust(64, "w"),
                original_filename=f"{po_key}-{i}.pdf",
                stored_path=str(stored),
                doc_type=dtype,
                extraction_status=ExtractionStatus.valid,
                po_no_raw=po_key,
                po_no_normalized=po_key,
                po_set_id=ps.id,
            )
            s.add(doc)
            s.flush()
            for line_no, description, qty in lines:
                s.add(
                    LineItem(
                        document_id=doc.id,
                        line_item_no=line_no,
                        description=description,
                        quantity=int(qty * 1000),
                    )
                )
        s.commit()
        return ps.id


def _full(po_qty, dn_qty, si_qty):
    return [
        (DocType.PO, [("10", "GATE VALVE", po_qty)]),
        (DocType.DN, [("10", "GATE VALVE", dn_qty)]),
        (DocType.SI, [("10", "GATE VALVE", si_qty)]),
    ]


def test_under_delivery_reads_as_waiting_and_stays_mismatched(cfg, client):
    _make_set(cfg, "4500000101", POSetStatus.mismatched, _full(100, 60, 60))
    page = client.get("/dashboard").text
    assert "Waiting (part delivered)" in page
    filtered = client.get("/dashboard?status=mismatched").text
    assert "4500000101" in filtered


def test_over_delivery_stays_needs_review(cfg, client):
    _make_set(cfg, "4500000102", POSetStatus.mismatched, _full(100, 120, 120))
    page = client.get("/dashboard").text
    assert "Needs Review" in page
    assert "Waiting (part delivered)" not in page


def test_mixed_short_and_over_stays_needs_review(cfg, client):
    rows = [
        (DocType.PO, [("10", "GATE VALVE", 100), ("20", "BALL VALVE", 50)]),
        (DocType.DN, [("10", "GATE VALVE", 60), ("20", "BALL VALVE", 70)]),
        (DocType.SI, [("10", "GATE VALVE", 60), ("20", "BALL VALVE", 70)]),
    ]
    _make_set(cfg, "4500000103", POSetStatus.mismatched, rows)
    page = client.get("/dashboard").text
    assert "Needs Review" in page
    assert "Waiting (part delivered)" not in page


def test_two_po_files_are_flagged_as_a_decision(cfg, client):
    rows = [
        (DocType.PO, [("10", "GATE VALVE", 100)]),
        (DocType.PO, [("20", "BALL VALVE", 50)]),
        (DocType.DN, [("10", "GATE VALVE", 100)]),
        (DocType.SI, [("10", "GATE VALVE", 100)]),
    ]
    _make_set(cfg, "4500000104", POSetStatus.pending, rows)
    page = client.get("/dashboard").text
    assert "Needs a decision: 2 PO files" in page


def test_one_po_pending_set_has_no_decision_tag(cfg, client):
    _make_set(cfg, "4500000105", POSetStatus.pending, _full(100, 100, 100)[:2])
    page = client.get("/dashboard").text
    assert "Pending / Waiting" in page
    assert "Needs a decision" not in page


def test_detail_heading_says_waiting_for_a_short_delivery(cfg, client):
    set_id = _make_set(
        cfg, "4500000106", POSetStatus.mismatched, _full(100, 60, 60), reason="delivered 60 of 100"
    )
    page = client.get(f"/po_sets/{set_id}/view").text
    assert "Why this set is waiting" in page
    assert "Why this set needs review" not in page


def test_detail_heading_keeps_needs_review_for_an_over_delivery(cfg, client):
    set_id = _make_set(
        cfg,
        "4500000107",
        POSetStatus.mismatched,
        _full(100, 120, 120),
        reason="delivered 120 of 100",
    )
    page = client.get(f"/po_sets/{set_id}/view").text
    assert "Why this set needs review" in page


def test_batched_shortfall_matches_the_per_set_result_and_stays_constant_in_queries(cfg):
    """Review A6: one batch for many sets gives the same answer and a fixed query count."""
    from sqlalchemy import event

    from app.api.routes.dashboard import _shortfall_shape, _shortfall_shapes

    under = _make_set(cfg, "4500000201", POSetStatus.mismatched, _full(100, 60, 60))
    over = _make_set(cfg, "4500000202", POSetStatus.mismatched, _full(100, 120, 120))
    empty = _make_set(cfg, "4500000203", POSetStatus.mismatched, [])
    ids = [under, over, empty]

    with Session(get_engine(cfg)) as s:
        batch = _shortfall_shapes(s, ids, cfg)
        per_set = {i: _shortfall_shape(s, s.get(POSet, i), cfg) for i in ids}
    assert batch == per_set
    assert batch[under] == "under" and batch[over] == "review" and batch[empty] == "review"

    engine = get_engine(cfg)
    counted: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        counted.append(statement)

    many = [under] + [
        _make_set(cfg, f"4500000{300 + n}", POSetStatus.mismatched, _full(100, 60, 60))
        for n in range(20)
    ]
    with Session(engine) as s:
        event.listen(engine, "before_cursor_execute", _count)
        try:
            _shortfall_shapes(s, [under], cfg)
            few = len(counted)
            counted.clear()
            _shortfall_shapes(s, many, cfg)
            many_count = len(counted)
        finally:
            event.remove(engine, "before_cursor_execute", _count)
    assert many_count == few, (few, many_count)
