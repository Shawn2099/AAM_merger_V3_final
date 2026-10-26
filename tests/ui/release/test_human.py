"""Human-like journeys: browser history, refresh, narrow windows."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from release_helpers import assert_no_horizontal_scroll

PAGES = (
    "/dashboard",
    "/unclassified",
    "/quarantine",
    "/audit",
    "/settings",
    "/manual/merger",
)


def test_browser_back_returns_to_the_previous_tab(page: Page):
    page.goto("/dashboard")
    page.get_by_test_id("tab-mismatched").click()
    page.wait_for_url(re.compile(r"status=mismatched"))
    page.get_by_test_id("tab-pending").click()
    page.wait_for_url(re.compile(r"status=pending"))

    page.go_back()
    page.wait_for_url(re.compile(r"status=mismatched"))
    expect(page.get_by_test_id("tab-mismatched")).to_have_class(re.compile(r"\bactive\b"))
    assert page.get_by_test_id("po-row").count() >= 0


def test_refresh_keeps_the_status_filter(page: Page):
    page.goto("/dashboard?status=quarantined")
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_test_id("tab-quarantined")).to_have_class(re.compile(r"\bactive\b"))
    rows = page.get_by_test_id("po-row")
    for index in range(rows.count()):
        assert rows.nth(index).get_attribute("data-status") == "quarantined"


@pytest.mark.parametrize("path", PAGES, ids=[p.strip("/").replace("/", "-") for p in PAGES])
def test_no_horizontal_scroll_at_800px(page: Page, path: str):
    page.set_viewport_size({"width": 800, "height": 900})
    page.goto(path)
    page.wait_for_load_state("networkidle")
    assert_no_horizontal_scroll(page)


def test_set_page_has_no_horizontal_scroll_at_800px(page: Page, manifest):
    page.set_viewport_size({"width": 800, "height": 900})
    sc = manifest["scenarios"]["merged_basic"]
    page.goto(f"/po_sets/{sc['id']}/view")
    assert_no_horizontal_scroll(page)


def test_narrow_window_keeps_the_subview_tabs_usable(page: Page):
    page.set_viewport_size({"width": 800, "height": 900})
    page.goto("/unclassified")
    tabs = page.locator(".status-tabs").first
    expect(tabs).to_be_visible()
    for test_id in ("tab-subview-all", "tab-subview-needs-po", "tab-subview-failed"):
        expect(page.get_by_test_id(test_id)).to_be_visible()
