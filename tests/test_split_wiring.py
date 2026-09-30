"""Split wiring: extraction sterile parent + ingestion linkage (PLAN Rev 2 §6).

No API key needed — `_call_vlm` is mocked to return the response dict.
"""

from hashlib import sha256
from pathlib import Path

from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus


def _cfg(tmp_path, name="w.db"):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / name)
    cfg.paths.input_folder = str(tmp_path / "input")
    cfg.paths.output_folder = str(tmp_path / "output")
    cfg.paths.quarantine_folder = str(tmp_path / "quarantine")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    cfg.paths.combined_folder = str(tmp_path / "combined")
    cfg.paths.log_folder = str(tmp_path / "logs")
    for p in (
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.combined_folder,
        cfg.paths.log_folder,
    ):
        Path(p).mkdir(parents=True, exist_ok=True)
    return cfg


def _pdf(path: Path, pages: int):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=100, height=100)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        w.write(f)


def _ingest_parent(tmp_path, cfg, pages=3):
    from app.services.ingestion import ingest_file

    src = Path(cfg.paths.input_folder) / "combined.pdf"
    _pdf(src, pages)
    return ingest_file(src, cfg)


def _resp(*components, page_count=3):
    return {
        "document_type": "COMBINED",
        "has_po_section": True,
        "has_dn_section": True,
        "has_si_section": True,
        "page_count": page_count,
        "components": list(components),
        "document_number": None,
        "po_no_raw": None,
        "po_reference": None,
        "po_reference_ambiguous": False,
        "vendor_name": "ACME",
        "line_items": [
            # 3-section union that must NOT be persisted (stale-design trap).
            {
                "line_item_no": "1",
                "description": "Union row",
                "quantity": "999",
                "unit_price": "1.00",
                "dn_no": None,
            }
        ],
    }


def _comp(dtype, pstart, pend):
    return {"doc_type": dtype, "document_type": dtype, "page_start": pstart, "page_end": pend}


def test_combined_persists_sterile_parent_and_cuts_files(tmp_path, monkeypatch):
    from app.services.extraction import extract_document

    cfg = _cfg(tmp_path, "w1.db")
    doc = _ingest_parent(tmp_path, cfg)
    resp = _resp(_comp("PO", 1, 1), _comp("DN", 2, 2), _comp("SI", 3, 3))
    monkeypatch.setattr("app.services.extraction._call_vlm", lambda *a, **kw: resp)
    extract_document(doc.id, cfg)

    eng = get_engine(cfg)
    with Session(eng) as s:
        parent = s.get(Document, doc.id)
        assert parent.doc_type == DocType.COMBINED
        assert parent.extraction_status == ExtractionStatus.valid
        assert parent.is_split_parent is True
        assert parent.po_no_normalized is None
        assert parent.po_set_id is None
        assert parent.line_items == []  # no union persisted
        assert parent.split_completed_at is not None
        assert parent.raw_extraction_json  # full raw JSON kept
        # No child ROWS until the next run.
        assert s.query(Document).filter(Document.parent_document_id == parent.id).count() == 0
    sha16 = sha256(Path(parent.stored_path).read_bytes()).hexdigest()[:16]
    kids = sorted(Path(cfg.paths.input_folder).glob(f"{sha16}_p*.pdf"))
    assert len(kids) == 3  # child FILES wait in input/
    assert not (Path(cfg.paths.input_folder) / "combined.pdf").exists()
    assert (Path(cfg.paths.combined_folder) / "combined.pdf").exists()


def test_bad_ranges_quarantine_parent_no_raise(tmp_path, monkeypatch):
    from app.services.extraction import extract_document

    cfg = _cfg(tmp_path, "w2.db")
    doc = _ingest_parent(tmp_path, cfg, pages=2)
    resp = _resp(_comp("PO", 1, 9), page_count=2)
    monkeypatch.setattr("app.services.extraction._call_vlm", lambda *a, **kw: resp)
    extract_document(doc.id, cfg)  # must NOT raise into Prefect retries

    eng = get_engine(cfg)
    with Session(eng) as s:
        parent = s.get(Document, doc.id)
        assert parent.extraction_status == ExtractionStatus.failed
        assert parent.split_completed_at is None
    assert (Path(cfg.paths.quarantine_folder) / "_documents").exists()


