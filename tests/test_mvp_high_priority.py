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

import hashlib
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
        # The helper uses synthetic hashes for uniqueness in tests; this test
        # exercises content matching, so give the row its real stored hash.
        import hashlib

        doc = s.query(Document).filter_by(po_set_id=ps_id).one()
        doc.sha256_hash = hashlib.sha256(Path(doc.stored_path).read_bytes()).hexdigest()
        stored = doc.stored_path
        s.commit()

    # the operator's file is still sitting in the input folder
    input_copy = Path(cfg.paths.input_folder) / "po.pdf"
    input_copy.write_bytes(Path(stored).read_bytes())

    audit = delete_quarantined(
        ps_id, cfg, justification="operator cleared this quarantined set for good"
    )

    assert audit.action == AuditAction.quarantine_delete
    assert not list(Path(cfg.paths.input_folder).glob("*.pdf")), "input copy still present"
    with Session(eng) as s:
        archived = s.get(POSet, ps_id)
        assert archived is not None and archived.archived_at is not None
        moved = Path(archived.archive_path) / "input" / "po.pdf"
    assert moved.exists(), "the file must be kept in the archive, not deleted"


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


def test_the_recovery_flow_reads_a_document_a_dead_worker_left_behind(tmp_path, monkeypatch):
    """Given a document left in `processing` by a worker that died, when the
    recovery flow runs, then the document is claimed again and read.

    This is the end-to-end proof that the re-claim is wired into the flow
    and not merely available as a helper: the document ends up `valid`, the
    way a document that was never claimed ends up.
    """
    from app.services import extraction as ext

    cfg = _cfg(tmp_path, "flow.db")
    monkeypatch.setattr("app.flows.sync.load_config", lambda *a, **kw: cfg)
    monkeypatch.setattr("app.services.sync_lock.load_config", lambda *a, **kw: cfg)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")

    read = ext._VLMPageExtraction(
        document_type="PO",
        document_number="PO-STUCK",
        po_reference=None,
        po_reference_ambiguous=False,
        vendor_name="ACME TRADING EST.",
        line_items=[
            ext._VLMLineItem(
                line_item_no="1",
                description="NUT, HEX 9/16",
                quantity="50.00",
                unit_price="350.00",
                dn_no=None,
            )
        ],
    )

    class _Completions:
        def create(self, **kwargs):
            return read

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(ext.instructor, "from_openai", lambda *a, **kw: _Client())

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    stored = Path(cfg.paths.stored_documents_folder) / "stuck.pdf"
    _pdf(stored)
    old = datetime.now(UTC) - timedelta(hours=2)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="stuck-in-flow",
            original_filename="stuck.pdf",
            stored_path=str(stored),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.processing,
            updated_at=old,
        )
        s.add(doc)
        s.commit()
        doc_id = doc.id

    from app.flows.sync import recovery_flow

    run_fn = getattr(recovery_flow, "fn", recovery_flow)
    res = run_fn()

    assert res.get("status") != "skipped", res
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        assert doc.extraction_status == ExtractionStatus.valid, (
            f"the document was not read again (status={doc.extraction_status})"
        )


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


def test_a_vendor_line_without_a_quantity_is_kept_and_does_not_merge(tmp_path):
    """Given a delivery note row the VLM could not read a quantity from, when
    the set is reconciled, then the row is kept and the set is quarantined.

    The row is the vendor's statement about a line. Dropping it would make
    the delivery look complete and could let the set merge with that line
    never checked. Kept as a zero-quantity line, the product's existing
    "zero or negative anywhere quarantines the set" rule fires and the
    reviewer is shown the line.
    """
    from app.services.reconciliation import reconcile_po_set

    cfg = _cfg(tmp_path, "vendor_noqty.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    dn_id = _extracted_doc(
        cfg,
        doc_type="DN",
        po_no="PO-VND",
        line_items=[
            {"line_item_no": "1", "description": "WASHER", "quantity": None, "unit_price": "1.00"},
        ],
    )
    with Session(eng) as s:
        dn = s.get(Document, dn_id)
        assert len(dn.line_items) == 1, "the row was dropped"
        assert dn.line_items[0].quantity == 0

        ps_id = _new_set(s, "PO-VND")
        dn.po_set_id = ps_id
        dn.po_no_normalized = "PO-VND"
        _add_doc(s, cfg, ps_id, "PO", "po.pdf", [("1", 10)], po_no="PO-VND")
        _add_doc(s, cfg, ps_id, "SI", "si.pdf", [("1", 10)], po_no="PO-VND")
        s.commit()

    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "quarantined", res
    assert res["reason"] == "non_positive_quantity", res
    with Session(eng) as s:
        ps = s.get(POSet, ps_id)
        assert ps.merged_output_path is None


