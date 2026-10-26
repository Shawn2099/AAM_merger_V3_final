"""Visual baselines for the redesign's stable regions.

Create the baselines once with ``--update-snapshots`` (that run reports the new
snapshots as failures by design), then verify them in the normal run. Every diff
is written to ``tests/ui/release/snapshot_failures/`` for review.
"""

from __future__ import annotations

from playwright.sync_api import Page


def _goto(page: Page, path: str, width: int = 1440, height: int = 900) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(path)
    page.wait_for_load_state("networkidle")


def test_dashboard_kpi_visual(page: Page, assert_snapshot):
    _goto(page, "/dashboard")
    assert_snapshot(page.locator(".kpi-grid"), name="dashboard-kpis-1440.png")


def test_dashboard_tabs_visual_at_two_widths(page: Page, assert_snapshot):
    _goto(page, "/dashboard", width=1440)
    assert_snapshot(page.locator("#status-tabs"), name="dashboard-tabs-1440.png")
    _goto(page, "/dashboard", width=800)
    assert_snapshot(page.locator("#status-tabs"), name="dashboard-tabs-800.png")


def test_set_matrix_visual_at_two_widths(page: Page, manifest, assert_snapshot):
    path = f"/po_sets/{manifest['scenarios']['merged_basic']['id']}/view"
    _goto(page, path, width=1440)
    assert_snapshot(page.locator(".reconcile-matrix"), name="set-matrix-1440.png")
    _goto(page, path, width=800)
    assert_snapshot(page.locator(".reconcile-matrix"), name="set-matrix-800.png")


def test_unclassified_subtabs_visual(page: Page, assert_snapshot):
    _goto(page, "/unclassified")
    assert_snapshot(
        page.locator("#status-tabs, .status-tabs").first,
        name="unclassified-subtabs-1440.png",
    )


def test_quarantine_row_visual(page: Page, manifest, assert_snapshot):
    sc = manifest["scenarios"]["quarantined_unmatched_vendor_line"]
    _goto(page, "/quarantine")
    row = page.locator(f'[data-testid="quarantine-row"][data-set-id="{sc["id"]}"]')
    assert_snapshot(row, name="quarantine-row-1440.png")


def test_settings_group_visual(page: Page, assert_snapshot):
    _goto(page, "/settings")
    assert_snapshot(
        page.get_by_test_id("settings-group").first,
        name="settings-group-1440.png",
    )


def test_404_visual(page: Page, assert_snapshot):
    _goto(page, "/po_sets/999999/view")
    assert_snapshot(page.locator("body"), name="404-1440.png")