def test_child_recombined_quarantines_parent(tmp_path, monkeypatch):
    from app.services.extraction import extract_document

    cfg = _cfg(tmp_path, "w3.db")
    doc = _ingest_parent(tmp_path, cfg)
    resp = _resp(_comp("PO", 1, 1), _comp("DN", 2, 2), _comp("SI", 3, 3))
    monkeypatch.setattr("app.services.extraction._call_vlm", lambda *a, **kw: resp)
    extract_document(doc.id, cfg)

    # Simulate a child row re-read as COMBINED on the next run.
    eng = get_engine(cfg)
    with Session(eng) as s:
        child = Document(
            sha256_hash="childhash123",
            original_filename="child.pdf",
            stored_path=str(Path(cfg.paths.stored_documents_folder) / "child.pdf"),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.pending,
            parent_document_id=doc.id,
        )
        Path(child.stored_path).write_bytes(b"%PDF-1.4 fake")
        s.add(child)
        s.commit()
        s.refresh(child)
        kid_id = child.id
    monkeypatch.setattr(
        "app.services.extraction._call_vlm",
        lambda *a, **kw: {**_resp(_comp("PO", 1, 1), page_count=1), "document_type": "COMBINED"},
    )
    extract_document(kid_id, cfg)

    with Session(eng) as s:
        kid = s.get(Document, kid_id)
        assert kid.extraction_status == ExtractionStatus.failed
        assert s.query(Document).filter(Document.parent_document_id == kid_id).count() == 0


def test_ingestion_links_child_filename_to_parent(tmp_path, monkeypatch):
    from app.services.extraction import extract_document
    from app.services.ingestion import ingest_file

    cfg = _cfg(tmp_path, "w4.db")
    doc = _ingest_parent(tmp_path, cfg)
    resp = _resp(_comp("PO", 1, 1), _comp("DN", 2, 2), _comp("SI", 3, 3))
    monkeypatch.setattr("app.services.extraction._call_vlm", lambda *a, **kw: resp)
    extract_document(doc.id, cfg)

    eng = get_engine(cfg)
    with Session(eng) as s:
        parent = s.get(Document, doc.id)
        sha16 = parent.sha256_hash[:16]
    # A next-run file carrying the child name links to the parent row.
    incoming = Path(cfg.paths.input_folder) / f"{sha16}_p1.pdf"
    kid = ingest_file(incoming, cfg)
    assert kid.parent_document_id == parent.id
    # Ordinary filenames never link.
    other = Path(cfg.paths.input_folder) / "invoice.pdf"
    other.write_bytes(b"%PDF-1.4 other")
    plain = ingest_file(other, cfg)
    assert plain.parent_document_id is None


