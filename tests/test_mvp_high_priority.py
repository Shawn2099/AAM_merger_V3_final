"""MVP high-priority fixes — one behaviour per test.

Each test states a rule the product must keep, in Given/When/Then form, with
static expected values. Every one of them fails on the pre-fix code:

1. A quarantine's plain-language reason must survive a later sweep.
2. A reconcile result carries a reason CODE, never a sentence.
3. Deleting a quarantined set must clear its copy from the input folder.
4. A document left in `processing` by a dead worker must be re-claimed.
5. Uploading a file that already exists unattached must attach it, not
   silently do nothing.
6. `po_reference_ambiguous` must block every attach path, not just one.
7. A line item the VLM returned must never vanish from the baseline.
8. A line item without a description must keep its quantity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import (
    AuditAction,
    DocType,
    Document,
    ExtractionStatus,
    LineItem,
    POSet,
    POSetStatus,
)
from app.models.base import Base

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _cfg(tmp_path, name="mvp.db"):
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


def _pdf(path: Path):
    w = PdfWriter()
    w.add_blank_page(width=100, height=100)
    w.write(str(path))


def _new_set(s, po_no: str) -> int:
    ps = POSet(po_no_normalized=po_no, status=POSetStatus.pending)
    s.add(ps)
    s.flush()
    return ps.id


def _add_doc(
    s,
    cfg,
    ps_id: int | None,
    doc_type: str,
    filename: str,
    lines=(),
    po_no: str | None = None,
    **extra,
) -> Document:
    """A document with real line items, as the VLM would have stored them."""
    path = Path(cfg.paths.stored_documents_folder) / filename
    _pdf(path)
    doc = Document(
        sha256_hash=f"{filename}-{ps_id}",
        original_filename=filename,
        stored_path=str(path),
        doc_type=DocType[doc_type],
        extraction_status=ExtractionStatus.valid,
        po_set_id=ps_id,
        po_no_normalized=po_no,
        **extra,
    )
    s.add(doc)
    s.flush()
    for line_no, qty in lines:
        s.add(
            LineItem(
                document_id=doc.id,
                line_item_no=line_no,
                description=f"item {line_no}",
                quantity=qty * 1000,
                unit_price=1000,
            )
        )
    s.flush()
    return doc


# ---------------------------------------------------------------------------
# 1 + 2 — a quarantine's reason survives, and the result carries a code
# ---------------------------------------------------------------------------


def test_quarantined_set_keeps_its_reason_across_a_later_sweep(tmp_path):
    """Given a set quarantined for an unmatched vendor line, when the nightly
    sweep reconciles it again, then the stored reason is unchanged.

    The sweep reconciles every open set, quarantined ones included. It must
    not replace the sentence that explains the quarantine with a generic
    "Awaiting further processing", which reads as a wait.
    """
    from app.services.reconciliation import REASON_TEXT, reconcile_po_set

    cfg = _cfg(tmp_path, "reason.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps_id = _new_set(s, "PO-REASON")
        _add_doc(s, cfg, ps_id, "PO", "po.pdf", [("1", 100)], po_no="PO-REASON")
        # line 2 has no PO counterpart -> the set is quarantined
        _add_doc(s, cfg, ps_id, "DN", "dn.pdf", [("2", 100)], po_no="PO-REASON")
        _add_doc(s, cfg, ps_id, "SI", "si.pdf", [("1", 100)], po_no="PO-REASON")
        s.commit()

    first = reconcile_po_set(ps_id, cfg)
    assert first["status"] == "quarantined", first
    assert first["reason"] == "unmatched_vendor_line", first
    expected = REASON_TEXT["unmatched_vendor_line"]
    with Session(eng) as s:
        assert s.get(POSet, ps_id).reconcile_reason == expected

    # a later sweep — the same call the nightly Phase-2 sweep makes
    second = reconcile_po_set(ps_id, cfg)
    assert second["status"] == "quarantined", second
    with Session(eng) as s:
        assert s.get(POSet, ps_id).reconcile_reason == expected


def test_reconcile_result_carries_a_reason_code_not_a_sentence(tmp_path):
    """Given an already-quarantined set, when it is reconciled again, then
    `reason` holds no sentence.

    `reason` is a reason CODE everywhere else in the result, and a code is
    what the API and the dashboard can act on. A sentence in that field
    cannot be mapped back to a state — that is what let a later sweep
    overwrite a quarantine's explanation.
    """
    from app.services.reconciliation import REASON_TEXT, reconcile_po_set

    cfg = _cfg(tmp_path, "reason_code.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps_id = _new_set(s, "PO-CODE")
        _add_doc(s, cfg, ps_id, "PO", "po.pdf", [("1", 100)], po_no="PO-CODE")
        _add_doc(s, cfg, ps_id, "DN", "dn.pdf", [("2", 100)], po_no="PO-CODE")
        _add_doc(s, cfg, ps_id, "SI", "si.pdf", [("1", 100)], po_no="PO-CODE")
        s.commit()

    reconcile_po_set(ps_id, cfg)
    again = reconcile_po_set(ps_id, cfg)

    assert again["status"] == "quarantined", again
    assert again.get("reason") is None, again
    assert again.get("detail") == REASON_TEXT["unmatched_vendor_line"], again


# ---------------------------------------------------------------------------
# 3 — deleting a quarantined set clears the input folder
# ---------------------------------------------------------------------------


def test_delete_quarantined_clears_the_input_folder_copy(tmp_path):
    """Given a quarantined set whose file is still in the input folder, when
    the operator deletes the set, then the input copy is moved out.

    The rows are deleted, so the next sync would otherwise re-ingest the same
    bytes and recreate the set the operator just deleted.
    """
    from app.services.quarantine import delete_quarantined

    cfg = _cfg(tmp_path, "delete.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps_id = _new_set(s, "PO-DELETE")
        _add_doc(s, cfg, ps_id, "PO", "po.pdf", [("1", 100)], po_no="PO-DELETE")
        s.commit()
        ps = s.get(POSet, ps_id)
        ps.status = POSetStatus.quarantined
        s.commit()
        stored = s.get(Document, ps_id).stored_path

    # the operator's file is still sitting in the input folder
    input_copy = Path(cfg.paths.input_folder) / "po.pdf"
    input_copy.write_bytes(Path(stored).read_bytes())

    audit = delete_quarantined(
        ps_id, cfg, justification="operator cleared this quarantined set for good"
    )

    assert audit.action == AuditAction.quarantine_delete
    assert not list(Path(cfg.paths.input_folder).glob("*.pdf")), "input copy still present"
    moved = Path(cfg.paths.quarantine_folder) / "deleted_inputs" / "po.pdf"
    assert moved.exists(), "the file must be kept, not deleted"


# ---------------------------------------------------------------------------
# 4 — a document stuck in `processing` is re-claimed
# ---------------------------------------------------------------------------


def test_a_document_stuck_in_processing_is_reclaimed(tmp_path):
    """Given a document left in `processing` by a worker that died, when the
    sweep starts, then it is released back to `pending` and read again.

    `extract_document` claims a document (pending -> processing) before the
    VLM call, and that claim is committed. Without a re-claim the row stays
    `processing` forever: the recovery sweep only looks at `pending`, and the
    extraction task returns the row unchanged — so the document is reported
    as processed and never read again.
    """
    from app.services.extraction import reclaim_stale_processing

    cfg = _cfg(tmp_path, "stale.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    old = datetime.now(UTC) - timedelta(hours=2)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="stuck",
            original_filename="stuck.pdf",
            stored_path=str(Path(cfg.paths.stored_documents_folder) / "stuck.pdf"),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.processing,
            updated_at=old,
        )
        s.add(doc)
        s.commit()
        doc_id = doc.id

    reclaimed = reclaim_stale_processing(cfg)

    assert reclaimed == 1
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        assert doc.extraction_status == ExtractionStatus.pending
        assert doc.extraction_attempt_count == 0, "the claim was never finished"


def test_a_document_being_read_now_is_not_reclaimed(tmp_path):
    """Given a document claimed a moment ago, when the re-claim runs, then it
    is left alone — a live extraction is not a dead one."""
    from app.services.extraction import reclaim_stale_processing

    cfg = _cfg(tmp_path, "fresh.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="fresh",
            original_filename="fresh.pdf",
            stored_path=str(Path(cfg.paths.stored_documents_folder) / "fresh.pdf"),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.processing,
        )
        s.add(doc)
        s.commit()
        doc_id = doc.id

    assert reclaim_stale_processing(cfg) == 0
    with Session(eng) as s:
        assert s.get(Document, doc_id).extraction_status == ExtractionStatus.processing


# ---------------------------------------------------------------------------
# 5 — uploading a file that already exists, unattached
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, "upload.db")
    monkeypatch.setattr("app.api.routes.dashboard.load_config", lambda path=None: cfg)
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: cfg)
    from app.main import app

    with TestClient(app) as c:
        yield c, cfg


def test_upload_attaches_a_file_that_already_exists_unattached(client):
    """Given a PDF that is already in the system but attached to nothing,
    when the operator uploads it as this set's shipping document, then it is
    attached to the set and the customs gate can clear.

    The file is the same bytes: the system holds one row per file. Refusing
    to attach it left the gate permanently unsatisfied and the operator with
    no way to tell a working upload from a discarded one.
    """
    from app.models import DocType, Document, ExtractionStatus

    c, cfg = client
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    pdf_path = Path(cfg.paths.stored_documents_folder) / "shipping.pdf"
    _pdf(pdf_path)
    data = pdf_path.read_bytes()
    import hashlib

    sha = hashlib.sha256(data).hexdigest()

    with Session(eng) as s:
        ps_id = _new_set(s, "PO-UPLOAD")
        ps = s.get(POSet, ps_id)
        ps.has_customs_toggle = True
        ps.status = POSetStatus.blocked_customs
        s.add(
            Document(
                sha256_hash=sha,
                original_filename="shipping.pdf",
                stored_path=str(pdf_path),
                doc_type=DocType.UNKNOWN,
                extraction_status=ExtractionStatus.failed,
                po_set_id=None,
            )
        )
        s.commit()

    with open(pdf_path, "rb") as fh:
        resp = c.post(
            f"/po_sets/{ps_id}/upload",
            files={"file": ("shipping.pdf", fh, "application/pdf")},
            data={"doc_type": "SHIPPING"},
            follow_redirects=False,
        )

    assert resp.status_code == 302, resp.text
    with Session(eng) as s:
        doc = s.query(Document).filter_by(sha256_hash=sha).one()
        ps = s.get(POSet, ps_id)
        assert doc.po_set_id == ps_id, "the file was not attached"
        assert doc.doc_type == DocType.SHIPPING
        assert ps.customs_doc_count == 1


def test_upload_refuses_a_file_that_carries_pipeline_data(client):
    """Given the same bytes are already a PO with line items, when the
    operator uploads them as this set's shipping document, then the upload is
    refused with a 409 that names the conflict.

    Re-typing a real document as a customs document would corrupt it and
    the set it belongs to.
    """
    from app.models import DocType, Document

    c, cfg = client
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    pdf_path = Path(cfg.paths.stored_documents_folder) / "po-file.pdf"
    _pdf(pdf_path)
    import hashlib

    sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()

    with Session(eng) as s:
        ps_id = _new_set(s, "PO-UPLOAD-2")
        doc = _add_doc(s, cfg, None, "PO", "po-file.pdf", [("1", 100)], po_no="PO-UPLOAD-2")
        doc.sha256_hash = sha
        s.commit()

    with open(pdf_path, "rb") as fh:
        resp = c.post(
            f"/po_sets/{ps_id}/upload",
            files={"file": ("po-file.pdf", fh, "application/pdf")},
            data={"doc_type": "SHIPPING"},
            follow_redirects=False,
        )

    assert resp.status_code == 409, resp.text
    assert "pipeline data" in resp.json()["detail"], resp.json()
    with Session(eng) as s:
        doc = s.query(Document).filter_by(sha256_hash=sha).one()
        assert doc.doc_type == DocType.PO, "the PO must not be re-typed"
        assert doc.po_set_id is None


# ---------------------------------------------------------------------------
# 6 — ambiguity blocks every attach path
# ---------------------------------------------------------------------------


def test_ambiguous_document_is_not_attached_by_the_resolution_sweep(tmp_path):
    """Given a document that shows more than one PO number, when the
    resolution sweep runs, then it stays unattached.

    Attaching it to any one PO strands the others, so it waits for a human.
    The attach sweep already refuses it; the resolution sweep must refuse it
    too, or a filename resemblance decides what a human has to decide.
    """
    from app.services.grouping import resolve_unattached_documents

    cfg = _cfg(tmp_path, "ambiguous.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps_id = _new_set(s, "PO-AMB")
        _add_doc(
            s,
            cfg,
            ps_id,
            "PO",
            "SIV-DTS-25-477-PO.pdf",
            [("1", 100)],
            po_no="PO-AMB",
        )
        ambiguous = _add_doc(
            s,
            cfg,
            None,
            "DN",
            "SIV-DTS-25-477-DN.pdf",
            [("1", 100)],
            po_reference_ambiguous=True,
        )
        s.commit()
        ambiguous_id = ambiguous.id

    resolve_unattached_documents(cfg)

    with Session(eng) as s:
        assert s.get(Document, ambiguous_id).po_set_id is None


# ---------------------------------------------------------------------------
# 7 + 8 — a line item the VLM returned is never dropped
# ---------------------------------------------------------------------------


def _extracted_doc(cfg, doc_type="PO", po_no="PO-LINE", line_items=()):
    """A document populated from a raw VLM result, as extraction does."""
    from app.models import Document
    from app.services.extraction import populate_from_raw_dict

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="extracted",
            original_filename="extracted.pdf",
            stored_path=str(Path(cfg.paths.stored_documents_folder) / "extracted.pdf"),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.flush()
        populate_from_raw_dict(
            doc,
            {"document_type": doc_type, "po_no_raw": po_no, "line_items": list(line_items)},
            s,
            cfg,
        )
        s.commit()
        return doc.id


def test_a_line_item_without_a_quantity_is_kept_not_dropped(tmp_path):
    """Given the VLM returned a row it could not read a quantity from, when
    the result is stored, then the row is kept and the set is quarantined.

    Dropping a PO row removes a line from the baseline the DN and SI are
    compared against, and the set can then merge with that line never
    checked. A row without a usable quantity is kept with a zero quantity,
    which the existing `non_positive_quantity` guard quarantines — the safe
    direction, and the reviewer sees the line.
    """
    from app.services.reconciliation import reconcile_po_set

    cfg = _cfg(tmp_path, "noqty.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    po_id = _extracted_doc(
        cfg,
        line_items=[
            {"line_item_no": "1", "description": "WASHER", "quantity": "10", "unit_price": "5.00"},
            {"line_item_no": "2", "description": "NUT", "quantity": None, "unit_price": "1.00"},
        ],
    )
    with Session(eng) as s:
        po = s.get(Document, po_id)
        assert len(po.line_items) == 2, "the row was dropped"
        zero = next(li for li in po.line_items if li.line_item_no == "2")
        assert zero.quantity == 0

        ps_id = _new_set(s, "PO-LINE")
        po.po_set_id = ps_id
        po.po_no_normalized = "PO-LINE"
        _add_doc(s, cfg, ps_id, "DN", "dn.pdf", [("1", 10)], po_no="PO-LINE")
        _add_doc(s, cfg, ps_id, "SI", "si.pdf", [("1", 10)], po_no="PO-LINE")
        s.commit()

    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "quarantined", res
    assert res["reason"] == "non_positive_quantity", res


def test_a_line_item_without_a_description_keeps_its_quantity(tmp_path):
    """Given the VLM returned a row without a description, when the result is
    stored, then the row is kept with its quantity.

    The description is a fallback matching signal, not a reconciliation
    signal. Losing the row to a missing description would remove a line from
    the baseline for no reason at all.
    """
    cfg = _cfg(tmp_path, "nodesc.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    po_id = _extracted_doc(
        cfg,
        line_items=[
            {"line_item_no": "1", "description": None, "quantity": "5", "unit_price": "1.00"},
        ],
    )
    with Session(eng) as s:
        po = s.get(Document, po_id)
        assert len(po.line_items) == 1, "the row was dropped"
        assert po.line_items[0].quantity == 5000
        assert po.line_items[0].description == ""
