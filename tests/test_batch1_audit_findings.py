"""Batch 1 audit findings: tests that expose the issues found in the code review.

These are deliberately separate from the product's acceptance tests.  A test
marked ``xfail(strict=True)`` encodes "the code does this wrong today; when it
is fixed, flip the marker".  Run with ``--runxfail`` to see the real failures.

The two accepted P1 risks are pinned as characterization tests (they pass today
and fail if the accepted behaviour changes), so the decision is visible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import AuditAction, AuditLog, DocType, POSet
from app.services.quarantine import SetRemovalRefused, remove_set_to_quarantine, set_aside_document
from app.services.reconciliation import reconcile_po_set
from test_business_scenarios_ui import _doc, _set, cfg, client  # noqa: F401
from test_holding_set_aside import _held_valid_si

NOTE = "wrong document in the set; replacing it with the corrected scan"


def _mismatched(cfg, po: str) -> int:
    """A real mismatched set (PO 10, DN 6, SI 10) that the removal card accepts."""
    pid = _set(cfg, po)
    _doc(cfg, pid, DocType.PO, f"{po}-po.pdf", [("1", "BOLT", 10)], po_no=po)
    _doc(cfg, pid, DocType.DN, f"{po}-dn.pdf", [("1", "BOLT", 6)], po_no=po)
    _doc(cfg, pid, DocType.SI, f"{po}-si.pdf", [("1", "BOLT", 10)], po_no=po, si_no=f"INV-{po}")
    result = reconcile_po_set(pid, cfg)
    assert result["status"] == "mismatched", result
    return pid


def _latest_removal_audit(cfg) -> AuditLog:
    with Session(get_engine(cfg)) as s:
        rows = s.scalars(
            select(AuditLog).where(AuditLog.action == AuditAction.quarantine_delete)
        ).all()
        return next(r for r in rows if '"deleted": true' in (r.detail or ""))


# ---------------------------------------------------------------------------
# P2: the detail page contradicts itself for a short delivery
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="P2: top badge and matrix still say Needs Review/Mismatch")
def test_under_delivery_detail_page_is_consistent(cfg, client):
    pid = _mismatched(cfg, "PO-UNDER")
    dashboard = client.get("/dashboard?status=mismatched").text
    page = client.get(f"/po_sets/{pid}/view").text

    assert "Waiting (part delivered)" in dashboard  # already correct
    assert "Waiting (part delivered)" in page  # top badge: currently Needs Review
    assert "Why this set is waiting" in page  # already correct
    matrix = re.search(r'data-testid="matrix-row".*?</tr>', page, re.S)
    assert matrix, "no matrix row"
    assert "Awaiting" in matrix.group(0)  # currently Mismatch


# ---------------------------------------------------------------------------
# P2: the set-aside report lies about attempts for a valid document
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="P2: set-aside report says Attempts 0/3 (cap reached)")
def test_set_aside_report_does_not_claim_cap_reached_for_a_valid_document(cfg, client):
    doc_id, _sha, _input_copy = _held_valid_si(cfg)
    folder = set_aside_document(doc_id, cfg)
    report = (folder / "QUARANTINE.txt").read_text(encoding="utf-8")

    assert "Attempts: 0/3" not in report
    assert "cap reached" not in report


# ---------------------------------------------------------------------------
# P2: failure after the database commit must not look like a failed removal
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="P2: REMOVED.txt write after commit raises out of the route")
def test_report_write_failure_after_commit_does_not_raise(cfg, client, monkeypatch):
    pid = _mismatched(cfg, "PO-POST")
    real_write = Path.write_text

    def fake_write(self, data, *args, **kwargs):
        if self.name == "REMOVED.txt":
            raise OSError("disk full")
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fake_write)
    try:
        remove_set_to_quarantine(pid, cfg, note=NOTE)
        raised = None
    except Exception as exc:  # noqa: BLE001 - the point is to report what escaped
        raised = exc

    assert raised is None, f"removal raised after commit: {raised!r}"


@pytest.mark.xfail(strict=True, reason="P2: mkdir failure before the try escapes as a bare 500")
def test_mkdir_failure_is_a_friendly_refusal(cfg, client, monkeypatch):
    pid = _mismatched(cfg, "PO-MKDIR")
    real_mkdir = Path.mkdir

    def fake_mkdir(self, *args, **kwargs):
        if self.name == "files":
            raise OSError("permission denied")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    try:
        remove_set_to_quarantine(pid, cfg, note=NOTE)
        raised = None
    except SetRemovalRefused as exc:
        raised = exc
    except Exception as exc:  # noqa: BLE001
        raised = exc

    assert isinstance(raised, SetRemovalRefused), f"unfriendly failure: {raised!r}"


# ---------------------------------------------------------------------------
# P3: removal result page / 404 / audit column
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="P3: result shows the id, not the PO, and the absolute path")
def test_removal_result_shows_the_po_number_and_no_absolute_path(cfg, client):
    pid = _mismatched(cfg, "PO-RESULT")
    response = client.post(
        f"/po_sets/{pid}/remove_to_quarantine",
        data={"note": NOTE},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    assert "PO-RESULT" in response.text
    assert str(cfg.paths.quarantine_folder) not in response.text


@pytest.mark.xfail(strict=True, reason="P3: 404 text still says only 'may have been archived'")
def test_missing_set_404_mentions_archived_or_removed(client):
    response = client.get("/po_sets/999999/view", headers={"Accept": "text/html"})
    assert "archived or removed" in response.text


@pytest.mark.xfail(strict=True, reason="P3: audit PO Set column shows '-', not '#id (removed)'")
def test_audit_page_puts_the_removed_id_in_the_po_set_column(cfg, client):
    pid = _mismatched(cfg, "PO-AUDIT")
    client.post(
        f"/po_sets/{pid}/remove_to_quarantine",
        data={"note": NOTE},
        headers={"Accept": "text/html"},
    )
    page = client.get("/audit").text
    rows = re.findall(r'<tr data-testid="audit-row".*?</tr>', page, re.S)
    row = next(r for r in rows if "Set Removed" in r)
    cells = re.findall(r"<td.*?</td>", row, re.S)
    assert f"#{pid} (removed)" in cells[2]


# ---------------------------------------------------------------------------
# P3: set-aside redirect and double-move message
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="P3: quarantine-page move redirects to /unclassified")
def test_set_aside_from_the_quarantine_page_returns_to_quarantine(cfg, client):
    doc_id, _sha, _input_copy = _held_valid_si(cfg)
    response = client.post(
        f"/unclassified/{doc_id}/set_aside",
        headers={"Referer": "http://testserver/quarantine"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/quarantine"


@pytest.mark.xfail(strict=True, reason="P3: a second move still says 'Moved', not 'already moved'")
def test_set_aside_twice_says_it_was_already_moved(cfg, client):
    doc_id, _sha, _input_copy = _held_valid_si(cfg)
    set_aside_document(doc_id, cfg)
    response = client.post(f"/unclassified/{doc_id}/set_aside", headers={"HX-Request": "true"})

    assert "already" in response.text.lower() or "no longer" in response.text.lower()


# ---------------------------------------------------------------------------
# Accepted P1 risks, pinned as current behaviour (not xfail)
# ---------------------------------------------------------------------------


def test_accepted_p1_removal_allows_an_empty_justification(cfg, client):
    pid = _mismatched(cfg, "PO-ACCEPT-EMPTY")
    response = client.post(
        f"/po_sets/{pid}/remove_to_quarantine",
        data={"note": ""},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    audit = _latest_removal_audit(cfg)
    assert audit.justification is None
    assert '"note": ""' in (audit.detail or "")


def test_accepted_p1_removal_accepts_a_one_character_note(cfg, client):
    pid = _mismatched(cfg, "PO-ACCEPT-SHORT")
    response = client.post(
        f"/po_sets/{pid}/remove_to_quarantine",
        data={"note": "x"},
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 200


def test_accepted_p1_removal_proceeds_when_a_stored_file_is_missing(cfg, client):
    pid = _mismatched(cfg, "PO-ACCEPT-MISSING")
    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, pid)
        assert ps is not None
        doc = ps.documents[0]
        Path(doc.stored_path).unlink()

    response = client.post(
        f"/po_sets/{pid}/remove_to_quarantine",
        data={"note": NOTE},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    assert "missing" in response.text.lower()