def test_sterile_parent_never_attaches_and_sweep_ignores_it(tmp_path, monkeypatch):
    """Layer-2 exclusion by construction, pinned: a sterile split parent has
    no po_no/dn_no/po_set, so neither attach sweep claims it and no PO Set
    is minted for it. A set containing only a parent can never exist, which
    is why the reconcile sweep needs no is_split_parent filter."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet
    from app.services.extraction import extract_document
    from app.services.grouping import (
        attach_unattached_to_open_sets,
        resolve_unattached_documents,
    )

    cfg = _cfg(tmp_path, "w6.db")
    doc = _ingest_parent(tmp_path, cfg)
    resp = _resp(_comp("PO", 1, 1), _comp("DN", 2, 2), _comp("SI", 3, 3))
    monkeypatch.setattr("app.services.extraction._call_vlm", lambda *a, **kw: resp)
    extract_document(doc.id, cfg)

    attach_unattached_to_open_sets(cfg)
    resolve_unattached_documents(cfg)

    eng = get_engine(cfg)
    with Session(eng) as s:
        parent = s.get(Document, doc.id)
        assert parent.po_set_id is None
        assert s.query(POSet).count() == 0


def test_children_reextract_attach_and_merge_end_to_end(tmp_path, monkeypatch):
    """The whole PLAN promise in one test: combined PDF -> sterile parent +
    child files -> next run ingests children (linkage) -> each re-extracts
    fresh as a single-section doc -> attach -> reconcile -> merged packet.
    The old same-run children design is gone; this is the two-run loop."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet
    from app.services.extraction import extract_document
    from app.services.grouping import (
        attach_unattached_to_open_sets,
        get_or_create_po_set,
    )
    from app.services.ingestion import ingest_file
    from app.services.reconciliation import reconcile_po_set

    cfg = _cfg(tmp_path, "w7.db")
    # Distinct page widths so each cut child has distinct bytes (blank
    # same-size pages would hash identically and dedup to one row).
    from pypdf import PdfWriter

    from app.services.ingestion import ingest_file as _ingest

    src = Path(cfg.paths.input_folder) / "combined.pdf"
    w = PdfWriter()
    for width in (100, 200, 300):
        w.add_blank_page(width=width, height=200)
    src.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "wb") as f:
        w.write(f)
    doc = _ingest(src, cfg)
    resp = _resp(_comp("PO", 1, 1), _comp("DN", 2, 2), _comp("SI", 3, 3))
    monkeypatch.setattr("app.services.extraction._call_vlm", lambda *a, **kw: resp)
    extract_document(doc.id, cfg)

    # ---- next run: child files are discovered and ingested (with linkage)
    eng = get_engine(cfg)
    with Session(eng) as s:
        parent = s.get(Document, doc.id)
        sha16 = parent.sha256_hash[:16]
    kids = []
    for i in (1, 2, 3):
        kids.append(ingest_file(Path(cfg.paths.input_folder) / f"{sha16}_p{i}.pdf", cfg))
    assert all(k.parent_document_id == doc.id for k in kids)

    # ---- each child re-extracts fresh as an ordinary single-section doc
    def single(dtype, number, po_ref, line_no="1", qty="100"):
        d = {
            "PO": ("PO-100", None),
            "DN": ("GDN-1", "PO-100"),
            "SI": ("INV-E2E", "PO-100"),
        }[dtype]
        return {
            "document_type": dtype,
            "document_number": d[0],
            "po_no_raw": d[1] or d[0],
            "po_reference": d[1],
            "po_reference_ambiguous": False,
            "vendor_name": "ACME",
            "line_items": [
                {
                    "line_item_no": line_no,
                    "description": "Widget",
                    "quantity": qty,
                    "unit_price": "10.00",
                    "dn_no": None,
                }
            ],
        }

    order = ["PO", "DN", "SI"]
    for kid, dtype in zip(kids, order, strict=True):
        monkeypatch.setattr(
            "app.services.extraction._call_vlm", lambda *a, _d=dtype, **kw: single(_d, None, None)
        )
        extract_document(kid.id, cfg)

    with Session(eng) as s:
        kinds = {
            s.get(Document, k.id).doc_type.value: s.get(Document, k.id).extraction_status.value
            for k in kids
        }
    assert kinds == {"PO": "valid", "DN": "valid", "SI": "valid"}

    # ---- attach (PO mints, DN/SI join) and reconcile to a merged packet
    get_or_create_po_set("PO-100", cfg)
    attach_unattached_to_open_sets(cfg)
    with Session(eng) as s:
        sets = {s.get(Document, k.id).po_set_id for k in kids}
        assert len(sets) == 1 and None not in sets
        ps_id = sets.pop()
        assert s.get(Document, doc.id).po_set_id is None  # parent stays out
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "merged", res
    assert [p.name for p in Path(cfg.paths.output_folder).glob("*.pdf")] == ["INV-E2E.pdf"]
    with Session(eng) as s:
        assert s.get(POSet, ps_id).status.value == "merged"


