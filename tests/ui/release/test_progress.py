"""Live ingestion progress bar (working-tree feature)."""

from __future__ import annotations

import json

from playwright.sync_api import Page, expect


def test_progress_bar_shows_a_running_sync(page: Page):
    payload = {
        "running": True,
        "phase": "extracting",
        "percent": 42,
        "current": 2,
        "total": 5,
        "current_file": "PO-7000001.pdf",
        "elapsed_seconds": 12,
        "eta_seconds": 20,
        "extracted": 2,
        "errors": 1,
    }
    page.route(
        "**/sync/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )
    page.goto("/dashboard")

    card = page.locator("#sync-progress-card")
    expect(card).to_be_visible(timeout=8000)
    expect(page.locator("#sync-progress-phase")).to_contain_text(
        "AI Vision Extraction in Progress (2 of 5 files)"
    )
    expect(page.locator("#sync-progress-percent")).to_have_text("42%")
    assert "width: 42%" in (page.locator("#sync-progress-bar").get_attribute("style") or "")
    expect(page.locator("#sync-current-file")).to_have_text("PO-7000001.pdf")
    expect(page.locator("#sync-count-info")).to_have_text("(2 of 5 files)")
    expect(page.locator("#sync-elapsed")).to_have_text("12s")
    expect(page.locator("#sync-eta")).to_have_text("~20s")
    expect(page.locator("#sync-extracted")).to_have_text("2")
    expect(page.locator("#sync-errors")).to_have_text("1")

    pill = page.locator("#nav-sync-pill")
    expect(pill).to_be_visible()
    expect(page.locator("#nav-sync-text")).to_have_text("Sync: 42% (2/5)")


def test_progress_bar_is_hidden_when_no_sync_runs(page: Page):
    page.goto("/dashboard")
    page.wait_for_load_state("networkidle")
    expect(page.locator("#sync-progress-card")).to_be_hidden()
    expect(page.locator("#nav-sync-pill")).to_be_hidden()
    expect(page.locator("#health-text")).to_have_text("Service Online")
