def test_reconcile_exact():
    """PO == AggDN == AggSI, exact integers, no tolerance."""
    from app.services.reconciliation import compare_po_set_lines

    res = compare_po_set_lines(
        [_l(1, 100)],
        [_l(1, 100)],
        [_l(1, 100)],
    )
    assert res["flags"] == []

    res = compare_po_set_lines([_l(1, 100)], [_l(1, 90)], [_l(1, 100)])
    assert [f["reason"] for f in res["flags"]] == ["quantity_mismatch"]


def _l(no, qty):
    return {"line_item_no": str(no), "description": "W", "quantity": qty * 1000}


def test_negative_quarantine():
    """A zero or negative quantity quarantines the whole set, it is never
    evaluated as a normal reconciliation case."""
    assert _reconcile_with_quantities(0, 0, 0) == "quarantined"
    assert _reconcile_with_quantities(100, -10, 100) == "quarantined"
    assert _reconcile_with_quantities(100, 100, 0) == "quarantined"


def test_price_check_removed():
    """The price check is not part of this product: quantities are the only
    signal. Retained as an explicit marker so the removal is visible rather
    than a silent gap. See AAM_merger_V3_PRODUCT.md, Accepted limitations."""
    import app.services.reconciliation as rec

    assert not hasattr(rec, "check_price")
    assert not hasattr(rec, "reconcile")


def _reconcile_with_quantities(po_q, dn_q, si_q):
    """Build a throwaway PO Set with one line per document and reconcile it."""
    import tempfile
    from pathlib import Path

    from pypdf import PdfWriter
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base
    from app.services.reconciliation import reconcile_po_set

    tmp = Path(tempfile.mkdtemp())
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp / "neg.db")
    cfg.paths.output_folder = str(tmp / "output")
    cfg.paths.stored_documents_folder = str(tmp / "stored")
    (tmp / "output").mkdir(parents=True, exist_ok=True)
    (tmp / "stored").mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="NEG", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id
        for n, (dtype, qty) in enumerate((("PO", po_q), ("DN", dn_q), ("SI", si_q)), start=1):
            p = tmp / "stored" / f"{n}_{dtype}.pdf"
            PdfWriter().write(str(p))
            d = Document(
                sha256_hash=f"neg_{n}",
                original_filename=f"{dtype}.pdf",
                stored_path=str(p),
                doc_type=DocType[dtype],
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps_id,
                po_no_normalized="NEG",
            )
            if dtype == "SI":
                d.si_no = "NEG-INV"
                d.invoice_no = "NEG-INV"
            s.add(d)
            s.commit()
            s.add(
                LineItem(
                    document_id=d.id,
                    line_item_no="1",
                    description="W",
                    quantity=qty * 1000,
                    unit_price=1000,
                )
            )
            s.commit()
    return reconcile_po_set(ps_id, cfg)["status"]


def _create_dummy_pdf(path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(str(path))


def test_reconcile_po_set_clean_auto_merges(tmp_path):
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base
    from app.services.reconciliation import reconcile_po_set

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "rec.db")
    cfg.paths.output_folder = str(tmp_path / "output")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    Path(cfg.paths.output_folder).mkdir(parents=True, exist_ok=True)
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO100", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

        po_pdf = tmp_path / "stored" / "po.pdf"
        dn1_pdf = tmp_path / "stored" / "dn1.pdf"
        dn2_pdf = tmp_path / "stored" / "dn2.pdf"
        si1_pdf = tmp_path / "stored" / "si1.pdf"
        si2_pdf = tmp_path / "stored" / "si2.pdf"
        for p in (po_pdf, dn1_pdf, dn2_pdf, si1_pdf, si2_pdf):
            _create_dummy_pdf(p)

        doc_po = Document(
            sha256_hash="h_po",
            original_filename="po.pdf",
            stored_path=str(po_pdf),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO100",
        )
        doc_dn1 = Document(
            sha256_hash="h_dn1",
            original_filename="dn1.pdf",
            stored_path=str(dn1_pdf),
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            dn_no="DN1",
        )
        doc_dn2 = Document(
            sha256_hash="h_dn2",
            original_filename="dn2.pdf",
            stored_path=str(dn2_pdf),
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            dn_no="DN2",
        )
        doc_si1 = Document(
            sha256_hash="h_si1",
            original_filename="si1.pdf",
            stored_path=str(si1_pdf),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            si_no="INV100",
            invoice_no="INV100",
        )
        doc_si2 = Document(
            sha256_hash="h_si2",
            original_filename="si2.pdf",
            stored_path=str(si2_pdf),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            si_no="INV101",
            invoice_no="INV101",
        )
        s.add_all([doc_po, doc_dn1, doc_dn2, doc_si1, doc_si2])
        s.commit()

        # PO qty = 100
        s.add(
            LineItem(
                document_id=doc_po.id,
                line_item_no="1",
                description="Item 1",
                quantity=100000,
                unit_price=50000,
            )
        )
        # DN1 = 40, DN2 = 60
        s.add(
            LineItem(
                document_id=doc_dn1.id,
                line_item_no="1",
                description="Item 1",
                quantity=40000,
                unit_price=50000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_dn2.id,
                line_item_no="1",
                description="Item 1",
                quantity=60000,
                unit_price=50000,
            )
        )
        # SI1 = 70, SI2 = 30
        s.add(
            LineItem(
                document_id=doc_si1.id,
                line_item_no="1",
                description="Item 1",
                quantity=70000,
                unit_price=50000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_si2.id,
                line_item_no="1",
                description="Item 1",
                quantity=30000,
                unit_price=50000,
            )
        )
        s.commit()

    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "merged"

    with Session(eng) as s:
        ps_after = s.get(POSet, ps_id)
        assert ps_after.status == POSetStatus.merged
        assert ps_after.merged_output_path is not None


