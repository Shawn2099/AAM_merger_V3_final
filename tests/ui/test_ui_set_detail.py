"""Set page (UI-S-*): reason card, matrix, documents, actions. Reference implementation."""

import re
from pathlib import Path

from playwright.sync_api import Page, expect


def _open(page: Page, manifest, name: str):
    sc = manifest["scenarios"][name]
    page.goto(f"/po_sets/{sc['id']}/view")
    expect(page.get_by_test_id("set-po")).to_have_text(sc["po_no"])
    return sc


def test_ui_s_01_merged_set_shows_a_match_per_line_and_all_documents(page: Page, manifest):
    sc = _open(page, manifest, "merged_basic")
    rows = page.get_by_test_id("matrix-row")
    expect(rows).to_have_count(2)
    for i in range(2):
        expect(rows.nth(i)).to_contain_text("Match")
    expect(page.get_by_test_id("doc-row")).to_have_count(len(sc["docs"]))
    for doc in sc["docs"]:
        expect(page.locator(f'[data-testid="doc-row"][data-doc-id="{doc["id"]}"]')).to_contain_text(
            doc["file"]
        )
    # A merged set cannot be merged again.
    expect(page.get_by_test_id("btn-merge-now")).to_be_disabled()
    expect(page.get_by_test_id("btn-force-merge")).to_be_disabled()


def test_ui_s_02_mapped_set_says_which_numbering_rule_paired_the_lines(page: Page, manifest):
    _open(page, manifest, "merged_mapped_x10")
    expect(page.get_by_test_id("flag").first).to_contain_text("x10")
    expect(page.get_by_test_id("matrix-row")).to_have_count(2)


def test_ui_s_03_waiting_set_shows_the_reason_and_the_unmatched_vendor_line(page: Page, manifest):
    sc = _open(page, manifest, "pending_vendor_line_not_on_po")
    expect(page.get_by_test_id("reason-card")).to_contain_text(sc["reason"][:40])
    flag = page.get_by_test_id("flag")
    expect(flag).to_have_count(1)
    expect(flag).to_contain_text("Not on the PO")
    expect(flag).to_contain_text("SI line 2")
    expect(flag).to_contain_text("qty 50")
    expect(page.get_by_test_id("matrix-row")).to_contain_text("Awaiting")


def test_ui_s_04_quarantined_set_explains_why_and_offers_archive_not_merge_overrides(
    page: Page, manifest
):
    sc = _open(page, manifest, "quarantined_unmatched_vendor_line")
    expect(page.get_by_test_id("reason-card")).to_contain_text(sc["reason"][:40])
    expect(page.get_by_test_id("flag").first).to_contain_text("has no matching PO line")
    expect(page.get_by_test_id("btn-archive")).to_be_visible()


def test_ui_s_05_document_preview_opens_the_pdf_in_the_drawer(page: Page, manifest):
    sc = _open(page, manifest, "merged_basic")
    first = page.locator(f'[data-testid="doc-row"][data-doc-id="{sc["docs"][0]["id"]}"]')
    with page.expect_response(re.compile(r"/documents/\d+/preview")) as resp:
        first.get_by_test_id("btn-preview").click()
    assert resp.value.status == 200
    assert resp.value.headers["content-type"].startswith("application/pdf")


def test_ui_s_06_force_merge_needs_the_modal_and_produces_a_packet(page: Page, manifest, db):
    sc = _open(page, manifest, "force_merge_target")
    assert sc["mutable"] and sc["status"] == "mismatched"
    page.get_by_test_id("btn-force-merge").click()
    page.locator("#force-merge-justification").fill(
        "UI test: operator accepts the 7 of 10 delivery."
    )
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/force_merge")) as resp:
        page.get_by_test_id("btn-confirm-force-merge").click()
    assert resp.value.status == 200, resp.value.text()
    page.goto(f"/po_sets/{sc['id']}/view")
    row = db.execute(
        "select status, merged_output_path from po_sets where id=?", (sc["id"],)
    ).fetchone()
    assert row["status"] == "merged"
    assert Path(row["merged_output_path"]).read_bytes()[:4] == b"%PDF"
    audit = db.execute("select action from audit_log where po_set_id=?", (sc["id"],)).fetchall()
    assert "force_merge" in [a["action"] for a in audit]


def test_ui_s_07_archive_removes_the_set_from_the_worklists(page: Page, manifest, db):
    sc = _open(page, manifest, "archive_target")
    page.get_by_test_id("btn-archive").click()
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/quarantine")) as resp:
        page.get_by_test_id("btn-confirm-archive").click()
    assert resp.value.status == 200, resp.value.text()
    page.goto("/dashboard")
    expect(page.locator(f'[data-testid="po-row"][data-set-id="{sc["id"]}"]')).to_have_count(0)
    assert db.execute("select archived_at from po_sets where id=?", (sc["id"],)).fetchone()[0]


