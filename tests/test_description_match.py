"""Description-fallback matches are honest, visible, and non-blocking.

The engine may attach a vendor row to a PO line by DESCRIPTION when there is no
usable printed line number. That link is a fallback, not an exact match. The
product decision (2026-10-23) is: still merge if the quantities reconcile, but
show an amber review tag so the CA checks the item. These tests pin both halves
— the tag exists, and it never changes the verdict.
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
from app.services.matching import group_by_line_no
from app.services.reconciliation import compare_po_set_lines, reconcile_po_set


@pytest.fixture()
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c.paths.database_path = tmp_path / "desc.db"
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
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: cfg)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _pdf(path: Path, width: float) -> Path:
    w = PdfWriter()
    w.add_blank_page(width=width, height=200)
    with open(path, "wb") as f:
        w.write(f)
    return path


def _make_set(cfg, po_key: str, rows, *, si_number: str | None = None) -> int:
    """rows: list of (doc_type, [(line_no | None, description, qty)])"""
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_key, status=POSetStatus.pending)
        s.add(ps)
        s.flush()
        for i, (dtype, lines) in enumerate(rows):
            stored = _pdf(cfg.paths.stored_documents_folder / f"{po_key}-{i}.pdf", 100 + i)
            doc = Document(
                sha256_hash=f"{po_key}-{i}".ljust(64, "z"),
                original_filename=f"{po_key}-{i}.pdf",
                stored_path=str(stored),
                doc_type=dtype,
                extraction_status=ExtractionStatus.valid,
                po_no_raw=po_key,
                po_no_normalized=po_key,
                po_set_id=ps.id,
                si_no=si_number if dtype == DocType.SI else None,
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


# ---------------------------------------------------------------------------
# The matcher records the fallback, and only the fallback
# ---------------------------------------------------------------------------


def test_a_numberless_vendor_row_is_recorded_as_a_description_match():
    po = [{"line_item_no": "1", "description": "GATE VALVE 2IN", "quantity": 5}]
    dn = [{"line_item_no": None, "description": "GATE VALVE 2 IN", "quantity": 5}]
    trace: list[dict] = []
    po_tot, v_tot, orphans, fail = group_by_line_no(po, dn, 85, desc_matches=trace)

    assert not orphans and fail is None
    assert v_tot == po_tot
    assert len(trace) == 1
    assert trace[0]["po_key"] == "1"
    assert trace[0]["vendor_line_item_no"] is None


def test_an_exact_number_match_is_not_recorded():
    po = [{"line_item_no": "1", "description": "GATE VALVE 2IN", "quantity": 5}]
    dn = [{"line_item_no": "1", "description": "SOMETHING ELSE ENTIRELY", "quantity": 5}]
    trace: list[dict] = []
    group_by_line_no(po, dn, 85, desc_matches=trace)
    assert trace == []


def test_a_number_absent_from_the_po_falls_back_and_is_recorded():
    po = [{"line_item_no": None, "description": "GATE VALVE 2IN", "quantity": 5}]
    dn = [{"line_item_no": "7", "description": "GATE VALVE 2 IN", "quantity": 5}]
    trace: list[dict] = []
    group_by_line_no(po, dn, 85, desc_matches=trace)
    assert len(trace) == 1


def test_compare_exposes_the_flag_without_a_failure():
    po = [{"line_item_no": "1", "description": "GATE VALVE 2IN", "quantity": 5}]
    dn = [{"line_item_no": None, "description": "GATE VALVE 2 IN", "quantity": 5}]
    si = [{"line_item_no": None, "description": "GATE VALVE 2 IN", "quantity": 5}]
    result = compare_po_set_lines(po, dn, si, 85)

    types = [f["type"] for f in result["flags"]]
    assert types.count("description_match") == 2
    assert "quantity" not in types
    assert "identification" not in types


# ---------------------------------------------------------------------------
# The verdict is unchanged, and the amber tag is persisted for the UI
# ---------------------------------------------------------------------------


def test_a_description_matched_set_still_merges_and_is_tagged(cfg):
    set_id = _make_set(
        cfg,
        "DESC-MERGE",
        [
            (DocType.PO, [("1", "GATE VALVE 2IN", 5)]),
            (DocType.DN, [(None, "GATE VALVE 2 IN", 5)]),
            (DocType.SI, [(None, "GATE VALVE 2 IN", 5)]),
        ],
        si_number="INV-DESC",
    )
    result = reconcile_po_set(set_id, cfg)
    assert result["status"] == "merged"
    assert Path(result["merged_output_path"]).is_file()

    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, set_id)
        assert ps.status == POSetStatus.merged
        assert ps.description_match_count == 2
        assert "matched by description" in (ps.reconcile_reason or "").lower()


def test_an_exact_number_match_is_not_tagged(cfg):
    set_id = _make_set(
        cfg,
        "EXACT-MERGE",
        [
            (DocType.PO, [("1", "GATE VALVE 2IN", 5)]),
            (DocType.DN, [("1", "GATE VALVE 2IN", 5)]),
            (DocType.SI, [("1", "GATE VALVE 2IN", 5)]),
        ],
        si_number="INV-EXACT",
    )
    result = reconcile_po_set(set_id, cfg)
    assert result["status"] == "merged"
    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, set_id)
        assert ps.description_match_count == 0
        assert "matched by description" not in (ps.reconcile_reason or "").lower()


def test_a_description_match_does_not_mask_a_quantity_mismatch(cfg):
    set_id = _make_set(
        cfg,
        "DESC-MISMATCH",
        [
            (DocType.PO, [("1", "GATE VALVE 2IN", 5)]),
            (DocType.DN, [(None, "GATE VALVE 2 IN", 4)]),
            (DocType.SI, [(None, "GATE VALVE 2 IN", 5)]),
        ],
        si_number="INV-MISMATCH",
    )
    result = reconcile_po_set(set_id, cfg)
    assert result["status"] == "mismatched"
    assert any(f["type"] == "description_match" for f in result["flags"])
    assert any(f["type"] == "quantity" for f in result["flags"])


# ---------------------------------------------------------------------------
# The UI: amber "Merged • review" on the dashboard and detail, line tag in matrix
# ---------------------------------------------------------------------------


def test_dashboard_shows_an_amber_review_tag_for_a_merged_description_match(cfg, client):
    set_id = _make_set(
        cfg,
        "DESC-UI",
        [
            (DocType.PO, [("1", "GATE VALVE 2IN", 5)]),
            (DocType.DN, [(None, "GATE VALVE 2 IN", 5)]),
            (DocType.SI, [(None, "GATE VALVE 2 IN", 5)]),
        ],
        si_number="INV-UI",
    )
    assert reconcile_po_set(set_id, cfg)["status"] == "merged"

    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "Merged • review" in page.text
    assert "Matched by description - needs a second check (2 lines)" in page.text

    detail = client.get(f"/po_sets/{set_id}/view")
    assert detail.status_code == 200
    assert "Description Match" in detail.text
    assert "desc match" in detail.text
    assert "matched to PO line 1 by description" in detail.text


def test_a_plain_merged_set_keeps_the_green_badge(cfg, client):
    set_id = _make_set(
        cfg,
        "PLAIN-MERGE",
        [
            (DocType.PO, [("1", "VALVE", 5)]),
            (DocType.DN, [("1", "VALVE", 5)]),
            (DocType.SI, [("1", "VALVE", 5)]),
        ],
        si_number="INV-PLAIN",
    )
    assert reconcile_po_set(set_id, cfg)["status"] == "merged"
    page = client.get("/dashboard")
    assert "Merged &amp; Ready" in page.text
    assert "Merged • review" not in page.text
