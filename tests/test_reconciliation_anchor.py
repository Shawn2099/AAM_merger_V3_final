"""Anchor TDD tests — SPEC §12 examples, must pass before dev proceeds (AGENTS.md §7)."""

from __future__ import annotations


def test_fr_10_1_pinned(tmp_path) -> None:
    """FR-10.1: PO 100, DN 40+60, SI 70+30 → reconciled and auto-merged (SPEC §10 example)."""
    from pypdf import PdfWriter
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base
    from app.services.reconciliation import reconcile_po_set

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "fr10_1.db"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "stored").mkdir(parents=True, exist_ok=True)

    for name in ("po.pdf", "dn1.pdf", "dn2.pdf", "si1.pdf", "si2.pdf"):
        p = tmp_path / "stored" / name
        PdfWriter().write(str(p))

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO100", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

        doc_po = Document(
            sha256_hash="h_po",
            original_filename="po.pdf",
            stored_path=str(tmp_path / "stored" / "po.pdf"),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO100",
        )
        doc_dn1 = Document(
            sha256_hash="h_dn1",
            original_filename="dn1.pdf",
            stored_path=str(tmp_path / "stored" / "dn1.pdf"),
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO100",
        )
        doc_dn2 = Document(
            sha256_hash="h_dn2",
            original_filename="dn2.pdf",
            stored_path=str(tmp_path / "stored" / "dn2.pdf"),
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO100",
        )
        doc_si1 = Document(
            sha256_hash="h_si1",
            original_filename="si1.pdf",
            stored_path=str(tmp_path / "stored" / "si1.pdf"),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO100",
            invoice_no="INV-100",
        )
        doc_si2 = Document(
            sha256_hash="h_si2",
            original_filename="si2.pdf",
            stored_path=str(tmp_path / "stored" / "si2.pdf"),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO100",
            invoice_no="INV-100",
        )
        s.add_all([doc_po, doc_dn1, doc_dn2, doc_si1, doc_si2])
        s.commit()

        li_po = LineItem(
            document_id=doc_po.id,
            line_item_no="1",
            description="Widget A",
            quantity=100_000,
            unit_price=10_000,
        )
        li_dn1 = LineItem(
            document_id=doc_dn1.id,
            line_item_no="1",
            description="Widget A",
            quantity=40_000,
            unit_price=0,
        )
        li_dn2 = LineItem(
            document_id=doc_dn2.id,
            line_item_no="1",
            description="Widget A",
            quantity=60_000,
            unit_price=0,
        )
        li_si1 = LineItem(
            document_id=doc_si1.id,
            line_item_no="1",
            description="Widget A",
            quantity=70_000,
            unit_price=10_000,
        )
        li_si2 = LineItem(
            document_id=doc_si2.id,
            line_item_no="1",
            description="Widget A",
            quantity=30_000,
            unit_price=10_000,
        )
        s.add_all([li_po, li_dn1, li_dn2, li_si1, li_si2])
        s.commit()

    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "merged"
    with Session(eng) as s:
        ps_final = s.get(POSet, ps_id)
        assert ps_final.status == POSetStatus.merged
        assert ps_final.merged_output_path is not None


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
    cfg.paths.output_folder = str(tmp_path / "output")
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    from datetime import UTC, datetime

    with Session(eng) as s:
        ps = POSet(
            po_no_normalized="PO_LOCKED",
            status=POSetStatus.pending,
            locked_by_action="force_merge",
            locked_at=datetime.now(UTC),
        )
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
