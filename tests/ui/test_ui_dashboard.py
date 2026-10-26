"""Dashboard (UI-D-*): KPI cards, tabs, rows. Reference implementation for the pattern."""

import re
from pathlib import Path

from playwright.sync_api import Page, expect

STATUS_TEXT = {
    "merged": ("Merged & Ready", "Merged"),
    "pending": ("Awaiting Documents", "Pending / Waiting"),
    "mismatched": ("Quantity Mismatch", "Partial Delivery", "Needs Review", "Waiting (part delivered)"),
    "blocked_customs": ("Awaiting Customs/Shipping Docs", "Awaiting Customs"),
    "quarantined": ("Exception (Rejected)", "Quarantined"),
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
        raw_words = STATUS_TEXT[ps["status"]]
        status_words = list(raw_words) if isinstance(raw_words, (list, tuple)) else [raw_words]
        if ps["status"] == "mismatched":
            status_words.append("Waiting (part delivered)")
        if not any(word in text for word in status_words):
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


def test_ui_d_07_an_unknown_status_filter_shows_a_page_not_json(page: Page):
    """D-07: a bad `?status=` link is a navigation, not an API call."""
    response = page.goto("/dashboard?status=nonsense")
    assert response is not None and response.status == 422
    assert "text/html" in response.headers["content-type"]
    expect(page.locator("nav.app-navbar")).to_be_visible()
    expect(page.get_by_text("not recognised", exact=False)).to_be_visible()
    page.get_by_role("link", name="Back to dashboard").click()
    page.wait_for_url(re.compile(r"/dashboard$"))


def _held_document_count(db) -> int:
    """The holding area query `_holding_area_query` counts (managed copy).

    Every document not in a set and not archived, except a split parent whose
    split completed (its children own the content).
    """
    return db.execute(
        "select count(*) from documents where archived_at is null and po_set_id is null "
        "and (coalesce(is_split_parent, 0) = 0 or (split_completed_at is null "
        "and extraction_status = 'failed'))"
    ).fetchone()[0]


def test_ui_d_06_the_status_dropdown_filters_like_the_tabs_and_keeps_the_choice(page: Page, db):
    """D-06: the select under the search box is a second way into the tabs' filter."""
    sets = _active_sets(db)
    wanted = {sid for sid, ps in sets.items() if ps["status"] == "mismatched"}
    page.goto("/dashboard")
    select = page.locator("#status-filter")
    expect(select).to_have_value("")
    with page.expect_navigation():
        select.select_option("mismatched")
    assert re.search(r"status=mismatched", page.url), page.url
    # The choice survives the page it caused.
    expect(select).to_have_value("mismatched")
    rows = page.get_by_test_id("po-row")
    expect(rows).to_have_count(len(wanted))
    shown = {int(rows.nth(i).get_attribute("data-set-id")) for i in range(rows.count())}
    assert shown == wanted, f"dropdown showed {shown}; mismatched sets are {wanted}"
    for i in range(rows.count()):
        assert rows.nth(i).get_attribute("data-status") == "mismatched"

    # "All statuses" goes back to the whole list, and the select follows.
    with page.expect_navigation():
        select.select_option("")
    expect(select).to_have_value("")
    expect(page.get_by_test_id("po-row")).to_have_count(len(_active_sets(db)))


def test_ui_d_08_the_table_polls_itself_without_a_reload(page: Page):
    """D-08: the dashboard refreshes its rows with an HTMX poll (every 5 s)."""
    page.goto("/dashboard")
    with page.expect_response(re.compile(r"/dashboard/table"), timeout=7000) as resp:
        pass
    assert resp.value.status == 200
    assert resp.value.request.headers.get("hx-request") == "true"
    assert "text/html" in resp.value.headers["content-type"]
    # It is a fragment swap, not a navigation: the page never left the dashboard.
    assert page.url.endswith("/dashboard")


def test_ui_d_09_the_nav_reaches_each_page_and_counts_the_held_documents(page: Page, db):
    """D-09: every nav link opens its own page, and the badge is the holding area."""
    held = _held_document_count(db)
    page.goto("/dashboard")
    badge = page.get_by_test_id("nav-unclassified")
    numbers = re.findall(r"\d+", badge.inner_text())
    if held:
        assert numbers and int(numbers[-1]) == held, badge.inner_text()
    else:
        assert not numbers, (
            f"the badge shows a count with an empty holding area: {badge.inner_text()}"
        )

    pages = [
        ("nav-dashboard", re.compile(r"/dashboard$"), "Dashboard — AAM Merger V3"),
        (
            "nav-unclassified",
            re.compile(r"/unclassified$"),
            "Unclassified Documents — AAM Merger V3",
        ),
        ("nav-quarantine", re.compile(r"/quarantine$"), "Quarantine Review — AAM Merger V3"),
        ("nav-audit", re.compile(r"/audit$"), "Audit Log — AAM Merger V3"),
        ("nav-merger", re.compile(r"/manual/merger$"), "Merge PDF files — AAM Merger V3"),
    ]
    for testid, url, title in pages:
        page.get_by_test_id(testid).click()
        page.wait_for_url(url)
        assert page.title() == title, f"{url.pattern} has title {page.title()!r}"
        if testid == "nav-unclassified":
            # The page's own count is the same number, and it is the row count.
            expect(page.get_by_test_id("unclassified-count")).to_have_text(
                f"{held} awaiting classification"
            )
            expect(page.get_by_test_id("unclassified-row")).to_have_count(held)

    # /health is a JSON endpoint but it is in the nav, so it must answer too.
    page.get_by_role("link", name=re.compile("Service Online")).click()
    page.wait_for_url(re.compile(r"/health$"))
    expect(page.get_by_text("status", exact=False).first).to_be_visible()


def test_ui_d_10_the_mapped_set_says_which_rule_paired_the_lines(page: Page, manifest):
    """D-10: a merge that only worked because line numbers were paired must say so."""
    sc = manifest["scenarios"]["merged_mapped_x10"]
    assert sc["line_mapping_rule"] == "x10"
    page.goto("/dashboard")
    row = page.locator(f'[data-testid="po-row"][data-set-id="{sc["id"]}"]')
    expect(row).to_contain_text("lines mapped (x10)")


def test_ui_d_11_the_customs_blocked_row_names_the_requirement(page: Page, manifest, db):
    """D-11: a customs hold must not look like an ordinary wait."""
    sc = manifest["scenarios"]["awaiting_customs"]
    reason = db.execute("select reconcile_reason from po_sets where id=?", (sc["id"],)).fetchone()[
        0
    ]
    page.goto("/dashboard")
    row = page.locator(f'[data-testid="po-row"][data-set-id="{sc["id"]}"]')
    expect(row).to_contain_text("Awaiting Customs")
    shown = row.get_by_test_id("row-reason").inner_text()
    assert reason[:40] in shown, f"row reason is {shown!r}, DB starts {reason[:40]!r}"
    assert "CUSTOMS" in shown and "SHIPPING" in shown, shown


def test_ui_d_13_a_settling_row_is_pending_and_says_why(page: Page, manifest, db):
    """D-13: every gate passed; only the settle clock is left, and the row says so."""
    from app.services.reconciliation import SETTLING_PREFIX

    sc = manifest["scenarios"]["settling"]
    reason = db.execute("select reconcile_reason from po_sets where id=?", (sc["id"],)).fetchone()[
        0
    ]
    assert reason.startswith(SETTLING_PREFIX), reason
    page.goto("/dashboard")
    row = page.locator(f'[data-testid="po-row"][data-set-id="{sc["id"]}"]')
    assert row.get_attribute("data-status") == "pending"
    expect(row).to_contain_text(re.compile(r"Awaiting Documents|Pending / Waiting"))
    shown = row.get_by_test_id("row-reason").inner_text()
    assert reason[:40] in shown, f"row reason is {shown!r}, DB starts {reason[:40]!r}"
    assert re.search(r"merges after \d{2}:\d{2}", shown), shown


def test_ui_d_14_a_row_placed_by_its_dn_number_says_why(page: Page, manifest, db):
    """D-14: a number-only placement is a human decision, and the row says so."""
    sc = manifest["scenarios"]["placed_by_number_only"]
    reason = db.execute("select reconcile_reason from po_sets where id=?", (sc["id"],)).fetchone()[
        0
    ]
    page.goto("/dashboard")
    row = page.locator(f'[data-testid="po-row"][data-set-id="{sc["id"]}"]')
    assert row.get_attribute("data-status") == "pending"
    shown = row.get_by_test_id("row-reason").inner_text()
    assert reason[:40] in shown, f"row reason is {shown!r}, DB starts {reason[:40]!r}"
    assert "was placed in this set only because its DN number matches" in shown, shown


def test_ui_d_12_the_scan_button_starts_a_sync_and_says_so(page: Page, scan_env):
    """D-12: pressing Scan posts /sync, reports it, and stays on the dashboard.

    Runs on a SECOND environment: the route runs the real sync flow inside the
    web process, which re-reconciles every open set with the poller's settle
    window off (`SETTLE_MINUTES` is only set by the poller). On the shared
    environment that merges `settling_target`, i.e. it destroys the state S-22
    checks. This test therefore drags its own database.
    """
    base = scan_env.base
    page.goto(base + "/dashboard")
    button = page.get_by_test_id("btn-scan-input")
    expect(button).to_be_enabled()
    with page.expect_response(re.compile(r"/sync$")) as resp:
        button.click()
    assert resp.value.status == 200, resp.value.text()
    assert resp.value.request.method == "POST"
    assert resp.value.json()["status"] == "sync started"

    # The only feedback the operator gets is this toast; the sync is a
    # background job, so the page must not navigate or break.
    expect(page.locator(".toast-success")).to_contain_text("Sync scan triggered")
    expect(page.locator("nav.app-navbar")).to_be_visible()
    expect(page.get_by_test_id("po-row").first).to_be_visible()
    assert page.url.endswith("/dashboard")