def test_reconcile_po_set_customs_blocks_merge(tmp_path):
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base
    from app.services.reconciliation import reconcile_po_set

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "rec_c.db")
    cfg.paths.output_folder = str(tmp_path / "output_c")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored_c")
    Path(cfg.paths.output_folder).mkdir(parents=True, exist_ok=True)
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(
            po_no_normalized="PO_CUSTOMS", status=POSetStatus.pending, has_customs_toggle=True
        )
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

        po_pdf = tmp_path / "stored_c" / "po.pdf"
        dn_pdf = tmp_path / "stored_c" / "dn.pdf"
        si_pdf = tmp_path / "stored_c" / "si.pdf"
        for p in (po_pdf, dn_pdf, si_pdf):
            _create_dummy_pdf(p)

        doc_po = Document(
            sha256_hash="c_po",
            original_filename="po.pdf",
            stored_path=str(po_pdf),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO_CUSTOMS",
        )
        doc_dn = Document(
            sha256_hash="c_dn",
            original_filename="dn.pdf",
            stored_path=str(dn_pdf),
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            dn_no="DN1",
        )
        doc_si = Document(
            sha256_hash="c_si",
            original_filename="si.pdf",
            stored_path=str(si_pdf),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            si_no="INV1",
            invoice_no="INV1",
        )
        s.add_all([doc_po, doc_dn, doc_si])
        s.commit()

        s.add(
            LineItem(
                document_id=doc_po.id,
                line_item_no="1",
                description="Item 1",
                quantity=10000,
                unit_price=5000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_dn.id,
                line_item_no="1",
                description="Item 1",
                quantity=10000,
                unit_price=5000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_si.id,
                line_item_no="1",
                description="Item 1",
                quantity=10000,
                unit_price=5000,
            )
        )
        s.commit()

    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "blocked_customs"

    with Session(eng) as s:
        ps_after = s.get(POSet, ps_id)
        assert ps_after.status == POSetStatus.blocked_customs


def test_reconcile_po_decoy_mismatch_quarantines(tmp_path):
    """SPEC Â§7.3 FR-6.3: Attached doc with mismatched PO reference causes quarantine."""
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base
    from app.services.reconciliation import reconcile_po_set

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "decoy.db")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored_decoy")
    cfg.paths.quarantine_folder = str(tmp_path / "quarantine_decoy")
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)
    Path(cfg.paths.quarantine_folder).mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO100", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

        po_pdf = tmp_path / "stored_decoy" / "po.pdf"
        dn_pdf = tmp_path / "stored_decoy" / "dn.pdf"
        si_pdf = tmp_path / "stored_decoy" / "si.pdf"
        for p in (po_pdf, dn_pdf, si_pdf):
            _create_dummy_pdf(p)

        doc_po = Document(
            sha256_hash="decoy_po",
            original_filename="po.pdf",
            stored_path=str(po_pdf),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO100",
        )
        # Decoy PO reference on DN
        doc_dn = Document(
            sha256_hash="decoy_dn",
            original_filename="dn.pdf",
            stored_path=str(dn_pdf),
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="DECOY999",
            dn_no="DN1",
        )
        doc_si = Document(
            sha256_hash="decoy_si",
            original_filename="si.pdf",
            stored_path=str(si_pdf),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO100",
            si_no="INV1",
        )
        s.add_all([doc_po, doc_dn, doc_si])
        s.commit()

        s.add(
            LineItem(
                document_id=doc_po.id,
                line_item_no="1",
                description="Item",
                quantity=10000,
                unit_price=5000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_dn.id,
                line_item_no="1",
                description="Item",
                quantity=10000,
                unit_price=5000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_si.id,
                line_item_no="1",
                description="Item",
                quantity=10000,
                unit_price=5000,
            )
        )
        s.commit()

    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "quarantined"
    assert res["reason"] == "po_reference_mismatch"

    with Session(eng) as s:
        ps_after = s.get(POSet, ps_id)
        assert ps_after.status == POSetStatus.quarantined


