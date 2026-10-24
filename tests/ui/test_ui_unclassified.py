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


def test_ui_q_01_quarantine_page_lists_exactly_the_quarantined_sets(page: Page, db):
    """Order-independent: other tests archive or re-extract sets in the shared
    session environment (S-07 archives one, S-16 re-extracts one), so the
    expectation comes from the database, never from the build-time manifest."""
    expected = {
        r["id"]
        for r in db.execute(
            "select id from po_sets where status='quarantined' and archived_at is null"
        )
    }
    page.goto("/quarantine")
    rows = page.get_by_test_id("quarantine-row")
    seen = {int(rows.nth(i).get_attribute("data-set-id")) for i in range(rows.count())}
    assert seen == expected, (
        f"listed but not quarantined: {seen - expected}; missing: {expected - seen}"
    )


def test_ui_q_04_the_destructive_control_is_called_archive(page: Page):
    """The action archives the set (rows + PDFs are kept). Calling it `Delete`
    tells the operator the opposite of what happens to the evidence."""
    page.goto("/quarantine")
    button = page.get_by_test_id("btn-archive-row").first
    expect(button).to_be_visible(timeout=5000)
    expect(button).to_have_text("Archive")


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


def test_ui_u_07_previewing_a_failed_file_explains_itself(page: Page, manifest):
    """U-07: the drawer must not render chrome's JSON viewer (F-02)."""
    held = manifest["held"]["rejected_on_arrival"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    with page.expect_response(re.compile(rf"/documents/{held['id']}/preview")) as resp:
        row.get_by_text("Preview").click()
    assert resp.value.status == 422
    assert resp.value.headers["content-type"].startswith("text/html")
    body = resp.value.text()
    assert "cannot be previewed" in body
    assert "Rejected on arrival" in body
    expect(
        page.frame_locator(".drawer-iframe").get_by_text("cannot be previewed", exact=False)
    ).to_be_visible()


def test_ui_u_06_an_ambiguous_po_is_marked_on_its_own_row(page: Page, manifest):
    """F-06/U-06: one badge for the page is not enough to find the file."""
    ambiguous = manifest["held"]["held_ambiguous_po"]
    other = manifest["held"]["held_dn_no_po"]
    page.goto("/unclassified")
    ambiguous_row = page.locator(
        f'[data-testid="unclassified-row"][data-doc-id="{ambiguous["id"]}"]'
    )
    other_row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{other["id"]}"]')
    expect(ambiguous_row).to_contain_text("multiple PO numbers")
    expect(other_row).not_to_contain_text("multiple PO numbers")


def test_ui_u_11_the_stored_type_is_visible_without_preselecting_it(page: Page, manifest):
    """F-05/U-11: never pre-select the control (U-03), but show what is stored."""
    held = manifest["held"]["held_dn_no_po"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    expect(row).to_contain_text(f"Stored type: {held['type']}")
    expect(row.get_by_test_id("select-doc-type")).to_have_value("")
    expect(row.get_by_test_id("btn-route")).to_be_disabled()


def test_ui_u_09_a_document_that_cannot_be_placed_says_so_after_the_refresh(
    page: Page, manifest, db
):
    """F-05: the route's explanation arrives with HX-Refresh, which reloads
    the page before htmx would swap the row in. It must survive as a toast."""
    held = manifest["held"]["held_reclassify_no_po"]
    assert held["mutable"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    row.get_by_test_id("select-doc-type").select_option("DN")
    with page.expect_response(re.compile(rf"/unclassified/{held['id']}/reclassify")) as resp:
        row.get_by_test_id("btn-route").click()
    assert resp.value.status == 200
    # The response body is gone once htmx reloads; the toast proves the
    # message in it was read before the reload.
    expect(page.locator(".toast-warning")).to_contain_text("no open PO Set exists", timeout=15000)
    row_after = db.execute(
        "select doc_type, po_set_id from documents where id=?", (held["id"],)
    ).fetchone()
    assert row_after["doc_type"] == "DN"
    assert row_after["po_set_id"] is None, "it must stay held, not be placed"
