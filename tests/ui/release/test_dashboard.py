"""Dashboard tabs, counts, rows and polling match the database."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from release_helpers import (
    DASHBOARD_TABS,
    active_set_ids,
    active_sets,
    number_in,
    set_row,
)


@pytest.mark.parametrize("status", list(DASHBOARD_TABS))
def test_each_tab_shows_exactly_that_status(page: Page, db, status: str):
    expected = active_set_ids(db, status)
    page.goto(f"/dashboard?status={status}")
    rows = page.get_by_test_id("po-row")
    expect(rows).to_have_count(len(expected))
    seen = set()
    for index in range(rows.count()):
        row = rows.nth(index)
        assert row.get_attribute("data-status") == status
        seen.add(int(row.get_attribute("data-set-id")))
    assert seen == expected, f"{status}: seen {seen}, expected {expected}"
    assert number_in(page.get_by_test_id(DASHBOARD_TABS[status])) == len(expected)


def test_all_tab_and_kpi_cards_match_the_database(page: Page, db):
    sets = active_sets(db)
    counts = {}
    for ps in sets:
        counts[ps["status"]] = counts.get(ps["status"], 0) + 1

    page.goto("/dashboard")
    expect(page.get_by_test_id("po-row")).to_have_count(len(sets))
    assert number_in(page.get_by_test_id("kpi-total")) == len(sets)
    assert number_in(page.get_by_test_id("tab-all")) == len(sets)
    assert number_in(page.get_by_test_id("kpi-merged")) == counts.get("merged", 0)
    assert number_in(page.get_by_test_id("kpi-mismatched")) == counts.get("mismatched", 0)
    assert number_in(page.get_by_test_id("kpi-blocked-customs")) == counts.get(
        "blocked_customs", 0
    )
    assert number_in(page.get_by_test_id("kpi-quarantined")) == counts.get("quarantined", 0)
    assert number_in(page.get_by_test_id("tab-pending")) == counts.get("pending", 0)


def test_a_row_doc_count_matches_the_database(page: Page, db):
    page.goto("/dashboard")
    problems = []
    for row in page.get_by_test_id("po-row").all()[:8]:
        set_id = int(row.get_attribute("data-set-id"))
        expected = db.execute(
            "select count(*) from documents where po_set_id=? and archived_at is null",
            (set_id,),
        ).fetchone()[0]
        actual = number_in(row.get_by_test_id("row-doc-count"))
        if actual != expected:
            problems.append(f"set {set_id}: page {actual}, db {expected}")
    assert not problems, "\n".join(problems)


def test_the_five_second_poll_keeps_the_same_rows(page: Page, db):
    page.goto("/dashboard")
    rows = page.get_by_test_id("po-row")
    expect(rows).to_have_count(len(active_sets(db)))
    before = [row.get_attribute("data-set-id") for row in rows.all()]

    # The table is refreshed from /dashboard/table every 5 s. Wait for the real
    # response rather than sleeping for a guessed interval.
    with page.expect_response(re.compile(r"/dashboard/table"), timeout=9000):
        pass
    expect(rows).to_have_count(len(before))
    after = [row.get_attribute("data-set-id") for row in rows.all()]
    assert after == before, "the poll changed the visible rows"


def test_a_row_opens_its_own_set(page: Page, manifest):
    sc = manifest["scenarios"]["merged_basic"]
    page.goto("/dashboard")
    set_row(page, sc["id"]).get_by_role("link").first.click()
    page.wait_for_url(re.compile(rf"/po_sets/{sc['id']}/view"))
    expect(page.get_by_test_id("set-po")).to_have_text(sc["po_no"])


def test_the_status_dropdown_filters_too(page: Page, db):
    expected = active_set_ids(db, "pending")
    page.goto("/dashboard")
    page.locator("#status-filter").select_option("pending")
    page.wait_for_url(re.compile(r"status=pending"))
    expect(page.get_by_test_id("po-row")).to_have_count(len(expected))
