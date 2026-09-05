"""Tests for merge — order SI→DN→PO→(AWB→Customs), filename=invoice_no, merged immutable (FR-14.1-14.7)."""

from pathlib import Path

from app.core.config import load_config


def _cfg_with_tmp(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test.db"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    (tmp_path / "stored").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    return cfg


def _tiny_pdf(p: Path, width: int = 200, height: int = 200) -> Path:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=width, height=height)
    w.write(str(p))
    return p


def _create_poset_with_docs(tmp_path, cfg, po_no="PO-1234", status=None, docs_info=None):
    """Create POSet and Document rows with tiny PDFs.

    docs_info: list of dict(doc_type, si_no/invoice_no, width) — width used to identify order.
    """
    import hashlib

    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
    from app.models.base import Base

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    if status is None:
        status = POSetStatus.pending
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=status)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id
        for info in docs_info or []:
            dt = info["doc_type"]
            w = info.get("width", 200)
            h = info.get("height", 200)
            pdf_path = tmp_path / f"{dt}_{hashlib.sha256(dt.encode()).hexdigest()[:6]}.pdf"
            # ensure unique per doc
            pdf_path = tmp_path / f"{dt}_{s.query(Document).count()}_{w}.pdf"
            _tiny_pdf(pdf_path, width=w, height=h)
            sha = hashlib.sha256(f"{ps_id}-{dt}-{s.query(Document).count()}".encode()).hexdigest()
            doc = Document(
                sha256_hash=sha,
                original_filename=f"{dt}.pdf",
                stored_path=str(pdf_path),
                doc_type=DocType(dt),
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps_id,
                si_no=info.get("si_no"),
                invoice_no=info.get("invoice_no"),
            )
            s.add(doc)
            s.flush()
            # one evidence line per doc — auto-merge requires line items (W-22)
            from app.models import LineItem as _LineItem

            s.add(
                _LineItem(
                    document_id=doc.id,
                    line_item_no="1",
                    description=f"{dt} item",
                    quantity=1000,
                    unit_price=1000,
                )
            )
        s.commit()
        return ps_id


def test_merge_order_si_dn_po(tmp_path):
    """FR-14.3: order SI→DN→PO, 3 pages."""
    from pypdf import PdfReader

    from app.services.merge import merge_po_set

    cfg = _cfg_with_tmp(tmp_path)
    # widths encode order: SI=400, DN=300, PO=200 => merged should be 400,300,200
    po_set_id = _create_poset_with_docs(
        tmp_path,
        cfg,
        docs_info=[
            {"doc_type": "PO", "width": 200},
            {"doc_type": "DN", "width": 300},
            {"doc_type": "SI", "width": 400, "si_no": "INV-001"},
        ],
    )
    out = merge_po_set(po_set_id, cfg)
    assert out is not None
    assert Path(out).exists()
    reader = PdfReader(str(out))
    assert len(reader.pages) == 3
    # verify order by page mediabox widths
    widths = [float(p.mediabox.width) for p in reader.pages]
    assert widths == [400, 300, 200], f"expected SI→DN→PO order, got {widths}"


def test_merge_filename_is_invoice_no(tmp_path):
    """FR-14.5: output filename is Invoice/SI number."""
    from app.services.merge import merge_po_set

    cfg = _cfg_with_tmp(tmp_path)
    po_set_id = _create_poset_with_docs(
        tmp_path,
        cfg,
        docs_info=[
            {"doc_type": "SI", "si_no": "INV-999", "width": 200},
            {"doc_type": "DN", "width": 200},
            {"doc_type": "PO", "width": 200},
        ],
    )
    out = merge_po_set(po_set_id, cfg)
    assert out is not None
    assert Path(out).name == "INV-999.pdf"

    # also test invoice_no fallback when si_no missing
    po_set_id2 = _create_poset_with_docs(
        tmp_path,
        cfg,
        po_no="PO-INV2",
        docs_info=[
            {"doc_type": "SI", "invoice_no": "INV-777", "width": 200},
            {"doc_type": "PO", "width": 200},
        ],
    )
    out2 = merge_po_set(po_set_id2, cfg)
    assert Path(out2).name == "INV-777.pdf"


