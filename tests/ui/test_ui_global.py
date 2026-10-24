"""Global page behaviour (UI-G-*): keyboard focus, inherited chrome.

Each test here opens a page and inspects behaviour that is not one page's
data; nothing changes a mutable scenario unless the test owns it.
"""

import re

from playwright.sync_api import Page, expect


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
