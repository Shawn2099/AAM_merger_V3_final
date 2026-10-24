"""Dashboard (UI-D-*): KPI cards, tabs, rows. Reference implementation for the pattern."""

import re
from pathlib import Path

from playwright.sync_api import Page, expect

STATUS_TEXT = {
    "merged": "Merged",
    "pending": "Pending / Waiting",
    "mismatched": "Needs Review",
    "blocked_customs": "Awaiting Customs",
    "quarantined": "Quarantined",
}


def test_ui_d_01_every_scenario_row_is_listed_with_its_status_and_doc_count(page: Page, manifest):
    page.goto("/dashboard")
    rows = page.get_by_test_id("po-row")
    expect(rows).to_have_count(len(manifest["scenarios"]))
    problems = []
    for name, sc in manifest["scenarios"].items():
        row = page.locator(f'[data-testid="po-row"][data-set-id="{sc["id"]}"]')
        if row.count() != 1:
            problems.append(f"{name}: row for set {sc['id']} missing")
            continue
        text = row.inner_text()
        if sc["po_no"] not in text:
            problems.append(f"{name}: PO {sc['po_no']} not shown")
        if STATUS_TEXT[sc["status"]] not in text:
            problems.append(f"{name}: status {sc['status']} not shown in {text[:80]!r}")
        if row.get_attribute("data-status") != sc["status"]:
            problems.append(
                f"{name}: data-status {row.get_attribute('data-status')} != {sc['status']}"
            )
        if row.get_by_test_id("row-doc-count").inner_text().strip() != str(len(sc["docs"])):
            problems.append(f"{name}: doc count differs from {len(sc['docs'])}")
        if sc["reason"] and sc["reason"][:40] not in row.get_by_test_id("row-reason").inner_text():
            problems.append(f"{name}: reason text differs")
    assert not problems, "\n".join(problems)


def test_ui_d_02_kpi_cards_and_tabs_match_the_database(page: Page, manifest):
    counts = manifest["counts"]
    total = len(manifest["scenarios"])
    page.goto("/dashboard")
    expect(page.get_by_test_id("kpi-total")).to_contain_text(str(total))
    expect(page.get_by_test_id("kpi-merged")).to_contain_text(str(counts.get("merged", 0)))
    expect(page.get_by_test_id("kpi-quarantined")).to_contain_text(
        str(counts.get("quarantined", 0))
    )
    expect(page.get_by_test_id("tab-all")).to_contain_text(str(total))
    expect(page.get_by_test_id("tab-pending")).to_contain_text(str(counts.get("pending", 0)))
    expect(page.get_by_test_id("tab-mismatched")).to_contain_text(str(counts.get("mismatched", 0)))
    expect(page.get_by_test_id("tab-blocked-customs")).to_contain_text(
        str(counts.get("blocked_customs", 0))
    )


def test_ui_d_03_a_status_tab_shows_only_that_status(page: Page, manifest):
    page.goto("/dashboard")
    page.get_by_test_id("tab-quarantined").click()
    page.wait_for_url(re.compile(r"status=quarantined"))
    rows = page.get_by_test_id("po-row")
    expect(rows).to_have_count(manifest["counts"]["quarantined"])
    for i in range(rows.count()):
        assert rows.nth(i).get_attribute("data-status") == "quarantined"


def test_ui_d_04_clicking_a_po_opens_its_set_page(page: Page, manifest):
    sc = manifest["scenarios"]["merged_basic"]
    page.goto("/dashboard")
    page.locator(f'[data-testid="po-row"][data-set-id="{sc["id"]}"] a.po-link').click()
    page.wait_for_url(re.compile(rf"/po_sets/{sc['id']}/view"))
    expect(page.get_by_test_id("set-po")).to_have_text(sc["po_no"])


def test_ui_d_05_a_merged_row_offers_a_working_pdf_download(page: Page, manifest):
    sc = manifest["scenarios"]["merged_basic"]
    page.goto("/dashboard")
    row = page.locator(f'[data-testid="po-row"][data-set-id="{sc["id"]}"]')
    with page.expect_download() as info:
        row.get_by_text("PDF").click()
    path = info.value.path()
    assert Path(path).read_bytes()[:4] == b"%PDF"
