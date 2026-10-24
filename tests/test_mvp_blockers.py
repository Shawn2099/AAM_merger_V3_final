# ruff: noqa: F811
"""MVP blockers: a set is judged only on documents that were read, and only
PO/DN/SI documents form a set.

Each test pins the behaviour the product rule requires:

* a failed, unread or unclassified document never lets a set reconcile or merge;
* an empty DN or SI is caught per document, not per pool;
* UNKNOWN/COMBINED documents are never attached by any sweep.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.orm import Session
from test_business_scenarios_ui import _doc, _set, _status, cfg, client  # noqa: F401

from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, POSetStatus
from app.services.grouping import (
    attach_unattached_to_open_sets,
    group_document,
    resolve_unattached_documents,
)
from app.services.reconciliation import reconcile_po_set

LINE = [("1", "BOLT", 100)]


def _std(cfg, po: str) -> int:
    pid = _set(cfg, po)
    _doc(cfg, pid, DocType.PO, f"{po}-po.pdf", LINE, po_no=po)
    _doc(cfg, pid, DocType.DN, f"{po}-dn.pdf", LINE, po_no=po)
    _doc(cfg, pid, DocType.SI, f"{po}-si.pdf", LINE, po_no=po, si_no=f"INV-{po}")
    return pid


def _attach_unknown(cfg, pid: int, name: str, po: str, dtype: DocType = DocType.UNKNOWN) -> int:
    did = _doc(cfg, pid, dtype, name, [("1", "BOLT", 50)], po_no=po)
    with Session(get_engine(cfg)) as s:
        s.get(Document, did).po_set_id = pid
        s.commit()
    return did


def _loose(cfg, name: str, dtype: DocType, po: str, **kw) -> int:
    with Session(get_engine(cfg)) as s:
        d = Document(
            sha256_hash=hashlib.sha256(name.encode()).hexdigest(),
            original_filename=name,
            stored_path="",
            doc_type=dtype,
            extraction_status=ExtractionStatus.valid,
            po_set_id=None,
            po_no_raw=po,
            po_no_normalized=po,
            **kw,
        )
        s.add(d)
        s.commit()
        return d.id


# --- A1: per-document empty check -------------------------------------------


def test_empty_dn_beside_full_dn_quarantines(cfg):
    pid = _std(cfg, "PO-EMPTY")
    _doc(cfg, pid, DocType.DN, "dn-empty.pdf", [], po_no="PO-EMPTY")
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "quarantined"
    assert res["reason"] == "vendor_document_has_no_line_items"
    assert "dn-empty.pdf" in res["detail"]
    assert "PO-EMPTY-dn.pdf" not in res["detail"]
    assert _status(cfg, pid).merged_output_path is None


# --- unclassified documents --------------------------------------------------


@pytest.mark.parametrize("dtype", [DocType.UNKNOWN, DocType.COMBINED])
def test_attached_unclassified_document_quarantines(cfg, dtype):
    pid = _std(cfg, "PO-UNK")
    _attach_unknown(cfg, pid, "misread.pdf", "PO-UNK", dtype)
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "quarantined"
    assert res["reason"] == "unclassified_document_in_set"
    assert "misread.pdf" in res["detail"]
    assert "misread.pdf" in (_status(cfg, pid).reconcile_reason or "")
    assert _status(cfg, pid).merged_output_path is None


# --- failed / unread documents -----------------------------------------------


def test_failed_document_stale_lines_never_reconcile_or_merge(cfg):
    pid = _set(cfg, "PO-FAIL")
    _doc(cfg, pid, DocType.PO, "po.pdf", LINE, po_no="PO-FAIL")
    _doc(
        cfg, pid, DocType.DN, "dn-failed.pdf", LINE, po_no="PO-FAIL", status=ExtractionStatus.failed
    )
    _doc(cfg, pid, DocType.SI, "si.pdf", LINE, po_no="PO-FAIL", si_no="INV")
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "quarantined"
    assert res["reason"] == "document_extraction_failed"
    assert "dn-failed.pdf could not be read; use Re-Extract" in res["detail"]
    ps = _status(cfg, pid)
    assert ps.status == POSetStatus.quarantined
    assert ps.merged_output_path is None
    assert "dn-failed.pdf" in (ps.reconcile_reason or "")


@pytest.mark.parametrize("st", [ExtractionStatus.pending, ExtractionStatus.processing])
def test_unread_document_holds_the_set_pending(cfg, st):
    pid = _set(cfg, "PO-UNREAD")
    _doc(cfg, pid, DocType.PO, "po.pdf", LINE, po_no="PO-UNREAD")
    _doc(cfg, pid, DocType.DN, "dn.pdf", LINE, po_no="PO-UNREAD", status=st)
    _doc(cfg, pid, DocType.SI, "si.pdf", LINE, po_no="PO-UNREAD", si_no="INV")
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "pending"
    assert res["reason"] == "document_not_yet_read"
    assert _status(cfg, pid).merged_output_path is None


def test_customs_documents_are_exempt_from_the_read_guards(cfg):
    pid = _std(cfg, "PO-CUS")
    _doc(cfg, pid, DocType.CUSTOMS, "c.pdf", [], po_no="PO-CUS", status=ExtractionStatus.pending)
    _doc(cfg, pid, DocType.SHIPPING, "s.pdf", [], po_no="PO-CUS", status=ExtractionStatus.failed)
    assert reconcile_po_set(pid, cfg)["status"] == "merged"


def test_redo_extract_failure_does_not_merge_stale_lines(cfg, monkeypatch):
    import app.api.routes.po_sets as po_routes
    import app.services.extraction as extraction

    pid = _std(cfg, "PO-REDO")
    monkeypatch.setattr(po_routes, "load_config", lambda *a, **k: cfg)

    def boom(doc_id, c):
        with Session(get_engine(c)) as s:
            s.get(Document, doc_id).extraction_status = ExtractionStatus.failed
            s.commit()
            return s.get(Document, doc_id)

    monkeypatch.setattr(extraction, "extract_document", boom)
    out = po_routes.redo_extract(pid)
    assert out["reconciliation"]["status"] == "quarantined"
    assert out["reconciliation"]["reason"] == "document_extraction_failed"
    assert _status(cfg, pid).merged_output_path is None


def test_redo_extract_that_raises_leaves_set_pending_not_merged(cfg, monkeypatch):
    import app.api.routes.po_sets as po_routes
    import app.services.extraction as extraction

    pid = _std(cfg, "PO-REDO2")
    monkeypatch.setattr(po_routes, "load_config", lambda *a, **k: cfg)

    def boom(doc_id, c):
        raise RuntimeError("vlm down")

    monkeypatch.setattr(extraction, "extract_document", boom)
    monkeypatch.setattr("app.services.retry.time.sleep", lambda *_: None, raising=False)
    out = po_routes.redo_extract(pid)
    assert out["reconciliation"]["status"] == "pending"
    assert out["reconciliation"]["reason"] == "document_not_yet_read"
    assert _status(cfg, pid).merged_output_path is None


# --- guard order --------------------------------------------------------------


def test_validity_guards_run_before_missing_side_checks(cfg):
    # No SI at all AND a failed DN: the failed document wins, not "missing SI".
    pid = _set(cfg, "PO-ORDER")
    _doc(cfg, pid, DocType.PO, "po.pdf", LINE, po_no="PO-ORDER")
    _doc(cfg, pid, DocType.DN, "dn.pdf", LINE, po_no="PO-ORDER", status=ExtractionStatus.failed)
    assert reconcile_po_set(pid, cfg)["reason"] == "document_extraction_failed"

    # No DN/SI and an attached UNKNOWN: unclassified wins over missing DN.
    pid2 = _set(cfg, "PO-ORDER2")
    _doc(cfg, pid2, DocType.PO, "po.pdf", LINE, po_no="PO-ORDER2")
    _attach_unknown(cfg, pid2, "mystery.pdf", "PO-ORDER2")
    assert reconcile_po_set(pid2, cfg)["reason"] == "unclassified_document_in_set"

    # Unread beats missing side, and failed beats unread.
    pid3 = _set(cfg, "PO-ORDER3")
    _doc(cfg, pid3, DocType.PO, "po.pdf", LINE, po_no="PO-ORDER3", status=ExtractionStatus.pending)
    assert reconcile_po_set(pid3, cfg)["reason"] == "document_not_yet_read"
    _doc(cfg, pid3, DocType.DN, "dn.pdf", LINE, po_no="PO-ORDER3", status=ExtractionStatus.failed)
    assert reconcile_po_set(pid3, cfg)["reason"] == "document_extraction_failed"


def test_clean_set_still_merges(cfg):
    """The guards do not over-block."""
    pid = _std(cfg, "PO-CLEAN")
    assert reconcile_po_set(pid, cfg)["status"] == "merged"


# --- grouping: UNKNOWN / COMBINED never attach --------------------------------


@pytest.mark.parametrize("dtype", [DocType.UNKNOWN, DocType.COMBINED])
def test_unclassified_with_po_no_is_not_attached_by_any_sweep(cfg, dtype):
    pid = _std(cfg, "PO-SWEEP")
    # An attached DN supplies the dn_no that the resolve sweep matches on.
    with Session(get_engine(cfg)) as s:
        for d in s.query(Document).filter(Document.po_set_id == pid):
            if d.doc_type == DocType.DN:
                d.dn_no = "DN-77"
        s.commit()
    did = _loose(cfg, "loose.pdf", dtype, "PO-SWEEP", dn_no="DN-77")

    with Session(get_engine(cfg)) as s:
        doc = s.get(Document, did)
        assert group_document(doc, cfg) == (None, None)

    assert attach_unattached_to_open_sets(cfg) == set()
    assert resolve_unattached_documents(cfg) == set()

    with Session(get_engine(cfg)) as s:
        doc = s.get(Document, did)
        assert doc.po_set_id is None
        assert doc.po_no_normalized == "PO-SWEEP"  # keeps its reference


def test_sweeps_still_attach_a_dn_to_an_open_set(cfg):
    """Control: the allowlist does not stop a real DN."""
    pid = _set(cfg, "PO-CTRL")
    _doc(cfg, pid, DocType.PO, "po.pdf", LINE, po_no="PO-CTRL")
    did = _loose(cfg, "late-dn.pdf", DocType.DN, "PO-CTRL")
    assert attach_unattached_to_open_sets(cfg) == {pid}
    with Session(get_engine(cfg)) as s:
        assert s.get(Document, did).po_set_id == pid


def test_anchor_types_stay_consistent_with_attachable():
    from app.flows.sync import _ANCHOR_TYPES
    from app.services.grouping import _ATTACHABLE, ANCHOR_TYPES

    assert set(_ANCHOR_TYPES) == set(ANCHOR_TYPES) == {"PO"}
    assert set(_ANCHOR_TYPES) <= _ATTACHABLE == {"PO", "DN", "SI"}


# --- operator-facing text per new reason code ---------------------------------


def test_failed_document_reason_is_shown_to_the_operator(cfg, client):
    pid = _set(cfg, "PO-PG1")
    _doc(cfg, pid, DocType.PO, "po.pdf", LINE, po_no="PO-PG1")
    _doc(cfg, pid, DocType.DN, "bad-scan.pdf", LINE, po_no="PO-PG1", status=ExtractionStatus.failed)
    _doc(cfg, pid, DocType.SI, "si.pdf", LINE, po_no="PO-PG1", si_no="I")
    assert reconcile_po_set(pid, cfg)["status"] == "quarantined"
    assert "bad-scan.pdf could not be read" in client.get("/quarantine").text
    assert "bad-scan.pdf could not be read" in client.get(f"/po_sets/{pid}/view").text


def test_unread_document_reason_is_shown_to_the_operator(cfg, client):
    pid = _set(cfg, "PO-PG2")
    _doc(cfg, pid, DocType.PO, "po.pdf", LINE, po_no="PO-PG2")
    _doc(cfg, pid, DocType.DN, "dn.pdf", LINE, po_no="PO-PG2", status=ExtractionStatus.pending)
    _doc(cfg, pid, DocType.SI, "si.pdf", LINE, po_no="PO-PG2", si_no="I")
    assert reconcile_po_set(pid, cfg)["status"] == "pending"
    assert "has not been read yet" in client.get(f"/po_sets/{pid}/view").text
    assert "has not been read yet" in client.get("/").text


def test_unclassified_reason_is_shown_to_the_operator(cfg, client):
    pid = _std(cfg, "PO-PG3")
    _attach_unknown(cfg, pid, "mystery-doc.pdf", "PO-PG3")
    assert reconcile_po_set(pid, cfg)["status"] == "quarantined"
    assert "mystery-doc.pdf" in client.get("/quarantine").text
    assert "Unclassified document" in client.get(f"/po_sets/{pid}/view").text


def test_empty_dn_reason_names_only_the_empty_file(cfg, client):
    pid = _std(cfg, "PO-PG4")
    _doc(cfg, pid, DocType.DN, "empty-dn.pdf", [], po_no="PO-PG4")
    assert reconcile_po_set(pid, cfg)["status"] == "quarantined"
    page = client.get("/quarantine").text
    assert "empty-dn.pdf" in page or "contains no line items" in page


# --- B: a failed customs append is persisted and retried ----------------------


def _merged_set_awaiting_customs(cfg, po: str) -> int:
    """A merged set with the requirement off and both customs documents attached."""
    pid = _std(cfg, po)
    assert reconcile_po_set(pid, cfg)["status"] == "merged"
    _doc(cfg, pid, DocType.CUSTOMS, f"{po}-customs.pdf", [], po_no=po, seed=f"{po}-c")
    _doc(cfg, pid, DocType.SHIPPING, f"{po}-ship.pdf", [], po_no=po, seed=f"{po}-s")
    return pid


def _break_rebuild(monkeypatch):
    import app.services.merge as merge_service

    real = merge_service._write_merged

    def boom(*a, **k):
        raise OSError("disk is full")

    monkeypatch.setattr(merge_service, "_write_merged", boom)
    return lambda: monkeypatch.setattr(merge_service, "_write_merged", real)


def test_customs_append_failure_is_persisted_and_retried_by_sync(cfg, client, monkeypatch):
    from pathlib import Path

    from test_business_scenarios_ui import _page_count

    from app.flows.sync import _recovery_flow_locked

    pid = _merged_set_awaiting_customs(cfg, "PO-RETRY")
    packet = Path(_status(cfg, pid).merged_output_path)
    assert _page_count(packet) == 3

    restore = _break_rebuild(monkeypatch)
    r = client.post(f"/po_sets/{pid}/toggle_customs")
    assert r.status_code == 200, r.text
    ps = _status(cfg, pid)
    assert ps.has_customs_toggle is True
    assert ps.customs_append_pending is True, "the failure must be persisted"
    assert _page_count(packet) == 3

    # A sync while the fault persists keeps the debt and does not lose it.
    _recovery_flow_locked()
    assert _status(cfg, pid).customs_append_pending is True

    restore()
    _recovery_flow_locked()
    ps = _status(cfg, pid)
    assert ps.customs_append_pending is False
    assert ps.status == POSetStatus.merged
    assert _page_count(packet) == 5

    from app.models import AuditAction, AuditLog

    with Session(get_engine(cfg)) as s:
        assert any(a.action == AuditAction.customs_append for a in s.query(AuditLog).all())


def test_toggle_reports_append_failure(cfg, client, monkeypatch):
    pid = _merged_set_awaiting_customs(cfg, "PO-REPORT")
    _break_rebuild(monkeypatch)
    r = client.post(f"/po_sets/{pid}/toggle_customs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["packet_rebuilt"] is False
    assert body["append_pending"] is True
    assert "disk is full" in body["append_error"]

    # Switching the requirement off drops the debt.
    r = client.post(f"/po_sets/{pid}/toggle_customs")
    assert r.status_code == 200
    assert r.json()["append_pending"] is False
    assert _status(cfg, pid).customs_append_pending is False


def test_badge_shows_when_append_pending(cfg, client):
    pid = _merged_set_awaiting_customs(cfg, "PO-BADGE")
    with Session(get_engine(cfg)) as s:
        ps = s.get(type(_status(cfg, pid)), pid)
        ps.has_customs_toggle = True
        ps.customs_doc_count = 2
        ps.customs_append_pending = False
        s.commit()
    assert "Customs append pending" not in client.get("/dashboard").text
    assert "customs not yet in packet" not in client.get(f"/po_sets/{pid}/view").text

    with Session(get_engine(cfg)) as s:
        s.get(type(_status(cfg, pid)), pid).customs_append_pending = True
        s.commit()
    assert "Customs append pending" in client.get("/dashboard").text
    assert "customs not yet in packet" in client.get(f"/po_sets/{pid}/view").text


def test_reclassify_with_busy_lock_still_records_pending_append(cfg, client):

    from test_business_scenarios_ui import _pdf

    from app.models import POSet
    from app.services.locking import acquire_lock

    pid = _std(cfg, "5005")
    assert reconcile_po_set(pid, cfg)["status"] == "merged"
    _doc(cfg, pid, DocType.CUSTOMS, "busy-c.pdf", [], po_no="5005", seed="busy-c")
    assert client.post(f"/po_sets/{pid}/toggle_customs").status_code == 200
    assert _status(cfg, pid).customs_append_pending is False  # only one of two attached

    sha, stored = _pdf(cfg, "busy-ship")
    with Session(get_engine(cfg)) as s:
        d = Document(
            sha256_hash=sha,
            original_filename="busy-ship.pdf",
            stored_path=str(stored),
            doc_type=DocType.SHIPPING,
            extraction_status=ExtractionStatus.valid,
            po_set_id=None,
        )
        s.add(d)
        s.commit()
        doc_id = d.id
    with Session(get_engine(cfg)) as s:
        assert acquire_lock(s.get(POSet, pid), "force_merge", s, cfg)

    r = client.post(
        f"/unclassified/{doc_id}/reclassify",
        data={"doc_type": "SHIPPING", "po_no": "PO-5005"},
        follow_redirects=False,
    )
    assert r.status_code in (200, 302), r.text
    assert _status(cfg, pid).customs_append_pending is True
