"""Audit 2026-10-23 — P2-8 (409 detail) and P2-10 (search re-applies after poll).

`static/js/app.js` carries the toast and filter behaviour, so it is executed
in Node with a minimal DOM/Alpine stub: the assertions are on what the user
sees, not on the fact that a string appears in the source. The template
checks are structural (the filename must never reach an HTML parser).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "static" / "js" / "app.js"
DASHBOARD = REPO_ROOT / "templates" / "dashboard.html"
MANUAL_MERGER = REPO_ROOT / "templates" / "manual_merger.html"
NODE = shutil.which("node")


HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const listeners = {};
const stores = {};
const dataDefs = {};
let rows = [];

globalThis.window = globalThis;
globalThis.document = {
  addEventListener(name, fn) {
    (listeners[name] = listeners[name] || []).push(fn);
  },
  removeEventListener(name, fn) {
    listeners[name] = (listeners[name] || []).filter((f) => f !== fn);
  },
  querySelectorAll() {
    return rows;
  },
};
globalThis.Alpine = {
  // Alpine.store(name) is a getter; Alpine.store(name, value) registers.
  store(name, value) {
    if (arguments.length > 1) stores[name] = value;
    return stores[name];
  },
  data(name, factory) { dataDefs[name] = factory; },
};

vm.runInThisContext(fs.readFileSync(process.argv[2], 'utf8'), { filename: 'app.js' });
(listeners['alpine:init'] || []).forEach((fn) => fn());

const out = {};

function raise409(body) {
  const items = stores.toasts.items;
  const before = items.length;
  listeners['htmx:responseError'][0]({
    detail: { xhr: { status: 409, responseText: body } },
  });
  if (items.length === before) return null;
  return items[items.length - 1].message;
}

out.nonLockDetail = raise409(JSON.stringify({
  detail: 'Auto-merge refuses: the set has no SI number.',
}));
out.lockDetail = raise409(JSON.stringify({
  detail: 'PO Set 7 is locked by a background operation (in progress).',
}));
out.noDetail = raise409('Upstream error');

const search = dataDefs.tableSearch();
search.init();
search.query = 'po100';

rows = [
  { dataset: { po: 'po100', status: 'pending' }, style: { display: '' } },
  { dataset: { po: 'po200', status: 'merged' }, style: { display: '' } },
];
search.filterRows();
out.filterAfterQuery = rows.map((r) => r.style.display);

// The poll replaces #po-table with fresh rows, which start visible.
rows = [
  { dataset: { po: 'po100', status: 'pending' }, style: { display: '' } },
  { dataset: { po: 'po200', status: 'merged' }, style: { display: '' } },
];
listeners['htmx:afterSwap'][0]({ target: { id: 'po-table' } });
out.filterAfterSwap = rows.map((r) => r.style.display);

// A swap somewhere else must not touch the table.
rows = [{ dataset: { po: 'po200', status: 'merged' }, style: { display: 'none' } }];
listeners['htmx:afterSwap'][0]({ target: { id: 'something-else' } });
out.afterOtherSwap = rows.map((r) => r.style.display);

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def js():
    if NODE is None:
        pytest.skip("node is required to execute static/js/app.js")
    # The harness lives in a temp file so a failure shows a real line number.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "harness.js"
        path.write_text(HARNESS, encoding="utf-8")
        result = subprocess.run(
            [NODE, str(path), str(APP_JS)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_lock_409_keeps_the_wait_wording(js):
    message = js["lockDetail"].lower()

    assert "locked" in message or "in progress" in message
    assert "wait a moment" in message


def test_a_non_lock_409_shows_the_server_detail(js):
    message = js["nonLockDetail"]

    assert "no SI number" in message
    assert "currently locked" not in message.lower()


def test_a_409_without_json_detail_does_not_claim_a_lock(js):
    message = js["noDetail"].lower()

    assert "currently locked" not in message
    assert "locked by a background operation" not in message


def test_search_filters_rows(js):
    assert js["filterAfterQuery"] == ["", "none"]


def test_search_is_reapplied_after_the_htmx_poll(js):
    assert js["filterAfterSwap"] == ["", "none"]


def test_search_ignores_unrelated_swaps(js):
    assert js["afterOtherSwap"] == ["none"]


def test_dashboard_no_longer_promises_vendor_filtering():
    html = DASHBOARD.read_text(encoding="utf-8")

    assert "vendor" not in html.lower()
    assert "Filter by PO number or status" in html


def test_manual_merger_builds_rows_without_innerHTML():
    script = (REPO_ROOT / "static" / "js" / "manual_merger.js").read_text(encoding="utf-8")
    template = MANUAL_MERGER.read_text(encoding="utf-8")

    assert ".innerHTML" not in script + template
    assert "insertAdjacentHTML" not in script + template
    # The filename specifically must be assigned as text.
    assert "name.textContent = item.name;" in script
