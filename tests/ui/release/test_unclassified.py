"""Unmatched Files: the three Alpine sub-views and their counts."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from release_helpers import number_in, visible_rows, wait_for_alpine


def _open(page: Page) -> None:
    page.goto("/unclassified")
    wait_for_alpine(page)


def _failed_ids(db) -> set[int]:
    return {
        row[0]
        for row in db.execute(
            "select id from documents "
            "where extraction_status='failed' "
            "or coalesce(raw_extraction_json,'') like '%\"ingest_rejected\"%'"
        )
    }


def _visible_ids(page: Page) -> set[int]:
    ids = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('[data-testid="unclassified-row"]'))
          .filter((el) => el.offsetParent !== null)
          .map((el) => Number(el.dataset.docId))
        """
    )
    return {int(doc_id) for doc_id in ids}


def _select_subview(page: Page, test_id: str) -> int:
    """Click a sub-view and wait until Alpine has actually shown its rows.

    Clicking sets Alpine state; x-show applies on the next tick. Reading the
    visible rows synchronously would race the framework and see the old view.
    The count badge is the contract, so wait until the visible rows equal it.
    """
    expected = number_in(page.get_by_test_id(test_id))
    page.get_by_test_id(test_id).click()
    expect(page.get_by_test_id(test_id)).to_have_class(re.compile(r"\bactive\b"))
    page.wait_for_function(
        "expected => Array.from(document.querySelectorAll('[data-testid=\"unclassified-row\"]'))"
        ".filter((el) => el.offsetParent !== null).length === expected",
        arg=expected,
    )
    return expected


@pytest.mark.parametrize(
    "test_id",
    ["tab-subview-all", "tab-subview-needs-po", "tab-subview-failed"],
)
def test_each_subview_count_equals_its_visible_rows(page: Page, test_id: str):
    _open(page)
    expected = _select_subview(page, test_id)
    expect(visible_rows(page, "unclassified-row")).to_have_count(expected)


def test_all_subview_shows_every_held_document(page: Page):
    _open(page)
    expected = _select_subview(page, "tab-subview-all")
    assert expected == page.get_by_test_id("unclassified-row").count()
    assert len(_visible_ids(page)) == expected
    assert expected > 0, "the seeded environment should hold documents"


def test_needs_po_excludes_failed_and_rejected(page: Page, db):
    _open(page)
    _select_subview(page, "tab-subview-needs-po")
    visible = _visible_ids(page)
    failed = _failed_ids(db)
    overlap = visible & failed
    assert not overlap, f"failed/rejected documents shown as actionable: {overlap}"
    assert visible, "the seeded environment should have at least one actionable document"


def test_failed_subview_contains_only_failed_or_rejected(page: Page, db):
    _open(page)
    _select_subview(page, "tab-subview-failed")
    visible = _visible_ids(page)
    failed = _failed_ids(db)
    assert visible, "the seeded environment should have at least one failed document"
    assert visible <= failed, f"non-failed documents shown in the failed tab: {visible - failed}"


def test_set_aside_updates_header_and_tab_counts(page: Page, manifest):
    """A row move must update the server-rendered counts, not only the row."""
    held = manifest["held"]["release_count_target"]
    _open(page)
    header = page.get_by_test_id("unclassified-count")
    before_header = number_in(header)
    before_all = number_in(page.get_by_test_id("tab-subview-all"))
    before_needs = number_in(page.get_by_test_id("tab-subview-needs-po"))
    before_failed = number_in(page.get_by_test_id("tab-subview-failed"))
    assert before_needs > 0

    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(re.compile(rf"/unclassified/{held['id']}/set_aside")) as response:
        row.get_by_test_id("btn-set-aside").click()
    assert response.value.status == 200

    expect(page.get_by_test_id("set-aside-result")).to_contain_text("Moved to the quarantine folder")
    expect(header).to_contain_text(f"{before_header - 1} awaiting classification")
    assert number_in(page.get_by_test_id("tab-subview-all")) == before_all - 1
    assert number_in(page.get_by_test_id("tab-subview-needs-po")) == before_needs - 1
    assert number_in(page.get_by_test_id("tab-subview-failed")) == before_failed


def test_reclassify_updates_header_and_tab_counts(page: Page, manifest):
    """The reclassify path must update the counts too, not only the row."""
    held = manifest["held"]["release_reclassify_count_target"]
    _open(page)
    header = page.get_by_test_id("unclassified-count")
    before_header = number_in(header)
    before_all = number_in(page.get_by_test_id("tab-subview-all"))
    before_needs = number_in(page.get_by_test_id("tab-subview-needs-po"))
    before_failed = number_in(page.get_by_test_id("tab-subview-failed"))

    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    row.get_by_test_id("select-doc-type").select_option("DN")
    row.get_by_test_id("input-po-no").fill("7000003")
    route = row.get_by_test_id("btn-route")
    expect(route).to_be_enabled()
    with page.expect_response(
        re.compile(rf"/unclassified/{held['id']}/reclassify")
    ) as response:
        route.click()
    assert response.value.status == 200

    expect(header).to_contain_text(f"{before_header - 1} awaiting classification")
    assert number_in(page.get_by_test_id("tab-subview-all")) == before_all - 1
    assert number_in(page.get_by_test_id("tab-subview-needs-po")) == before_needs - 1
    assert number_in(page.get_by_test_id("tab-subview-failed")) == before_failed


def test_switching_subviews_does_not_reload_the_page(page: Page):
    _open(page)
    page.evaluate("window.__release_marker = 'kept'")
    _select_subview(page, "tab-subview-needs-po")
    _select_subview(page, "tab-subview-failed")
    _select_subview(page, "tab-subview-all")
    assert page.evaluate("window.__release_marker") == "kept"
    assert page.url.endswith("/unclassified")


def test_default_subview_is_the_actionable_inbox(page: Page):
    _open(page)
    expect(page.get_by_test_id("tab-subview-needs-po")).to_have_class(re.compile(r"\bactive\b"))


def test_subviews_are_announced_as_tabs(page: Page):
    _open(page)
    expect(page.get_by_test_id("tab-subview-needs-po")).to_have_attribute("role", "tab")
    expect(page.get_by_test_id("tab-subview-needs-po")).to_have_attribute("aria-selected", "true")
    _select_subview(page, "tab-subview-failed")
    expect(page.get_by_test_id("tab-subview-failed")).to_have_attribute("aria-selected", "true")
    expect(page.get_by_test_id("tab-subview-needs-po")).to_have_attribute("aria-selected", "false")