def test_reconcile_missing_si_stays_pending(tmp_path):
    """PO Set missing SI should remain pending, not mismatched."""
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base
    from app.services.reconciliation import reconcile_po_set

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "pending.db")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored_pending")
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO_PENDING", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

        po_pdf = tmp_path / "stored_pending" / "po.pdf"
        dn_pdf = tmp_path / "stored_pending" / "dn.pdf"
        _create_dummy_pdf(po_pdf)
        _create_dummy_pdf(dn_pdf)

        doc_po = Document(
            sha256_hash="pen_po",
            original_filename="po.pdf",
            stored_path=str(po_pdf),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO_PENDING",
        )
        doc_dn = Document(
            sha256_hash="pen_dn",
            original_filename="dn.pdf",
            stored_path=str(dn_pdf),
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO_PENDING",
            dn_no="DN1",
        )
        s.add_all([doc_po, doc_dn])
        s.commit()

        s.add(
            LineItem(
                document_id=doc_po.id,
                line_item_no="1",
                description="Item",
                quantity=10000,
                unit_price=5000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_dn.id,
                line_item_no="1",
                description="Item",
                quantity=10000,
                unit_price=5000,
            )
        )
        s.commit()

    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "pending"

    with Session(eng) as s:
        ps_after = s.get(POSet, ps_id)
        assert ps_after.status == POSetStatus.pending


def test_reconcile_partial_fulfillment_stays_pending(tmp_path):
    """When a PO has multiple lines and vendor only delivers a subset, set stays pending."""
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base
    from app.services.reconciliation import reconcile_po_set

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "part.db")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored_part")
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO_PART", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

        po_pdf = tmp_path / "stored_part" / "po.pdf"
        dn_pdf = tmp_path / "stored_part" / "dn.pdf"
        si_pdf = tmp_path / "stored_part" / "si.pdf"
        for p in (po_pdf, dn_pdf, si_pdf):
            _create_dummy_pdf(p)

        doc_po = Document(
            sha256_hash="h_part_po",
            original_filename="po.pdf",
            stored_path=str(po_pdf),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO_PART",
        )
        doc_dn = Document(
            sha256_hash="h_part_dn",
            original_filename="dn.pdf",
            stored_path=str(dn_pdf),
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO_PART",
            dn_no="DN1",
        )
        doc_si = Document(
            sha256_hash="h_part_si",
            original_filename="si.pdf",
            stored_path=str(si_pdf),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO_PART",
            si_no="SI1",
        )
        s.add_all([doc_po, doc_dn, doc_si])
        s.commit()

        # PO has lines 1 and 2
        s.add(
            LineItem(
                document_id=doc_po.id,
                line_item_no="1",
                description="Item 1",
                quantity=10000,
                unit_price=1000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_po.id,
                line_item_no="2",
                description="Item 2",
                quantity=20000,
                unit_price=2000,
            )
        )
        # DN and SI only deliver line 1
        s.add(
            LineItem(
                document_id=doc_dn.id,
                line_item_no="1",
                description="Item 1",
                quantity=10000,
                unit_price=1000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_si.id,
                line_item_no="1",
                description="Item 1",
                quantity=10000,
                unit_price=1000,
            )
        )
        s.commit()

    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "pending"
    assert res["reason"] == "partial_fulfillment"

    with Session(eng) as s:
        ps_after = s.get(POSet, ps_id)
        assert ps_after.status == POSetStatus.pending


