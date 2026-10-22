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


def test_po_line_without_a_number_quarantines_and_both_pages_say_why(cfg, client):
    pid = _set(cfg, "PO-NOLINE")
    _doc(cfg, pid, DocType.PO, "po-noline.pdf", [(None, "BOLT", 100)], po_no="PO-NOLINE")
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


def test_two_po_documents_quarantine_and_the_pages_explain_it(cfg, client):
    pid = _two_po_set(cfg, "PO-DUP")
    result = reconcile_po_set(pid, cfg)

    assert result["status"] == "quarantined"
    assert result["reason"] == "multiple_po_documents"
    ps = _status(cfg, pid)
    assert ps.merged_output_path is None
    assert "2 PO documents" in (ps.reconcile_reason or "")

    quarantine = client.get("/quarantine").text
    assert "PO-DUP" in quarantine
    assert "more than one PO" in quarantine.lower() or "2 PO documents" in quarantine

    # This is the regression: every line matches, so the matrix is green, but
    # the set is quarantined. The page must still say why.
    detail = client.get(f"/po_sets/{pid}/view").text
    assert "2 PO documents" in detail or "more than one PO" in detail.lower()


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


def test_duplicate_po_with_a_different_hash_still_quarantines(cfg, client):
    pid = _two_po_set(cfg, "PO-DIFFHASH")
    result = reconcile_po_set(pid, cfg)
    assert result["reason"] == "multiple_po_documents"


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
    pid = _standard_set(cfg, "PLACED")
    _doc(cfg, pid, DocType.DN, "placed-dn.pdf", [("1", "BOLT", 100)], po_no="PLACED")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "placed-si.pdf",
        [("1", "BOLT", 100)],
        si_no="INV-PLACED",
        po_no="PLACED",
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
        data={"doc_type": "SHIPPING", "po_no": "PO-PLACED"},
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
    assert "Awaiting Customs" in client.get("/dashboard").text

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
    doc_id = _doc(cfg, pid, DocType.PO, "del-po.pdf", [(None, "BOLT", 100)], po_no="PO-DEL")
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
    assert stored.exists(), "stored document must survive the record delete"
    with Session(eng) as s:
        assert s.get(POSet, pid) is None
        assert s.query(Document).filter(Document.po_set_id == pid).count() == 0
        actions = [a.action for a in s.query(AuditLog).all()]
        assert AuditAction.quarantine_delete in actions


# ---------------------------------------------------------------------------
# Every remaining verdict, and what the pages say about it
# ---------------------------------------------------------------------------


def test_quarantine_refresh_fragment_also_shows_the_reason(cfg, client):
    pid = _two_po_set(cfg, "PO-FRAG")
    assert reconcile_po_set(pid, cfg)["reason"] == "multiple_po_documents"

    fragment = client.get("/quarantine/table").text
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

    pid = _standard_set(cfg, "PO-AGAIN")
    _doc(cfg, pid, DocType.DN, "again-dn.pdf", [("1", "BOLT", 100)], po_no="PO-AGAIN")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "again-si.pdf",
        [("1", "BOLT", 100)],
        si_no="INV-AGAIN",
        po_no="PO-AGAIN",
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
            po_no_normalized="PO-AGAIN",
            po_no_raw="PO-AGAIN",
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
    pid = _set(cfg, "HUMAN")
    _doc(cfg, pid, DocType.PO, "human-po.pdf", [("1", "BOLT", 100)], po_no="HUMAN")
    _doc(cfg, pid, DocType.DN, "human-dn.pdf", [("1", "BOLT", 100)], po_no="HUMAN")

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
        data={"doc_type": "SI", "po_no": "PO-HUMAN"},
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

    assert result["status"] == "pending"
    assert _status(cfg, pid).merged_output_path is None
