"""Small, shared helpers for the release suite."""

from __future__ import annotations

import re

from playwright.sync_api import Locator, Page, expect

#: The approved 1-to-1 status vocabulary for the redesign.
STATUS_LABEL = {
    "pending": "Awaiting Documents",
    "mismatched": "Quantity Mismatch",
    "blocked_customs": "Awaiting Customs/Shipping Docs",
    "merged": "Merged & Ready",
    "quarantined": "Flagged",
}

DASHBOARD_TABS = {
    "pending": "tab-pending",
    "mismatched": "tab-mismatched",
    "blocked_customs": "tab-blocked-customs",
    "merged": "tab-merged",
    "quarantined": "tab-quarantined",
}


def number_in(locator: Locator) -> int:
    """The whole number inside a card, tab or badge ('12' never matches '1')."""
    match = re.search(r"\d+", locator.inner_text().replace("%", " "))
    assert match, locator.inner_text()
    return int(match.group())


def active_sets(db) -> list[dict]:
    """Every set the dashboard should show, read from the database at test time."""
    return [
        dict(row)
        for row in db.execute(
            "select id, po_no_normalized as po, status, reconcile_reason as reason "
            "from po_sets where archived_at is null order by id"
        )
    ]


def active_set_ids(db, status: str) -> set[int]:
    return {row["id"] for row in active_sets(db) if row["status"] == status}


def visible_rows(page: Page, test_id: str) -> Locator:
    """Rows the operator can actually see (Alpine's x-show hides the rest)."""
    return page.get_by_test_id(test_id).filter(visible=True)


def wait_for_alpine(page: Page) -> None:
    """Wait until Alpine has taken over the first x-data component.

    `page.goto` returns when the document and its assets load; Alpine then
    initialises on the next microtask. A click sent in that gap is a no-op
    (the tab does not change), which looks like a product bug in a test.
    """
    page.wait_for_function(
        "() => { const el = document.querySelector('[x-data]'); "
        "return !!(el && el._x_dataStack); }",
        timeout=5000,
    )


def assert_no_horizontal_scroll(page: Page) -> None:
    scroll_width = page.evaluate("document.documentElement.scrollWidth")
    client_width = page.evaluate("document.documentElement.clientWidth")
    assert scroll_width <= client_width, (
        f"horizontal scroll: scrollWidth {scroll_width} > clientWidth {client_width} "
        f"at {page.viewport_size}"
    )


def open_preview(page: Page, button: Locator, expected_name: str | None = None) -> None:
    """Open the global PDF drawer and prove it is showing the requested document."""
    drawer = page.get_by_test_id("pdf-drawer")
    with page.expect_response(re.compile(r"/documents/\d+/preview")) as response:
        button.click()
    assert response.value.status == 200, response.value.text()
    assert response.value.headers.get("content-type", "").startswith("application/pdf")
    expect(drawer).to_have_class(re.compile(r"\bopen\b"))
    if expected_name:
        expect(drawer).to_contain_text(expected_name)


def close_preview(page: Page) -> None:
    page.keyboard.press("Escape")
    expect(page.get_by_test_id("pdf-drawer")).not_to_have_class(re.compile(r"\bopen\b"))


def set_row(page: Page, set_id: int) -> Locator:
    return page.locator(f'[data-testid="po-row"][data-set-id="{set_id}"]')


def quarantine_row(page: Page, set_id: int) -> Locator:
    return page.locator(f'[data-testid="quarantine-row"][data-set-id="{set_id}"]')


def body_text(page: Page) -> str:
    return page.locator("body").inner_text()


def pdf_bytes(pages: int = 1) -> bytes:
    """A small, valid PDF for upload tests."""
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def toast_text(page: Page) -> str:
    """All currently visible toast messages, joined for assertions."""
    return "\n".join(page.locator(".toast").all_inner_texts())


#: The one expected 404 in the app: no favicon is shipped.
FAVICON = ("favicon.ico",)
