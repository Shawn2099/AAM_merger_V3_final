"""Flagged / quarantine review surface after the rename."""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from release_helpers import quarantine_row


def test_flagged_review_title_and_badge(page: Page):
    page.goto("/quarantine")
    body = page.locator("body").inner_text()
    assert "Flagged Review" in body
    assert "(Quarantine)" in body
    first_badge = page.get_by_test_id("quarantine-row").first.locator(".badge").first
    expect(first_badge).to_have_text("🚨 Flagged")


def test_archive_modal_is_named_for_the_quarantine_folder(page: Page, manifest, db):
    sc = manifest["scenarios"]["quarantined_po_no_lines"]
    page.goto("/quarantine")
    row = quarantine_row(page, sc["id"])
    assert row.count() == 1, "the quarantined scenario is missing from the desk"

    row.get_by_test_id("btn-archive-row").click()
    modal = row.get_by_test_id("archive-row-modal")
    expect(modal).to_have_class(re.compile(r"\bopen\b"))
    expect(modal).to_contain_text("Move to Quarantine Folder")
    expect(modal).to_contain_text("archive")
    expect(modal.get_by_test_id("btn-confirm-archive-row")).to_contain_text(
        "Quarantine Folder"
    )

    row.get_by_test_id("btn-cancel-archive-row").click()
    expect(modal).not_to_have_class(re.compile(r"\bopen\b"))
    archived = db.execute(
        "select archived_at from po_sets where id=?", (sc["id"],)
    ).fetchone()[0]
    assert archived is None, "Cancel archived the set"


def test_cancel_sends_no_delete_request(page: Page, manifest):
    sc = manifest["scenarios"]["quarantined_po_no_lines"]
    deletes: list[str] = []
    page.on("request", lambda r: deletes.append(r.url) if r.method == "DELETE" else None)
    page.goto("/quarantine")
    row = quarantine_row(page, sc["id"])
    row.get_by_test_id("btn-archive-row").click()
    row.get_by_test_id("btn-cancel-archive-row").click()
    assert deletes == [], f"Cancel sent a DELETE: {deletes}"


def test_files_set_aside_is_labeled_and_previewable(page: Page):
    page.goto("/quarantine")
    section = page.get_by_test_id("files-set-aside")
    if section.count() == 0:
        import pytest

        pytest.skip("no files set aside in this environment")
    expect(section).to_contain_text("Files set aside")
    button = section.get_by_test_id("btn-set-aside-quarantine").first
    expect(button).to_contain_text("Quarantine Folder")
    expect(section.get_by_test_id("btn-preview-set-aside").first).to_contain_text("Preview")
