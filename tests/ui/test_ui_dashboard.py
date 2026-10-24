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


def _active_sets(db):
    """What the database holds NOW. Other tests archive or merge sets in the shared session
    environment, so expectations come from here and never from the build-time manifest."""
    return {
        r["id"]: r
        for r in db.execute(
            "select id, po_no_normalized as po, status, reconcile_reason as reason "
            "from po_sets where archived_at is null"
        )
    }


def _doc_count(db, set_id):
    return db.execute(
        "select count(*) from documents where po_set_id=? and archived_at is null", (set_id,)
    ).fetchone()[0]


def test_ui_d_01_every_active_set_row_is_listed_with_its_status_and_doc_count(page: Page, db):
    sets = _active_sets(db)
    page.goto("/dashboard")
    rows = page.get_by_test_id("po-row")
    expect(rows).to_have_count(len(sets))
    problems = []
    for set_id, ps in sets.items():
        row = page.locator(f'[data-testid="po-row"][data-set-id="{set_id}"]')
        if row.count() != 1:
            problems.append(f"set {set_id}: row missing")
            continue
        text = row.inner_text()
        if ps["po"] not in text:
            problems.append(f"set {set_id}: PO {ps['po']} not shown")
        if STATUS_TEXT[ps["status"]] not in text:
            problems.append(f"set {set_id}: status {ps['status']} not shown in {text[:80]!r}")
        if row.get_attribute("data-status") != ps["status"]:
            problems.append(f"set {set_id}: data-status is {row.get_attribute('data-status')}")
        if row.get_by_test_id("row-doc-count").inner_text().strip() != str(_doc_count(db, set_id)):
            problems.append(f"set {set_id}: doc count differs from the database")
        reason = ps["reason"]
        if reason and reason[:40] not in row.get_by_test_id("row-reason").inner_text():
            problems.append(f"set {set_id}: reason text differs")
    assert not problems, "\n".join(problems)


def _number_in(locator):
    """The whole number inside a card or tab ('12' never matches '1')."""
    match = re.search(r"\d+", locator.inner_text().replace("%", " "))
    assert match, locator.inner_text()
    return int(match.group())


def test_ui_d_02_kpi_cards_and_tabs_match_the_database(page: Page, db):
    sets = _active_sets(db)
    counts = {}
    for ps in sets.values():
        counts[ps["status"]] = counts.get(ps["status"], 0) + 1
    page.goto("/dashboard")
    assert _number_in(page.get_by_test_id("kpi-total")) == len(sets)
    assert _number_in(page.get_by_test_id("kpi-merged")) == counts.get("merged", 0)
    assert _number_in(page.get_by_test_id("kpi-quarantined")) == counts.get("quarantined", 0)
    assert _number_in(page.get_by_test_id("tab-all")) == len(sets)
    assert _number_in(page.get_by_test_id("tab-pending")) == counts.get("pending", 0)
    assert _number_in(page.get_by_test_id("tab-mismatched")) == counts.get("mismatched", 0)
    assert _number_in(page.get_by_test_id("tab-blocked-customs")) == counts.get(
        "blocked_customs", 0
    )


def test_ui_d_03_a_status_tab_shows_only_that_status(page: Page, db):
    wanted = sum(1 for ps in _active_sets(db).values() if ps["status"] == "quarantined")
    page.goto("/dashboard")
    page.get_by_test_id("tab-quarantined").click()
    page.wait_for_url(re.compile(r"status=quarantined"))
    rows = page.get_by_test_id("po-row")
    expect(rows).to_have_count(wanted)
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
