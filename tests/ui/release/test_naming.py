"""The redesign's naming contract: one word for one thing, everywhere."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from release_helpers import active_sets, body_text, set_row

OLD_LABELS = (
    "Matched & Completed",
    "Quantity Discrepancy",
    "Exception (Rejected)",
)


def test_kpi_cards_and_tabs_use_the_approved_names(page: Page):
    page.goto("/dashboard")
    expected = {
        "kpi-total": "Total PO Sets",
        "kpi-merged": "Merged & Ready",
        "kpi-mismatched": "Quantity Mismatch",
        "kpi-blocked-customs": "Awaiting Customs/Shipping Docs",
        "kpi-quarantined": "Flagged",
        "tab-all": "All",
        "tab-pending": "Awaiting Documents",
        "tab-mismatched": "Quantity Mismatch",
        "tab-blocked-customs": "Awaiting Customs/Shipping Docs",
        "tab-merged": "Merged & Ready",
        "tab-quarantined": "Flagged",
    }
    for test_id, text in expected.items():
        expect(page.get_by_test_id(test_id)).to_contain_text(text)


def test_every_row_badge_uses_the_status_vocabulary(page: Page, db):
    page.goto("/dashboard")
    problems = []
    for ps in active_sets(db):
        row = set_row(page, ps["id"])
        if row.count() != 1:
            problems.append(f"set {ps['id']}: row missing")
            continue
        text = row.inner_text()
        if ps["status"] == "pending":
            ok = "Awaiting Documents" in text
        elif ps["status"] == "mismatched":
            ok = "Quantity Mismatch" in text or "Partial Delivery" in text
        elif ps["status"] == "blocked_customs":
            ok = "Awaiting Customs/Shipping Docs" in text
        elif ps["status"] == "merged":
            ok = "Merged & Ready" in text
        elif ps["status"] == "quarantined":
            ok = "Flagged" in text
        else:  # a new status must fail loudly, not silently pass
            ok = False
        if not ok:
            problems.append(f"set {ps['id']} ({ps['status']}): {text[:120]!r}")
        for old in OLD_LABELS:
            if old in text:
                problems.append(f"set {ps['id']}: old label {old!r} still shown")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    "scenario,label",
    [
        ("merged_basic", "Merged & Ready"),
        ("merged_mapped_x10", "Merged & Ready"),
        ("description_match_target", "Merged & Ready"),
        ("pending_no_dn", "Awaiting Documents"),
        ("needs_review_quantity", "Partial Delivery"),
        ("awaiting_customs", "Awaiting Customs/Shipping Docs"),
        ("quarantined_unmatched_vendor_line", "Flagged"),
    ],
)
def test_set_page_badge_uses_the_same_word(page: Page, manifest, scenario: str, label: str):
    sc = manifest["scenarios"][scenario]
    page.goto(f"/po_sets/{sc['id']}/view")
    body = body_text(page)
    assert label in body, f"{scenario}: {label!r} missing from the set page"
    for old in OLD_LABELS:
        assert old not in body, f"{scenario}: old label {old!r} still shown"


def test_old_status_names_are_gone_from_the_dashboard(page: Page, manifest):
    for path in ("/dashboard", f"/po_sets/{manifest['scenarios']['merged_basic']['id']}/view"):
        page.goto(path)
        body = body_text(page)
        for old in OLD_LABELS:
            assert old not in body, f"{path}: old label {old!r} still shown"


def test_quarantine_actions_name_the_folder(page: Page, manifest, db):
    page.goto("/quarantine")
    assert page.get_by_test_id("quarantine-row").count() >= 1
    button = page.get_by_test_id("btn-archive-row").first
    expect(button).to_contain_text("Quarantine Folder")
    button.click()
    modal = page.get_by_test_id("archive-row-modal").first
    expect(modal).to_have_class(re.compile(r"\bopen\b"))
    expect(modal).to_contain_text("Quarantine Folder")
    expect(modal.get_by_test_id("btn-confirm-archive-row")).to_contain_text("Quarantine Folder")
    modal.get_by_test_id("btn-cancel-archive-row").click()
    expect(modal).not_to_have_class(re.compile(r"\bopen\b"))

    sc = manifest["scenarios"]["quarantined_unmatched_vendor_line"]
    page.goto(f"/po_sets/{sc['id']}/view")
    expect(page.get_by_test_id("remove-set-card")).to_be_visible()
    expect(page.get_by_test_id("btn-confirm-remove-set")).to_contain_text("Quarantine Folder")


def test_held_document_move_names_the_folder(page: Page):
    page.goto("/unclassified")
    assert page.get_by_test_id("btn-set-aside").count() >= 1
    expect(page.get_by_test_id("btn-set-aside").first).to_contain_text("Quarantine Folder")

    page.goto("/quarantine")
    if page.get_by_test_id("files-set-aside").count():
        expect(page.get_by_test_id("btn-set-aside-quarantine").first).to_contain_text(
            "Quarantine Folder"
        )


def test_a_row_and_its_set_page_agree(page: Page, manifest):
    sc = manifest["scenarios"]["needs_review_quantity"]
    page.goto("/dashboard")
    row_text = set_row(page, sc["id"]).inner_text()
    page.goto(f"/po_sets/{sc['id']}/view")
    set_text = body_text(page)
    # The under-delivery case must use one status word on both surfaces; the
    # page may explain why it is waiting, but the badge is "Partial Delivery".
    assert "Partial Delivery" in row_text
    assert "Partial Delivery" in set_text
    assert "Why this set is waiting" in set_text


def test_merged_review_is_a_tag_not_a_status(page: Page, manifest):
    sc = manifest["scenarios"]["merged_mapped_x10"]
    page.goto("/dashboard")
    row = set_row(page, sc["id"])
    expect(row).to_contain_text("Merged & Ready")
    expect(row).to_contain_text("Review")
    assert "Merged • review" not in row.inner_text()

    page.goto(f"/po_sets/{sc['id']}/view")
    body = body_text(page)
    assert "Merged & Ready" in body
    assert "Review" in body
