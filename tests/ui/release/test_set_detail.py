"""PO Set detail: the 6-column matrix, customs switch, remove-set card, print."""

from __future__ import annotations

import io

import pytest
from playwright.sync_api import Page, expect
from pypdf import PdfReader

from release_helpers import assert_no_horizontal_scroll


def _open(page: Page, manifest, name: str) -> dict:
    sc = manifest["scenarios"][name]
    page.goto(f"/po_sets/{sc['id']}/view")
    expect(page.get_by_test_id("set-po")).to_have_text(sc["po_no"])
    return sc


def test_matrix_has_exactly_six_columns(page: Page, manifest):
    _open(page, manifest, "merged_basic")
    headers = [
        text.strip().lower()
        for text in page.locator(".reconcile-matrix thead th").all_inner_texts()
    ]
    assert headers == [
        "line #",
        "item description",
        "po ordered",
        "dn delivered",
        "si invoiced",
        "match status",
    ], headers


def test_matrix_has_no_total_variance_or_signoff_blocks(page: Page, manifest):
    _open(page, manifest, "merged_basic")
    matrix = page.locator(".reconcile-matrix")
    expect(matrix).to_be_visible()
    matrix_text = matrix.inner_text()
    assert "Variance" not in matrix_text
    assert "Total" not in matrix_text
    assert page.locator(".reconcile-matrix tfoot").count() == 0

    body = page.locator("body").inner_text()
    for forbidden in ("Sign-off", "Signoff", "Authorised by", "Approved by"):
        assert forbidden not in body, f"old {forbidden!r} block is still on the page"


def test_the_page_says_three_way_reconciliation(page: Page, manifest):
    _open(page, manifest, "merged_basic")
    assert "3-Way Reconciliation Matrix" in page.locator("body").inner_text()


@pytest.mark.parametrize(
    "scenario,customs_on",
    [
        ("merged_basic", False),
        ("awaiting_customs", True),
        ("merged_with_customs", True),
    ],
)
def test_customs_switch_reflects_the_stored_state(
    page: Page, manifest, scenario: str, customs_on: bool
):
    _open(page, manifest, scenario)
    toggle = page.get_by_test_id("btn-toggle-customs")
    expect(toggle).to_be_visible()
    expect(toggle).to_contain_text("ON" if customs_on else "OFF")
    classes = toggle.get_attribute("class") or ""
    assert ("is-active" in classes) is customs_on, classes


@pytest.mark.parametrize(
    "scenario,visible",
    [
        ("merged_basic", False),
        ("pending_no_dn", True),
        ("needs_review_quantity", True),
        ("awaiting_customs", True),
        ("quarantined_po_no_lines", True),
        ("archived_readonly", False),
    ],
)
def test_remove_set_card_visibility(page: Page, manifest, scenario: str, visible: bool):
    _open(page, manifest, scenario)
    card = page.get_by_test_id("remove-set-card")
    if visible:
        expect(card).to_be_visible()
        expect(card).to_contain_text("Remove Set to Quarantine Folder")
    else:
        expect(card).to_have_count(0)


def test_every_document_row_has_a_preview_button(page: Page, manifest):
    _open(page, manifest, "merged_basic")
    rows = page.get_by_test_id("doc-row")
    assert rows.count() >= 1
    for index in range(rows.count()):
        expect(rows.nth(index).get_by_test_id("btn-preview")).to_be_visible()


def test_print_layout_is_clean_and_contains_the_matrix(page: Page, manifest, artifacts):
    _open(page, manifest, "merged_basic")
    page.emulate_media(media="print")
    assert_no_horizontal_scroll(page)
    pdf_bytes = page.pdf(format="A4", print_background=True)
    (artifacts / "set-detail-print.pdf").write_bytes(pdf_bytes)
    assert pdf_bytes.startswith(b"%PDF")

    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) >= 1
    text = "\n".join((pdf_page.extract_text() or "") for pdf_page in reader.pages).lower()
    assert "po ordered" in text
    assert "line #" in text