def test_ui_s_08_customs_toggle_turns_the_requirement_on_and_off(page: Page, manifest, db):
    sc = _open(page, manifest, "customs_toggle_target")
    button = page.get_by_test_id("btn-toggle-customs")
    expect(button).to_contain_text("OFF")
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/toggle_customs")) as resp:
        button.click()
    assert resp.value.status == 200, resp.value.text()
    # The page reloads itself after the toggle; expect() retries until it has.
    expect(page.get_by_test_id("btn-toggle-customs")).to_contain_text("ON")
    # ...and the confirmation must survive that reload (F-07): a toast that is
    # destroyed by the reload it triggered is not feedback.
    expect(page.locator(".toast-info")).to_contain_text("Customs requirement setting updated")
    assert (
        db.execute("select has_customs_toggle from po_sets where id=?", (sc["id"],)).fetchone()[0]
        == 1
    )


def test_ui_s_17_an_unknown_set_shows_a_friendly_page(page: Page):
    """S-17: opening a set that no longer exists must not dead-end in JSON."""
    response = page.goto("/po_sets/999999/view")
    assert response is not None and response.status == 404
    expect(page.locator("nav.app-navbar")).to_be_visible()
    expect(page.get_by_text("does not exist", exact=False)).to_be_visible()
    assert "Traceback" not in page.content()


def test_ui_s_15_a_refused_force_merge_says_why_once(page: Page, manifest):
    """F-10: the toast must not repeat the icon the template already renders."""
    sc = _open(page, manifest, "force_merge_validation_target")
    page.get_by_test_id("btn-force-merge").click()
    page.locator("#force-merge-justification").fill("too short")
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/force_merge")) as resp:
        page.get_by_test_id("btn-confirm-force-merge").click()
    assert resp.value.status == 422
    toast = page.locator(".toast-error")
    expect(toast).to_contain_text("Justification must be at least 20 characters")
    text = toast.inner_text()
    assert text.count("❌") == 1, f"the type icon is rendered twice: {text!r}"


def test_ui_s_16_reextract_without_a_provider_key_reports_failure(page: Page, manifest):
    """F-01/S-16: the outcome is reported, not "queued".

    The set's status afterwards is the engine's decision (a failed re-read
    leaves it `pending`; pinned by tests/test_mvp_blockers.py). What this test
    requires is that the operator is told nothing was read, and that the
    message survives the reload the action triggers.
    """
    sc = _open(page, manifest, "reextract_target")
    assert sc["mutable"] and sc["status"] == "quarantined"
    with page.expect_response(
        re.compile(rf"/po_sets/{sc['id']}/redo_extract"), timeout=60000
    ) as resp:
        page.get_by_test_id("btn-reextract").click()
    assert resp.value.status == 200, resp.value.text()
    # The page reloads itself on success; the toast must survive that reload
    # and must not claim the documents were read (they were not).
    expect(page.locator(".toast-error")).to_contain_text("Extraction failed", timeout=20000)


def test_ui_s_21_a_wrong_customs_file_can_be_removed_before_the_merge(page: Page, manifest, db):
    """S-21: Remove takes the CUSTOMS file out, says so, and the gate shows it missing."""
    sc = _open(page, manifest, "customs_remove_target")
    assert sc["mutable"] and sc["status"] == "pending"
    customs = next(d for d in sc["docs"] if d["type"] == "CUSTOMS")
    shipping = next(d for d in sc["docs"] if d["type"] == "SHIPPING")
    customs_row = page.locator(f'[data-testid="doc-row"][data-doc-id="{customs["id"]}"]')
    expect(page.get_by_test_id(f"btn-remove-supporting-{customs['id']}")).to_be_visible()
    expect(page.get_by_test_id(f"btn-remove-supporting-{shipping['id']}")).to_be_visible()
    clearance = page.locator("div", has=page.locator("strong", has_text="Customs Clearance:")).last
    expect(clearance).to_contain_text("Attached")

    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(re.compile(r"/remove_supporting")) as resp:
        page.get_by_test_id(f"btn-remove-supporting-{customs['id']}").click()
    assert resp.value.status == 200, resp.value.text()

    # The page reloads itself; the confirmation must survive that reload.
    expect(customs_row).to_have_count(0)
    expect(page.locator(".toast-success")).to_contain_text(f"Removed {customs['file']}")
    expect(clearance).to_contain_text("Missing")
    expect(page.get_by_test_id(f"btn-remove-supporting-{shipping['id']}")).to_be_visible()
    row = db.execute(
        "select po_set_id, archived_at from documents where id=?", (customs["id"],)
    ).fetchone()
    assert row["po_set_id"] is None and row["archived_at"]


def test_ui_s_21b_a_merged_set_offers_no_remove_button(page: Page, manifest):
    _open(page, manifest, "merged_basic")
    expect(page.locator('[data-testid^="btn-remove-supporting-"]')).to_have_count(0)
