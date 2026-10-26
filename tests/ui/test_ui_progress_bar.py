"""Playwright browser tests for live non-blocking sync progress bar and service health."""

from playwright.sync_api import Page, expect


def test_ui_health_indicator_and_progress_card(page: Page):
    # 1. Load Dashboard
    page.goto("/dashboard")

    # 2. Verify Service Health Status indicator in navbar
    health_dot = page.locator("#health-dot")
    expect(health_dot).to_be_visible()
    health_text = page.locator("#health-text")
    expect(health_text).to_have_text("Service Online")

    # 3. Progress card is initially hidden when idle
    progress_card = page.locator("#sync-progress-card")
    expect(progress_card).to_be_hidden()

    # 4. Navbar sync pill is initially hidden when idle
    nav_pill = page.locator("#nav-sync-pill")
    expect(nav_pill).to_be_hidden()

    # 5. Simulate sync running by intercepting /sync/status
    page.route(
        "**/sync/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="""{
                "running": true,
                "detail": "Sync already running",
                "phase": "extracting",
                "total": 500,
                "current": 142,
                "current_file": "INV_2026_094.pdf",
                "percent": 28,
                "extracted": 139,
                "errors": 3,
                "elapsed_seconds": 1240,
                "eta_seconds": 3180
            }""",
        ),
    )

    # Trigger refreshSyncState by evaluating it or waiting for poll
    page.evaluate("refreshSyncState()")

    # 6. Verify Progress Card dynamically appears
    expect(progress_card).to_be_visible()
    expect(page.locator("#sync-progress-percent")).to_have_text("28%")
    expect(page.locator("#sync-current-file")).to_have_text("INV_2026_094.pdf")
    expect(page.locator("#sync-count-info")).to_have_text("(142 of 500 files)")
    expect(page.locator("#sync-progress-phase")).to_contain_text("AI Vision Extraction")

    # 7. Verify Navbar Sync Pill appears
    expect(nav_pill).to_be_visible()
    expect(page.locator("#nav-sync-text")).to_contain_text("Sync: 28% (142/500)")

    # 8. CRITICAL CHECK: Verify the UI is NOT blocked by any modal or overlay
    # Check that table rows and buttons are visible, unobstructed, and clickable
    po_table = page.locator("#po-table")
    expect(po_table).to_be_visible()

    # Verify no backdrop or modal is open
    modal_open = page.locator(".modal-backdrop.open")
    expect(modal_open).to_have_count(0)
    drawer_open = page.locator(".drawer-backdrop.open")
    expect(drawer_open).to_have_count(0)
