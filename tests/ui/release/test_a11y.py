"""Accessibility: axe on every page, keyboard reach, modal focus."""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, expect

from release_helpers import quarantine_row

PAGES = (
    "/dashboard",
    "/unclassified",
    "/quarantine",
    "/audit",
    "/settings",
    "/manual/merger",
)


def _serious(results) -> list[dict]:
    return [
        violation
        for violation in results.response["violations"]
        if violation.get("impact") in ("critical", "serious")
    ]


@pytest.mark.parametrize("path", PAGES, ids=[p.strip("/").replace("/", "-") for p in PAGES])
def test_no_critical_or_serious_axe_violations(page: Page, axe, artifacts, path: str):
    page.goto(path)
    page.wait_for_load_state("networkidle")
    results = axe.run(page)
    (artifacts / f"axe-{path.strip('/').replace('/', '-')}.json").write_text(
        json.dumps(results.response, indent=2), encoding="utf-8"
    )
    serious = _serious(results)
    assert not serious, results.generate_report()


def test_set_page_has_no_critical_or_serious_axe_violations(page: Page, axe, artifacts, manifest):
    sc = manifest["scenarios"]["merged_basic"]
    page.goto(f"/po_sets/{sc['id']}/view")
    page.wait_for_load_state("networkidle")
    results = axe.run(page)
    (artifacts / "axe-set-detail.json").write_text(
        json.dumps(results.response, indent=2), encoding="utf-8"
    )
    serious = _serious(results)
    assert not serious, results.generate_report()


def test_keyboard_reaches_the_dashboard_tabs(page: Page):
    page.goto("/dashboard")
    reached = None
    for _ in range(24):
        page.keyboard.press("Tab")
        reached = page.evaluate(
            "() => document.activeElement && document.activeElement.getAttribute('data-testid')"
        )
        if reached == "tab-all":
            break
    assert reached == "tab-all", f"keyboard never reached the status tabs (last focus: {reached})"


def test_keyboard_can_activate_a_subview(page: Page):
    page.goto("/unclassified")
    from release_helpers import wait_for_alpine

    wait_for_alpine(page)
    reached = None
    for _ in range(24):
        page.keyboard.press("Tab")
        reached = page.evaluate(
            "() => document.activeElement && document.activeElement.getAttribute('data-testid')"
        )
        if reached == "tab-subview-needs-po":
            break
    assert reached == "tab-subview-needs-po", (
        f"keyboard never reached the sub-view tabs (last focus: {reached})"
    )
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("tab-subview-needs-po")).to_have_class(
        re.compile(r"\bactive\b")
    )


def test_quarantine_modal_takes_focus_and_escape_closes_it(page: Page, manifest):
    sc = manifest["scenarios"]["quarantined_po_no_lines"]
    page.goto("/quarantine")
    row = quarantine_row(page, sc["id"])
    trigger = row.get_by_test_id("btn-archive-row")
    trigger.click()
    modal = row.get_by_test_id("archive-row-modal")
    expect(modal).to_have_class(re.compile(r"\bopen\b"))

    for _ in range(6):
        page.keyboard.press("Tab")
        inside = page.evaluate(
            "() => { const m = document.querySelector('.modal-backdrop.open'); "
            "return !!m && m.contains(document.activeElement); }"
        )
        assert inside, "Tab moved focus out of the open quarantine modal"

    page.keyboard.press("Escape")
    expect(modal).not_to_have_class(re.compile(r"\bopen\b"))
