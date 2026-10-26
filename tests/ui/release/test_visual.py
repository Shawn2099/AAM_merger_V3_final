"""Visual baselines for the redesign's stable regions.

Create the baselines once with ``--update-snapshots`` (that run reports the new
snapshots as failures by design), then verify them in the normal run. Every diff
is written to ``tests/ui/release/snapshot_failures/`` for review.

These tests read a dedicated, never-mutated environment. Other release tests
change dashboard counts, which can change the tab bar's height; sharing the
environment would make a pixel baseline depend on test order.
"""

from __future__ import annotations

from playwright.sync_api import Page


def _goto(page: Page, base: str, path: str, width: int = 1440, height: int = 900) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(base + path)
    page.wait_for_load_state("networkidle")


def test_dashboard_kpi_visual(page: Page, visual_base_url, assert_snapshot):
    _goto(page, visual_base_url, "/dashboard")
    assert_snapshot(page.locator(".kpi-grid"), name="dashboard-kpis-1440.png")


def test_dashboard_tabs_visual_at_two_widths(page: Page, visual_base_url, assert_snapshot):
    _goto(page, visual_base_url, "/dashboard", width=1440)
    assert_snapshot(page.locator("#status-tabs"), name="dashboard-tabs-1440.png")
    _goto(page, visual_base_url, "/dashboard", width=800)
    assert_snapshot(page.locator("#status-tabs"), name="dashboard-tabs-800.png")


def test_set_matrix_visual_at_two_widths(
    page: Page, visual_base_url, visual_manifest, assert_snapshot
):
    path = f"/po_sets/{visual_manifest['scenarios']['merged_basic']['id']}/view"
    _goto(page, visual_base_url, path, width=1440)
    assert_snapshot(page.locator(".reconcile-matrix"), name="set-matrix-1440.png")
    _goto(page, visual_base_url, path, width=800)
    assert_snapshot(page.locator(".reconcile-matrix"), name="set-matrix-800.png")


def test_unclassified_subtabs_visual(page: Page, visual_base_url, assert_snapshot):
    _goto(page, visual_base_url, "/unclassified")
    assert_snapshot(
        page.locator("#status-tabs, .status-tabs").first,
        name="unclassified-subtabs-1440.png",
    )


def test_quarantine_row_visual(page: Page, visual_base_url, visual_manifest, assert_snapshot):
    sc = visual_manifest["scenarios"]["quarantined_unmatched_vendor_line"]
    _goto(page, visual_base_url, "/quarantine")
    row = page.locator(f'[data-testid="quarantine-row"][data-set-id="{sc["id"]}"]')
    assert_snapshot(row, name="quarantine-row-1440.png")


def test_settings_group_visual(page: Page, visual_base_url, assert_snapshot):
    _goto(page, visual_base_url, "/settings")
    assert_snapshot(
        page.get_by_test_id("settings-group").first,
        name="settings-group-1440.png",
    )


def test_404_visual(page: Page, visual_base_url, assert_snapshot):
    _goto(page, visual_base_url, "/po_sets/999999/view")
    assert_snapshot(page.locator("body"), name="404-1440.png")
