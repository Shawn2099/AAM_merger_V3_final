"""Business scenarios, end to end, including what the operator sees.

These tests exist because the engine's verdict and the dashboard's explanation
are two different things, and a set that is quarantined for a reason the detail
page does not reproduce (two PO documents, a PO reference mismatch, a packet
that cannot be named) looks like a fully matching set with no explanation.

Every scenario builds real rows + real PDFs, runs the engine, then reads the
pages an operator actually uses: the dashboard, the quarantine page and the set
detail page.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import (
    AuditAction,
    AuditLog,
    DocType,
    Document,
    ExtractionStatus,
    LineItem,
    POSet,
    POSetStatus,
)
from app.models.base import Base
from app.services.reconciliation import reconcile_po_set

K = 1000  # quantities are stored scaled x1000


@pytest.fixture()
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c.paths.database_path = tmp_path / "scenario.db"
    c.paths.input_folder = tmp_path / "input"
    c.paths.output_folder = tmp_path / "output"
    c.paths.quarantine_folder = tmp_path / "quarantine"
    c.paths.stored_documents_folder = tmp_path / "stored"
    c.paths.combined_folder = tmp_path / "combined"
    c.paths.log_folder = tmp_path / "logs"
    for p in [
        c.paths.input_folder,
        c.paths.output_folder,
        c.paths.quarantine_folder,
        c.paths.stored_documents_folder,
        c.paths.combined_folder,
        c.paths.log_folder,
    ]:
        Path(p).mkdir(parents=True, exist_ok=True)
    eng = get_engine(c)
    Base.metadata.create_all(eng)
    return c


@pytest.fixture()
def client(cfg, monkeypatch):
    monkeypatch.setattr("app.api.routes.sync.load_config", lambda path=None: cfg)
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: cfg)
    monkeypatch.setattr("app.api.routes.dashboard.load_config", lambda path=None: cfg)
    monkeypatch.setattr("app.flows.sync.load_config", lambda path=None: cfg)
    monkeypatch.setattr("app.services.sync_lock.load_config", lambda path=None: cfg)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _pdf(cfg, seed: str) -> tuple[str, Path]:
    import hashlib as _h

    sha = _h.sha256(seed.encode()).hexdigest()
    path = Path(cfg.paths.stored_documents_folder) / f"{sha}.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(str(path))
    return sha, path


def _set(cfg, po_no: str) -> int:
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        return ps.id


def _doc(
    cfg,
    ps_id: int,
    dtype: DocType,
    filename: str,
    lines: list[tuple],
    *,
    seed: str | None = None,
    si_no: str | None = None,
    po_no: str = "",
    status: ExtractionStatus = ExtractionStatus.valid,
) -> int:
    """Add one attached document with line items.

    `lines` are `(line_no, description, qty)` or
    `(line_no, description, qty, unit_price)`, unscaled.
    """
    sha, path = _pdf(cfg, seed or f"{ps_id}-{filename}-{dtype.value}")
    eng = get_engine(cfg)
    with Session(eng) as s:
        doc = Document(
            sha256_hash=sha,
            original_filename=filename,
            stored_path=str(path),
            doc_type=dtype,
            extraction_status=status,
            po_set_id=ps_id if dtype != DocType.UNKNOWN else None,
            po_no_normalized=po_no or None,
            si_no=si_no,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        for name, desc, qty, *rest in lines:
            price = rest[0] if rest else 0
            s.add(
                LineItem(
                    document_id=doc.id,
                    line_item_no=name,
                    description=desc,
                    quantity=qty * K,
                    unit_price=price * K,
                )
            )
        s.commit()
        return doc.id


def _status(cfg, pid: int) -> POSet:
    eng = get_engine(cfg)
    with Session(eng) as s:
        return s.get(POSet, pid)


def _standard_set(cfg, po_no: str, po_qty: int = 100):
    pid = _set(cfg, po_no)
    _doc(cfg, pid, DocType.PO, f"{po_no}-PO.pdf", [("1", "BOLT", po_qty)], po_no=po_no)
    return pid


# ---------------------------------------------------------------------------
# 1. An unrelated document
# ---------------------------------------------------------------------------


def test_unrelated_document_is_held_visibly_and_never_quarantined(cfg, client):
    pid = _set(cfg, "UNRELATED-ANCHOR")  # not needed by the doc; keeps stats honest
    _doc(cfg, pid, DocType.PO, "anchor.pdf", [("1", "X", 1)], po_no="UNRELATED-ANCHOR")

    eng = get_engine(cfg)
    with Session(eng) as s:
        doc = Document(
            sha256_hash=hashlib.sha256(b"unrelated").hexdigest(),
            original_filename="vendor-catalogue.pdf",
            stored_path="",
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=None,
        )
        s.add(doc)
        s.commit()

    assert client.get("/unclassified").status_code == 200
    held = client.get("/unclassified").text
    assert "vendor-catalogue.pdf" in held

    quarantine = client.get("/quarantine").text
    assert "vendor-catalogue.pdf" not in quarantine
    assert "UNRELATED-ANCHOR" not in quarantine

    # It created no PO Set of its own.
    with Session(eng) as s:
        assert s.query(POSet).count() == 1


def test_po_without_a_number_is_held_not_quarantined(cfg, client):
    from app.services.grouping import group_document

    eng = get_engine(cfg)
    with Session(eng) as s:
        doc = Document(
            sha256_hash=hashlib.sha256(b"numberless-po").hexdigest(),
            original_filename="numberless-po.pdf",
            stored_path="",
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_no_normalized=None,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)

        ps_id, problem = group_document(doc, cfg)

        assert ps_id is None
        assert problem == "missing_po_number"
        assert s.get(Document, doc.id).extraction_status == ExtractionStatus.valid

    assert "numberless-po.pdf" in client.get("/unclassified").text
    assert "numberless-po.pdf" not in client.get("/quarantine").text


# ---------------------------------------------------------------------------
# 2. No line-item number
# ---------------------------------------------------------------------------


def test_po_line_without_a_number_or_description_quarantines_and_both_pages_say_why(cfg, client):
    pid = _set(cfg, "PO-NOLINE")
    _doc(cfg, pid, DocType.PO, "po-noline.pdf", [(None, "", 100)], po_no="PO-NOLINE")
    _doc(cfg, pid, DocType.DN, "dn-noline.pdf", [("1", "BOLT", 100)], po_no="PO-NOLINE")
    _doc(cfg, pid, DocType.SI, "si-noline.pdf", [("1", "BOLT", 100)], po_no="PO-NOLINE")

    result = reconcile_po_set(pid, cfg)

    assert result["status"] == "quarantined"
    assert result["reason"] == "po_line_missing_line_item_no"
    ps = _status(cfg, pid)
    assert ps.status == POSetStatus.quarantined
    assert "line number" in (ps.reconcile_reason or "")

    quarantine = client.get("/quarantine").text
    assert "PO-NOLINE" in quarantine
    assert "line number" in quarantine

    detail = client.get(f"/po_sets/{pid}/view").text
    assert "line number" in detail


def test_a_numberless_po_line_reconciles_by_description(cfg, client):
    """The table content identifies the item on both sides. A PO row with no
    number is matched to a numbered DN row and a numberless SI row by its
    description; the quantities still have to agree exactly."""
    pid = _set(cfg, "NOLINEPO")
    _doc(
        cfg,
        pid,
        DocType.PO,
        "nolinepo-po.pdf",
        [(None, "BOLT HEX 1/2 IN YELLOW ZINC", 100)],
        po_no="NOLINEPO",
    )
    _doc(
        cfg,
        pid,
        DocType.DN,
        "nolinepo-dn.pdf",
        [("1", "BOLT HEX 1/2 IN YELLOW ZINC", 100)],
        po_no="NOLINEPO",
    )
    _doc(
        cfg,
        pid,
        DocType.SI,
        "nolinepo-si.pdf",
        [(None, "BOLT HEX 1/2 IN YELLOW ZINC", 100)],
        si_no="INV-NOLINEPO",
        po_no="NOLINEPO",
    )

    result = reconcile_po_set(pid, cfg)

    assert result["status"] == "merged", _status(cfg, pid).reconcile_reason
    detail = client.get(f"/po_sets/{pid}/view").text
    assert "BOLT HEX 1/2 IN YELLOW ZINC" in detail
    assert "row-match" in detail and "\u2705 Match" in detail


# ---------------------------------------------------------------------------
# 3 + 4. More than one PO document
# ---------------------------------------------------------------------------


def _two_po_set(cfg, po_no: str):
    pid = _set(cfg, po_no)
    _doc(cfg, pid, DocType.PO, f"{po_no}-PO-A.pdf", [("1", "BOLT", 100)], po_no=po_no, seed="a")
    _doc(cfg, pid, DocType.PO, f"{po_no}-PO-B.pdf", [("1", "BOLT", 100)], po_no=po_no, seed="b")
    _doc(cfg, pid, DocType.DN, f"{po_no}-DN.pdf", [("1", "BOLT", 100)], po_no=po_no)
    _doc(
        cfg, pid, DocType.SI, f"{po_no}-SI.pdf", [("1", "BOLT", 100)], si_no="INV-DUP", po_no=po_no
    )
    return pid


def test_two_po_documents_hold_the_set_pending_for_review(cfg, client):
    pid = _two_po_set(cfg, "PO-DUP")
    result = reconcile_po_set(pid, cfg)

    assert result["status"] == "pending"
    assert result["reason"] == "multiple_po_documents"
    ps = _status(cfg, pid)
    assert ps.merged_output_path is None
    assert "2 PO documents" in (ps.reconcile_reason or "")

    dashboard = client.get("/dashboard").text
    assert "PO-DUP" in dashboard
    assert "2 PO documents" in dashboard or "more than one PO" in dashboard.lower()

    # It is a review hold, not a quarantine.
    assert "PO-DUP" not in client.get("/quarantine").text

    detail = client.get(f"/po_sets/{pid}/view").text
    assert "2 PO documents" in detail or "more than one PO" in detail.lower()
    assert "row-pending" in detail


def test_identical_po_bytes_are_one_document_not_a_second_po(cfg, client):
    from app.services.ingestion import ingest_file

    src = Path(cfg.paths.input_folder) / "duplicate-po.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(str(src))

    first = ingest_file(src, cfg)
    second = ingest_file(src, cfg)
    assert first.id == second.id

    eng = get_engine(cfg)
    with Session(eng) as s:
        assert s.query(Document).filter(Document.sha256_hash == first.sha256_hash).count() == 1

    # Make it a valid PO in a set with a matching DN and SI: it must merge.
    pid = _set(cfg, "PO-SAMEBYTES")
    with Session(eng) as s:
        row = s.get(Document, first.id)
        row.doc_type = DocType.PO
        row.extraction_status = ExtractionStatus.valid
        row.po_no_normalized = "PO-SAMEBYTES"
        row.po_set_id = pid
        s.add(
            LineItem(
                document_id=row.id,
                line_item_no="1",
                description="BOLT",
                quantity=100 * K,
                unit_price=0,
            )
        )
        s.commit()
    _doc(cfg, pid, DocType.DN, "same-dn.pdf", [("1", "BOLT", 100)], po_no="PO-SAMEBYTES")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "same-si.pdf",
        [("1", "BOLT", 100)],
        si_no="INV-SAME",
        po_no="PO-SAMEBYTES",
    )

    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "merged", _status(cfg, pid).reconcile_reason
    assert Path(result["merged_output_path"]).exists()


def test_duplicate_po_with_a_different_hash_is_still_held(cfg, client):
    pid = _two_po_set(cfg, "PO-DIFFHASH")
    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "pending"
    assert result["reason"] == "multiple_po_documents"


def test_page_split_po_documents_are_held_not_summed(cfg):
    """More than one PO document is a human decision, even with disjoint lines.

    Page 1 prints lines 1-2 and page 2 prints lines 3-4. The engine must not
    guess that they are one PO; it holds the set pending for review.
    """
    pid = _set(cfg, "PO-PAGES")
    _doc(
        cfg,
        pid,
        DocType.PO,
        "pages-1.pdf",
        [("1", "BOLT", 100), ("2", "NUT", 50)],
        po_no="PO-PAGES",
        seed="pages-a",
    )
    _doc(
        cfg,
        pid,
        DocType.PO,
        "pages-2.pdf",
        [("3", "WASHER", 25), ("4", "SCREW", 10)],
        po_no="PO-PAGES",
        seed="pages-b",
    )
    _doc(
        cfg,
        pid,
        DocType.DN,
        "pages-dn.pdf",
        [("1", "BOLT", 100), ("2", "NUT", 50), ("3", "WASHER", 25), ("4", "SCREW", 10)],
        po_no="PO-PAGES",
    )
    _doc(
        cfg,
        pid,
        DocType.SI,
        "pages-si.pdf",
        [("1", "BOLT", 100), ("2", "NUT", 50), ("3", "WASHER", 25), ("4", "SCREW", 10)],
        po_no="PO-PAGES",
        si_no="INV-PAGES",
    )

    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "pending", result
    assert result["reason"] == "multiple_po_documents"
    assert _status(cfg, pid).merged_output_path is None


def test_vendor_document_with_zero_lines_quarantines_instead_of_pending(cfg):
    """A complete-looking set whose DN read as zero lines can never progress.

    Reporting it as "waiting for delivery" was a pending state that no later
    sweep could ever leave.
    """
    pid = _set(cfg, "PO-ZERODN")
    _doc(cfg, pid, DocType.PO, "zero-po.pdf", [("1", "BOLT", 100)], po_no="PO-ZERODN")
    _doc(cfg, pid, DocType.DN, "zero-dn.pdf", [], po_no="PO-ZERODN", seed="zero-dn")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "zero-si.pdf",
        [("1", "BOLT", 100)],
        po_no="PO-ZERODN",
        si_no="INV-ZERODN",
    )

    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "quarantined"
    assert result["reason"] == "vendor_document_has_no_line_items"
    assert "zero-dn.pdf" in result["detail"]


def test_unclassified_does_not_list_a_document_already_in_a_set(cfg, client):
    pid = _set(cfg, "PO-ATTACHED")
    doc_id = _doc(
        cfg,
        pid,
        DocType.PO,
        "already-attached.pdf",
        [("1", "BOLT", 100)],
        po_no="PO-ATTACHED",
    )
    with Session(get_engine(cfg)) as s:
        doc = s.get(Document, doc_id)
        doc.po_reference_ambiguous = True
        s.commit()

    page = client.get("/unclassified").text
    assert "already-attached.pdf" not in page
    assert "All caught up" in page


def test_reclassify_refuses_a_document_in_a_merged_set(cfg, client):
    pid = _set(cfg, "PO-LOCKED")
    _doc(cfg, pid, DocType.PO, "locked-po.pdf", [("1", "BOLT", 100)], po_no="PO-LOCKED")
    _doc(cfg, pid, DocType.DN, "locked-dn.pdf", [("1", "BOLT", 100)], po_no="PO-LOCKED")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "locked-si.pdf",
        [("1", "BOLT", 100)],
        po_no="PO-LOCKED",
        si_no="INV-LOCKED",
    )
    assert reconcile_po_set(pid, cfg)["status"] == "merged"
    doc_id = _doc(cfg, pid, DocType.CUSTOMS, "locked-customs.pdf", [], po_no="PO-LOCKED")

    r = client.post(
        f"/unclassified/{doc_id}/reclassify",
        data={"doc_type": "SHIPPING"},
    )
    assert r.status_code == 409, r.text


def test_empty_status_filter_means_all(cfg, client):
    assert client.get("/dashboard?status=").status_code == 200
    assert client.get("/dashboard/table?status=").status_code == 200


def test_preview_rejects_a_non_pdf_stored_file(cfg, client):
    pid = _set(cfg, "PO-BADPDF")
    path = Path(cfg.paths.stored_documents_folder) / "not-a-pdf.pdf"
    path.write_bytes(b"\x00" * 128)
    with Session(get_engine(cfg)) as s:
        doc = Document(
            sha256_hash="not-a-pdf-hash",
            original_filename="not-a-pdf.pdf",
            stored_path=str(path),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.failed,
            po_set_id=pid,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    r = client.get(f"/documents/{doc_id}/preview")
    assert r.status_code == 422
    assert "not a readable PDF" in r.text


# ---------------------------------------------------------------------------
# 5. One PO, many DN and many SI
# ---------------------------------------------------------------------------


def test_one_po_two_dn_two_si_sum_and_merge(cfg):
    pid = _set(cfg, "PO-SPLIT")
    _doc(cfg, pid, DocType.PO, "split-po.pdf", [("1", "BOLT", 100)], po_no="PO-SPLIT")
    _doc(cfg, pid, DocType.DN, "split-dn-1.pdf", [("1", "BOLT", 40)], po_no="PO-SPLIT")
    _doc(cfg, pid, DocType.DN, "split-dn-2.pdf", [("1", "BOLT", 60)], po_no="PO-SPLIT")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "split-si-1.pdf",
        [("1", "BOLT", 70)],
        si_no="INV-SPLIT",
        po_no="PO-SPLIT",
    )
    _doc(
        cfg,
        pid,
        DocType.SI,
        "split-si-2.pdf",
        [("1", "BOLT", 30)],
        si_no="INV-SPLIT-2",
        po_no="PO-SPLIT",
    )

    result = reconcile_po_set(pid, cfg)

    assert result["status"] == "merged", _status(cfg, pid).reconcile_reason
    assert Path(result["merged_output_path"]).exists()
    eng = get_engine(cfg)
    with Session(eng) as s:
        assert s.query(Document).filter(Document.po_set_id == pid).count() == 5


def test_only_quantities_decide_the_verdict(cfg):
    """The contract: line number is the key, quantities are the only signal.

    Prices, descriptions and leading zeros on the line number must not change
    the verdict. One PO against two DNs and two SIs, each split 40/60 and
    70/30, with deliberately different descriptions and prices, merges.
    """
    pid = _set(cfg, "ONLYQTY")
    _doc(
        cfg,
        pid,
        DocType.PO,
        "onlyqty-po.pdf",
        [("1", "BOLT HEX 1/2 IN", 100, 10)],
        po_no="ONLYQTY",
    )
    _doc(
        cfg,
        pid,
        DocType.DN,
        "onlyqty-dn-1.pdf",
        [("01", "COMPLETELY DIFFERENT TEXT", 40, 999)],
        po_no="ONLYQTY",
    )
    _doc(
        cfg,
        pid,
        DocType.DN,
        "onlyqty-dn-2.pdf",
        [("1", "ANOTHER DESCRIPTION", 60, 1)],
        po_no="ONLYQTY",
    )
    _doc(
        cfg,
        pid,
        DocType.SI,
        "onlyqty-si-1.pdf",
        [("001", "PRICE IS WRONG", 70, 500)],
        si_no="INV-ONLYQTY",
        po_no="ONLYQTY",
    )
    _doc(
        cfg,
        pid,
        DocType.SI,
        "onlyqty-si-2.pdf",
        [("1", "DESCRIPTION IS IRRELEVANT", 30, 2)],
        si_no="INV-ONLYQTY-2",
        po_no="ONLYQTY",
    )

    result = reconcile_po_set(pid, cfg)

    assert result["status"] == "merged", _status(cfg, pid).reconcile_reason
    assert Path(result["merged_output_path"]).exists()


def test_a_numberless_vendor_row_falls_back_to_description(cfg):
    """When a vendor row carries no line number, its description is the only
    way to place it. A close description match still reconciles."""
    pid = _set(cfg, "DESCFALL")
    _doc(
        cfg,
        pid,
        DocType.PO,
        "descf-po.pdf",
        [("1", "BOLT HEX 1/2 IN YELLOW ZINC PLATED", 100)],
        po_no="DESCFALL",
    )
    _doc(
        cfg,
        pid,
        DocType.DN,
        "descf-dn.pdf",
        [(None, "BOLT HEX 1/2 IN YELLOW ZINC PLATED", 100)],
        po_no="DESCFALL",
    )
    _doc(
        cfg,
        pid,
        DocType.SI,
        "descf-si.pdf",
        [("1", "BOLT HEX 1/2 IN YELLOW ZINC PLATED", 100)],
        si_no="INV-DESCF",
        po_no="DESCFALL",
    )

    result = reconcile_po_set(pid, cfg)

    assert result["status"] == "merged", _status(cfg, pid).reconcile_reason


# ---------------------------------------------------------------------------
# Needs Review (mismatched) vs Quarantined
# ---------------------------------------------------------------------------


def test_needs_review_shows_the_numbers_and_stays_off_the_quarantine_page(cfg, client):
    pid = _set(cfg, "PO-REVIEW")
    _doc(cfg, pid, DocType.PO, "review-po.pdf", [("1", "BOLT", 100)], po_no="PO-REVIEW")
    _doc(cfg, pid, DocType.DN, "review-dn.pdf", [("1", "BOLT", 60)], po_no="PO-REVIEW")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "review-si.pdf",
        [("1", "BOLT", 60)],
        si_no="INV-REVIEW",
        po_no="PO-REVIEW",
    )

    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "mismatched"
    ps = _status(cfg, pid)
    assert ps.status == POSetStatus.mismatched
    assert "60 of 100" in (ps.reconcile_reason or "")

    review = client.get("/dashboard?status=mismatched").text
    assert "PO-REVIEW" in review
    assert "60 of 100" in review

    quarantine = client.get("/quarantine").text
    assert "PO-REVIEW" not in quarantine

    # The KPI counts it as Needs Review, not as Quarantined.
    dashboard = client.get("/dashboard").text
    assert "Needs Review" in dashboard


# ---------------------------------------------------------------------------
# 6. Customs: after the merge vs before it
# ---------------------------------------------------------------------------


_PDF_SEQ = 0


def _pdf_bytes() -> bytes:
    """A unique one-page PDF. Unique bytes matter: the upload path dedups by
    SHA-256, so two documents built identically would be ONE document."""
    global _PDF_SEQ
    import io

    _PDF_SEQ += 1
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": f"supporting-document-{_PDF_SEQ}"})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _page_count(path: str | Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def test_customs_documents_can_be_appended_after_merge(cfg, client):
    """The client's rule: customs is off by default, but it can be switched on
    later and the two documents appended to the packet already delivered.

    The set must stay `merged` throughout, and the packet must never be
    truncated by a failed rebuild.
    """
    pid = _standard_set(cfg, "PO-APPEND")
    _doc(cfg, pid, DocType.DN, "append-dn.pdf", [("1", "BOLT", 100)], po_no="PO-APPEND")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "append-si.pdf",
        [("1", "BOLT", 100)],
        si_no="INV-APPEND",
        po_no="PO-APPEND",
    )
    assert reconcile_po_set(pid, cfg)["status"] == "merged"

    packet = Path(_status(cfg, pid).merged_output_path)
    assert _page_count(packet) == 3  # PO + DN + SI

    # The requirement was off at merge time. Turn it on afterwards.
    r = client.post(f"/po_sets/{pid}/toggle_customs")
    assert r.status_code == 200, r.text
    assert _status(cfg, pid).status == POSetStatus.merged
    assert _status(cfg, pid).has_customs_toggle is True
    assert _page_count(packet) == 3, "nothing to append yet"

    # The dashboard tells the operator the append is outstanding.
    assert "Customs append pending" in client.get("/dashboard").text

    # First document: attached, packet not rebuilt yet.
    r = client.post(
        f"/po_sets/{pid}/upload",
        data={"doc_type": "CUSTOMS"},
        files={"file": ("customs.pdf", _pdf_bytes(), "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert _status(cfg, pid).status == POSetStatus.merged
    assert _page_count(packet) == 3

    # Second document: both are present, so the packet is rebuilt.
    r = client.post(
        f"/po_sets/{pid}/upload",
        data={"doc_type": "SHIPPING"},
        files={"file": ("shipping.pdf", _pdf_bytes(), "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert "appended" in r.headers["location"].lower()

    ps = _status(cfg, pid)
    assert ps.status == POSetStatus.merged
    assert ps.merged_output_path == str(packet)
    assert ps.customs_doc_count == 2
    assert _page_count(packet) == 5  # PO + DN + SI + SHIPPING + CUSTOMS
    assert "Customs append pending" not in client.get("/dashboard").text

    eng = get_engine(cfg)
    with Session(eng) as s:
        actions = [a.action for a in s.query(AuditLog).all()]
        assert AuditAction.customs_append in actions

    # Idempotent: toggling off and on rebuilds from the same five documents.
    client.post(f"/po_sets/{pid}/toggle_customs")  # off
    client.post(f"/po_sets/{pid}/toggle_customs")  # on
    assert _page_count(packet) == 5, "a rebuild must not duplicate pages"

    # Turning the requirement off cannot remove pages already in the packet.
    client.post(f"/po_sets/{pid}/toggle_customs")
    assert _page_count(packet) == 5
    assert _status(cfg, pid).status == POSetStatus.merged


def test_customs_docs_uploaded_before_the_requirement_is_switched_on_rebuild_on_toggle(cfg, client):
    """Both documents can be attached while the requirement is off; switching
    it on afterwards must still rebuild the packet."""
    pid = _standard_set(cfg, "PO-PRELOAD")
    _doc(cfg, pid, DocType.DN, "preload-dn.pdf", [("1", "BOLT", 100)], po_no="PO-PRELOAD")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "preload-si.pdf",
        [("1", "BOLT", 100)],
        si_no="INV-PRELOAD",
        po_no="PO-PRELOAD",
    )
    assert reconcile_po_set(pid, cfg)["status"] == "merged"
    packet = Path(_status(cfg, pid).merged_output_path)
    assert _page_count(packet) == 3

    for kind, name in (("CUSTOMS", "c.pdf"), ("SHIPPING", "s.pdf")):
        r = client.post(
            f"/po_sets/{pid}/upload",
            data={"doc_type": kind},
            files={"file": (name, _pdf_bytes(), "application/pdf")},
            follow_redirects=False,
        )
        assert r.status_code == 302, r.text
    assert _page_count(packet) == 3, "requirement is off: nothing is appended"

    r = client.post(f"/po_sets/{pid}/toggle_customs")
    assert r.status_code == 200, r.text
    assert r.json()["packet_rebuilt"] is True
    assert _page_count(packet) == 5
    assert _status(cfg, pid).status == POSetStatus.merged


def test_customs_doc_placed_from_the_holding_area_also_rebuilds_the_packet(cfg, client):
    """A customs/shipping document can arrive through the holding area, not
    only the upload form. It must reach an already-merged packet too."""
    pid = _standard_set(cfg, "5001")
    _doc(cfg, pid, DocType.DN, "placed-dn.pdf", [("1", "BOLT", 100)], po_no="5001")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "placed-si.pdf",
        [("1", "BOLT", 100)],
        si_no="INV-PLACED",
        po_no="5001",
    )
    assert reconcile_po_set(pid, cfg)["status"] == "merged"
    packet = Path(_status(cfg, pid).merged_output_path)
    assert _page_count(packet) == 3
    assert client.post(f"/po_sets/{pid}/toggle_customs").status_code == 200

    # One of the two arrives by upload; the other waits unplaced.
    client.post(
        f"/po_sets/{pid}/upload",
        data={"doc_type": "CUSTOMS"},
        files={"file": ("placed-customs.pdf", _pdf_bytes(), "application/pdf")},
        follow_redirects=False,
    )
    assert _page_count(packet) == 3

    eng = get_engine(cfg)
    sha, stored = _pdf(cfg, "placed-shipping")
    with Session(eng) as s:
        doc = Document(
            sha256_hash=sha,
            original_filename="placed-shipping.pdf",
            stored_path=str(stored),
            doc_type=DocType.SHIPPING,
            extraction_status=ExtractionStatus.valid,
            po_set_id=None,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    r = client.post(
        f"/unclassified/{doc_id}/reclassify",
        data={"doc_type": "SHIPPING", "po_no": "PO-5001"},
        follow_redirects=False,
    )
    assert r.status_code in (200, 302), r.text
    assert _page_count(packet) == 5
    assert _status(cfg, pid).status == POSetStatus.merged


def test_customs_on_before_merge_blocks_then_merges_after_both_docs(cfg, client):
    pid = _standard_set(cfg, "PO-CUSTOMS")
    _doc(cfg, pid, DocType.DN, "cus-dn.pdf", [("1", "BOLT", 100)], po_no="PO-CUSTOMS")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "cus-si.pdf",
        [("1", "BOLT", 100)],
        si_no="INV-CUS",
        po_no="PO-CUSTOMS",
    )

    # Operator turns the requirement on before the set is reconciled.
    assert client.post(f"/po_sets/{pid}/toggle_customs").status_code == 200
    assert _status(cfg, pid).has_customs_toggle is True

    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "blocked_customs"
    assert result["reason"] == "blocked_customs"
    assert "Awaiting Customs" in client.get("/dashboard").text
    page = client.get(f"/po_sets/{pid}/view").text
    assert "Customs requirement is ON" in page

    _doc(cfg, pid, DocType.CUSTOMS, "cus-customs.pdf", [], po_no="PO-CUSTOMS", seed="customs")
    _doc(cfg, pid, DocType.SHIPPING, "cus-shipping.pdf", [], po_no="PO-CUSTOMS", seed="shipping")

    merged = client.post(f"/po_sets/{pid}/merge")
    assert merged.status_code == 200
    assert merged.json()["status"] == "merged", merged.text
    assert _status(cfg, pid).status == POSetStatus.merged


# ---------------------------------------------------------------------------
# Quarantine page: delete removes the record, keeps the files, audits it
# ---------------------------------------------------------------------------


def test_quarantine_delete_removes_it_from_the_page_and_keeps_files(cfg, client):
    pid = _set(cfg, "PO-DEL")
    doc_id = _doc(cfg, pid, DocType.PO, "del-po.pdf", [(None, "", 100)], po_no="PO-DEL")
    _doc(cfg, pid, DocType.DN, "del-dn.pdf", [("1", "BOLT", 100)], po_no="PO-DEL")
    _doc(cfg, pid, DocType.SI, "del-si.pdf", [("1", "BOLT", 100)], si_no="INV-DEL", po_no="PO-DEL")
    assert reconcile_po_set(pid, cfg)["status"] == "quarantined"
    assert "PO-DEL" in client.get("/quarantine").text

    eng = get_engine(cfg)
    with Session(eng) as s:
        stored = Path(s.get(Document, doc_id).stored_path)
    assert stored.exists()

    r = client.delete(f"/po_sets/{pid}/quarantine")
    assert r.status_code == 200, r.text

    assert "PO-DEL" not in client.get("/quarantine").text
    assert stored.exists(), "stored document must survive the archive"
    with Session(eng) as s:
        archived = s.get(POSet, pid)
        assert archived is not None, "archive keeps the PO Set row"
        assert archived.archived_at is not None
        assert s.query(Document).filter(Document.po_set_id == pid).count() == 3
        actions = [a.action for a in s.query(AuditLog).all()]
        assert AuditAction.quarantine_delete in actions


def _quarantined_set(cfg, po_no: str) -> int:
    pid = _set(cfg, po_no)
    _doc(cfg, pid, DocType.PO, f"{po_no}-PO.pdf", [("1", "BOLT", 100)], po_no=po_no)
    _doc(cfg, pid, DocType.DN, f"{po_no}-DN.pdf", [("2", "BOLT", 100)], po_no=po_no)
    _doc(
        cfg,
        pid,
        DocType.SI,
        f"{po_no}-SI.pdf",
        [("1", "BOLT", 100)],
        po_no=po_no,
        si_no=f"INV-{po_no}",
    )
    assert reconcile_po_set(pid, cfg)["status"] == "quarantined"
    return pid


def test_archive_keeps_rows_manifest_and_hides_from_worklists(cfg, client):
    """Delete is an export: rows/files stay, worklists hide the set."""
    import json

    po_no = "PO-ARCHIVE"
    pid = _quarantined_set(cfg, po_no)
    assert po_no in client.get("/quarantine").text

    r = client.delete(f"/po_sets/{pid}/quarantine")
    assert r.status_code == 200, r.text
    assert po_no not in client.get("/quarantine").text
    assert po_no not in client.get("/dashboard").text

    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        assert ps is not None and ps.archived_at is not None
        assert ps.archive_path and Path(ps.archive_path).is_dir()
        archive = Path(ps.archive_path)
        docs = s.query(Document).filter_by(po_set_id=pid).all()
        assert len(docs) == 3
        assert all(d.archived_at is not None for d in docs)
        assert s.query(LineItem).count() == 3

        po_json = json.loads((archive / "po_set.json").read_text(encoding="utf-8"))
        assert Path(po_json["archive_path"]) == archive
        manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["po_set_id"] == pid
        assert manifest["document_count"] == 3
        assert len(manifest["files"]) == 3
        assert {row["sha256_hash"] for row in manifest["files"]} == {d.sha256_hash for d in docs}
        exported_docs = json.loads((archive / "documents.json").read_text(encoding="utf-8"))
        assert len(exported_docs) == 3
        assert (archive / "line_items.json").is_file()
        assert (archive / "audit_log.json").is_file()
        assert (archive / "ARCHIVE.txt").is_file()
        assert len(list((archive / "files").glob("*.pdf"))) == 3

    detail = client.get(f"/po_sets/{pid}/view").text
    assert "Archived PO Set" in detail
    assert "Force Merge" not in detail

    # Idempotent: a second Delete does not create a second export folder.
    before = sorted(p.name for p in Path(ps.archive_path).parent.iterdir())
    assert client.delete(f"/po_sets/{pid}/quarantine").status_code == 200
    assert sorted(p.name for p in Path(ps.archive_path).parent.iterdir()) == before

    # Archived sets are read-only server-side, not just in the template.
    assert client.post(f"/po_sets/{pid}/redo_match").status_code == 409
    assert client.post(f"/po_sets/{pid}/merge").status_code == 409
    assert client.post(f"/po_sets/{pid}/force_merge").status_code == 409


def test_archive_allows_a_new_set_for_the_same_po(cfg):
    from app.services.grouping import get_or_create_po_set

    po_no = "5002"
    pid = _quarantined_set(cfg, po_no)
    from app.services.quarantine import delete_quarantined

    delete_quarantined(pid, cfg, source="operator")

    new_set = get_or_create_po_set(po_no, cfg)
    assert new_set is not None
    assert new_set.id != pid
    assert new_set.archived_at is None


def test_archived_document_does_not_block_identical_reingest(cfg):
    from app.services.ingestion import ingest_file

    po_no = "PO-REHASH"
    pid = _quarantined_set(cfg, po_no)
    eng = get_engine(cfg)
    with Session(eng) as s:
        old = s.query(Document).filter_by(po_set_id=pid).first()
        old_id = old.id
        source = Path(old.stored_path)

    from app.services.quarantine import delete_quarantined

    delete_quarantined(pid, cfg, source="operator")
    fresh = ingest_file(source, cfg)
    assert fresh.id != old_id
    assert fresh.archived_at is None
    assert fresh.sha256_hash == hashlib.sha256(source.read_bytes()).hexdigest()


def test_archive_copy_failure_leaves_rows_untouched(cfg, monkeypatch):
    import pytest

    from app.services import quarantine as quarantine_module

    po_no = "PO-COPYFAIL"
    pid = _quarantined_set(cfg, po_no)

    def _fail(*args, **kwargs):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(quarantine_module.shutil, "copy2", _fail)
    with pytest.raises(OSError, match="simulated copy failure"):
        quarantine_module.delete_quarantined(pid, cfg, source="operator")

    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        assert ps is not None and ps.archived_at is None
        assert s.query(Document).filter_by(po_set_id=pid).count() == 3
        assert all(d.archived_at is None for d in s.query(Document).filter_by(po_set_id=pid))


def test_archive_moves_input_copies_only_on_content_match(cfg):
    import shutil

    from app.services.quarantine import delete_quarantined

    po_no = "PO-INPUT"
    pid = _quarantined_set(cfg, po_no)
    eng = get_engine(cfg)
    with Session(eng) as s:
        doc = s.query(Document).filter_by(po_set_id=pid).first()
        stored = Path(doc.stored_path)
        # _doc seeds a synthetic hash for test uniqueness; this test exercises
        # content matching, so make the row's hash match the stored bytes.
        doc.sha256_hash = hashlib.sha256(stored.read_bytes()).hexdigest()
        s.commit()

    input_dir = Path(cfg.paths.input_folder)
    matching = input_dir / "matching.pdf"
    other = input_dir / "other.pdf"
    shutil.copy2(stored, matching)
    other.write_bytes(b"%PDF-1.4 unrelated")

    delete_quarantined(pid, cfg, source="operator")

    assert not matching.exists()
    assert other.exists()
    with Session(eng) as s:
        archive = Path(s.get(POSet, pid).archive_path)
    assert (archive / "input" / "matching.pdf").is_file()


def test_archive_retry_after_commit_failure_reuses_one_export(cfg, monkeypatch):
    import json

    from sqlalchemy.orm import Session as OrmSession

    from app.services.quarantine import delete_quarantined

    po_no = "PO-RETRY"
    pid = _quarantined_set(cfg, po_no)
    eng = get_engine(cfg)
    with Session(eng) as s:
        stored = [Path(d.stored_path) for d in s.query(Document).filter_by(po_set_id=pid)]
    stored[0].unlink()  # force one missing file into the manifest

    original_commit = OrmSession.commit
    calls = {"n": 0}

    def fail_once(self):
        if calls["n"] == 0:
            calls["n"] = 1
            raise RuntimeError("simulated commit failure")
        return original_commit(self)

    monkeypatch.setattr(OrmSession, "commit", fail_once)
    try:
        import pytest

        with pytest.raises(RuntimeError, match="simulated commit failure"):
            delete_quarantined(pid, cfg, source="operator")
    finally:
        monkeypatch.undo()

    # Retry: must find the completed folder, mark rows, and not duplicate it.
    audit = delete_quarantined(pid, cfg, source="operator")
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        assert ps.archived_at is not None
        archive = Path(ps.archive_path)
        folders = [
            p for p in archive.parent.iterdir() if p.is_dir() and p.name.startswith(f"{po_no}_")
        ]
        assert len(folders) == 1, folders
        assert s.query(Document).filter_by(po_set_id=pid).count() == 3
        assert all(d.archived_at is not None for d in s.query(Document).filter_by(po_set_id=pid))

    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    detail = json.loads(audit.detail)
    assert detail["missing_files"] == len(manifest["missing_files"]) == 1


# ---------------------------------------------------------------------------
# Every remaining verdict, and what the pages say about it
# ---------------------------------------------------------------------------


def test_pending_refresh_fragment_also_shows_the_reason(cfg, client):
    pid = _two_po_set(cfg, "PO-FRAG")
    assert reconcile_po_set(pid, cfg)["reason"] == "multiple_po_documents"

    fragment = client.get("/dashboard/table").text
    assert "PO-FRAG" in fragment
    assert "2 PO documents" in fragment


def test_po_reference_mismatch_quarantines_with_the_offending_file(cfg, client):
    pid = _set(cfg, "PO-REF")
    _doc(cfg, pid, DocType.PO, "ref-po.pdf", [("1", "BOLT", 100)], po_no="PO-REF")
    _doc(cfg, pid, DocType.DN, "ref-dn.pdf", [("1", "BOLT", 100)], po_no="SOMEONE-ELSE")
    _doc(cfg, pid, DocType.SI, "ref-si.pdf", [("1", "BOLT", 100)], si_no="INV-REF", po_no="PO-REF")

    result = reconcile_po_set(pid, cfg)
    assert result["reason"] == "po_reference_mismatch"

    ps = _status(cfg, pid)
    assert "ref-dn.pdf" in (ps.reconcile_reason or "")
    assert "SOMEONE-ELSE" in (ps.reconcile_reason or "")
    page = client.get("/quarantine").text
    assert "ref-dn.pdf" in page


def test_vendor_line_with_no_po_counterpart_quarantines(cfg, client):
    pid = _set(cfg, "PO-ORPHAN")
    _doc(cfg, pid, DocType.PO, "orph-po.pdf", [("1", "BOLT", 100)], po_no="PO-ORPHAN")
    _doc(cfg, pid, DocType.DN, "orph-dn.pdf", [("2", "NUT", 5)], po_no="PO-ORPHAN")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "orph-si.pdf",
        [("1", "BOLT", 100)],
        si_no="INV-ORPH",
        po_no="PO-ORPHAN",
    )

    result = reconcile_po_set(pid, cfg)
    assert result["reason"] == "unmatched_vendor_line"
    assert "could not be matched" in (client.get("/quarantine").text).lower()


def test_non_positive_quantity_quarantines(cfg, client):
    pid = _set(cfg, "PO-ZERO")
    _doc(cfg, pid, DocType.PO, "zero-po.pdf", [("1", "BOLT", 0)], po_no="PO-ZERO")
    _doc(cfg, pid, DocType.DN, "zero-dn.pdf", [("1", "BOLT", 0)], po_no="PO-ZERO")
    _doc(cfg, pid, DocType.SI, "zero-si.pdf", [("1", "BOLT", 0)], si_no="INV-ZERO", po_no="PO-ZERO")

    result = reconcile_po_set(pid, cfg)
    assert result["reason"] == "non_positive_quantity"
    assert "zero" in (client.get("/quarantine").text).lower()


def test_po_read_with_no_line_items_quarantines_with_that_reason(cfg, client):
    pid = _set(cfg, "PO-EMPTY")
    _doc(cfg, pid, DocType.PO, "empty-po.pdf", [], po_no="PO-EMPTY")
    _doc(cfg, pid, DocType.DN, "empty-dn.pdf", [("1", "BOLT", 100)], po_no="PO-EMPTY")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "empty-si.pdf",
        [("1", "BOLT", 100)],
        si_no="INV-EMPTY",
        po_no="PO-EMPTY",
    )

    result = reconcile_po_set(pid, cfg)
    assert result["reason"] == "po_document_has_no_line_items"
    assert "no line items" in (client.get("/quarantine").text).lower()


def test_partial_delivery_is_pending_not_quarantined(cfg, client):
    """A PO line the vendor has not reported at all is a wait, not a failure."""
    pid = _set(cfg, "PO-PART")
    _doc(
        cfg,
        pid,
        DocType.PO,
        "part-po.pdf",
        [("1", "BOLT", 100), ("2", "NUT", 50)],
        po_no="PO-PART",
    )
    _doc(cfg, pid, DocType.DN, "part-dn.pdf", [("1", "BOLT", 100)], po_no="PO-PART")
    _doc(
        cfg, pid, DocType.SI, "part-si.pdf", [("1", "BOLT", 100)], si_no="INV-PART", po_no="PO-PART"
    )

    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "pending"
    assert result["reason"] == "partial_fulfillment"

    ps = _status(cfg, pid)
    assert ps.status == POSetStatus.pending
    assert "Waiting on more deliveries" in (ps.reconcile_reason or "")
    assert "PO-PART" not in client.get("/quarantine").text


def test_a_second_po_after_the_first_set_merged_starts_a_new_set(cfg):
    """The dedup/multiple-PO rules must not block a genuinely new delivery of
    the same PO after the first packet is final."""
    from app.models import Document as Doc
    from app.services.grouping import group_document

    pid = _standard_set(cfg, "5003")
    _doc(cfg, pid, DocType.DN, "again-dn.pdf", [("1", "BOLT", 100)], po_no="5003")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "again-si.pdf",
        [("1", "BOLT", 100)],
        si_no="INV-AGAIN",
        po_no="5003",
    )
    assert reconcile_po_set(pid, cfg)["status"] == "merged"

    eng = get_engine(cfg)
    with Session(eng) as s:
        sha = hashlib.sha256(b"second-po").hexdigest()
        doc = Doc(
            sha256_hash=sha,
            original_filename="again-po-2.pdf",
            stored_path="",
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_no_normalized="5003",
            po_no_raw="5003",
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)

        new_id, problem = group_document(doc, cfg)

    assert problem is None
    assert new_id is not None and new_id != pid
    assert _status(cfg, new_id).status == POSetStatus.pending


def test_packet_name_collision_quarantines_the_second_set(cfg, client):
    """Two sets, same invoice number: the second must never overwrite the first
    packet. It quarantines as packet_naming_failed and the page says so."""
    first = _set(cfg, "PO-NAME-A")
    _doc(cfg, first, DocType.PO, "name-a-po.pdf", [("1", "BOLT", 100)], po_no="PO-NAME-A")
    _doc(cfg, first, DocType.DN, "name-a-dn.pdf", [("1", "BOLT", 100)], po_no="PO-NAME-A")
    _doc(
        cfg,
        first,
        DocType.SI,
        "name-a-si.pdf",
        [("1", "BOLT", 100)],
        si_no="INV-COLLIDE",
        po_no="PO-NAME-A",
    )
    assert reconcile_po_set(first, cfg)["status"] == "merged"

    second = _set(cfg, "PO-NAME-B")
    _doc(cfg, second, DocType.PO, "name-b-po.pdf", [("1", "BOLT", 100)], po_no="PO-NAME-B")
    _doc(cfg, second, DocType.DN, "name-b-dn.pdf", [("1", "BOLT", 100)], po_no="PO-NAME-B")
    _doc(
        cfg,
        second,
        DocType.SI,
        "name-b-si.pdf",
        [("1", "BOLT", 100)],
        si_no="INV-COLLIDE",
        po_no="PO-NAME-B",
    )
    result = reconcile_po_set(second, cfg)

    assert result["status"] == "quarantined"
    assert result["reason"] == "packet_naming_failed"
    assert "PO-NAME-B" in client.get("/quarantine").text
    assert "already exists" in (client.get("/quarantine").text).lower()


def test_failed_document_is_in_the_holding_area_not_the_quarantine_page(cfg, client):
    eng = get_engine(cfg)
    with Session(eng) as s:
        s.add(
            Document(
                sha256_hash=hashlib.sha256(b"broken").hexdigest(),
                original_filename="broken-scan.pdf",
                stored_path="",
                doc_type=DocType.UNKNOWN,
                extraction_status=ExtractionStatus.failed,
                extraction_attempt_count=3,
                po_set_id=None,
            )
        )
        s.commit()

    holding = client.get("/unclassified").text
    assert "broken-scan.pdf" in holding
    assert "failed" in holding.lower()
    assert "broken-scan.pdf" not in client.get("/quarantine").text


def test_needs_review_detail_page_shows_the_numbers(cfg, client):
    pid = _set(cfg, "PO-REVIEW2")
    _doc(cfg, pid, DocType.PO, "rev2-po.pdf", [("1", "BOLT", 100)], po_no="PO-REVIEW2")
    _doc(cfg, pid, DocType.DN, "rev2-dn.pdf", [("1", "BOLT", 60)], po_no="PO-REVIEW2")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "rev2-si.pdf",
        [("1", "BOLT", 60)],
        si_no="INV-REV2",
        po_no="PO-REVIEW2",
    )
    assert reconcile_po_set(pid, cfg)["status"] == "mismatched"

    detail = client.get(f"/po_sets/{pid}/view").text
    assert "Why this set needs review" in detail
    assert "60 of 100" in detail


def test_holding_area_count_is_the_same_everywhere(cfg, client):
    """A valid but unplaced DN is holding-area work. The dashboard badge used
    to count only UNKNOWN documents, so it showed 2 while the page held 37."""
    eng = get_engine(cfg)
    with Session(eng) as s:
        s.add(
            Document(
                sha256_hash=hashlib.sha256(b"held-dn").hexdigest(),
                original_filename="held-dn.pdf",
                stored_path="",
                doc_type=DocType.DN,
                extraction_status=ExtractionStatus.valid,
                po_set_id=None,
                po_no_normalized=None,
            )
        )
        s.add(
            Document(
                sha256_hash=hashlib.sha256(b"held-unknown").hexdigest(),
                original_filename="held-unknown.pdf",
                stored_path="",
                doc_type=DocType.UNKNOWN,
                extraction_status=ExtractionStatus.valid,
                po_set_id=None,
            )
        )
        s.commit()

    holding = client.get("/unclassified")
    assert "2 awaiting classification" in holding.text
    assert '<span class="nav-counter-badge">2</span>' in holding.text

    dashboard = client.get("/dashboard")
    assert '<span class="nav-counter-badge">2</span>' in dashboard.text


def test_naming_the_po_on_a_held_document_reconciles_its_set(cfg, client):
    """A human naming the PO must close the loop, not wait for the next scan."""
    pid = _set(cfg, "5004")
    _doc(cfg, pid, DocType.PO, "human-po.pdf", [("1", "BOLT", 100)], po_no="5004")
    _doc(cfg, pid, DocType.DN, "human-dn.pdf", [("1", "BOLT", 100)], po_no="5004")

    # The invoice was read but carries no PO reference: it waits in the holding area.
    eng = get_engine(cfg)
    with Session(eng) as s:
        from app.models import LineItem

        sha, stored = _pdf(cfg, "human-si")
        doc = Document(
            sha256_hash=sha,
            original_filename="human-si.pdf",
            stored_path=str(stored),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=None,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        s.add(
            LineItem(
                document_id=doc.id,
                line_item_no="1",
                description="BOLT",
                quantity=100 * K,
                unit_price=0,
            )
        )
        s.commit()
        doc_id = doc.id

    assert "human-si.pdf" in client.get("/unclassified").text

    r = client.post(
        f"/unclassified/{doc_id}/reclassify",
        data={"doc_type": "SI", "po_no": "PO-5004"},
        follow_redirects=False,
    )
    assert r.status_code in (200, 302), r.text

    ps = _status(cfg, pid)
    assert ps.status == POSetStatus.merged, ps.reconcile_reason
    assert ps.merged_output_path and Path(ps.merged_output_path).exists()


def test_missing_stored_file_refuses_to_merge_instead_of_crashing(cfg):
    """A row whose stored copy is gone (or blank) is a refusal, not a 500.

    The sweep must survive a damaged row: `_write_merged` treats it as missing,
    auto-merge returns None, and the set keeps its previous status.
    """
    pid = _set(cfg, "PO-LOST")
    _doc(cfg, pid, DocType.PO, "lost-po.pdf", [("1", "BOLT", 100)], po_no="PO-LOST")
    _doc(cfg, pid, DocType.DN, "lost-dn.pdf", [("1", "BOLT", 100)], po_no="PO-LOST")
    _doc(
        cfg, pid, DocType.SI, "lost-si.pdf", [("1", "BOLT", 100)], si_no="INV-LOST", po_no="PO-LOST"
    )

    eng = get_engine(cfg)
    with Session(eng) as s:
        for doc in s.query(Document).filter(Document.po_set_id == pid).all():
            doc.stored_path = ""
        s.commit()

    result = reconcile_po_set(pid, cfg)  # must not raise

    assert result["status"] == "quarantined"
    assert result["reason"] == "packet_source_unreadable"
    assert "lost-" in (result.get("detail") or "")
    assert _status(cfg, pid).merged_output_path is None
