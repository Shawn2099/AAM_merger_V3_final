"""Exhaustive negative/edge coverage for the Batch 1 surface.

This file deliberately probes what the happy-path tests do not: guards, bad
input, missing files, locked/archived/merged sets, duplicate uploads, path
traversal, XSS, 404 modes, labels, IST and data integrity.  A failure here is a
candidate product bug, not a known limitation, unless marked otherwise.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.templating import ist
from app.core.database import get_engine
from app.models import AuditAction, AuditLog, DocType, Document, POSet, POSetStatus
from app.services.quarantine import set_aside_document
from app.services.reconciliation import reconcile_po_set
from test_business_scenarios_ui import _doc, _set, cfg, client  # noqa: F401
from test_holding_set_aside import _held_valid_si


def _pdf_bytes() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _mismatched(cfg, po: str) -> int:
    pid = _set(cfg, po)
    _doc(cfg, pid, DocType.PO, f"{po}-po.pdf", [("1", "BOLT", 10)], po_no=po)
    _doc(cfg, pid, DocType.DN, f"{po}-dn.pdf", [("1", "BOLT", 6)], po_no=po)
    _doc(cfg, pid, DocType.SI, f"{po}-si.pdf", [("1", "BOLT", 10)], po_no=po, si_no=f"INV-{po}")
    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "mismatched", result
    return pid


def _set_ps(cfg, pid: int, **values) -> None:
    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, pid)
        assert ps is not None
        for key, value in values.items():
            setattr(ps, key, value)
        s.commit()


def _add_supporting(cfg, pid: int, po: str, kind: DocType, name: str) -> None:
    _doc(cfg, pid, kind, name, [], po_no=po)
    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, pid)
        assert ps is not None
        ps.has_customs_toggle = True
        ps.customs_doc_count = len(
            [
                d
                for d in ps.documents
                if d.archived_at is None and d.doc_type in (DocType.CUSTOMS, DocType.SHIPPING)
            ]
        )
        s.commit()


def _row(html: str, set_id: int) -> str:
    m = re.search(rf'<tr[^>]*data-set-id="{set_id}".*?</tr>', html, re.S)
    return m.group(0) if m else ""


def _latest_audit(cfg, action: AuditAction) -> AuditLog:
    with Session(get_engine(cfg)) as s:
        rows = s.scalars(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id.desc())
        ).all()
        assert rows
        return rows[0]


# ---------------------------------------------------------------------------
# Removal guards
# ---------------------------------------------------------------------------


def test_removal_unknown_set_is_404(client):
    r = client.post("/po_sets/999999/remove_to_quarantine", data={"note": "x" * 20})
    assert r.status_code == 404


def test_removal_archived_set_is_409(cfg, client):
    pid = _mismatched(cfg, "PO-ARCH")
    _set_ps(cfg, pid, archived_at=datetime.now(UTC), archived_by="test")
    r = client.post(f"/po_sets/{pid}/remove_to_quarantine", data={"note": "x" * 20})
    assert r.status_code == 409


def test_removal_merged_set_succeeds(cfg, client):
    pid = _mismatched(cfg, "PO-MERGED")
    _set_ps(cfg, pid, status=POSetStatus.merged, merged_at=datetime.now(UTC))
    r = client.post(f"/po_sets/{pid}/remove_to_quarantine", data={"note": "x" * 20})
    assert r.status_code == 200


def test_removal_locked_set_is_409(cfg, client):
    pid = _mismatched(cfg, "PO-LOCK")
    _set_ps(cfg, pid, locked_by_action="force_merge", locked_at=datetime.now(UTC))
    r = client.post(f"/po_sets/{pid}/remove_to_quarantine", data={"note": "x" * 20})
    assert r.status_code == 409


def test_removal_while_sync_runs_is_409(cfg, client, monkeypatch):
    pid = _mismatched(cfg, "PO-SYNC")
    monkeypatch.setattr("app.services.sync_lock._is_sync_running", lambda *a, **k: True)
    r = client.post(f"/po_sets/{pid}/remove_to_quarantine", data={"note": "x" * 20})
    assert r.status_code == 409


def test_removal_empty_set_succeeds_with_an_export(cfg, client):
    pid = _set(cfg, "PO-EMPTY")
    r = client.post(
        f"/po_sets/{pid}/remove_to_quarantine",
        data={"note": "empty set cleanup"},
        headers={"Accept": "text/html"},
    )
    assert r.status_code == 200
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, pid) is None
    folders = list((Path(cfg.paths.quarantine_folder) / "_removed_sets").glob("PO-EMPTY_*"))
    assert len(folders) == 1
    for name in ("po_set.json", "documents.json", "line_items.json", "audit_log.json", "manifest.json"):
        assert (folders[0] / name).is_file(), name
    assert "0 document(s)" in r.text


def test_removal_many_documents_copies_every_one(cfg, client):
    pid = _set(cfg, "PO-MANY")
    for i in range(25):
        _doc(cfg, pid, DocType.PO, f"PO-MANY-{i}.pdf", [("1", f"Item {i}", 1)], po_no="PO-MANY")
    r = client.post(
        f"/po_sets/{pid}/remove_to_quarantine",
        data={"note": "many docs"},
        headers={"Accept": "text/html"},
    )
    assert r.status_code == 200
    folders = list((Path(cfg.paths.quarantine_folder) / "_removed_sets").glob("PO-MANY_*"))
    assert len(folders) == 1
    assert len(list((folders[0] / "files").glob("*.pdf"))) == 25


def test_removal_note_is_truncated_and_escaped(cfg, client):
    pid = _mismatched(cfg, "PO-NOTE")
    note = "<script>alert(1)</script>" + "x" * 250
    r = client.post(
        f"/po_sets/{pid}/remove_to_quarantine",
        data={"note": note},
        headers={"Accept": "text/html"},
    )
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    audit = _latest_audit(cfg, AuditAction.quarantine_delete)
    detail = json.loads(audit.detail or "{}")
    assert len(detail["note"]) == 200
    assert detail["note"].startswith("<script>")


def test_removal_path_traversal_filename_never_escapes_the_files_folder(cfg, client):
    pid = _set(cfg, "PO-TRAV")
    _doc(cfg, pid, DocType.PO, "..\\..\\evil.pdf", [("1", "Item", 1)], po_no="PO-TRAV")
    r = client.post(
        f"/po_sets/{pid}/remove_to_quarantine",
        data={"note": "traversal"},
        headers={"Accept": "text/html"},
    )
    assert r.status_code == 200
    hits = list(Path(cfg.paths.quarantine_folder).rglob("evil.pdf"))
    assert hits, "the copy was not written at all"
    for hit in hits:
        assert "files" in hit.parts, f"escaped the files folder: {hit}"


def test_removal_twice_is_404_the_second_time(cfg, client):
    pid = _mismatched(cfg, "PO-TWICE")
    first = client.post(f"/po_sets/{pid}/remove_to_quarantine", data={"note": "first"})
    assert first.status_code in (200, 302)
    second = client.post(f"/po_sets/{pid}/remove_to_quarantine", data={"note": "second"})
    assert second.status_code == 404


def test_removal_keeps_stored_copies_and_moves_input_copies(cfg, client):
    pid = _mismatched(cfg, "PO-COPIES")
    with Session(get_engine(cfg)) as s:
        docs = s.query(Document).filter(Document.po_set_id == pid).all()
        stored = [Path(d.stored_path) for d in docs]
        data = stored[0].read_bytes()
        # The shared fixture gives documents synthetic hashes (hash of the seed),
        # not the PDF bytes. The removal matches input copies by the document's
        # stored hash, so make this one a real, consistent hash for the test.
        docs[0].sha256_hash = hashlib.sha256(data).hexdigest()
        s.commit()
        input_copy = Path(cfg.paths.input_folder) / "input-copy.pdf"
        input_copy.write_bytes(data)

    r = client.post(
        f"/po_sets/{pid}/remove_to_quarantine",
        data={"note": "copies"},
        headers={"Accept": "text/html"},
    )
    assert r.status_code == 200
    assert all(p.is_file() for p in stored), "a stored copy was deleted"
    assert not input_copy.exists(), "the input copy was left behind"
    folders = list((Path(cfg.paths.quarantine_folder) / "_removed_sets").glob("PO-COPIES_*"))
    assert (folders[0] / "input" / "input-copy.pdf").is_file()


# ---------------------------------------------------------------------------
# Customs toggle guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [DocType.CUSTOMS, DocType.SHIPPING])
def test_toggle_off_is_refused_with_one_supporting_file(cfg, client, kind):
    pid = _set(cfg, f"PO-TOG-{kind.value}")
    _add_supporting(cfg, pid, f"PO-TOG-{kind.value}", kind, f"{kind.value}.pdf")
    r = client.post(f"/po_sets/{pid}/toggle_customs")
    assert r.status_code == 409
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, pid).has_customs_toggle is True


def test_toggle_off_is_allowed_after_the_files_are_detached(cfg, client):
    pid = _set(cfg, "PO-TOG-DETACH")
    _add_supporting(cfg, pid, "PO-TOG-DETACH", DocType.CUSTOMS, "CUSTOMS.pdf")
    with Session(get_engine(cfg)) as s:
        for d in s.query(Document).filter(Document.po_set_id == pid).all():
            d.po_set_id = None
            d.archived_at = datetime.now(UTC)
        s.commit()
    r = client.post(f"/po_sets/{pid}/toggle_customs")
    assert r.status_code == 200
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, pid).has_customs_toggle is False


def test_toggle_on_is_allowed_and_audited(cfg, client):
    pid = _set(cfg, "PO-TOG-AUDIT")
    r = client.post(f"/po_sets/{pid}/toggle_customs")
    assert r.status_code == 200
    audit = _latest_audit(cfg, AuditAction.manual_status_change)
    assert "Customs check switched ON" in (audit.detail or "")


def test_toggle_off_on_a_merged_set_is_allowed(cfg, client):
    pid = _mismatched(cfg, "PO-TOG-MERGED")
    _add_supporting(cfg, pid, "PO-TOG-MERGED", DocType.CUSTOMS, "CUSTOMS.pdf")
    _set_ps(cfg, pid, status=POSetStatus.merged, merged_at=datetime.now(UTC))
    r = client.post(f"/po_sets/{pid}/toggle_customs")
    assert r.status_code == 200
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, pid).has_customs_toggle is False


def test_toggle_archived_is_409(cfg, client):
    pid = _set(cfg, "PO-TOG-ARCH")
    _set_ps(cfg, pid, archived_at=datetime.now(UTC))
    r = client.post(f"/po_sets/{pid}/toggle_customs")
    assert r.status_code == 409


def test_toggle_locked_is_409(cfg, client):
    pid = _set(cfg, "PO-TOG-LOCK")
    _set_ps(cfg, pid, locked_by_action="merge", locked_at=datetime.now(UTC))
    r = client.post(f"/po_sets/{pid}/toggle_customs")
    assert r.status_code == 409


def test_toggle_while_sync_runs_is_409(cfg, client, monkeypatch):
    pid = _set(cfg, "PO-TOG-SYNC")
    monkeypatch.setattr("app.services.sync_lock._is_sync_running", lambda *a, **k: True)
    r = client.post(f"/po_sets/{pid}/toggle_customs")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Packet facts
# ---------------------------------------------------------------------------


def _merged_set(cfg, po: str, *, customs: bool) -> int:
    pid = _set(cfg, po)
    _doc(cfg, pid, DocType.PO, f"{po}-po.pdf", [("1", "BOLT", 5)], po_no=po)
    _doc(cfg, pid, DocType.DN, f"{po}-dn.pdf", [("1", "BOLT", 5)], po_no=po)
    _doc(cfg, pid, DocType.SI, f"{po}-si.pdf", [("1", "BOLT", 5)], po_no=po, si_no=f"INV-{po}")
    if customs:
        _add_supporting(cfg, pid, po, DocType.CUSTOMS, f"CUSTOMS-{po}.pdf")
        _add_supporting(cfg, pid, po, DocType.SHIPPING, f"AWB-{po}.pdf")
    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "merged", result
    return pid


def test_packet_facts_yes_when_customs_was_on(cfg, client):
    pid = _merged_set(cfg, "PO-PKT-YES", customs=True)
    page = client.get(f"/po_sets/{pid}/view").text
    assert "Packet contains customs pages:" in page
    assert "Yes" in page
    assert "Customs is switched off" not in page


def test_packet_facts_yes_with_note_when_customs_is_off(cfg, client):
    pid = _merged_set(cfg, "PO-PKT-OFF", customs=True)
    _set_ps(cfg, pid, has_customs_toggle=False)
    page = client.get(f"/po_sets/{pid}/view").text
    assert "Packet contains customs pages:" in page
    assert "Yes" in page
    assert "Customs is switched off, but the delivered packet still contains customs pages" in page


def test_packet_facts_no_without_customs(cfg, client):
    pid = _merged_set(cfg, "PO-PKT-NO", customs=False)
    page = client.get(f"/po_sets/{pid}/view").text
    assert "Packet contains customs pages:" in page
    assert "No" in page


def test_packet_facts_absent_before_merge(cfg, client):
    pid = _mismatched(cfg, "PO-PKT-PENDING")
    page = client.get(f"/po_sets/{pid}/view").text
    assert "Packet contains customs pages:" not in page


# ---------------------------------------------------------------------------
# Labels: under/over/mixed/pending
# ---------------------------------------------------------------------------


def test_over_delivery_is_needs_review_not_waiting(cfg, client):
    pid = _set(cfg, "PO-OVER")
    _doc(cfg, pid, DocType.PO, "po.pdf", [("1", "BOLT", 10)], po_no="PO-OVER")
    _doc(cfg, pid, DocType.DN, "dn.pdf", [("1", "BOLT", 12)], po_no="PO-OVER")
    _doc(cfg, pid, DocType.SI, "si.pdf", [("1", "BOLT", 12)], po_no="PO-OVER", si_no="INV-OVER")
    assert reconcile_po_set(pid, cfg)["status"] == "mismatched"
    row = _row(client.get("/dashboard?status=mismatched").text, pid)
    assert "Needs Review" in row
    assert "Waiting (part delivered)" not in row
    page = client.get(f"/po_sets/{pid}/view").text
    assert "Why this set needs review" in page


def test_mixed_short_and_over_is_needs_review(cfg, client):
    pid = _set(cfg, "PO-MIXED")
    _doc(
        cfg,
        pid,
        DocType.PO,
        "po.pdf",
        [("1", "A", 10), ("2", "B", 10)],
        po_no="PO-MIXED",
    )
    _doc(
        cfg,
        pid,
        DocType.DN,
        "dn.pdf",
        [("1", "A", 6), ("2", "B", 12)],
        po_no="PO-MIXED",
    )
    _doc(
        cfg,
        pid,
        DocType.SI,
        "si.pdf",
        [("1", "A", 10), ("2", "B", 10)],
        po_no="PO-MIXED",
        si_no="INV-MIXED",
    )
    assert reconcile_po_set(pid, cfg)["status"] == "mismatched"
    row = _row(client.get("/dashboard?status=mismatched").text, pid)
    assert "Needs Review" in row
    assert "Waiting (part delivered)" not in row


def test_missing_si_is_pending_not_waiting_part_delivered(cfg, client):
    pid = _set(cfg, "PO-NOSI")
    _doc(cfg, pid, DocType.PO, "po.pdf", [("1", "BOLT", 10)], po_no="PO-NOSI")
    _doc(cfg, pid, DocType.DN, "dn.pdf", [("1", "BOLT", 10)], po_no="PO-NOSI")
    assert reconcile_po_set(pid, cfg)["status"] == "pending"
    row = _row(client.get("/dashboard").text, pid)
    assert "Pending / Waiting" in row
    assert "Waiting (part delivered)" not in row


def test_two_po_files_get_the_decision_tag(cfg, client):
    pid = _set(cfg, "PO-TWOPO")
    _doc(cfg, pid, DocType.PO, "po1.pdf", [("1", "A", 1)], po_no="PO-TWOPO")
    _doc(cfg, pid, DocType.PO, "po2.pdf", [("2", "B", 1)], po_no="PO-TWOPO")
    _doc(cfg, pid, DocType.DN, "dn.pdf", [("1", "A", 1), ("2", "B", 1)], po_no="PO-TWOPO")
    _doc(cfg, pid, DocType.SI, "si.pdf", [("1", "A", 1), ("2", "B", 1)], po_no="PO-TWOPO", si_no="INV-TWOPO")
    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "pending" and result["reason"] == "multiple_po_documents"
    row = _row(client.get("/dashboard").text, pid)
    assert "Needs a decision: 2 PO files" in row
    assert "2 PO documents" in client.get(f"/po_sets/{pid}/view").text


def test_description_match_label(cfg, client):
    pid = _set(cfg, "PO-DESC")
    _doc(cfg, pid, DocType.PO, "po.pdf", [("", "Widget", 5)], po_no="PO-DESC")
    _doc(cfg, pid, DocType.DN, "dn.pdf", [("1", "Widget", 5)], po_no="PO-DESC")
    _doc(cfg, pid, DocType.SI, "si.pdf", [("1", "Widget", 5)], po_no="PO-DESC", si_no="INV-DESC")
    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "merged", result
    row = _row(client.get("/dashboard").text, pid)
    assert "Matched by description - needs a second check" in row


def test_force_merged_label(cfg, client):
    pid = _mismatched(cfg, "PO-FORCE-LABEL")
    r = client.post(
        f"/po_sets/{pid}/force_merge",
        data={"justification": "Browser test: operator accepts the short delivery."},
    )
    assert r.status_code == 200, r.text
    row = _row(client.get("/dashboard").text, pid)
    assert "Force-merged - needs a second check" in row


# ---------------------------------------------------------------------------
# IST
# ---------------------------------------------------------------------------


def test_ist_filter_naive_and_aware():
    assert ist(datetime(2026, 1, 1, 0, 0)) == "2026-01-01 05:30 IST"
    assert ist(datetime(2026, 1, 1, 0, 0, tzinfo=UTC)) == "2026-01-01 05:30 IST"
    assert ist(None) == "—"


def test_set_page_audit_and_holding_show_ist(cfg, client):
    pid = _mismatched(cfg, "PO-IST")
    page = client.get(f"/po_sets/{pid}/view").text
    assert "Created:" in page and "IST" in page
    audit = client.get("/audit").text
    assert "IST" in audit
    _held_valid_si(cfg)
    assert "IST" in client.get("/unclassified").text


def test_set_aside_report_has_utc_and_ist(cfg, client):
    doc_id, _sha, _input_copy = _held_valid_si(cfg)
    folder = set_aside_document(doc_id, cfg)
    report = (folder / "QUARANTINE.txt").read_text(encoding="utf-8")
    assert "UTC" in report
    assert "IST" in report


# ---------------------------------------------------------------------------
# 404 modes
# ---------------------------------------------------------------------------


def test_404_browser_is_a_page(client):
    r = client.get("/definitely-not-a-page", headers={"Accept": "text/html"})
    assert r.status_code == 404
    assert "Page not found" in r.text
    assert "nav" in r.text
    assert "Traceback" not in r.text


def test_missing_set_browser_page_has_nav_and_heading(client):
    r = client.get("/po_sets/999999/view", headers={"Accept": "text/html"})
    assert r.status_code == 404
    assert "PO Set not found" in r.text
    assert "nav" in r.text
    assert "Traceback" not in r.text


def test_404_api_is_json(client):
    r = client.get("/po_sets/999999/view", headers={"Accept": "application/json"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert "detail" in r.json()


@pytest.mark.xfail(
    strict=True,
    reason="NEW P3: set-detail 404 returns HTML even for HTMX requests",
)
def test_404_htmx_is_json(client):
    r = client.get(
        "/po_sets/999999/view",
        headers={"Accept": "text/html", "HX-Request": "true"},
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_404_static_is_not_html(client):
    r = client.get("/static/definitely-not-here.css")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Upload guards
# ---------------------------------------------------------------------------


def _upload(client, pid: int, *, filename: str, data: bytes, doc_type: str = "CUSTOMS"):
    return client.post(
        f"/po_sets/{pid}/upload",
        files={"file": (filename, data, "application/pdf")},
        data={"doc_type": doc_type},
    )


def test_upload_non_pdf_is_422(cfg, client):
    pid = _set(cfg, "PO-UP-NONPDF")
    r = _upload(client, pid, filename="x.txt", data=b"not a pdf")
    assert r.status_code == 422


def test_upload_empty_is_422(cfg, client):
    pid = _set(cfg, "PO-UP-EMPTY")
    r = _upload(client, pid, filename="x.pdf", data=b"")
    assert r.status_code == 422


def test_upload_invalid_doc_type_is_422(cfg, client):
    pid = _set(cfg, "PO-UP-TYPE")
    r = _upload(client, pid, filename="x.pdf", data=_pdf_bytes(), doc_type="PO")
    assert r.status_code == 422


def test_upload_duplicate_to_same_set_is_idempotent(cfg, client):
    pid = _set(cfg, "PO-UP-DUP")
    data = _pdf_bytes()
    first = _upload(client, pid, filename="CUSTOMS.pdf", data=data)
    second = _upload(client, pid, filename="CUSTOMS.pdf", data=data)
    assert first.status_code in (200, 302)
    assert second.status_code in (200, 302)
    with Session(get_engine(cfg)) as s:
        assert s.query(Document).filter(Document.po_set_id == pid).count() == 1


def test_upload_duplicate_to_another_set_is_409(cfg, client):
    a = _set(cfg, "PO-UP-A")
    b = _set(cfg, "PO-UP-B")
    data = _pdf_bytes()
    assert _upload(client, a, filename="CUSTOMS.pdf", data=data).status_code in (200, 302)
    r = _upload(client, b, filename="CUSTOMS.pdf", data=data)
    assert r.status_code == 409


def test_upload_to_archived_is_409(cfg, client):
    pid = _set(cfg, "PO-UP-ARCH")
    _set_ps(cfg, pid, archived_at=datetime.now(UTC))
    assert _upload(client, pid, filename="CUSTOMS.pdf", data=_pdf_bytes()).status_code == 409


def test_upload_to_locked_is_409(cfg, client):
    pid = _set(cfg, "PO-UP-LOCK")
    _set_ps(cfg, pid, locked_by_action="merge", locked_at=datetime.now(UTC))
    assert _upload(client, pid, filename="CUSTOMS.pdf", data=_pdf_bytes()).status_code == 409


def test_upload_while_sync_runs_is_409(cfg, client, monkeypatch):
    pid = _set(cfg, "PO-UP-SYNC")
    monkeypatch.setattr("app.services.sync_lock._is_sync_running", lambda *a, **k: True)
    assert _upload(client, pid, filename="CUSTOMS.pdf", data=_pdf_bytes()).status_code == 409


def test_upload_to_merged_set_attaches(cfg, client):
    pid = _merged_set(cfg, "PO-UP-MERGED", customs=False)
    r = _upload(client, pid, filename="CUSTOMS.pdf", data=_pdf_bytes())
    assert r.status_code in (200, 302), r.text
    with Session(get_engine(cfg)) as s:
        types = {
            d.doc_type
            for d in s.query(Document).filter(Document.po_set_id == pid, Document.archived_at.is_(None))
        }
        assert DocType.CUSTOMS in types


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_foreign_origin_post_is_refused(client):
    r = client.post("/sync", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_hostile_filename_is_escaped(cfg, client):
    pid = _mismatched(cfg, "PO-XSS")
    with Session(get_engine(cfg)) as s:
        d = s.query(Document).filter(Document.po_set_id == pid).first()
        d.original_filename = "<img src=x onerror=window.__xss=1>.pdf"
        s.commit()
    page = client.get(f"/po_sets/{pid}/view").text
    assert "<img src=x" not in page
    assert "&lt;img" in page


def test_sql_injection_in_a_path_parameter_is_not_500(client):
    r = client.get("/po_sets/1%20OR%201=1/view")
    assert r.status_code in (404, 422)
