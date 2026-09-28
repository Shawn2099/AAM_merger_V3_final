"""Wiring tests for the newly adopted v20.5 features.

- SKU rescue is honoured and can be disabled from config
- part_no survives extraction (item_code -> part_no) and rescues a real set
- non-GOODS rows are excluded from quantity math
- audit justification validation
"""

from __future__ import annotations


# --------------------------------------------------------------------------- config
def test_sku_rescue_can_be_disabled():
    """enable_sku_rescue: false must turn Step 3 off, not silently keep it on."""
    from app.services.matching import assign_lines

    po = [{"line_item_no": "1", "description": "Widget Alpha", "part_no": "AB-100"}]
    vendor = [{"line_item_no": None, "description": "Reworded entirely", "part_no": "ab100"}]

    on, reason_on = assign_lines(po, vendor, use_sku=True)
    assert reason_on is None
    assert len(on) == 1

    off, reason_off = assign_lines(po, vendor, use_sku=False)
    assert reason_off == "AMBIGUOUS_LINE_MATCH"
    assert off == {}


def test_sku_flag_default_on():
    from app.core.config import load_config

    cfg = load_config("config.example.yaml")
    assert cfg.matching.enable_sku_rescue is True
    assert cfg.matching.sanity_description_threshold == 40
    assert cfg.matching.fuzzy_margin == 5


# --------------------------------------------------------------------------- line_type
def test_line_type_normalization():
    from app.services.extraction import _norm_line_type

    assert _norm_line_type("goods") == "GOODS"
    assert _norm_line_type(" Tax ") == "TAX"
    assert _norm_line_type(None) == "GOODS"
    assert _norm_line_type("nonsense") == "GOODS"


# --------------------------------------------------------------------------- justification
def test_justification_validation():
    from app.services.quarantine import validate_justification

    assert validate_justification(None) is None
    assert validate_justification("   ") is None
    long_note = "Vendor confirmed the over-shipment in writing."
    assert validate_justification(long_note) == long_note
    for bad in ["too short", "nope"]:
        try:
            validate_justification(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


# --------------------------------------------------------------------------- DB wiring
def _cfg(tmp_path, name):
    from pathlib import Path

    from app.core.config import load_config

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / f"{name}.db")
    for sub in ("stored", "output", "quarantine"):
        p = tmp_path / sub
        p.mkdir(parents=True, exist_ok=True)
        setattr(
            cfg.paths, f"{sub}_documents_folder" if sub == "stored" else f"{sub}_folder", str(p)
        )
    Path(cfg.paths.output_folder).mkdir(parents=True, exist_ok=True)
    return cfg


def _pdf(path):
    from pypdf import PdfWriter

    PdfWriter().write(str(path))
    return path


def test_sku_rescue_wired_through_db(tmp_path):
    """PO part_no stored, DN/SI numbers absent and descriptions reworded ->
    Step 3 SKU rescue resolves them and the set merges.

    Note the boundary: a line that DOES carry a number which is on the PO is
    governed by Step 1's sanity guard and quarantines instead (see
    test_sku_does_not_override_sanity_guard).
    """
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base
    from app.services.reconciliation import reconcile_po_set

    cfg = _cfg(tmp_path, "skuwired")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="POSKU", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        pid = ps.id
        rows = [
            ("po", DocType.PO, "1", "Widget Alpha", 5000, "AB-100", {}),
            ("dn", DocType.DN, None, "Totally reworded text", 5000, "ab100", {}),
            (
                "si",
                DocType.SI,
                None,
                "Totally reworded text",
                5000,
                "AB-100",
                {"si_no": "INV-SKU", "invoice_no": "INV-SKU"},
            ),
        ]
        for name, dtype, lno, desc, qty, part_no, extra in rows:
            p = _pdf(tmp_path / "stored" / f"{name}.pdf")
            d = Document(
                sha256_hash=f"h_sku_{name}",
                original_filename=f"{name}.pdf",
                stored_path=str(p),
                doc_type=dtype,
                extraction_status=ExtractionStatus.valid,
                po_set_id=pid,
                po_no_normalized="POSKU",
                **extra,
            )
            s.add(d)
            s.commit()
            s.add(
                LineItem(
                    document_id=d.id,
                    line_item_no=lno,
                    description=desc,
                    quantity=qty,
                    unit_price=100000,
                    part_no=part_no,
                )
            )
            s.commit()

    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "merged", f"got {res['status']} reason={res.get('reason')}"


def test_sku_does_not_override_sanity_guard(tmp_path):
    """v20.5: a line whose number IS on the PO but whose description is
    unrelated quarantines as INDEX_DESCRIPTION_MISMATCH. SKU is Step 3 and
    must not paper over a wrong-index row."""
    from app.services.matching import match_vendor_line

    po = [{"line_item_no": "1", "description": "Widget Alpha", "part_no": "AB-100"}]
    vendor = [{"line_item_no": "1", "description": "Copper Pipe Fitting", "part_no": "AB-100"}]
    idx, reason = match_vendor_line(vendor[0], po)
    assert idx is None
    assert reason == "INDEX_DESCRIPTION_MISMATCH"


def test_non_goods_rows_excluded_from_math(tmp_path):
    """A TAX row on the DN must not inflate the DN aggregate."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base
    from app.services.reconciliation import reconcile_po_set

    cfg = _cfg(tmp_path, "taxrow")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="POTAX", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        pid = ps.id
        for name, dtype, extra in [
            ("po", DocType.PO, {}),
            ("dn", DocType.DN, {}),
            ("si", DocType.SI, {"si_no": "INV-TAX", "invoice_no": "INV-TAX"}),
        ]:
            p = _pdf(tmp_path / "stored" / f"{name}.pdf")
            d = Document(
                sha256_hash=f"h_tax_{name}",
                original_filename=f"{name}.pdf",
                stored_path=str(p),
                doc_type=dtype,
                extraction_status=ExtractionStatus.valid,
                po_set_id=pid,
                po_no_normalized="POTAX",
                **extra,
            )
            s.add(d)
            s.commit()
            s.add(
                LineItem(
                    document_id=d.id,
                    line_item_no="1",
                    description="Widget",
                    quantity=5000,
                    unit_price=100000,
                    line_type="GOODS",
                )
            )
            s.commit()
            if name == "dn":
                s.add(
                    LineItem(
                        document_id=d.id,
                        line_item_no="2",
                        description="VAT 5%",
                        quantity=250,
                        unit_price=100000,
                        line_type="TAX",
                    )
                )
                s.commit()

    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "merged", f"got {res['status']} reason={res.get('reason')}"
