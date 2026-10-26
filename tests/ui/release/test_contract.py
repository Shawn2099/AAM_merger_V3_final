"""Global release contract: pages load cleanly, titles, nav, critical test ids."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

from release_helpers import FAVICON, open_preview

NAV_PAGES = (
    "/dashboard",
    "/unclassified",
    "/quarantine",
    "/audit",
    "/settings",
    "/manual/merger",
)


def set_page(manifest, name: str = "merged_basic") -> str:
    return f"/po_sets/{manifest['scenarios'][name]['id']}/view"


@pytest.mark.parametrize("path", NAV_PAGES, ids=[p.strip("/").replace("/", "-") for p in NAV_PAGES])
def test_every_page_loads_without_errors(page: Page, path: str, evidence):
    response = page.goto(path)
    assert response is not None, path
    assert response.status < 400, f"{path} answered {response.status}"
    page.wait_for_load_state("networkidle")
    evidence.assert_clean(allow=FAVICON)


def test_a_set_page_and_the_404_page_load_cleanly(page: Page, manifest, evidence):
    response = page.goto(set_page(manifest))
    assert response is not None and response.status == 200
    page.wait_for_load_state("networkidle")
    evidence.assert_clean(allow=FAVICON)

    response = page.goto("/po_sets/999999/view")
    assert response is not None and response.status == 404
    page.wait_for_load_state("networkidle")
    # A 404 is the expected answer here; everything else must be clean.
    evidence.assert_clean(allow=(*FAVICON, "/po_sets/999999/view"))


def test_critical_test_ids_exist(page: Page, manifest):
    required = {
        "/dashboard": [
            "nav-dashboard",
            "kpi-total",
            "kpi-merged",
            "kpi-mismatched",
            "kpi-blocked-customs",
            "kpi-quarantined",
            "tab-all",
            "tab-pending",
            "tab-mismatched",
            "tab-blocked-customs",
            "tab-merged",
            "tab-quarantined",
            "po-row",
        ],
        "/unclassified": [
            "nav-unclassified",
            "unclassified-count",
            "tab-subview-all",
            "tab-subview-needs-po",
            "tab-subview-failed",
            "unclassified-row",
        ],
        "/quarantine": [
            "nav-quarantine",
            "quarantine-row",
            "btn-archive-row",
            "archive-row-modal",
            "btn-cancel-archive-row",
            "btn-confirm-archive-row",
        ],
        "/settings": [
            "nav-settings",
            "settings-form",
            "settings-save",
            "settings-reset",
            "setting-row",
        ],
    }
    for path, ids in required.items():
        page.goto(path)
        for test_id in ids:
            assert page.get_by_test_id(test_id).count() >= 1, f"{path}: missing data-testid={test_id}"

    page.goto(set_page(manifest))
    for test_id in (
        "set-po",
        "reason-card",
        "matrix-row",
        "btn-preview",
        "btn-merge-now",
        "btn-force-merge",
        "btn-toggle-customs",
    ):
        assert page.get_by_test_id(test_id).count() >= 1, f"set page: missing data-testid={test_id}"

    # The remove-set card is only offered on sets that can be replaced.
    pending = manifest["scenarios"]["pending_no_dn"]
    page.goto(f"/po_sets/{pending['id']}/view")
    for test_id in ("remove-set-card", "btn-confirm-remove-set"):
        assert page.get_by_test_id(test_id).count() >= 1, (
            f"pending set: missing data-testid={test_id}"
        )


def test_every_nav_page_has_a_distinct_title(page: Page, manifest):
    paths = [*NAV_PAGES, set_page(manifest), "/po_sets/999999/view", "/dashboard?status=nonsense"]
    titles = {}
    for path in paths:
        page.goto(path)
        titles[path] = page.title()
    assert all(titles.values()), titles
    duplicates = {t for t in titles.values() if list(titles.values()).count(t) > 1}
    assert not duplicates, f"pages share a title: {duplicates}"


def test_the_active_nav_item_matches_the_page(page: Page):
    expected = {
        "/dashboard": "nav-dashboard",
        "/unclassified": "nav-unclassified",
        "/quarantine": "nav-quarantine",
        "/audit": "nav-audit",
        "/settings": "nav-settings",
        "/manual/merger": "nav-merger",
    }
    for path, test_id in expected.items():
        page.goto(path)
        active = page.locator("nav.app-navbar .nav-link.active")
        expect(active).to_have_count(1)
        assert active.get_attribute("data-testid") == test_id, path


def test_no_request_leaves_the_app_host(page: Page, base_url, manifest):
    host = urlsplit(base_url).netloc
    requested: list[str] = []
    page.on("request", lambda request: requested.append(request.url))

    for path in NAV_PAGES:
        page.goto(path)
        page.wait_for_load_state("networkidle")

    page.goto(set_page(manifest))
    open_preview(page, page.get_by_test_id("btn-preview").first, None)

    external = sorted(
        {
            request
            for request in requested
            if urlsplit(request).scheme in ("http", "https", "ws", "wss")
            and urlsplit(request).netloc != host
        }
    )
    assert not external, f"requests left the app host: {external}"
    assert requested, "no request was recorded, so the check proved nothing"