def test_merge_order_with_customs(tmp_path):
    """FR-14.3: SI→DN→PO→(SHIPPING→CUSTOMS) when customs applies."""
    from pypdf import PdfReader

    from app.services.merge import merge_po_set

    cfg = _cfg_with_tmp(tmp_path)
    # include SHIPPING (AWB) and CUSTOMS after PO
    po_set_id = _create_poset_with_docs(
        tmp_path,
        cfg,
        docs_info=[
            {"doc_type": "PO", "width": 200},
            {"doc_type": "DN", "width": 300},
            {"doc_type": "SI", "si_no": "INV-CUST", "width": 400},
            {"doc_type": "SHIPPING", "width": 500},
            {"doc_type": "CUSTOMS", "width": 600},
        ],
    )
    out = merge_po_set(po_set_id, cfg)
    assert out is not None
    reader = PdfReader(str(out))
    assert len(reader.pages) == 5
    widths = [float(p.mediabox.width) for p in reader.pages]
    assert widths == [400, 300, 200, 500, 600], f"expected SI→DN→PO→SHIPPING→CUSTOMS, got {widths}"


def test_merge_returns_none_if_mismatched(tmp_path):
    """FR-14.1: mismatched/quarantined/blocked not merged (returns None)."""
    from app.models import POSetStatus
    from app.services.merge import merge_po_set

    cfg = _cfg_with_tmp(tmp_path)
    for st in [POSetStatus.mismatched, POSetStatus.quarantined]:
        po_set_id = _create_poset_with_docs(
            tmp_path,
            cfg,
            po_no=f"PO-{st.value}",
            status=st,
            docs_info=[
                {"doc_type": "SI", "si_no": "INV-X", "width": 200},
                {"doc_type": "PO", "width": 200},
            ],
        )
        out = merge_po_set(po_set_id, cfg)
        assert out is None, f"expected None for status {st.value}"


def test_merge_blocked_customs_returns_none(tmp_path):
    """FR-14.1: blocked_customs must not auto-merge."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet, POSetStatus
    from app.services.merge import merge_po_set

    cfg = _cfg_with_tmp(tmp_path)
    po_set_id = _create_poset_with_docs(
        tmp_path,
        cfg,
        docs_info=[
            {"doc_type": "SI", "si_no": "INV-BLOCKED", "width": 200},
            {"doc_type": "PO", "width": 200},
        ],
    )
    # toggle customs -> blocked
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        ps.has_customs_toggle = True
        ps.status = POSetStatus.blocked_customs
        s.commit()
    out = merge_po_set(po_set_id, cfg)
    assert out is None


def test_merged_immutable_first_completed_wins(tmp_path):
    """FR-14.6/14.7: once merged, first output wins, not overwritten."""
    from pypdf import PdfReader
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet
    from app.services.merge import merge_po_set

    cfg = _cfg_with_tmp(tmp_path)
    po_set_id = _create_poset_with_docs(
        tmp_path,
        cfg,
        docs_info=[
            {"doc_type": "SI", "si_no": "INV-IMMUT", "width": 400},
            {"doc_type": "PO", "width": 200},
        ],
    )
    out1 = merge_po_set(po_set_id, cfg)
    assert out1 is not None
    _mtime = Path(out1).stat().st_mtime
    reader1 = PdfReader(str(out1))
    assert len(reader1.pages) == 2

    # second call should return same path, not re-create/overwrite
    out2 = merge_po_set(po_set_id, cfg)
    assert Path(out2) == Path(out1)
    assert Path(out2).exists()
    # merged_at/status immutable check in DB
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        assert ps.status.value == "merged"
        assert ps.merged_output_path == str(out1)
        assert ps.merged_at is not None

    # even if we add a new DN doc after merged, merge should still return original
    _tiny_extra = tmp_path / "extra_dn.pdf"
    _tiny_pdf(_tiny_extra, width=999)
    import hashlib

    from app.models import DocType, Document, ExtractionStatus

    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        doc = Document(
            sha256_hash=hashlib.sha256(b"extra").hexdigest(),
            original_filename="DN_extra.pdf",
            stored_path=str(_tiny_extra),
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=po_set_id,
        )
        s.add(doc)
        s.commit()
    out3 = merge_po_set(po_set_id, cfg)
    assert Path(out3) == Path(out1)
    reader3 = PdfReader(str(out3))
    assert len(reader3.pages) == 2, "immutable: should not grow to 3 pages"


def test_force_merge_bypasses_and_writes_audit(tmp_path):
    """Force merge bypasses mismatched/blocked and writes audit if implemented."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet, POSetStatus
    from app.services.merge import force_merge

    cfg = _cfg_with_tmp(tmp_path)
    po_set_id = _create_poset_with_docs(
        tmp_path,
        cfg,
        status=POSetStatus.mismatched,
        docs_info=[
            {"doc_type": "SI", "si_no": "INV-FORCE", "width": 400},
            {"doc_type": "PO", "width": 200},
        ],
    )
    # force_merge should succeed even when mismatched
    out = force_merge(po_set_id, cfg)
    assert out is not None
    assert Path(out).exists()
    assert Path(out).name == "INV-FORCE.pdf"
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        assert ps.status.value == "merged"
        assert ps.merged_output_path == str(out)


