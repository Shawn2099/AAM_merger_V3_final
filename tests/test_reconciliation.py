def test_reconcile_exact():
    from app.services.reconciliation import reconcile

    assert reconcile(100000, 100000, 100000)["ok"] is True  # 100*1000
    assert reconcile(100000, 90000, 100000)["ok"] is False


def test_negative_quarantine():
    from app.services.reconciliation import reconcile

    assert reconcile(100000, -10000, 100000)["quarantine"] is True
    assert reconcile(0, 0, 0)["quarantine"] is True
    assert reconcile(100000, 100000, 0)["quarantine"] is True


def test_price_flag_priority():
    from app.services.reconciliation import check_price

    assert check_price(10000, 10000)["flag"] is False
    assert check_price(10000, 9000)["flag"] is True  # price-only not block, but flagged


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
