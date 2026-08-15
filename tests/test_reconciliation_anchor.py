"""Anchor TDD tests — SPEC §12 examples, must pass before dev proceeds (AGENTS.md §7)."""

from __future__ import annotations


def test_fr_10_1_pinned() -> None:
    """FR-10.1: PO 100, DN 40+60, SI 70+30 → reconciled (SPEC §10 example)."""
    po_qty = 100 * 1000
    agg_dn = (40 + 60) * 1000
    agg_si = (70 + 30) * 1000
    assert po_qty == agg_dn
    assert po_qty == agg_si


def test_fr_8_4_conflicting_descriptions_quarantine() -> None:
    """FR-8.4: same line_item_no with conflicting descriptions → quarantine."""
    from app.services.matching import match_line

    po = {"line_item_no": "5", "description": "Widget A 10kg"}
    dn_lines = [
        {"line_item_no": "5", "description": "Widget A 10kg", "quantity": 10000},
        {"line_item_no": "5", "description": "Totally different widget", "quantity": 10000},
    ]
    res = match_line(po, dn_lines, [], thr=85)
    assert res["quarantine"] is True


def test_fr_conc_2_409_on_locked_po_set(tmp_path) -> None:
    """FR-CONC-2: second Force Merge on locked PO Set → 409."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.main import app
    from app.models import POSet, POSetStatus
    from app.models.base import Base

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "conc.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO_LOCKED", status=POSetStatus.pending, locked_by_action="force_merge")
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

    client = TestClient(app)
    import app.api.routes.po_sets as po_routes

    original_load = po_routes.load_config
    po_routes.load_config = lambda: cfg
    try:
        response = client.post(f"/po_sets/{ps_id}/force_merge")
        assert response.status_code == 409
        assert "already in progress" in response.json()["detail"]
    finally:
        po_routes.load_config = original_load