def test_combined_excludes_separate_docs(tmp_path):
    """FR-14.7: COMBINED + separate docs → authoritative COMBINED packet only,
    separate docs remain visible but non-authoritative (no content doubling)."""
    from pypdf import PdfReader

    from app.services.merge import merge_po_set

    cfg = _cfg_with_tmp(tmp_path)
    po_set_id = _create_poset_with_docs(
        tmp_path,
        cfg,
        docs_info=[
            {"doc_type": "COMBINED", "invoice_no": "INV-COMB-1", "width": 100},
            {"doc_type": "SI", "si_no": "INV-SI-9", "width": 400},
            {"doc_type": "DN", "width": 300},
            {"doc_type": "PO", "width": 200},
        ],
    )
    out = merge_po_set(po_set_id, cfg)
    assert out is not None
    reader = PdfReader(str(out))
    assert len(reader.pages) == 1


def test_zero_evidence_auto_merge_refused(tmp_path):
    """W-22: auto-merge with zero line items refuses, status untouched."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
    from app.models.base import Base
    from app.services.merge import merge_po_set

    cfg = _cfg_with_tmp(tmp_path)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO_EMPTY", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id
        pdf = tmp_path / "po_empty.pdf"
        _tiny_pdf(pdf)
        s.add(
            Document(
                sha256_hash="h_empty_po",
                original_filename="po.pdf",
                stored_path=str(pdf),
                doc_type=DocType.PO,
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps_id,
                si_no="INV-EMPTY",
            )
        )
        s.commit()

    assert merge_po_set(ps_id, cfg) is None
    with Session(eng) as s:
        ps_after = s.get(POSet, ps_id)
        assert ps_after.status == POSetStatus.pending
        assert ps_after.merged_output_path is None


def test_standard_set_names_strictly_from_si(tmp_path):
    """W-2: standard set where only the DN carries an invoice number cannot
    be named from it → auto-merge refuses instead of misnaming the packet."""
    from app.services.merge import merge_po_set

    cfg = _cfg_with_tmp(tmp_path)
    po_set_id = _create_poset_with_docs(
        tmp_path,
        cfg,
        docs_info=[
            {"doc_type": "SI"},
            {"doc_type": "DN", "invoice_no": "DN-INV-9"},
            {"doc_type": "PO"},
        ],
    )
    assert merge_po_set(po_set_id, cfg) is None
