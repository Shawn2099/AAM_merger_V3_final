"""Known gaps found by the interactive release pass.

Each test fails today for a documented product/UX reason and is a strict xfail:
when the gap is fixed, the test XPASSes and the marker must be removed.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from release_helpers import pdf_bytes, quarantine_row


def test_archive_modal_names_the_real_destination(page: Page, manifest, harness_paths):
    sc = manifest["scenarios"]["quarantined_po_no_lines"]
    page.goto("/quarantine")
    row = quarantine_row(page, sc["id"])
    row.get_by_test_id("btn-archive-row").click()
    modal = row.get_by_test_id("archive-row-modal")
    expect(modal).to_have_class(re.compile(r"\bopen\b"))
    text = modal.inner_text()
    actual_folder = harness_paths["archive_folder"]
    assert actual_folder.name in text, text
    assert "data/quarantine/" not in text, text


def test_quarantined_reason_uses_the_flagged_word(page: Page, manifest):
    sc = manifest["scenarios"]["quarantined_unmatched_vendor_line"]
    page.goto(f"/po_sets/{sc['id']}/view")
    reason = page.get_by_test_id("reason-card").inner_text()
    assert "Why this set is flagged" in reason, reason
    assert "quarantined" not in reason.lower(), reason


def test_preview_close_returns_focus_to_its_trigger(page: Page, manifest):
    sc = manifest["scenarios"]["merged_basic"]
    page.goto(f"/po_sets/{sc['id']}/view")
    trigger = page.get_by_test_id("btn-preview").first
    trigger.click()
    expect(page.get_by_test_id("pdf-drawer")).to_have_class(re.compile(r"\bopen\b"))
    page.get_by_test_id("btn-drawer-close").click()
    expect(trigger).to_be_focused()


def test_upload_success_shows_an_explicit_confirmation(page: Page, manifest):
    sc = manifest["scenarios"]["release_upload_target"]
    page.goto(f"/po_sets/{sc['id']}/view")
    form = page.get_by_test_id("upload-form")
    form.locator('input[type="file"]').set_input_files(
        {"name": "SHIPPING-confirm.pdf", "mimeType": "application/pdf", "buffer": pdf_bytes()}
    )
    form.locator('select[name="doc_type"]').select_option("SHIPPING")
    with page.expect_navigation():
        form.get_by_role("button", name="Upload & Attach").click()
    assert (
        page.get_by_test_id("notice").count() >= 1
        or page.locator(".toast").count() >= 1
    ), "no success message after Upload & Attach"