def test_crash_before_stamp_rederives_children(tmp_path, monkeypatch):
    """`split_completed_at` is the authority: a parent stamped sterile but
    never stamped (crash between cut and commit) re-derives its children
    from the parent SHA instead of trusting them to exist."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.services.extraction import extract_document

    cfg = _cfg(tmp_path, "w8.db")
    doc = _ingest_parent(tmp_path, cfg)
    resp = _resp(_comp("PO", 1, 1), _comp("DN", 2, 2), _comp("SI", 3, 3))
    monkeypatch.setattr("app.services.extraction._call_vlm", lambda *a, **kw: resp)
    extract_document(doc.id, cfg)

    eng = get_engine(cfg)
    with Session(eng) as s:
        parent = s.get(Document, doc.id)
        sha16 = parent.sha256_hash[:16]
        parent.split_completed_at = None  # the crash: cut happened, stamp lost
        s.commit()
    orphan = next(Path(cfg.paths.input_folder).glob(f"{sha16}_p*.pdf"))
    orphan.unlink()  # and a child file went missing too

    extract_document(doc.id, cfg)  # recovery re-runs the split

    with Session(eng) as s:
        assert s.get(Document, doc.id).split_completed_at is not None
    assert len(list(Path(cfg.paths.input_folder).glob(f"{sha16}_p*.pdf"))) == 3


def test_reupload_of_combined_dedups_to_parent(tmp_path, monkeypatch):
    """Re-uploading the same combined bytes hits the parent SHA
    (`is_split_parent=True`) → same row, no duplicate children."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.services.extraction import extract_document
    from app.services.ingestion import ingest_file

    cfg = _cfg(tmp_path, "w9.db")
    doc = _ingest_parent(tmp_path, cfg)
    resp = _resp(_comp("PO", 1, 1), _comp("DN", 2, 2), _comp("SI", 3, 3))
    monkeypatch.setattr("app.services.extraction._call_vlm", lambda *a, **kw: resp)
    extract_document(doc.id, cfg)

    # The operator drops the same file into input/ again.
    again = Path(cfg.paths.input_folder) / "combined.pdf"
    again.write_bytes(Path(doc.stored_path).read_bytes())
    dup = ingest_file(again, cfg)
    assert dup.id == doc.id
    assert dup.is_split_parent is True
    extract_document(dup.id, cfg)
    eng = get_engine(cfg)
    with Session(eng) as s:
        parent = s.get(Document, doc.id)
        sha16 = parent.sha256_hash[:16]
    assert len(list(Path(cfg.paths.input_folder).glob(f"{sha16}_p*.pdf"))) == 3


def test_unknown_child_prefix_never_links(tmp_path):
    """A `<sha>_p<i>.pdf` name with no matching split parent stays a normal
    document — linkage must never guess."""
    from app.services.ingestion import ingest_file

    cfg = _cfg(tmp_path, "w10.db")
    stray = Path(cfg.paths.input_folder) / "deadbeefcafe1234_p1.pdf"
    stray.write_bytes(b"%PDF-1.4 stray")
    doc = ingest_file(stray, cfg)
    assert doc.parent_document_id is None


def test_resplit_is_idempotent_no_duplicate_children(tmp_path, monkeypatch):
    from app.services.extraction import extract_document

    cfg = _cfg(tmp_path, "w5.db")
    doc = _ingest_parent(tmp_path, cfg)
    resp = _resp(_comp("PO", 1, 1), _comp("DN", 2, 2), _comp("SI", 3, 3))
    monkeypatch.setattr("app.services.extraction._call_vlm", lambda *a, **kw: resp)
    extract_document(doc.id, cfg)
    before = sorted(p.name for p in Path(cfg.paths.input_folder).glob("*.pdf"))
    extract_document(doc.id, cfg)  # e.g. pending sweep re-extracts
    after = sorted(p.name for p in Path(cfg.paths.input_folder).glob("*.pdf"))
    assert before == after
    assert len(after) == 3
