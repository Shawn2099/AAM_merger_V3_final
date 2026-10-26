"""Global page behaviour (UI-G-*): keyboard focus, inherited chrome.

Each test here opens a page and inspects behaviour that is not one page's
data; nothing changes a mutable scenario unless the test owns it.
"""

import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

NAV_PAGES = (
    "/dashboard",
    "/unclassified",
    "/quarantine",
    "/audit",
    "/manual/merger",
)


def _pages(manifest) -> list[str]:
    """The nav pages plus the three set pages the global cases inspect."""
    paths = list(NAV_PAGES)
    for name in ("merged_basic", "needs_review_quantity", "quarantined_unmatched_vendor_line"):
        paths.append(f"/po_sets/{manifest['scenarios'][name]['id']}/view")
    return paths


def test_ui_g_04_the_force_merge_modal_takes_and_traps_focus(page: Page, manifest):
    """F-08/G-04: `aria-modal="true"` must be true for the keyboard too."""
    sc = manifest["scenarios"]["force_merge_validation_target"]
    page.goto(f"/po_sets/{sc['id']}/view")
    trigger = page.get_by_test_id("btn-force-merge")
    trigger.focus()
    trigger.click()

    modal = page.locator("#force-merge-modal")
    expect(modal).to_have_class(re.compile(r"\bopen\b"))
    # Opening the dialog moves focus into it (the justification is the field
    # the operator must fill).
    expect(page.locator("#force-merge-justification")).to_be_focused()

    for _ in range(8):
        page.keyboard.press("Tab")
        inside = page.evaluate(
            "() => {"
            "  const modal = document.querySelector('#force-merge-modal');"
            "  return !!modal && modal.contains(document.activeElement);"
            "}"
        )
        assert inside, "Tab moved focus out of the open modal"

    page.keyboard.press("Escape")
    expect(modal).not_to_have_class(re.compile(r"\bopen\b"))
    expect(trigger).to_be_focused()


def test_ui_g_01_every_page_loads_without_errors(page: Page, base_url, manifest):
    """G-01: a green page with a 404 behind it is not a working page."""
    console_errors: list[tuple[str, str]] = []
    failed_responses: list[str] = []
    failed_requests: list[str] = []
    page.on(
        "console",
        lambda m: (
            m.type == "error"
            and console_errors.append(((m.location or {}).get("url") or "", m.text))
        ),
    )
    page.on(
        "response", lambda r: r.status >= 400 and failed_responses.append(f"{r.status} {r.url}")
    )
    page.on("requestfailed", lambda r: failed_requests.append(f"{r.url} {r.failure}"))

    for path in _pages(manifest):
        response = page.goto(path)
        assert response is not None, path
        assert response.status < 400, f"{path} answered {response.status}"
        page.wait_for_load_state("networkidle")

    # covered by G-01b: the one known 404 is the missing favicon.
    def _not_favicon(url: str) -> bool:
        return not url.endswith("/favicon.ico")

    real_responses = [e for e in failed_responses if _not_favicon(e.split()[-1])]
    real_requests = [e for e in failed_requests if _not_favicon(e.split()[-1])]
    real_console = [
        f"{url} {text}"
        for url, text in console_errors
        if _not_favicon(url) and "favicon.ico" not in text
    ]
    assert not real_responses, "\n".join(failed_responses)
    assert not real_requests, "\n".join(failed_requests)
    assert not real_console, "\n".join(real_console)

    # The vendored assets the offline LAN depends on are served, not 404s.
    for asset in (
        "/static/css/theme.css",
        "/static/js/htmx.min.js",
        "/static/js/alpine.min.js",
        "/static/js/app.js",
    ):
        served = page.request.get(base_url + asset)
        assert served.status == 200, f"{asset} answered {served.status}"


def test_ui_g_01b_a_favicon_is_served(page: Page, base_url):
    """G-01b: the 404 G-01 has to excuse is a real (small) defect."""
    assert page.request.get(f"{base_url}/favicon.ico").status == 200


def test_ui_g_02_no_request_leaves_the_app_host(page: Page, base_url, manifest):
    """G-02: the office LAN has no internet; a CDN font would break the page there."""
    app_netloc = urlsplit(base_url).netloc
    requested: list[str] = []
    page.on("request", lambda r: requested.append(r.url))

    for path in _pages(manifest):
        page.goto(path)
        page.wait_for_load_state("networkidle")

    # One preview drawer too: the PDF viewer is the one place a page reaches
    # for another origin.
    sc = manifest["scenarios"]["merged_basic"]
    page.goto(f"/po_sets/{sc['id']}/view")
    page.wait_for_load_state("networkidle")
    with page.expect_response(re.compile(r"/documents/\d+/preview")):
        page.get_by_test_id("btn-preview").first.click()
    expect(page.get_by_test_id("pdf-drawer")).to_have_class(re.compile(r"\bopen\b"))

    counted = [url for url in requested if urlsplit(url).scheme in ("http", "https", "ws", "wss")]
    other = sorted({url for url in counted if urlsplit(url).netloc != app_netloc})
    assert not other, f"requests went to {other}"
    assert counted, "no request was recorded, so the check proved nothing"


