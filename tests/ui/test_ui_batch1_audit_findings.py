"""Batch 1 audit: real-browser checks for the changed/new behaviour and known issues.

Run with::

    RUN_E2E=1 python -m pytest tests/ui/test_ui_batch1_audit_findings.py -o addopts= -p no:cacheprovider

Known issues are ``xfail(strict=True)``: they must fail today, and a fix will
make them XPASS (then flip the marker).  The new-feature checks are normal
tests and must pass.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, expect


def _open(page: Page, manifest, name: str):
    sc = manifest["scenarios"][name]
    page.goto(f"/po_sets/{sc['id']}/view")
    expect(page.get_by_test_id("set-po")).to_have_text(sc["po_no"])
    return sc


# ---------------------------------------------------------------------------
# New behaviour: removal / delete
# ---------------------------------------------------------------------------


def test_ui_remove_set_card_visibility(page: Page, manifest):
    """The card is offered only on active sets that can be replaced.

    A merged set is delivered and terminal; offering removal there would delete
    a delivered packet's rows. Archived sets are read-only.
    """
    for name in (
        "pending_no_dn",
        "needs_review_quantity",
        "quarantined_unmatched_vendor_line",
        "awaiting_customs",
    ):
        _open(page, manifest, name)
        expect(page.get_by_test_id("remove-set-card")).to_be_visible()

    for name in ("merged_basic", "archived_readonly"):
        _open(page, manifest, name)
        expect(page.get_by_test_id("remove-set-card")).to_have_count(0)


def test_ui_remove_set_result_404_audit(page: Page, manifest):
    sc = _open(page, manifest, "remove_set_target")
    expect(page.get_by_test_id("remove-set-card")).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_test_id("btn-confirm-remove-set").click()
    expect(page.get_by_test_id("remove-set-result")).to_be_visible()

    result = page.get_by_test_id("remove-set-result")
    expect.soft(result).to_contain_text(sc["po_no"])
    expect.soft(result).not_to_contain_text(manifest["root"])

    page.goto(f"/po_sets/{sc['id']}/view")
    expect.soft(page.locator("body")).to_contain_text("archived or removed")

    page.goto("/audit")
    row = page.locator('[data-testid="audit-row"]', has_text="Set Removed").first
    expect.soft(row.locator("td").nth(2)).to_contain_text(f"#{sc['id']} (removed)")


# ---------------------------------------------------------------------------
# New behaviour: customs toggle refusal, packet facts, remove-supporting
# ---------------------------------------------------------------------------


def test_ui_customs_toggle_off_is_refused_while_files_are_attached(page: Page, manifest):
    sc = _open(page, manifest, "customs_toggle_refusal_target")
    button = page.get_by_test_id("btn-toggle-customs")
    expect(button).to_contain_text("ON")

    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/toggle_customs")) as resp:
        button.click()
    assert resp.value.status == 409, resp.value.text()
    expect(page.locator(".toast-warning")).to_contain_text("Delete them first")
    # The refusal must not have flipped the requirement.
    expect(page.get_by_test_id("btn-toggle-customs")).to_contain_text("ON")


def test_ui_packet_customs_facts(page: Page, manifest):
    _open(page, manifest, "merged_with_customs")
    expect(page.get_by_test_id("packet-customs-facts")).to_contain_text("Yes")

    _open(page, manifest, "merged_customs_off_with_docs")
    expect(page.get_by_test_id("packet-customs-facts")).to_contain_text("Yes")
    expect(page.get_by_test_id("customs-off-packet-note")).to_be_visible()

    _open(page, manifest, "merged_basic")
    expect(page.get_by_test_id("packet-customs-facts")).to_contain_text("No")


# ---------------------------------------------------------------------------
# New behaviour: files set aside, set-aside report, redirect
# ---------------------------------------------------------------------------


def test_ui_files_set_aside_lists_failed_only(page: Page, manifest):
    page.goto("/quarantine")
    card = page.get_by_test_id("files-set-aside")
    expect(card).to_be_visible()

    failed = manifest["held"]["held_set_aside_failed_target"]
    valid = manifest["held"]["held_set_aside_target"]
    expect(card.locator(f'[data-testid="set-aside-row"][data-doc-id="{failed["id"]}"]')).to_have_count(1)
    expect(card.locator(f'[data-testid="set-aside-row"][data-doc-id="{valid["id"]}"]')).to_have_count(0)


def test_ui_set_aside_report_is_true(page: Page, manifest, db):
    held = manifest["held"]["held_set_aside_target"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    page.once("dialog", lambda dialog: dialog.accept())
    row.get_by_test_id("btn-set-aside").click()
    expect(page.get_by_test_id("set-aside-result")).to_be_visible()

    stored = db.execute(
        "select stored_path from documents where id=?", (held["id"],)
    ).fetchone()["stored_path"]
    report = (
        Path(manifest["root"])
        / "quarantine"
        / "_documents"
        / Path(stored).stem
        / "QUARANTINE.txt"
    )
    text = report.read_text(encoding="utf-8")
    assert "Attempts: 0/3" not in text
    assert "cap reached" not in text


def test_ui_set_aside_from_quarantine_stays(page: Page, manifest):
    held = manifest["held"]["held_set_aside_failed_target"]
    page.goto("/quarantine")
    row = page.locator(f'[data-testid="set-aside-row"][data-doc-id="{held["id"]}"]')
    row.get_by_test_id("btn-set-aside-quarantine").click()
    page.wait_for_load_state()
    assert urlparse(page.url).path == "/quarantine"


# ---------------------------------------------------------------------------
# New behaviour: worklist labels and IST
# ---------------------------------------------------------------------------


def test_ui_under_delivery_is_consistent(page: Page, manifest):
    sc = _open(page, manifest, "needs_review_quantity")
    expect.soft(page.locator("body")).to_contain_text("Partial Delivery")
    expect.soft(page.get_by_test_id("reason-card")).to_contain_text("Why this set is waiting")
    expect.soft(page.get_by_test_id("matrix-row").first).to_contain_text("Awaiting")
    assert sc["status"] == "mismatched"


def test_ui_description_and_force_merged_labels(page: Page, manifest):
    desc = manifest["scenarios"]["description_match_target"]
    page.goto("/dashboard")
    row = page.locator(f'[data-testid="po-row"][data-set-id="{desc["id"]}"]')
    expect(row).to_contain_text("Matched by description - needs a second check")

    sc = _open(page, manifest, "force_merge_label_target")
    page.get_by_test_id("btn-force-merge").click()
    page.locator("#force-merge-justification").fill("UI test: operator accepts the short delivery.")
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/force_merge")) as resp:
        page.get_by_test_id("btn-confirm-force-merge").click()
    assert resp.value.status == 200, resp.value.text()
    page.goto("/dashboard")
    row = page.locator(f'[data-testid="po-row"][data-set-id="{sc["id"]}"]')
    expect(row).to_contain_text("Force-merged - needs a second check")


def test_ui_ist_display(page: Page, manifest):
    page.goto("/audit")
    first = page.get_by_test_id("audit-row").first
    expect(first.locator("td").first).to_contain_text("IST")

    page.goto("/unclassified")
    first = page.get_by_test_id("unclassified-row").first
    expect(first).to_contain_text("IST")

    sc = _open(page, manifest, "merged_basic")
    expect(page.locator("body")).to_contain_text("IST")


# ---------------------------------------------------------------------------
# New behaviour: audit rows
# ---------------------------------------------------------------------------


def test_ui_audit_customs_toggle_rows(page: Page, manifest):
    sc = _open(page, manifest, "customs_toggle_audit_target")
    button = page.get_by_test_id("btn-toggle-customs")
    for _ in range(2):
        with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/toggle_customs")) as resp:
            button.click()
        assert resp.value.status == 200, resp.value.text()
        button = page.get_by_test_id("btn-toggle-customs")

    page.goto("/audit")
    expect(page.locator("body")).to_contain_text("Customs check switched ON")
    expect(page.locator("body")).to_contain_text("Customs check switched OFF")


def test_ui_audit_reclassify_row(page: Page, manifest):
    held = manifest["held"]["held_reclassify_audit_target"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    row.get_by_test_id("select-doc-type").select_option("DN")
    row.get_by_test_id("input-po-no").fill("7000099")
    with page.expect_response(re.compile(rf"/unclassified/{held['id']}/reclassify")) as resp:
        row.get_by_test_id("btn-route").click()
    assert resp.value.status == 200, resp.value.text()

    page.goto("/audit")
    expect(page.locator("body")).to_contain_text("Reclassified")
    expect(page.locator("body")).to_contain_text(held["file"])


# ---------------------------------------------------------------------------
# Known issue: 404 text / Re-Match wording
# ---------------------------------------------------------------------------


def test_ui_missing_set_404_says_archived_or_removed(page: Page):
    response = page.goto("/po_sets/999999/view")
    assert response is not None and response.status == 404
    expect(page.locator("body")).to_contain_text("archived or removed")


def test_ui_re_match_toast_is_human(page: Page, manifest):
    _open(page, manifest, "rematch_toast_target")
    with page.expect_response(re.compile(r"/po_sets/\d+/redo_match")) as resp:
        page.locator('[hx-post$="/redo_match"]').click()
    assert resp.value.status == 200, resp.value.text()
    toast = page.locator(".toast").last
    expect(toast).to_contain_text("Re-matched")
    assert "missing_dn_document" not in toast.inner_text()


# ---------------------------------------------------------------------------
# Accessibility / narrow window
# ---------------------------------------------------------------------------


def test_ui_accessibility_labels(page: Page, manifest):
    page.goto("/dashboard")
    expect(page.locator('input[aria-label="Search PO sets by PO number or status"]')).to_have_count(1)
    expect(page.locator('label[for="status-filter"]')).to_have_count(1)

    held = manifest["held"]["held_dn_no_po"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    expect(row.locator("select[aria-label]")).to_have_count(1)
    expect(row.locator('input[aria-label^="PO number for"]')).to_have_count(1)


def test_ui_narrow_window_does_not_scroll_sideways(page: Page):
    page.set_viewport_size({"width": 800, "height": 600})
    page.goto("/dashboard")
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - window.innerWidth"
    )
    assert overflow <= 1, f"page scrolls sideways by {overflow}px at 800px"
