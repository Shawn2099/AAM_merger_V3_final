"""Zero-state copy on a completely empty database."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from release_helpers import FAVICON

EMPTY_STATES = [
    (
        "mismatched",
        "All Clear: No Quantity Mismatches",
        "No quantity discrepancies found across active purchase orders.",
    ),
    (
        "quarantined",
        "Clean Pipeline: No Flagged Sets",
        "No purchase orders are currently flagged for critical exceptions or conflicts.",
    ),
    (
        "blocked_customs",
        "Clearance Complete: No Customs Holds",
        "No purchase orders are currently blocked by missing customs clearance or shipping documents.",
    ),
    (
        "pending",
        "All Caught Up: No Orders Awaiting Documents",
        "No purchase orders are currently waiting for vendor delivery notes or invoices.",
    ),
    (
        "merged",
        "No Merged & Ready Orders Yet",
        "Packets will appear here once all line item quantities match and customs documents are attached.",
    ),
]


@pytest.mark.parametrize("status,heading,detail", EMPTY_STATES, ids=[s[0] for s in EMPTY_STATES])
def test_empty_tab_shows_the_approved_copy(
    page: Page, empty_base_url: str, evidence, status: str, heading: str, detail: str
):
    response = page.goto(f"{empty_base_url}/dashboard?status={status}")
    assert response is not None and response.status == 200
    page.wait_for_load_state("networkidle")
    expect(page.get_by_test_id("po-row")).to_have_count(0)
    body = page.locator("body").inner_text()
    assert heading in body, f"{status}: missing heading {heading!r}"
    assert detail in body, f"{status}: missing detail {detail!r}"
    evidence.assert_clean(allow=FAVICON)


def test_empty_all_tab_explains_how_to_start(page: Page, empty_base_url: str):
    page.goto(f"{empty_base_url}/dashboard")
    expect(page.get_by_test_id("po-row")).to_have_count(0)
    body = page.locator("body").inner_text()
    assert "No PO Sets found" in body
    assert "Drop PDF invoices and delivery notes" in body


def test_empty_database_still_has_zeroed_kpis_and_navigation(page: Page, empty_base_url: str):
    page.goto(f"{empty_base_url}/dashboard")
    for test_id in ("kpi-total", "kpi-merged", "kpi-mismatched", "kpi-quarantined"):
        expect(page.get_by_test_id(test_id)).to_be_visible()
    for href in ("/dashboard", "/unclassified", "/quarantine", "/audit", "/manual/merger"):
        assert page.locator(f'nav a[href="{href}"]').count() >= 1, href
