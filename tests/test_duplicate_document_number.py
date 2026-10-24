"""Two DNs (or invoices) with one document number: a re-scan is held, pages of one are summed."""

import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
from app.models.base import Base
from app.services.reconciliation import reconcile_po_set


def _run(tmp_path, si_pages, po_lines=(("1", 100), ("2", 50))):
    """Reconcile PO(po_lines) + one DN + SI pages. si_pages: [(invoice_no, [(line, qty)])]."""
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "d.db"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    for f in ("output", "stored", "quarantine"):
        (tmp_path / f).mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    docs = [("po", DocType.PO, None, list(po_lines)), ("dn", DocType.DN, "DN-1", list(po_lines))]
    docs += [(f"si{i}", DocType.SI, no, ln) for i, (no, ln) in enumerate(si_pages)]
    with Session(eng) as s:
        ps = POSet(po_no_normalized="100", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        for name, typ, no, lines in docs:
            path = tmp_path / "stored" / f"{name}.pdf"
            w = PdfWriter()
            w.add_blank_page(width=100, height=100)
            w.write(str(path))
            d = Document(
                sha256_hash=f"h_{name}",
                original_filename=f"{name}.pdf",
                stored_path=str(path),
                doc_type=typ,
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps.id,
                po_no_normalized="100",
                dn_no=no if typ == DocType.DN else None,
                invoice_no=no if typ == DocType.SI else None,
            )
            s.add(d)
            s.commit()
            for ln, qty in lines:
                s.add(
                    LineItem(
                        document_id=d.id,
                        line_item_no=ln,
                        description="item",
                        unit_price=0,
                        quantity=qty * 1000,
                    )
                )
            s.commit()
        ps_id = ps.id
    return reconcile_po_set(ps_id, cfg)


def test_rescan_of_same_invoice_is_quarantined(tmp_path):
    res = _run(tmp_path, [("INV-7", [("1", 50)]), ("INV-7", [("1", 50), ("2", 50)])])
    assert res["status"] == "quarantined"
    assert res["reason"] == "duplicate_document_number"
    assert "INV-7" in res["detail"]


def test_numberless_lines_under_one_invoice_number_are_quarantined(tmp_path):
    res = _run(tmp_path, [("INV-7", [("", 100)]), ("INV-7", [("", 100)])])
    assert res["reason"] == "duplicate_document_number"


def test_pages_of_one_invoice_with_distinct_lines_are_summed(tmp_path):
    res = _run(tmp_path, [("INV-7", [("1", 100)]), ("INV-7", [("2", 50)])])
    assert res["status"] == "merged"


def test_empty_totals_page_with_the_same_invoice_number_is_covered(tmp_path):
    res = _run(tmp_path, [("INV-7", [("1", 100), ("2", 50)]), ("INV-7", [])])
    assert res["status"] == "merged"


@pytest.mark.parametrize("second", ["INV-8"])
def test_distinct_invoice_numbers_sum_as_part_deliveries(tmp_path, second):
    res = _run(tmp_path, [("INV-7", [("1", 100), ("2", 20)]), (second, [("2", 30)])])
    assert res["status"] == "merged"