# ---------------------------------------------------------------------------
# the fixes must not have broken the behaviour they sit next to
# ---------------------------------------------------------------------------


def test_a_quarantine_reason_refreshes_when_a_new_code_exists(tmp_path):
    """Given a quarantined set, when the operator presses Redo matching and
    the set quarantines again for a DIFFERENT reason, then the new reason is
    stored.

    The rule is "a stored reason is never overwritten by a sweep", not "a
    stored reason is frozen". A verdict that has a code of its own always
    writes its own text.
    """
    from app.services.reconciliation import REASON_TEXT, reconcile_po_set

    cfg = _cfg(tmp_path, "refresh.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps_id = _new_set(s, "PO-REFRESH")
        _add_doc(s, cfg, ps_id, "PO", "po.pdf", [("1", 100)], po_no="PO-REFRESH")
        _add_doc(s, cfg, ps_id, "DN", "dn.pdf", [("2", 100)], po_no="PO-REFRESH")
        _add_doc(s, cfg, ps_id, "SI", "si.pdf", [("1", 100)], po_no="PO-REFRESH")
        s.commit()

    first = reconcile_po_set(ps_id, cfg)
    assert first["reason"] == "unmatched_vendor_line", first

    # the PO line is now unreadable -> a different, decisive verdict
    with Session(eng) as s:
        li = s.query(LineItem).filter_by(line_item_no="1").first()
        li.quantity = 0
        s.commit()
        ps = s.get(POSet, ps_id)
        ps.status = POSetStatus.pending  # what the operator's Redo does
        s.commit()

    second = reconcile_po_set(ps_id, cfg)
    assert second["reason"] == "non_positive_quantity", second
    with Session(eng) as s:
        assert s.get(POSet, ps_id).reconcile_reason == REASON_TEXT["non_positive_quantity"]


def test_delete_quarantined_survives_a_missing_input_file(tmp_path):
    """Given the set's file is no longer in the input folder, when the
    operator deletes the set, then the delete still succeeds and is audited."""
    from app.services.quarantine import delete_quarantined

    cfg = _cfg(tmp_path, "missing_input.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps_id = _new_set(s, "PO-NOFILE")
        _add_doc(s, cfg, ps_id, "PO", "po.pdf", [("1", 100)], po_no="PO-NOFILE")
        s.commit()
        ps = s.get(POSet, ps_id)
        ps.status = POSetStatus.quarantined
        s.commit()

    audit = delete_quarantined(
        ps_id, cfg, justification="operator cleared this quarantined set for good"
    )
    assert audit.action == AuditAction.quarantine_delete
    with Session(eng) as s:
        archived = s.get(POSet, ps_id)
        assert archived is not None and archived.archived_at is not None


def test_the_reclaim_leaves_finished_documents_alone(tmp_path):
    """Given documents that were read (or definitively failed), when the
    re-claim runs, then nothing is touched."""
    from app.services.extraction import reclaim_stale_processing

    cfg = _cfg(tmp_path, "finished.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    old = datetime.now(UTC) - timedelta(hours=2)
    with Session(eng) as s:
        for name, status in (
            ("valid", ExtractionStatus.valid),
            ("failed", ExtractionStatus.failed),
            ("pending", ExtractionStatus.pending),
        ):
            s.add(
                Document(
                    sha256_hash=name,
                    original_filename=f"{name}.pdf",
                    stored_path=str(Path(cfg.paths.stored_documents_folder) / f"{name}.pdf"),
                    doc_type=DocType.UNKNOWN,
                    extraction_status=status,
                    updated_at=old,
                )
            )
        s.commit()

    assert reclaim_stale_processing(cfg) == 0
    with Session(eng) as s:
        for name, status in (
            ("valid", ExtractionStatus.valid),
            ("failed", ExtractionStatus.failed),
            ("pending", ExtractionStatus.pending),
        ):
            assert s.query(Document).filter_by(sha256_hash=name).one().extraction_status == status


def test_an_unambiguous_document_is_still_attached_by_the_resolution_sweep(tmp_path):
    """Given a document with ONE PO number, when the resolution sweep runs,
    then it is still attached — by the content, not by its name.

    The content-based strategy here is the delivery-note number printed on
    the document: it matches a document that is already in an open set.
    """
    from app.services.grouping import resolve_unattached_documents

    cfg = _cfg(tmp_path, "unambiguous.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps_id = _new_set(s, "PO-UNAMB")
        _add_doc(s, cfg, ps_id, "PO", "SIV-DTS-25-477-PO.pdf", [("1", 100)], po_no="PO-UNAMB")
        # a delivery note whose own DN number is printed on it, and which
        # names the PO set it belongs to
        sibling = _add_doc(
            s,
            cfg,
            None,
            "DN",
            "SIV-DTS-25-477-DN.pdf",
            [("1", 100)],
            dn_no="GDN-477",
        )
        # a document already in a set that carries the same DN number
        _add_doc(
            s,
            cfg,
            ps_id,
            "DN",
            "another-dn.pdf",
            [("2", 50)],
            dn_no="GDN-477",
        )
        s.commit()
        sibling_id = sibling.id

    resolve_unattached_documents(cfg)

    with Session(eng) as s:
        assert s.get(Document, sibling_id).po_set_id is not None


def test_a_filename_is_never_evidence(tmp_path):
    """Given a document whose NAME matches a document in a PO Set, when the
    resolution sweep runs, then it is NOT attached.

    A filename is not evidence: the same bytes can be renamed, and a batch
    export can give two unrelated documents the same prefix. Only what is
    printed on the document may place it.
    """
    from app.services.grouping import resolve_unattached_documents

    cfg = _cfg(tmp_path, "filename.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps_id = _new_set(s, "PO-NAME")
        _add_doc(s, cfg, ps_id, "PO", "SIV-DTS-25-477-PO.pdf", [("1", 100)], po_no="PO-NAME")
        # same batch name, but nothing printed on it links it to that set
        lookalike = _add_doc(s, cfg, None, "DN", "SIV-DTS-25-477-DN.pdf", [("1", 100)], dn_no=None)
        s.commit()
        lookalike_id = lookalike.id

    resolve_unattached_documents(cfg)

    with Session(eng) as s:
        assert s.get(Document, lookalike_id).po_set_id is None, (
            "a document was placed on the strength of its filename"
        )


def test_a_document_with_no_po_reference_is_held_not_quarantined(tmp_path):
    """Given a document that was read successfully but carries no PO
    reference, when the flow groups it, then it stays valid and unplaced.

    It is not broken: marking it failed would hide it from the content-
    based sweeps (which only place valid documents) and from the operator.
    It waits, visible, for the content to place it or for a human to name
    its PO.
    """
    from app.services.grouping import group_document

    cfg = _cfg(tmp_path, "held.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        d = Document(
            sha256_hash="held-dn",
            original_filename="dn.pdf",
            stored_path=str(Path(cfg.paths.stored_documents_folder) / "dn.pdf"),
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
        )
        s.add(d)
        s.commit()
        d_id = d.id

    with Session(eng) as s:
        d = s.get(Document, d_id)
        ps_id, problem = group_document(d, cfg)

    assert ps_id is None
    assert problem == "missing_po_number"
    with Session(eng) as s:
        d = s.get(Document, d_id)
        assert d.extraction_status == ExtractionStatus.valid, "a readable document is not broken"
        assert d.po_set_id is None


def test_an_unplaced_document_is_visible_in_the_holding_area(client):
    """Given a document that is valid and in no PO Set, when the operator
    opens the holding area, then it is there.

    A document that waits for a human the human cannot see waits forever.
    """
    c, cfg = client
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        _add_doc(
            s,
            cfg,
            None,
            "DN",
            "unplaced.pdf",
            [("1", 100)],
            dn_no=None,
        )
        s.commit()

    resp = c.get("/unclassified")
    assert resp.status_code == 200, resp.text
    assert "unplaced.pdf" in resp.text, "the unplaced document is invisible"
    assert "waiting to be placed" in resp.text, resp.text


def test_an_ambiguous_document_is_visible_in_the_holding_area(client):
    """Given a document that shows more than one PO number, when the operator
    opens the holding area, then the document is there.

    Both attach sweeps refuse it on purpose, so nothing will ever place it.
    A document that waits for a human who cannot see it waits forever.
    """
    c, cfg = client
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        _add_doc(
            s,
            cfg,
            None,
            "DN",
            "multi-po.pdf",
            [("1", 100)],
            po_reference_ambiguous=True,
        )
        s.commit()

    resp = c.get("/unclassified")
    assert resp.status_code == 200, resp.text
    assert "multi-po.pdf" in resp.text, "the ambiguous document is invisible"
    assert "need a PO number" in resp.text, resp.text


def test_naming_the_po_number_resolves_an_ambiguous_document(client):
    """Given a document the operator names a PO number for, when it is
    reclassified, then it is attached and the ambiguity is cleared.

    A human decision supersedes the model's uncertainty: the document stops
    being held and stops being reported as unresolved.
    """
    c, cfg = client
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = _add_doc(
            s,
            cfg,
            None,
            "DN",
            "multi-po.pdf",
            [("1", 100)],
            po_reference_ambiguous=True,
        )
        s.commit()
        doc_id = doc.id

    resp = c.post(
        f"/unclassified/{doc_id}/reclassify",
        data={"doc_type": "DN", "po_no": "4500043712"},
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302), resp.text

    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        assert doc.po_set_id is not None, "the document was not attached"
        assert doc.po_no_normalized == "4500043712"
        assert doc.po_reference_ambiguous is False, "the ambiguity was not resolved"

    # and it no longer waits in the holding area
    resp = c.get("/unclassified")
    assert "multi-po.pdf" not in resp.text, "the resolved document is still held"


def test_upload_of_a_new_file_still_creates_the_document(client):
    """Given a file the system has never seen, when the operator uploads it,
    then a document is created and attached."""
    from app.models import DocType, Document, ExtractionStatus

    c, cfg = client
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    pdf_path = Path(cfg.paths.stored_documents_folder) / "brand-new.pdf"
    _pdf(pdf_path)
    import hashlib

    sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()

    with Session(eng) as s:
        ps_id = _new_set(s, "PO-NEW")
        ps = s.get(POSet, ps_id)
        ps.has_customs_toggle = True
        s.commit()

    with open(pdf_path, "rb") as fh:
        resp = c.post(
            f"/po_sets/{ps_id}/upload",
            files={"file": ("brand-new.pdf", fh, "application/pdf")},
            data={"doc_type": "CUSTOMS"},
            follow_redirects=False,
        )

    assert resp.status_code == 302, resp.text
    with Session(eng) as s:
        doc = s.query(Document).filter_by(sha256_hash=sha).one()
        assert doc.po_set_id == ps_id
        assert doc.doc_type == DocType.CUSTOMS
        assert doc.extraction_status == ExtractionStatus.valid
        assert s.get(POSet, ps_id).customs_doc_count == 1


# ---------------------------------------------------------------------------
# a document that has been read is not read again
# ---------------------------------------------------------------------------


def test_a_document_that_was_already_read_is_not_read_again(tmp_path, monkeypatch):
    """Given a document that was read successfully, when the sync runs again,
    then the VLM is not called for it a second time.

    A file stays in the input folder until its PO Set merges, so a set that
    is still waiting for a document would be re-read on every sync: one VLM
    call per file per night, and the attempt count climbing until a
    perfectly good document was marked permanently failed.
    """
    import app.services.extraction as ext

    cfg = _cfg(tmp_path, "reread.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="already-read",
            original_filename="read.pdf",
            stored_path=str(Path(cfg.paths.stored_documents_folder) / "read.pdf"),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            extraction_attempt_count=1,
        )
        s.add(doc)
        s.commit()
        doc_id = doc.id

    def _boom(*a, **kw):
        raise AssertionError("the VLM must not be called for a document that was already read")

    monkeypatch.setattr(ext, "_call_vlm", _boom)

    ext.extract_document(doc_id, cfg)

    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        assert doc.extraction_status == ExtractionStatus.valid
        assert doc.extraction_attempt_count == 1, "the attempt count was spent for nothing"


def test_a_failed_document_is_read_again(tmp_path, monkeypatch):
    """Given a document whose extraction failed, when the sync runs again,
    then it is read again — that is the recovery path."""
    import app.services.extraction as ext

    cfg = _cfg(tmp_path, "refail.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    stored = Path(cfg.paths.stored_documents_folder) / "failed.pdf"
    _pdf(stored)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="failed-once",
            original_filename="failed.pdf",
            stored_path=str(stored),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.failed,
            extraction_attempt_count=1,
        )
        s.add(doc)
        s.commit()
        doc_id = doc.id

    calls = []

    def _fake(path, dtype, cfg):
        calls.append(path)
        return {
            "document_type": "PO",
            "po_no_raw": "PO-RETRY",
            "line_items": [
                {"line_item_no": "1", "description": "ITEM", "quantity": "1", "unit_price": "1.00"}
            ],
        }

    monkeypatch.setattr(ext, "_call_vlm", _fake)

    ext.extract_document(doc_id, cfg)

    assert len(calls) == 1, "a failed document must be read again"
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        assert doc.extraction_status == ExtractionStatus.valid
        assert doc.extraction_attempt_count == 2


# ---------------------------------------------------------------------------
# Q1 — a document with no PO reference must not vanish
# ---------------------------------------------------------------------------


def test_a_po_without_a_number_is_reported_not_dropped(tmp_path):
    """Given a purchase order whose own number could not be read, when it is
    grouped, then the problem is reported.

    A PO is just another document like a DN or an SI — it needs a PO
    reference like they do. Without this the document was stored, attached
    to nothing, in no set and in no view: it simply disappeared.
    """
    from app.services.grouping import group_document

    cfg = _cfg(tmp_path, "no-number.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        d = Document(
            sha256_hash="po-no-number",
            original_filename="po.pdf",
            stored_path=str(Path(cfg.paths.stored_documents_folder) / "po.pdf"),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
        )
        s.add(d)
        s.commit()
        d_id = d.id

    with Session(eng) as s:
        d = s.get(Document, d_id)
        ps_id, problem = group_document(d, cfg)

    assert ps_id is None
    assert problem == "missing_po_number"


def test_an_ambiguous_po_does_not_mint_a_set(tmp_path):
    """Given a purchase order that shows more than one PO number, when it is
    grouped, then it mints no set and waits for a human.

    A PO is the only type that mints a set. Minting one under whichever
    number was named first would strand the other PO's invoices.
    """
    from app.services.grouping import group_document

    cfg = _cfg(tmp_path, "ambiguous-po.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        d = Document(
            sha256_hash="ambiguous-po",
            original_filename="po.pdf",
            stored_path=str(Path(cfg.paths.stored_documents_folder) / "po.pdf"),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_no_raw="12345",
            po_no_normalized="12345",
            po_reference_ambiguous=True,
        )
        s.add(d)
        s.commit()
        d_id = d.id

    with Session(eng) as s:
        d = s.get(Document, d_id)
        ps_id, problem = group_document(d, cfg)

    assert ps_id is None
    assert problem == "ambiguous_po_number"
    with Session(eng) as s:
        assert s.query(POSet).count() == 0, "an ambiguous PO must not mint a set"


def test_a_normal_po_still_mints_its_set(tmp_path):
    """The ordinary case is untouched: a PO with a number mints its set."""
    from app.services.grouping import group_document

    cfg = _cfg(tmp_path, "normal-po.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        d = Document(
            sha256_hash="normal-po",
            original_filename="po.pdf",
            stored_path=str(Path(cfg.paths.stored_documents_folder) / "po.pdf"),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_no_raw="4500043712",
            po_no_normalized="4500043712",
        )
        s.add(d)
        s.commit()
        d_id = d.id

    with Session(eng) as s:
        d = s.get(Document, d_id)
        ps_id, problem = group_document(d, cfg)

    assert problem is None
    assert ps_id is not None
    with Session(eng) as s:
        assert s.get(POSet, ps_id).po_no_normalized == "4500043712"


# ---------------------------------------------------------------------------
# Q6 — the customs gate cannot be turned on after the merge
# ---------------------------------------------------------------------------


def test_the_customs_toggle_is_allowed_on_a_merged_set(client):
    """Given a PO Set whose packet has been written, when the operator
    toggles the customs gate, then the toggle is accepted and the set stays
    merged.

    The requirement can be turned on after delivery. The packet is rebuilt
    once both documents are attached; the status never regresses, so a
    delivered packet is never shown as "Awaiting Customs".
    """
    c, cfg = client
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps_id = _new_set(s, "PO-MERGED")
        ps = s.get(POSet, ps_id)
        ps.status = POSetStatus.merged
        ps.merged_at = datetime.now(UTC)
        ps.merged_output_path = str(Path(cfg.paths.output_folder) / "INV-X.pdf")
        s.commit()

    resp = c.post(f"/po_sets/{ps_id}/toggle_customs", follow_redirects=False)

    assert resp.status_code == 200, resp.text
    assert resp.json()["has_customs_toggle"] is True
    with Session(eng) as s:
        ps = s.get(POSet, ps_id)
        assert ps.status == POSetStatus.merged, "a delivered packet must stay merged"
        assert ps.has_customs_toggle is True


# ---------------------------------------------------------------------------
# Q7 — a nightly sync must not pay for documents it has already read
# ---------------------------------------------------------------------------


def test_a_read_document_is_not_scheduled_for_extraction(tmp_path, monkeypatch):
    """Given a file that has already been read, when the sync runs, then no
    extraction task is scheduled for it.

    A Prefect task on the `process` work pool launches a process per call.
    Scheduling a task for a document that is already read costs a process
    launch to be told "nothing to do" — about eleven seconds per file on the
    live vendor batch, fifty minutes of a nightly sync spent launching
    processes.
    """
    import app.flows.sync as flow_mod

    cfg = _cfg(tmp_path, "notscheduled.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    # a file in the input folder whose content is already stored and read
    input_dir = Path(cfg.paths.input_folder)
    input_dir.mkdir(parents=True, exist_ok=True)
    pdf = input_dir / "read.pdf"
    w = PdfWriter()
    w.add_blank_page(width=100, height=100)
    w.write(str(pdf))
    data = pdf.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    stored = Path(cfg.paths.stored_documents_folder) / f"{digest}.pdf"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(data)

    with Session(eng) as s:
        s.add(
            Document(
                sha256_hash=digest,
                original_filename="read.pdf",
                stored_path=str(stored),
                doc_type=DocType.PO,
                extraction_status=ExtractionStatus.valid,
                extraction_attempt_count=1,
            )
        )
        s.commit()

    scheduled: list[int] = []

    class _Task:
        def __call__(self, doc_id, cfg_path=None):
            scheduled.append(doc_id)
            raise AssertionError("a document that was read already must not be scheduled")

    monkeypatch.setattr(flow_mod, "_extract_task_for", lambda cfg: _Task())
    monkeypatch.setattr(flow_mod, "load_config", lambda *a, **kw: cfg)

    flow_mod._sync_flow_locked()

    assert scheduled == [], "an already-read document was scheduled for extraction"


def test_the_stability_poll_of_a_batch_is_not_serial(tmp_path):
    """Given a batch of files, when the stability poll runs, then the batch
    is polled together rather than one file after another.

    The poll is pure waiting (sample the size, sleep, sample again), so
    polling 200 files one after another spent thirteen minutes of the sync
    asleep. The files are independent; they are sampled together.
    """
    import time

    import app.flows.sync as flow_mod

    cfg = _cfg(tmp_path, "poll.db")
    cfg.ingestion.stability_poll_interval_seconds = 0.3
    cfg.ingestion.stability_poll_count = 1

    input_dir = Path(cfg.paths.input_folder)
    input_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(8):
        p = input_dir / f"f{i}.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        files.append(p)

    started = time.monotonic()
    stable_map = flow_mod._poll_stability(files, cfg)
    elapsed = time.monotonic() - started

    assert len(stable_map) == len(files)
    assert all(stable_map.values()), "every settled file must be reported stable"
    serial = 8 * 0.3
    assert elapsed < serial / 2, (
        f"the batch was polled serially ({elapsed:.2f}s for {serial:.2f}s of waiting)"
    )