def test_reconcile_over_qty_fails_set(tmp_path):
    """FR-10.2 (decision 2026-09-05): fully-delivered lines with wrong qty
    (including over-delivery) fail the whole set â€” no partial pass."""
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base
    from app.services.reconciliation import reconcile_po_set

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "over.db")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored_over")
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO_OVER", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

        po_pdf = tmp_path / "stored_over" / "po.pdf"
        dn_pdf = tmp_path / "stored_over" / "dn.pdf"
        si_pdf = tmp_path / "stored_over" / "si.pdf"
        for p in (po_pdf, dn_pdf, si_pdf):
            _create_dummy_pdf(p)

        doc_po = Document(
            sha256_hash="h_over_po",
            original_filename="po.pdf",
            stored_path=str(po_pdf),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO_OVER",
        )
        doc_dn = Document(
            sha256_hash="h_over_dn",
            original_filename="dn.pdf",
            stored_path=str(dn_pdf),
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO_OVER",
            dn_no="DN1",
        )
        doc_si = Document(
            sha256_hash="h_over_si",
            original_filename="si.pdf",
            stored_path=str(si_pdf),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="PO_OVER",
            si_no="SI1",
        )
        s.add_all([doc_po, doc_dn, doc_si])
        s.commit()

        # PO lines 1 (10.0) and 2 (20.0); line 1 delivered exactly,
        # line 2 OVER-delivered (25.0 vs 20.0) on both DN and SI.
        s.add(
            LineItem(
                document_id=doc_po.id,
                line_item_no="1",
                description="Item 1",
                quantity=10000,
                unit_price=1000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_po.id,
                line_item_no="2",
                description="Item 2",
                quantity=20000,
                unit_price=2000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_dn.id,
                line_item_no="1",
                description="Item 1",
                quantity=10000,
                unit_price=1000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_dn.id,
                line_item_no="2",
                description="Item 2",
                quantity=25000,
                unit_price=2000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_si.id,
                line_item_no="1",
                description="Item 1",
                quantity=10000,
                unit_price=1000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_si.id,
                line_item_no="2",
                description="Item 2",
                quantity=25000,
                unit_price=2000,
            )
        )
        s.commit()

    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "mismatched"
    qty_flags = [f for f in res["flags"] if f.get("type") == "quantity"]
    assert any(f.get("line_item_no") == "2" for f in qty_flags)

    with Session(eng) as s:
        ps_after = s.get(POSet, ps_id)
        assert ps_after.status == POSetStatus.mismatched


def test_step_10_numbering_no_longer_maps_and_quarantines(tmp_path):
    """PINS A KNOWN LIMITATION â€” do not "fix" this without a product decision.

    A PO numbering its lines 10, 20 against DNs/SIs numbering theirs 1, 2 used
    to be reconciled by an ERP step-10 heuristic. That heuristic is not part of
    this product: line numbers are compared as printed, so 10 never matches 1
    and the set quarantines instead of merging.

    The failure is in the safe direction (quarantine, not a wrong merge), at the
    cost of a human reviewing sets whose vendors number the two documents
    differently. See AAM_merger_V3_PRODUCT.md, Accepted limitations.
    """
    from pathlib import Path

    from pypdf import PdfWriter
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base
    from app.services.reconciliation import reconcile_po_set

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "step10.db")
    cfg.paths.output_folder = str(tmp_path / "output_step10")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored_step10")
    Path(cfg.paths.output_folder).mkdir(parents=True, exist_ok=True)
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO_STEP10", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

        docs = {}
        for dtype in ("PO", "DN", "SI"):
            p = tmp_path / "stored_step10" / f"{dtype.lower()}.pdf"
            PdfWriter().write(str(p))
            d = Document(
                sha256_hash=f"step10_{dtype}",
                original_filename=f"{dtype.lower()}.pdf",
                stored_path=str(p),
                doc_type=DocType[dtype],
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps_id,
                po_no_normalized="PO_STEP10",
            )
            if dtype == "SI":
                d.si_no = "STEP10-INV"
                d.invoice_no = "STEP10-INV"
            s.add(d)
            s.commit()
            docs[dtype] = d

        # PO numbers 10 and 20; DN and SI number them 1 and 2.
        for dtype, nos in (("PO", ("10", "20")), ("DN", ("1", "2")), ("SI", ("1", "2"))):
            for no in nos:
                s.add(
                    LineItem(
                        document_id=docs[dtype].id,
                        line_item_no=no,
                        description=f"Item {no}",
                        quantity=1000,
                        unit_price=0,
                    )
                )
        s.commit()

    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "quarantined"
    assert res["reason"] == "unmatched_vendor_line"

    with Session(eng) as s:
        ps_after = s.get(POSet, ps_id)
        assert ps_after.merged_output_path is None