G03_VIEWPORTS = ((1024, 768), (1366, 768), (768, 1024))


#: At 768px the nav used to overflow (`.nav-right` ended at x=830). The narrow-window
#: rules in theme.css (Batch 1 step 5, I-50) fixed it, so this is now a guard.
@pytest.mark.parametrize(
    "width,height,kind",
    [
        pytest.param(width, height, kind, id=f"{width}-{kind}")
        for width, height in G03_VIEWPORTS
        for kind in ("dashboard", "set")
    ],
)
def test_ui_g_03_no_horizontal_scroll(page: Page, manifest, width, height, kind):
    """G-03: a horizontally scrolled table hides the status it exists to show."""
    path = "/dashboard"
    if kind == "set":
        path = f"/po_sets/{manifest['scenarios']['merged_basic']['id']}/view"
    page.set_viewport_size({"width": width, "height": height})
    page.goto(path)
    scroll_width = page.evaluate("document.documentElement.scrollWidth")
    client_width = page.evaluate("document.documentElement.clientWidth")
    assert scroll_width <= client_width, (
        f"{path} at {width}x{height}: scrollWidth {scroll_width} > clientWidth {client_width}"
    )


def test_ui_g_05_a_foreign_origin_post_is_refused(page: Page, base_url, manifest, db):
    """G-05: no login is accepted risk; CSRF is not (audit P1-10)."""
    sc = manifest["scenarios"]["merged_basic"]
    assert sc["status"] == "merged"  # immutable: even a bypass would change nothing
    before = db.execute(
        "select status, merged_output_path from po_sets where id=?", (sc["id"],)
    ).fetchone()

    refused = page.request.post(
        f"{base_url}/po_sets/{sc['id']}/merge", headers={"Origin": "http://evil.example"}
    )
    assert refused.status == 403, refused.text()
    assert "Cross-origin request refused" in refused.text(), refused.text()

    # The same request from the app's own origin is allowed (it changes nothing:
    # the set is already merged and immutable).
    allowed = page.request.post(
        f"{base_url}/po_sets/{sc['id']}/merge", headers={"Origin": base_url}
    )
    assert allowed.status == 200, allowed.text()

    after = db.execute(
        "select status, merged_output_path from po_sets where id=?", (sc["id"],)
    ).fetchone()
    assert (after["status"], after["merged_output_path"]) == (
        before["status"],
        before["merged_output_path"],
    ), "the merge request changed a delivered packet"


def test_ui_g_06_every_page_has_its_own_title(page: Page, manifest):
    """G-06: the browser tab is how several open pages are told apart."""
    expected = {
        "/dashboard": "Dashboard — AAM Merger V3",
        "/unclassified": "Unclassified Documents — AAM Merger V3",
        "/quarantine": "Quarantine Review — AAM Merger V3",
        "/audit": "Audit Log — AAM Merger V3",
        "/manual/merger": "Merge PDF files — AAM Merger V3",
    }
    set_page = f"/po_sets/{manifest['scenarios']['merged_basic']['id']}/view"
    expected[set_page] = (
        f"PO Set {manifest['scenarios']['merged_basic']['po_no']} — 3-Way Reconciliation"
    )
    for path, title in expected.items():
        page.goto(path)
        assert page.title() == title, f"{path} has title {page.title()!r}"

    # An unknown set and a bad filter are pages too, and must still be
    # distinguishable in the tab strip.
    titles = dict(expected)
    for path in ("/po_sets/999999/view", "/dashboard?status=nonsense"):
        page.goto(path)
        titles[path] = page.title()
    assert all(title for title in titles.values()), titles
    assert len(set(titles.values())) == len(titles), f"two pages share a title: {titles}"


NAV_ARIA = "".join(
    [
        "- navigation:\n",
        '  - link "⚡ AAM Merger v3":\n',
        "    - /url: /dashboard\n",
        '  - link "📊 Reconciliation Dashboard":\n',
        "    - /url: /dashboard\n",
        # The count follows the holding area, which other tests change. Playwright
        # 1.64 only treats a whole unquoted scalar as a regex, so the link name
        # is one regex with the literal badge text inside it.
        "  - link /❓ Unmatched Files \\d+/:\n",
        "    - /url: /unclassified\n",
        '  - link "🚨 Flagged":\n',
        "    - /url: /quarantine\n",
        '  - link "📜 Audit Log":\n',
        "    - /url: /audit\n",
        '  - link "🧩 Quick PDF Merger":\n',
        "    - /url: /manual/merger\n",
        '  - link "Service Online":\n',
        "    - /url: /health",
    ]
)


def test_ui_g_07_the_nav_is_announced_the_same_way_every_time(page: Page):
    """G-07: the inherited chrome is what a screen reader hears on every page.

    The only part that follows the data is the Unclassified count badge, so it
    is the one value matched by a regular expression instead of a literal.
    """
    page.goto("/dashboard")
    expect(page.locator("nav.app-navbar")).to_match_aria_snapshot(NAV_ARIA)
