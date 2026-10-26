"""The PDF preview drawer is available on every document surface."""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from release_helpers import close_preview, open_preview, visible_rows, wait_for_alpine


def test_preview_from_the_set_detail(page: Page, manifest, evidence):
    sc = manifest["scenarios"]["merged_basic"]
    page.goto(f"/po_sets/{sc['id']}/view")
    row = page.locator(f'[data-testid="doc-row"][data-doc-id="{sc["docs"][0]["id"]}"]')
    open_preview(page, row.get_by_test_id("btn-preview"), sc["docs"][0]["file"])
    close_preview(page)
    # Closing the drawer aborts the iframe request; that is expected.
    evidence.assert_clean(allow=("favicon.ico", "ERR_ABORTED"))


def test_preview_from_unmatched_files(page: Page, evidence):
    page.goto("/unclassified")
    wait_for_alpine(page)
    row = visible_rows(page, "unclassified-row").first
    button = row.get_by_test_id("btn-preview")
    expected = button.get_attribute("data-preview-name")
    assert expected, "the preview button carries no filename"
    open_preview(page, button, expected)
    close_preview(page)
    evidence.assert_clean(allow=("favicon.ico", "ERR_ABORTED"))


def test_preview_from_files_set_aside(page: Page, evidence):
    page.goto("/quarantine")
    section = page.get_by_test_id("files-set-aside")
    if section.count() == 0 or section.get_by_test_id("btn-preview-set-aside").count() == 0:
        import pytest

        pytest.skip("no files set aside in this environment")

    # The set-aside list includes a rejected non-PDF file; pick a real PDF so
    # this checks the preview feature, not the friendly "cannot preview" page.
    buttons = section.get_by_test_id("btn-preview-set-aside")
    button = None
    for index in range(buttons.count()):
        candidate = buttons.nth(index)
        name = candidate.get_attribute("data-preview-name") or ""
        if name and "fake-not-a-pdf" not in name:
            button = candidate
            break
    if button is None:
        import pytest

        pytest.skip("no previewable PDF in the set-aside list")
    open_preview(page, button, button.get_attribute("data-preview-name"))
    close_preview(page)
    evidence.assert_clean(allow=("favicon.ico", "ERR_ABORTED"))


def test_the_close_button_also_closes_the_drawer(page: Page, manifest):
    sc = manifest["scenarios"]["merged_basic"]
    page.goto(f"/po_sets/{sc['id']}/view")
    open_preview(page, page.get_by_test_id("btn-preview").first, sc["docs"][0]["file"])
    page.get_by_test_id("btn-drawer-close").click()
    expect(page.get_by_test_id("pdf-drawer")).not_to_have_class(re.compile(r"\bopen\b"))


def test_preview_does_not_change_the_page(page: Page, db, manifest):
    sc = manifest["scenarios"]["merged_basic"]
    before = db.execute("select status, merged_output_path from po_sets where id=?", (sc["id"],)).fetchone()
    page.goto(f"/po_sets/{sc['id']}/view")
    open_preview(page, page.get_by_test_id("btn-preview").first, None)
    close_preview(page)
    after = db.execute("select status, merged_output_path from po_sets where id=?", (sc["id"],)).fetchone()
    assert tuple(after) == tuple(before)
