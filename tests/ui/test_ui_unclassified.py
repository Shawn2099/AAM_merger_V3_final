"""Unclassified page (UI-U-*), Quarantine page (UI-Q-*), Audit page (UI-A-*)."""

import re

from playwright.sync_api import Page, expect


def test_ui_u_01_every_held_document_is_listed_with_a_reason_to_wait(page: Page, manifest):
    page.goto("/unclassified")
    rows = page.get_by_test_id("unclassified-row")
    expect(rows).to_have_count(len(manifest["held"]))
    for name, doc in manifest["held"].items():
        row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{doc["id"]}"]')
        expect(row, name).to_contain_text(doc["file"])


def test_ui_u_02_failed_and_rejected_files_are_marked_failed(page: Page, manifest):
    page.goto("/unclassified")
    failed = page.locator(
        f'[data-testid="unclassified-row"][data-doc-id="{manifest["held"]["held_failed"]["id"]}"]'
    )
    expect(failed).to_contain_text("Failed")
    rejected = page.locator(
        f'[data-testid="unclassified-row"][data-doc-id="{manifest["held"]["rejected_on_arrival"]["id"]}"]'
    )
    expect(rejected).to_contain_text("Failed")
    expect(rejected).to_contain_text("Rejected on arrival")


def test_ui_u_03_route_button_is_disabled_until_a_type_is_chosen(page: Page, manifest):
    page.goto("/unclassified")
    row = page.locator(
        f'[data-testid="unclassified-row"][data-doc-id="{manifest["held"]["held_dn_no_po"]["id"]}"]'
    )
    expect(row.get_by_test_id("btn-route")).to_be_disabled()
    row.get_by_test_id("select-doc-type").select_option("DN")
    expect(row.get_by_test_id("btn-route")).to_be_enabled()


def test_ui_u_04_reclassifying_a_dn_with_a_po_number_places_it_in_that_set(
    page: Page, manifest, db
):
    held = manifest["held"]["held_reclassify_target"]
    target = manifest["scenarios"]["pending_no_dn"]
    assert held["mutable"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    row.get_by_test_id("select-doc-type").select_option("DN")
    row.get_by_test_id("input-po-no").fill(target["po_no"])
    with page.expect_response(re.compile(rf"/unclassified/{held['id']}/reclassify")) as resp:
        row.get_by_test_id("btn-route").click()
    assert resp.value.status == 200, resp.value.text()
    placed = db.execute(
        "select po_set_id, doc_type from documents where id=?", (held["id"],)
    ).fetchone()
    assert placed["po_set_id"] == target["id"] and placed["doc_type"] == "DN"


def test_ui_q_01_quarantine_page_lists_exactly_the_quarantined_sets(page: Page, manifest):
    """Order-independent: archive_target may already have been archived by another test."""
    page.goto("/quarantine")
    quarantined = {
        sc["id"] for sc in manifest["scenarios"].values() if sc["status"] == "quarantined"
    }
    archived_by_tests = {manifest["scenarios"]["archive_target"]["id"]}
    rows = page.get_by_test_id("quarantine-row")
    seen = {int(rows.nth(i).get_attribute("data-set-id")) for i in range(rows.count())}
    assert seen <= quarantined, f"listed but not quarantined: {seen - quarantined}"
    assert quarantined - archived_by_tests <= seen, (
        f"missing: {quarantined - archived_by_tests - seen}"
    )


def test_ui_a_01_audit_page_shows_the_seeded_row_and_its_justification(page: Page):
    """Other tests add newer audit rows, so find the seeded one by its text."""
    page.goto("/audit")
    seeded = page.get_by_test_id("audit-row").filter(has_text="Seeded row for the UI tests")
    expect(seeded).to_have_count(1)


def test_ui_x_01_a_hostile_filename_is_shown_as_text_not_executed(page: Page, manifest):
    """Filenames come from outside. HTML in one must be escaped on every page that lists it."""
    held = manifest["held"]["held_hostile_name"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    expect(row).to_contain_text("<b>bold</b>")  # the markup is visible as literal text
    assert page.evaluate("window.__xss") is None  # and the onerror handler never ran
    expect(row.locator("img[src='x']")).to_have_count(0)
    expect(row.locator("b")).to_have_count(0)
