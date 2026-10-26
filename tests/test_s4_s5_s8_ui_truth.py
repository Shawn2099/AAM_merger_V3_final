"""Opus review S4, S5, S8: what the screens say must be true.

S4  "Age" is the set's age (created_at); a lock/unlock bumps updated_at and must not move it.
S5  A merged set's matrix is not recomputed with today's line-number rules.
S8  Reason text names the Unclassified page; QUARANTINE.txt matches its case; Merge Now on a
    mismatched set says what disagrees.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session
from test_business_scenarios_ui import _doc, _set, cfg, client  # noqa: F401

from app.core.database import get_engine
from app.models import DocType, Document, POSet, POSetStatus
from app.services.locking import acquire_lock, release_lock
from app.services.quarantine import quarantine_document
from app.services.reconciliation import REASON_TEXT, reconcile_po_set


def _backdate_created(cfg, pid: int, days: int) -> None:  # noqa: F811
    with Session(get_engine(cfg)) as s:
        s.execute(
            update(POSet)
            .where(POSet.id == pid)
            .values(created_at=datetime.now(UTC) - timedelta(days=days))
        )
        s.commit()


def _touch_by_lock(cfg, pid: int) -> None:  # noqa: F811
    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, pid)
        assert acquire_lock(ps, "sweep", s, cfg)
        release_lock(ps, s, action="sweep")


def test_s4_age_and_order_ignore_lock_and_unlock(cfg, client):  # noqa: F811
    older = _set(cfg, "S4-OLDER")
    newer = _set(cfg, "S4-NEWER")
    _backdate_created(cfg, older, 3)
    _backdate_created(cfg, newer, 0)

    before = client.get("/dashboard/table").text
    assert "3d ago" in before
    assert before.index("S4-NEWER") < before.index("S4-OLDER")

    # The sweep touches the OLD set last: updated_at is now newer than the other set's.
    _touch_by_lock(cfg, older)
    with Session(get_engine(cfg)) as s:
        o, n = s.get(POSet, older), s.get(POSet, newer)
        assert o.updated_at > n.updated_at

    after = client.get("/dashboard/table").text
    assert "3d ago" in after
    assert after.index("S4-NEWER") < after.index("S4-OLDER")


def test_s4_detail_page_labels_the_two_timestamps(cfg, client):  # noqa: F811
    pid = _set(cfg, "S4-DETAIL")
    html = client.get(f"/po_sets/{pid}/view").text
    assert "Created:" in html
    assert "Last checked:" in html
    assert "Last Updated" not in html


def test_s5_merged_set_keeps_its_rules_when_settings_change(cfg, client):  # noqa: F811
    pid = _set(cfg, "S5-MERGED")
    _doc(
        cfg, pid, DocType.PO, "s5-po.pdf", [("10", "BOLT", 5), ("20", "NUT", 7)], po_no="S5-MERGED"
    )
    _doc(cfg, pid, DocType.DN, "s5-dn.pdf", [("1", "BOLT", 5), ("2", "NUT", 7)], po_no="S5-MERGED")
    _doc(
        cfg,
        pid,
        DocType.SI,
        "s5-si.pdf",
        [("1", "BOLT", 5), ("2", "NUT", 7)],
        si_no="INV-S5",
        po_no="S5-MERGED",
    )
    assert reconcile_po_set(pid, cfg)["status"] == "merged"
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, pid).line_mapping_rule == "x10"

    page = client.get(f"/po_sets/{pid}/view").text
    assert "Mismatch" not in page
    assert "shown as at merge time" in page

    # Operator turns every rule off on the Settings page; the live rules would now disagree.
    fb = cfg.matching.line_number_fallback
    fb.step10 = fb.step10_reverse = fb.release_suffix = fb.release_suffix_reverse = False
    cfg.matching.fuzzy_description_threshold = 100
    page = client.get(f"/po_sets/{pid}/view").text
    assert "Mismatch" not in page
    assert "Awaiting (PO" not in page
    assert "Match" in page
    dash = client.get("/dashboard/table").text
    assert "S5-MERGED" in dash and "Quantity mismatch" not in dash.lower()


def test_s5_unmerged_set_still_uses_the_live_rules(cfg, client):  # noqa: F811
    pid = _set(cfg, "S5-LIVE")
    _doc(cfg, pid, DocType.PO, "s5l-po.pdf", [("10", "BOLT", 5)], po_no="S5-LIVE")
    _doc(cfg, pid, DocType.DN, "s5l-dn.pdf", [("1", "BOLT", 5)], po_no="S5-LIVE")
    fb = cfg.matching.line_number_fallback
    fb.step10 = False
    page = client.get(f"/po_sets/{pid}/view").text
    assert "shown as at merge time" not in page


def test_s8_missing_dn_text_names_the_unclassified_page():
    assert "Unclassified" in REASON_TEXT["missing_dn_document"]
    assert "still being read" in REASON_TEXT["missing_dn_document"]


def _qtext(cfg, doc_id: int, reason: str) -> str:  # noqa: F811
    folder = quarantine_document(doc_id, cfg, reason=reason)
    return (Path(folder) / "QUARANTINE.txt").read_text(encoding="utf-8")


def test_s8_quarantine_txt_extraction_failure_uses_the_configured_cap(cfg):  # noqa: F811
    cfg.extraction.max_retries = 2
    pid = _set(cfg, "S8-FAIL")
    did = _doc(cfg, pid, DocType.PO, "fail.pdf", [], po_no="S8-FAIL")
    with Session(get_engine(cfg)) as s:
        s.execute(update(Document).where(Document.id == did).values(extraction_attempt_count=2))
        s.commit()
    text = _qtext(cfg, did, "Extraction failed permanently (attempt cap reached)")
    assert "Attempts    : 2/2" in text
    assert "could not be read" in text


def test_s8_quarantine_txt_combined_file_was_read_and_has_no_attempts(cfg):  # noqa: F811
    pid = _set(cfg, "S8-COMB")
    did = _doc(cfg, pid, DocType.COMBINED, "comb.pdf", [], po_no="S8-COMB")
    text = _qtext(cfg, did, "combined_document: one PDF holds several documents")
    assert "Attempts" not in text
    assert "could not be read" not in text
    assert "was read" in text and "several documents" in text


def test_s8_quarantine_txt_ingest_reject_says_rejected_on_arrival(cfg):  # noqa: F811
    pid = _set(cfg, "S8-REJ")
    did = _doc(cfg, pid, DocType.UNKNOWN, "rej.pdf", [])
    with Session(get_engine(cfg)) as s:
        s.execute(
            update(Document)
            .where(Document.id == did)
            .values(raw_extraction_json=json.dumps({"ingest_rejected": "encrypted PDF"}))
        )
        s.commit()
    text = _qtext(cfg, did, "ingest rejected")
    assert "Attempts" not in text
    assert "rejected on arrival (encrypted PDF)" in text
    assert "could not be read" not in text


def test_s8_merge_now_on_a_mismatched_set_says_what_disagrees(cfg, client):  # noqa: F811
    pid = _set(cfg, "S8-MM")
    _doc(cfg, pid, DocType.PO, "mm-po.pdf", [("1", "SEAL", 10)], po_no="S8-MM")
    _doc(cfg, pid, DocType.DN, "mm-dn.pdf", [("1", "SEAL", 8)], po_no="S8-MM")
    _doc(cfg, pid, DocType.SI, "mm-si.pdf", [("1", "SEAL", 10)], si_no="INV-MM", po_no="S8-MM")
    r = client.post(f"/po_sets/{pid}/merge")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "mismatched"
    assert "delivered 8 of 10" in body["detail"]
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, pid).status == POSetStatus.mismatched