def test_sync_flow_sweep_reconciles_stale_mismatched_set(tmp_path):
    from pathlib import Path

    import yaml
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.flows.sync import sync_flow
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "sync_test.db"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.log_folder = tmp_path / "logs"
    cfg.ingestion.stability_poll_interval_seconds = 1
    cfg.ingestion.stability_poll_count = 1

    cfg_file = tmp_path / "config.yaml"
    cfg_data = cfg.model_dump()
    for k, v in cfg_data.get("paths", {}).items():
        cfg_data["paths"][k] = str(v)
    with open(cfg_file, "w") as f:
        yaml.dump(cfg_data, f)
    for p in [
        cfg.paths.input_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.log_folder,
    ]:
        Path(p).mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO_STALE", status=POSetStatus.mismatched)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

        po_pdf = tmp_path / "stored" / "po.pdf"
        dn_pdf = tmp_path / "stored" / "dn.pdf"
        si_pdf = tmp_path / "stored" / "si.pdf"
        _create_dummy_pdf(po_pdf)
        _create_dummy_pdf(dn_pdf)
        _create_dummy_pdf(si_pdf)

        doc_po = Document(
            sha256_hash="hash_po_stale",
            original_filename="po.pdf",
            stored_path=str(po_pdf),
            doc_type=DocType.PO,
            po_no_raw="PO_STALE",
            po_no_normalized="PO_STALE",
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
        )
        doc_dn = Document(
            sha256_hash="hash_dn_stale",
            original_filename="dn.pdf",
            stored_path=str(dn_pdf),
            doc_type=DocType.DN,
            po_no_raw="PO_STALE",
            po_no_normalized="PO_STALE",
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
        )
        doc_si = Document(
            sha256_hash="hash_si_stale",
            original_filename="si.pdf",
            stored_path=str(si_pdf),
            doc_type=DocType.SI,
            po_no_raw="PO_STALE",
            po_no_normalized="PO_STALE",
            si_no="INV-STALE-001",
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
        )
        s.add_all([doc_po, doc_dn, doc_si])
        s.commit()
        s.refresh(doc_po)
        s.refresh(doc_dn)
        s.refresh(doc_si)

        s.add(
            LineItem(
                document_id=doc_po.id,
                line_item_no="1",
                description="Item A",
                quantity=5000,
                unit_price=100000,
            )
        )
        s.add(
            LineItem(
                document_id=doc_dn.id,
                line_item_no="1",
                description="Item A",
                quantity=5000,
                unit_price=0,
            )
        )
        s.add(
            LineItem(
                document_id=doc_si.id,
                line_item_no="1",
                description="Item A",
                quantity=5000,
                unit_price=100000,
            )
        )
        s.commit()

    # Input folder has no new files. sync_flow should sweep and reconcile the stale mismatched set to merged.
    run_fn = getattr(sync_flow, "fn", sync_flow)
    result = run_fn(str(cfg_file))
    assert result["reconciled_count"] >= 1

    with Session(eng) as s:
        ps_after = s.get(POSet, ps_id)
        assert ps_after.status == POSetStatus.merged
        assert ps_after.merged_output_path is not None


def test_re_reconcile_merged_set_preserves_naming_reason(tmp_path):
    """Re-reconciling a merged set must not overwrite existing naming explanation."""
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import POSet, POSetStatus
    from app.models.base import Base
    from app.services.reconciliation import reconcile_po_set

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "merged_preserve.db")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    cfg.paths.output_folder = str(tmp_path / "output")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    original_reason = (
        "Fully reconciled — packet merged. No invoice number was extracted; "
        "packet named 'PO123' from the PO number"
    )

    with Session(eng) as s:
        ps = POSet(
            po_no_normalized="PO123",
            status=POSetStatus.merged,
            merged_output_path=str(tmp_path / "PO123.pdf"),
            reconcile_reason=original_reason,
        )
        s.add(ps)
        s.commit()
        ps_id = ps.id

    res = reconcile_po_set(ps_id, cfg)
    assert res.get("status") == "merged"

    with Session(eng) as s:
        ps_after = s.get(POSet, ps_id)
        assert ps_after.reconcile_reason == original_reason

