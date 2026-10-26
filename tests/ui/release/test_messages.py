"""Return messages: human words, no raw reason codes, no false success."""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect


def test_404_message_mentions_archived_or_removed(page: Page):
    response = page.goto("/po_sets/999999/view")
    assert response is not None and response.status == 404
    body = page.locator("body").inner_text()
    assert "archived" in body.lower()
    assert "removed" in body.lower()


def test_re_match_toast_is_human(page: Page, manifest):
    sc = manifest["scenarios"]["pending_no_dn"]
    page.goto(f"/po_sets/{sc['id']}/view")
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/redo_match")) as response:
        page.get_by_test_id("btn-rematch").click()
    assert response.value.status == 200
    toast = page.locator(".toast").first
    expect(toast).to_contain_text("Re-matched")
    text = toast.inner_text()
    assert "missing_dn_document" not in text, text
    assert "_" not in text, text


def test_merge_now_pending_toast_is_human(page: Page, manifest):
    sc = manifest["scenarios"]["pending_no_dn"]
    page.goto(f"/po_sets/{sc['id']}/view")
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/merge$")) as response:
        page.get_by_test_id("btn-merge-now").click()
    assert response.value.status == 200
    toast = page.locator(".toast").first
    expect(toast).to_contain_text("Still pending")
    text = toast.inner_text()
    assert "missing_dn_document" not in text, text
    assert "_" not in text, text


def test_force_merge_toast_is_human(page: Page, manifest):
    sc = manifest["scenarios"]["force_merge_validation_target"]
    page.goto(f"/po_sets/{sc['id']}/view")
    page.get_by_test_id("btn-force-merge").click()
    page.locator("#force-merge-justification").fill(
        "Release gate: the operator accepts the short delivery and records why."
    )
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/force_merge")) as response:
        page.get_by_test_id("btn-confirm-force-merge").click()
    assert response.value.status == 200, response.value.text()

    toast = page.locator(".toast-success").first
    expect(toast).to_contain_text("Force merge completed")
    expect(toast).to_contain_text("audit log recorded")
    assert "C:\\" not in toast.inner_text()


def test_customs_toggle_toast_is_human(page: Page, manifest, db):
    sc = manifest["scenarios"]["customs_toggle_audit_target"]
    page.goto(f"/po_sets/{sc['id']}/view")
    button = page.get_by_test_id("btn-toggle-customs")
    expect(button).to_contain_text("OFF")
    with page.expect_response(
        re.compile(rf"/po_sets/{sc['id']}/toggle_customs")
    ) as response:
        button.click()
    assert response.value.status == 200, response.value.text()

    toast = page.locator(".toast-info").first
    expect(toast).to_contain_text("Customs requirement setting updated")
    assert "_" not in toast.inner_text()
    stored = db.execute(
        "select has_customs_toggle from po_sets where id=?", (sc["id"],)
    ).fetchone()[0]
    assert stored == 1
