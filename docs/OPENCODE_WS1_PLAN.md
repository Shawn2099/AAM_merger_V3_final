# WS1 plan: finish the browser UI test suite (worker: DeepSeek v4.1 flash)

Written 2026-10-25 by the architect (Claude Opus). This file is your complete work order. Do the steps
in order. Do not improvise. If a step does not work as written, stop and report (section 9).

Read first, in this order: `CLAUDE.md` ("Conventions that bite"), `docs/OPENCODE_UI_TEST_BRIEF.md`
(style rules, still binding except where this file overrides it), `docs/UI_TEST_PLAN.md`,
`tests/ui/conftest.py`, `tests/ui/test_ui_set_detail.py`, `scripts/ui_test_env.py`.

The expected seeded states (4.7) and messages in this plan were derived from reading the code; nothing
here has been executed yet. If reality differs, the stop conditions apply: report, never adjust a seed
or an expected value to fit.

Scope: the 29 TODO cases in `docs/UI_TEST_PLAN.md` section 3, plus 6 new cases for the two operator
states `settling` and `placed_by_number_only` (35 cases). UI_TEST_PLAN section 4 (screenshots,
failure injection, two tabs, accessibility smoke) is OUT of scope for this packet.

Do NOT test dropped features: no system status pill (PF-11), no misread hold, no "Move to another PO"
action (WS2). If you see one of these words in a doc, ignore it.

---

## 1. Hard rules (break one and the work is rejected)

1. **Allowed files only.**
   - `tests/ui/**` (new tests go in the existing files named in section 5, except `tests/ui/test_ui_scan.py`).
   - `scripts/ui_test_env.py` (only the additions in section 4).
   - `templates/*.html`: ONLY the exact `data-testid` insertions listed in section 3. Nothing else in a
     template: no reformatting, no other attribute, no text change. (This overrides the "do not edit
     templates" line in OPENCODE_UI_TEST_BRIEF.md; DEPLOYMENT_PLAN WS1 allows testid-only edits.)
   - Forbidden: `src/`, `static/`, `alembic/`, `config*.yaml`, `.env`, `data/`, every test outside `tests/ui/`.
2. **No git.** No add, commit, stash, checkout, reset, push. Leave everything in the working tree.
3. **No network, no API key, no paid provider.** The harness blanks `OPENROUTER_API_KEY`. Never set it.
   Never start the app against `config.yaml` or `data/aam_merger.db`.
4. **Never weaken an assertion.** Do not change an expected value, loosen a comparison, add a retry
   loop, add `time.sleep`, or add `skip`. If the product disagrees with this plan, mark that test
   `@pytest.mark.xfail(strict=True, reason="SUSPECTED PRODUCT BUG UI-<case id>: <one line>")` and
   report it as a finding. Only two xfails are pre-approved (S-14b and G-01b, section 5); any other
   xfail needs evidence in the report (request, response, DB value).
5. **Never edit a DONE test** (any test that exists today in `tests/ui/`). If one breaks, stop and report.
6. Independence: a test that changes data owns exactly ONE `mutable` scenario, named in this plan.
   Expected values come from the DB (`db` fixture) or the manifest, never from a number you read off
   the screen. Tests must pass in any order.
7. Locators: `page.get_by_test_id(...)` first; `get_by_role` / `get_by_text` / an element `#id` only
   where this plan says so. No CSS class selectors, no positional XPath, no `nth-child`.
8. Every action that changes data is wrapped in `page.expect_response(...)` for its request, and the
   response status is asserted.
9. Quantities are integers x1000 in the DB. To compare a page number with the DB, convert the page
   text to an integer: `round(float(text) * 1000)`, then compare integers.
10. Done means: the whole `tests/ui` suite passes 3 times in a row AND once in reverse file order;
    ruff check and ruff format are clean; the commands in section 7 pass.

---

## 2. Order of work

| Step | What | Stop if |
|---|---|---|
| 0 | Baseline: run the commands in 7.1 once. Record the pass count. Then run 7.3 and copy `$env:TEMP\aam_ui_test\manifest.json` to `$env:TEMP\aam_ui_manifest_step0.json` (step 2 compares against this copy). | any DONE test fails (report, do nothing else) |
| 1 | Template testid insertions (section 3). Then run 7.2 (non-UI tests that read templates) and 7.1. | any test fails: revert that one insertion, list it under NEEDS |
| 2 | Harness additions (section 4). Build by hand (7.3) and check every new manifest entry against the table in 4.7. | a seeded status or reason differs from 4.7: STOP, report the manifest entry. Do not change the seed to "make it fit" |
| 3 | Read-only cases (section 5, marked RO), one at a time: write, run alone with `-k`, then run the whole file | |
| 4 | Data-changing cases (marked MUT), one at a time | |
| 5 | D-12 in its own environment (`tests/ui/test_ui_scan.py`) | |
| 6 | Global cases (G-*) | |
| 7 | Stability: 3 identical runs, reverse order, ruff, manual-merger e2e (section 7) | |
| 8 | Report (section 9) | |

Two failed attempts at one case: stop working on that case, list it under NEEDS with the error, move on.

---

## 3. Template `data-testid` insertions (exact text; each `old` occurs exactly once, verified)

Insert the attribute exactly as shown. Do NOT touch `<span class="nav-counter-badge">`:
`tests/test_business_scenarios_ui.py` asserts that exact string.

### templates/po_set_detail.html
| # | old | new |
|---|---|---|
| T1 | `<div class="muted" style="font-size:0.9rem;">{{ notice }}</div>` | `<div class="muted" data-testid="notice" style="font-size:0.9rem;">{{ notice }}</div>` |
| T2 | `<div style="font-weight:700; color:#374151; margin-bottom:4px;">📦 Archived PO Set — read-only</div>` | `<div data-testid="archived-banner" style="font-weight:700; color:#374151; margin-bottom:4px;">📦 Archived PO Set — read-only</div>` |
| T3 | `href="/po_sets/{{ po_set.id }}/merged_pdf"` | `data-testid="btn-download-merged" href="/po_sets/{{ po_set.id }}/merged_pdf"` |
| T4 | `<span class="qty-val" style="color:#0f172a;">` | `<span class="qty-val" data-testid="cell-po-qty" style="color:#0f172a;">` |
| T5 | `<span class="qty-val {% if r.dn_agg_qty` | `<span data-testid="cell-dn-qty" class="qty-val {% if r.dn_agg_qty` |
| T6 | `<span class="qty-val {% if r.si_agg_qty` | `<span data-testid="cell-si-qty" class="qty-val {% if r.si_agg_qty` |

### templates/base.html
| # | old | new |
|---|---|---|
| T7 | `<a href="/dashboard" class="nav-link` | `<a href="/dashboard" data-testid="nav-dashboard" class="nav-link` |
| T8 | `<a href="/unclassified" class="nav-link` | `<a href="/unclassified" data-testid="nav-unclassified" class="nav-link` |
| T9 | `<a href="/quarantine" class="nav-link` | `<a href="/quarantine" data-testid="nav-quarantine" class="nav-link` |
| T10 | `<a href="/audit" class="nav-link` | `<a href="/audit" data-testid="nav-audit" class="nav-link` |
| T11 | `<a href="/manual/merger" class="nav-link` | `<a href="/manual/merger" data-testid="nav-merger" class="nav-link` |
| T12 | `class="drawer-backdrop"` | `data-testid="pdf-drawer" class="drawer-backdrop"` |
| T13 | `title="Close (Esc)"` | `data-testid="btn-drawer-close" title="Close (Esc)"` |

### templates/unclassified.html
| # | old | new |
|---|---|---|
| T14 | `<span class="badge badge-gray" style="font-size:0.8rem;">{{ documents\|length }} awaiting classification</span>` | `<span class="badge badge-gray" data-testid="unclassified-count" style="font-size:0.8rem;">{{ documents\|length }} awaiting classification</span>` |
| T15 | `<div x-show="norm.length > 0"` | `<div data-testid="po-key-preview" x-show="norm.length > 0"` |
| T16 | `title="Preview PDF in slide-over drawer"` | `data-testid="btn-preview" title="Preview PDF in slide-over drawer"` |

(In T14 the `\|` is a markdown escape; the file contains a plain `|`.)

### templates/_quarantine_table.html
| # | old | new |
|---|---|---|
| T17 | `<td class="reason-cell" style="max-width:420px;">` | `<td class="reason-cell" data-testid="quarantine-row-reason" style="max-width:420px;">` |
| T18 | `class="btn btn-secondary btn-sm">Inspect` | `class="btn btn-secondary btn-sm" data-testid="btn-inspect-row">Inspect` |
| T19 | `<div id="qd-modal-{{ ps.id }}" class="modal-backdrop">` | `<div id="qd-modal-{{ ps.id }}" data-testid="archive-row-modal" class="modal-backdrop">` |
| T20 | `classList.remove('open')">Cancel</button>` | `classList.remove('open')" data-testid="btn-cancel-archive-row">Cancel</button>` |
| T21 | `<button class="btn btn-danger" hx-delete=` | `<button class="btn btn-danger" data-testid="btn-confirm-archive-row" hx-delete=` |

Self-check after step 1: `git diff --stat templates` (read-only git is allowed for this one check)
shows only these four files, and `git diff -U0 templates` shows only lines whose sole change is an
added `data-testid="..."`. If not, undo the extra change.

Existing hooks you will use (already in the templates, verified): `po-row`, `row-reason`,
`row-doc-count`, `kpi-*`, `tab-*`, `btn-scan-input`, `set-po`, `reason-card`, `flag`, `matrix-row`,
`doc-row` (+ `data-doc-id`), `btn-preview`, `btn-merge-now`, `btn-rematch`, `btn-reextract`,
`btn-force-merge`, `btn-confirm-force-merge`, `btn-toggle-customs`, `btn-archive`,
`btn-confirm-archive`, `btn-confirm-placement`, `btn-remove-supporting-<id>`, `upload-form`,
`sync-running-note`, `unclassified-row` (+ `data-doc-id`), `select-doc-type`, `input-po-no`,
`btn-route`, `quarantine-row` (+ `data-set-id`), `btn-archive-row`, `audit-row`.
Element ids you may use: `#status-filter`, `#force-merge-justification`.

---

## 4. Harness additions (`scripts/ui_test_env.py`)

### 4.1 Add to `SCENARIOS` (append after `customs_remove_target`)
```python
    # Settle window (D5): reconciled, but the newest file is younger than the
    # settle time, so the engine holds it. Only these sets are reconciled with
    # SETTLE_MINUTES set (see build()).
    "settling": {
        "po": "7000019",
        "settle_minutes": 240,
        "docs": [
            ("PO", "PO-7000019.pdf", None, [L("1", "4", "Spring")]),
            ("DN", "DN-7000019.pdf", "7000019", [L("1", "4", "Spring")]),
            ("SI", "INV-7000019.pdf", "7000019", [L("1", "4", "Spring")]),
        ],
    },
    "settling_merge_now_target": {
        "po": "7000020",
        "mutable": True,
        "settle_minutes": 240,
        "docs": [
            ("PO", "PO-7000020.pdf", None, [L("1", "6", "Washer")]),
            ("DN", "DN-7000020.pdf", "7000020", [L("1", "6", "Washer")]),
            ("SI", "INV-7000020.pdf", "7000020", [L("1", "6", "Washer")]),
        ],
    },
    # D9: the "-b" DN prints no PO number. Its DN number (DN-<po>, same as the
    # first DN) places it by number only, so the set holds. It carries a
    # DIFFERENT line number, or after Confirm the duplicate-DN guard would
    # quarantine the set instead of merging it.
    "placed_by_number_only": {
        "po": "7000021",
        "docs": [
            ("PO", "PO-7000021.pdf", None, [L("1", "5", "Gear"), L("2", "3", "Shaft")]),
            ("DN", "DN-7000021-a.pdf", "7000021", [L("1", "5", "Gear")]),
            ("DN", "DN-7000021-b.pdf", None, [L("2", "3", "Shaft")]),
            ("SI", "INV-7000021.pdf", "7000021", [L("1", "5", "Gear"), L("2", "3", "Shaft")]),
        ],
    },
    "confirm_placement_target": {
        "po": "7000022",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000022.pdf", None, [L("1", "5", "Cog"), L("2", "3", "Axle")]),
            ("DN", "DN-7000022-a.pdf", "7000022", [L("1", "5", "Cog")]),
            ("DN", "DN-7000022-b.pdf", None, [L("2", "3", "Axle")]),
            ("SI", "INV-7000022.pdf", "7000022", [L("1", "5", "Cog"), L("2", "3", "Axle")]),
        ],
    },
    # S-13's own target (pending_no_dn is changed by U-04, so it cannot be used).
    "rematch_target": {
        "po": "7000023",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000023.pdf", None, [L("1", "7", "Hinge")]),
            ("SI", "INV-7000023.pdf", "7000023", [L("1", "7", "Hinge")]),
        ],
    },
    "merge_now_mismatch_target": {
        "po": "7000024",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000024.pdf", None, [L("1", "10", "Valve seat")]),
            ("DN", "DN-7000024.pdf", "7000024", [L("1", "8", "Valve seat")]),
            ("SI", "INV-7000024.pdf", "7000024", [L("1", "10", "Valve seat")]),
        ],
    },
    # S-14b's own target (S-14 owns merge_now_mismatch_target).
    "merge_now_reason_target": {
        "po": "7000029",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000029.pdf", None, [L("1", "10", "Seal ring")]),
            ("DN", "DN-7000029.pdf", "7000029", [L("1", "8", "Seal ring")]),
            ("SI", "INV-7000029.pdf", "7000029", [L("1", "10", "Seal ring")]),
        ],
    },
    "audit_force_merge_target": {
        "po": "7000025",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000025.pdf", None, [L("1", "10", "Flange")]),
            ("DN", "DN-7000025.pdf", "7000025", [L("1", "9", "Flange")]),
            ("SI", "INV-7000025.pdf", "7000025", [L("1", "10", "Flange")]),
        ],
    },
    "audit_archive_target": {
        "po": "7000026",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000026.pdf", None, [L("1", "5", "Gland")]),
            ("DN", "DN-7000026.pdf", "7000026", [L("9", "5", "Gland")]),
            ("SI", "INV-7000026.pdf", "7000026", [L("1", "5", "Gland")]),
        ],
    },
    # Quarantined, then archived during build(): S-20 checks it is read-only.
    "archived_readonly": {
        "po": "7000027",
        "archive_in_build": True,
        "docs": [
            ("PO", "PO-7000027.pdf", None, [L("1", "5", "Bush")]),
            ("DN", "DN-7000027.pdf", "7000027", [L("8", "5", "Bush")]),
            ("SI", "INV-7000027.pdf", "7000027", [L("1", "5", "Bush")]),
        ],
    },
    # X-02: the second PO document's stored filename is replaced by HTML after
    # the build loop (before reconcile, so the reason names it too).
    "hostile_name_in_set": {
        "po": "7000028",
        "hostile_doc_index": 1,
        "docs": [
            ("PO", "PO-7000028-p1.pdf", None, [L("1", "2", "Pin")]),
            ("PO", "PO-7000028-p2.pdf", None, [L("1", "2", "Pin")]),
            ("DN", "DN-7000028.pdf", "7000028", [L("1", "2", "Pin")]),
            ("SI", "INV-7000028.pdf", "7000028", [L("1", "2", "Pin")]),
        ],
    },
```

### 4.2 Add to `HELD` (append after `held_reclassify_no_po`)
```python
    # U-10's own document: reclassified as a DN with a fresh PO number (7000090).
    "held_count_target": {
        "kind": "DN",
        "file": "DN-COUNT.pdf",
        "po_ref": None,
        "state": "read",
        "mutable": True,
        "lines": [L("1", "2", "Count item")],
    },
```

### 4.3 In `build()`: rename the hostile document (insert right after the `for name, sc in SCENARIOS.items():` loop that ends with `scenario_docs[name] = ids`, before `held_ids: dict[str, int] = {}`)
```python
    for name, sc in SCENARIOS.items():
        idx = sc.get("hostile_doc_index")
        if idx is not None:
            with Session(eng) as s:
                s.get(Document, scenario_docs[name][idx]).original_filename = HOSTILE_NAME
                s.commit()
```

### 4.4 In `build()`: replace the final reconcile loop
Replace exactly:
```python
    with Session(eng) as s:
        ids = [r[0] for r in s.query(POSet.id).order_by(POSet.id)]
    for ps_id in ids:
        reconcile_po_set(ps_id, cfg)
```
with:
```python
    from app.services.reconciliation import SETTLE_MINUTES

    settle_by_po = {
        sc["po"]: sc["settle_minutes"] for sc in SCENARIOS.values() if sc.get("settle_minutes")
    }
    with Session(eng) as s:
        ids = [(r[0], r[1]) for r in s.query(POSet.id, POSet.po_no_normalized).order_by(POSet.id)]
    for ps_id, po_no in ids:
        minutes = settle_by_po.get(po_no)
        if minutes:
            # Only a poller-started sync sets this in production (D5).
            token = SETTLE_MINUTES.set(minutes)
            try:
                reconcile_po_set(ps_id, cfg)
            finally:
                SETTLE_MINUTES.reset(token)
        else:
            reconcile_po_set(ps_id, cfg)

    from app.services.quarantine import delete_quarantined

    for sc in SCENARIOS.values():
        if sc.get("archive_in_build"):
            with Session(eng) as s:
                ps = s.query(POSet).filter(POSet.po_no_normalized == sc["po"]).one()
                ps_id, status = ps.id, ps.status.value
            if status != "quarantined":
                raise SystemExit(f"{sc['po']} must be quarantined before archiving, got {status}")
            delete_quarantined(ps_id, cfg, justification=None, source="ui_test_env")
```

### 4.5 Manifest fields
In the `manifest["scenarios"][name] = {...}` dict add:
```python
                "archived": ps.archived_at is not None,
                "settle_minutes": sc.get("settle_minutes", 0),
```
and in each `docs` entry add `"po_no_source": d.po_no_source,`.

### 4.6 Do not change anything else in the harness.

### 4.7 Expected seeded state (check after `uv run python scripts/ui_test_env.py --root $env:TEMP\aam_ui_test`, read `$env:TEMP\aam_ui_test\manifest.json`)
| Entry | status | reason must contain | other |
|---|---|---|---|
| settling | pending | starts with `Reconciled; waiting for more files to arrive` | 3 docs |
| settling_merge_now_target | pending | same prefix | 3 docs |
| placed_by_number_only | pending | `DN-7000021-b.pdf was placed in this set only because its DN number matches` | 4 docs; exactly one doc has `po_no_source == "dn_number"` and it is `DN-7000021-b.pdf` |
| confirm_placement_target | pending | `DN-7000022-b.pdf was placed in this set only` | 4 docs; one `dn_number` doc |
| rematch_target | pending | `No delivery note attached to this set yet (not received, still being read, or waiting on the Unclassified page)` | |
| merge_now_mismatch_target | mismatched | `delivered 8 of 10` | |
| audit_force_merge_target | mismatched | `delivered 9 of 10` | |
| audit_archive_target | quarantined | `could not be matched` | |
| archived_readonly | quarantined | | `archived: true` |
| hostile_name_in_set | pending | `holds 2 PO documents` and `<b>bold</b>` | one doc `file` equals HOSTILE_NAME |
| held.held_count_target | (held) | | `po_set_id: null`, `extraction_status: valid` |
| merge_now_reason_target | mismatched | `delivered 8 of 10` | |

Every pre-existing entry keeps the status and reason it had at step 0 (compare with
`$env:TEMP\aam_ui_manifest_step0.json`; set ids may shift, compare by scenario name).

---

## 5. The cases

Format per case: **ID (RO/MUT) - file - function name**; scenario; locators; steps; assertions
(and what they are compared against). Helpers you may add at the top of a test file:
```python
def _num(text: str) -> int:
    """Page quantity text -> DB integer (x1000)."""
    return round(float(text.strip()) * 1000)

ORPHAN = re.compile(r"\b(DN|SI) line (\S+) \(qty ([0-9.]+)\)")
```

### Dashboard: `tests/ui/test_ui_dashboard.py` (reuse `_active_sets`, `_number_in`)

**D-06 (RO) `test_ui_d_06_status_dropdown_filters_and_keeps_the_choice`**
- `page.goto("/dashboard")`; `with page.expect_navigation(): page.locator("#status-filter").select_option("mismatched")`.
- Assert URL matches `status=mismatched`; `expect(page.get_by_test_id("po-row")).to_have_count(N)` where
  N = DB count `select count(*) from po_sets where status='mismatched' and archived_at is null`;
  every row `data-status == "mismatched"`; `expect(page.locator("#status-filter")).to_have_value("mismatched")`.
- Then `select_option("")` (All statuses): row count == DB count of all non-archived sets; value `""`.

**D-08 (RO) `test_ui_d_08_the_table_refreshes_itself`**
- `page.goto("/dashboard")`; `with page.expect_request(re.compile(r"/dashboard/table"), timeout=7000) as req: pass`
  (place the goto before; the poll is `every 5s`). Assert `req.value.method == "GET"`. No sleep.

**D-09 (RO) `test_ui_d_09_nav_links_and_the_unclassified_badge`**
- For each (testid, url regex): `nav-unclassified` `/unclassified$`, `nav-quarantine` `/quarantine$`,
  `nav-audit` `/audit$`, `nav-merger` `/manual/merger$`, `nav-dashboard` `/dashboard$`: click it from the
  previous page, `page.wait_for_url(...)`.
- Badge: on `/unclassified`, R = `page.get_by_test_id("unclassified-row").count()` (after
  `expect(...).to_have_count` is not possible without a number, so first `expect(page.get_by_test_id("unclassified-row").first).to_be_visible()`).
  Then the number inside `page.get_by_test_id("nav-unclassified")` (use `_number_in`) == R, and the
  number in `unclassified-count` == R. Do NOT use `len(manifest["held"])` (U-04 changes it).

**D-10 (RO) `test_ui_d_10_mapped_row_says_lines_mapped_x10`**: row `[data-testid="po-row"][data-set-id="<merged_mapped_x10 id>"]`
  contains `lines mapped (x10)`; DB `line_mapping_rule` for that set == `"x10"`.

**D-11 (RO) `test_ui_d_11_awaiting_customs_row_shows_the_gate`**: row for `awaiting_customs` contains
  `Awaiting Customs`; its `row-reason` contains DB `reconcile_reason[:40]` and the words `CUSTOMS` and `SHIPPING`.

**D-13 (RO) `test_ui_d_13_settling_row_is_pending_and_says_why`** (new state `settling`)
- DB reason R for `settling`; assert `R.startswith(SETTLING_PREFIX)` (import
  `from app.services.reconciliation import SETTLING_PREFIX`).
- Row: `data-status == "pending"`, contains `Pending / Waiting`, `row-reason` contains `R[:40]` and
  matches `re.compile(r"merges after \d{2}:\d{2}")`.

**D-14 (RO) `test_ui_d_14_placed_by_number_row_says_why`**: row for `placed_by_number_only`:
  `data-status == "pending"`, `row-reason` contains DB reason `[:40]` and `was placed in this set only because its DN number matches`.

**D-12 (RO, own environment) `tests/ui/test_ui_scan.py::test_ui_d_12_scan_button_answers_without_breaking_the_page`**
- Why separate: `POST /sync` starts a real sync. While it runs every action button on every set page
  is disabled and returns 409, and the sweep re-reconciles every open set with settle 0 (it would
  merge `settling`). It must never run in the shared session environment.
- Module fixture (copy this; do not import from conftest):
```python
REPO = Path(__file__).resolve().parents[2]

def _load_env_module():
    spec = importlib.util.spec_from_file_location("ui_test_env_scan", REPO / "scripts" / "ui_test_env.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

@pytest.fixture(scope="module")
def scan_env(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui_test_scan")
    env = {**os.environ, "OPENROUTER_API_KEY": ""}
    subprocess.run([sys.executable, str(REPO / "scripts" / "ui_test_env.py"), "--root", str(root)],
                   cwd=str(REPO), env=env, check=True, capture_output=True, text=True)
    proc, base = _load_env_module().serve(root)
    try:
        yield SimpleNamespace(base=base, root=root)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if proc.stdout is not None:
            proc.stdout.close()
```
- Test: `page.goto(scan_env.base + "/dashboard")` (absolute URL). `with page.expect_response(re.compile(r"/sync$")) as resp: page.get_by_test_id("btn-scan-input").click()`.
  Assert `resp.value.status == 200` and `resp.value.json()["status"] == "sync started"`;
  `expect(page.locator(".toast-success")).to_contain_text("Sync scan triggered")`;
  `expect(page.get_by_test_id("po-row").first).to_be_visible()`. Record in the report what toast text
  appeared and how long the sync took if you checked `/sync/status` by hand. Do not wait for the sync.

### Set page: `tests/ui/test_ui_set_detail.py` (reuse `_open`)

**S-09 (RO) `test_ui_s_09_matrix_totals_equal_the_database_for_every_set`**
- Sets: every row of `select id from po_sets` (include archived; the set page renders them too).
- For each set: `page.goto(f"/po_sets/{id}/view")`; rows = `matrix-row`.
- Expected row count = DB `select count(distinct li.line_item_no) from line_items li join documents d on d.id=li.document_id where d.po_set_id=? and d.doc_type='PO'`
  (all seeded PO lines are numbered and unique per set). A set with no PO lines must show 0 rows.
- For each type T in PO, DN, SI: page column sum = sum of `_num(row.get_by_test_id(f"cell-{t}-qty").inner_text())`
  over rows. Orphan sum = sum of `_num(m.group(3))` for every `ORPHAN` match in the text of every
  `flag` whose group(1) == T (PO has no orphans). DB sum = `select coalesce(sum(li.quantity),0) from line_items li join documents d on d.id=li.document_id where d.po_set_id=? and d.doc_type=?`
  (no `archived_at` filter: the page does not filter either).
- Assert page column sum + orphan sum == DB sum, for every set and type. Collect problems in a list and
  assert the list is empty at the end (like D-01), so one run shows every failing set.

**S-10 (RO) `test_ui_s_10_two_po_documents_reason_names_both_files`**: `pending_two_po_documents`:
  `reason-card` contains `holds 2 PO documents`, `PO-7000006-p1.pdf`, `PO-7000006-p2.pdf`.

**S-11 (RO) `test_ui_s_11_awaiting_customs_says_both_documents_are_needed`**: `awaiting_customs`:
  `reason-card` contains `CUSTOMS` and `SHIPPING` and DB reason `[:40]`; `upload-form` visible;
  the clearance line (locator copied from S-21: `page.locator("div", has=page.locator("strong", has_text="Customs Clearance:")).last`) contains `Missing`.

**S-12 (MUT `upload_customs_target`) `test_ui_s_12_upload_refuses_a_non_pdf_and_attaches_two_pdfs`**
- Before: D0 = DB `select count(*) from documents where po_set_id=? and archived_at is null`.
- Non-PDF: `form = page.get_by_test_id("upload-form")`;
  `form.locator("input[type=file]").set_input_files({"name": "not-a-pdf.pdf", "mimeType": "application/pdf", "buffer": b"hello, not a pdf"})`;
  `form.locator("select[name=doc_type]").select_option("CUSTOMS")`;
  `with page.expect_response(re.compile(rf"/po_sets/{id}/upload$")) as r: form.get_by_role("button", name="Upload & Attach").click()`.
  Assert `r.value.status == 302`; `expect(page.get_by_test_id("notice")).to_contain_text("not a PDF file")`;
  DB count == D0.
- Valid PDFs: build two in memory with pypdf (`PdfWriter(); add_blank_page(width=611, height=791); add_metadata({"/Title": "ui-s-12-customs"})`
  and width 613, Title `ui-s-12-shipping`) so their bytes are unique. Upload the first as CUSTOMS,
  then reopen the set page and upload the second as SHIPPING, each inside `expect_response` (302).
- Assert: `doc-row` count == D0 + 2; DB count == D0 + 2; DB has one CUSTOMS and one SHIPPING doc for the set;
  DB `customs_doc_count == 2`.

**S-13 (MUT `rematch_target`) `test_ui_s_13_rematch_reloads_and_keeps_the_reason`**
- Before = DB reason. Click `btn-rematch` inside `expect_response(redo_match)` (200).
- `expect(page.locator(".toast-success")).to_contain_text("re-matched")` (survives the reload).
- `expect(page.get_by_test_id("reason-card")).to_contain_text(before[:40])`; DB reason after == before; DB status `pending`.

**S-14 (MUT `merge_now_mismatch_target`) `test_ui_s_14_merge_now_on_a_mismatch_does_not_merge`**
- Click `btn-merge-now` inside `expect_response(re.compile(rf"/po_sets/{id}/merge$"))`; status 200;
  `resp.value.json()["status"] == "mismatched"`.
- `expect(page.locator(".toast-warning")).to_contain_text("Not merged - still mismatched")`.
- DB status `mismatched`, `merged_output_path` NULL; `reason-card` contains DB reason `[:40]`.

**S-14b (pre-approved xfail) `test_ui_s_14b_merge_now_toast_states_the_quantity_difference`**
`@pytest.mark.xfail(strict=True, reason="SUSPECTED PRODUCT BUG UI-S14: Merge Now on a mismatched set returns reason=None, so the toast does not say what disagrees (known WS1b finding)")`
- Own scenario `merge_now_reason_target` (MUT); same click as S-14. Assert the `.toast-warning` contains `delivered 8 of 10`.
- If S-14b unexpectedly passes, that is a strict-xfail failure: report it, do not remove the marker.

**S-18 (RO) `test_ui_s_18_preview_drawer_closes_by_button_and_by_escape`**: `merged_basic`, first doc row:
  click its `btn-preview`; `expect(page.get_by_test_id("pdf-drawer")).to_have_class(re.compile(r"\bopen\b"))`;
  click `btn-drawer-close`; expect not to have class `open`. Open again; `page.keyboard.press("Escape")`; expect not `open`.

**S-19 (RO) `test_ui_s_19_merged_set_downloads_the_merged_pdf`**: `merged_basic`:
  `with page.expect_download() as d: page.get_by_test_id("btn-download-merged").click()`;
  bytes of `d.value.path()` == `Path(DB merged_output_path).read_bytes()`, and start with `b"%PDF"`.

**S-20 (RO) `test_ui_s_20_an_archived_set_is_read_only`**: `archived_readonly`:
  `archived-banner` visible; each of `btn-toggle-customs`, `btn-merge-now`, `btn-rematch`,
  `btn-reextract`, `btn-force-merge`, `btn-archive`, `upload-form`, `btn-confirm-placement` has count 0,
  and `[data-testid^="btn-remove-supporting-"]` has count 0.
  API check (no change expected): `r = page.request.post(f"{base_url}/po_sets/{id}/redo_match")` -> `r.status == 409`
  and `"archived" in r.text()`. DB status still `quarantined`, `archived_at` not null.

**S-22 (RO) `test_ui_s_22_settling_set_page_shows_why_it_waits`** (state `settling`):
  `reason-card` contains DB reason `[:40]` and `Why this set is waiting`; every `matrix-row` contains
  `Match`; `btn-merge-now` enabled; DB status `pending`, reason startswith `SETTLING_PREFIX`.

**S-23 (MUT `settling_merge_now_target`) `test_ui_s_23_merge_now_merges_a_settling_set_at_once`**
  (D5: only the poller waits; Merge Now has settle 0): click `btn-merge-now` in `expect_response` (200,
  json status `merged`); `expect(page.locator(".toast-success")).to_contain_text("Merged:")`;
  DB status `merged`; `Path(merged_output_path).read_bytes()[:4] == b"%PDF"`.

**S-24 (RO) `test_ui_s_24_placed_by_number_set_offers_confirm_on_that_document_only`** (state `placed_by_number_only`):
- placed = the manifest doc with `po_no_source == "dn_number"`; po_doc = the manifest doc with type `PO`.
- `reason-card` contains DB reason `[:40]` and `DN-7000021-b.pdf`.
- `expect(page.get_by_test_id("btn-confirm-placement")).to_have_count(1)`; and
  `page.locator(f'[data-testid="doc-row"][data-doc-id="{placed["id"]}"]').get_by_test_id("btn-confirm-placement")` is visible.
- API negative (no change): `page.request.post(f"{base_url}/po_sets/{id}/documents/{po_doc['id']}/confirm_placement")`
  -> 409 and `needs no confirmation` in the body. DB status still `pending`.

**S-25 (MUT `confirm_placement_target`) `test_ui_s_25_confirming_the_placement_lets_the_set_merge`**
- Click `btn-confirm-placement` inside `expect_response(re.compile(r"/confirm_placement$"))`; status 200; json status `merged`.
- After the reload: `expect(page.get_by_test_id("btn-confirm-placement")).to_have_count(0)`.
- DB: placed doc `po_no_source == "manual"`; set status `merged`; packet starts with `%PDF`;
  `select count(*) from audit_log where po_set_id=? and action='manual_status_change' and detail like '%confirmed_placement%'` == 1.
- (No toast is shown for this action today. Do not assert one; note it in the report.)

**X-02 (RO) `test_ui_x_02_a_hostile_filename_is_escaped_on_the_set_page`** (put it in this file): `hostile_name_in_set`:
  hostile = manifest doc whose `file == HOSTILE_NAME` (copy the constant from the harness);
  its `doc-row` contains the literal text `<b>bold</b>`; `reason-card` contains `<b>bold</b>`;
  `page.evaluate("window.__xss") is None`; `page.locator("img[src='x']")` count 0;
  `reason-card.locator("b")` and that `doc-row.locator("b")` count 0.

### Unclassified, Quarantine, Audit: `tests/ui/test_ui_unclassified.py` (append at the END of the file)

**U-05 (RO) `test_ui_u_05_the_po_box_shows_the_server_key`**: row `held_dn_no_po`:
  `with page.expect_response(re.compile(r"/unclassified/normalize\?po_no=")) as r: row.get_by_test_id("input-po-no").fill("PO-7000003")`;
  `r.value.json()["normalized"] == "7000003"`; `expect(row.get_by_test_id("po-key-preview")).to_contain_text("Server key: 7000003")`.
  (The fill types no data; nothing is saved.)

**U-08 (RO, refused action) `test_ui_u_08_a_rejected_file_cannot_be_routed`**: row `rejected_on_arrival`:
  select `DN`, click `btn-route` inside `expect_response(reclassify)`; status 422;
  `expect(page.locator(".toast-error")).to_contain_text("rejected on arrival")`; the row is still there
  (count 1); DB `doc_type` and `po_set_id` equal the values read before the click (po_set_id NULL).

**U-10 (MUT `held_count_target`) `test_ui_u_10_the_count_badge_follows_a_reclassify`**
- Must be appended at the end of `test_ui_unclassified.py` (U-01 counts `manifest["held"]` and must run first; see finding H-03).
- Before: R0 = `unclassified-row` count; assert `_number_in(unclassified-count) == R0` (copy `_number_in` from the dashboard file).
- Select `DN`, fill PO `7000090`, click `btn-route` in `expect_response` (200).
- `page.reload()`; rows == R0 - 1; `unclassified-count` number == R0 - 1; `nav-unclassified` number == R0 - 1.
- DB: the doc's `po_set_id` is not NULL and that set's `po_no_normalized == "7000090"`.

**Q-02 (RO) `test_ui_q_02_each_quarantine_row_shows_its_reason_and_opens_its_set`**
- For each `quarantine-row`: set id from `data-set-id`; DB reason R; its `quarantine-row-reason`
  contains `R[:40]` (or `Reason not recorded` when R is NULL).
- Click `btn-inspect-row` in the row of `quarantined_po_no_lines`; `wait_for_url(rf"/po_sets/{id}/view")`; `set-po` == `7000010`.

**Q-03 (MUT `archive_modal_target`) `test_ui_q_03_the_table_archive_modal_cancels_and_confirms`**
- `deletes = []; page.on("request", lambda r: deletes.append(r.url) if r.method == "DELETE" else None)`.
- row = `quarantine-row` with that set id; click its `btn-archive-row`; `expect(row.get_by_test_id("archive-row-modal")).to_have_class(re.compile(r"\bopen\b"))`.
- Click `btn-cancel-archive-row`; modal not `open`; `deletes == []`; DB `archived_at` NULL.
- Open again; `with page.expect_response(lambda r: r.request.method == "DELETE" and f"/po_sets/{id}/quarantine" in r.url) as resp: row.get_by_test_id("btn-confirm-archive-row").click()`; status 200.
- The row for that id reaches count 0 (`expect(...).to_have_count(0)`); DB `archived_at` not NULL;
  DB `select count(*) from audit_log where po_set_id=? and action='quarantine_delete'` == 1.
- The quarantine table polls every 3 s. If a poll in flight swaps the table and closes the modal, the
  test flakes: that is a product finding (report it with a trace), not a reason to add a retry.

**A-02 (MUT `audit_force_merge_target`) `test_ui_a_02_a_force_merge_appears_in_the_audit_log`**
- Do the S-06 steps on this set with JUST = `"UI A-02: operator accepts 9 of 10 delivered."`.
- `page.goto("/audit")`; `row = page.get_by_test_id("audit-row").filter(has_text=JUST)`; count 1;
  row contains `Force Merge` and has `page.get_by_role("link", name=f"PO Set #{id}", exact=True)`.
- DB `select justification from audit_log where po_set_id=? and action='force_merge'` == [JUST].

**A-03 (MUT `audit_archive_target`) `test_ui_a_03_an_archive_appears_in_the_audit_log`**
- Do the S-07 steps on this set (set page `btn-archive` then `btn-confirm-archive`, DELETE 200).
- `/audit`: `page.get_by_test_id("audit-row").filter(has=page.get_by_role("link", name=f"PO Set #{id}", exact=True)).filter(has_text="Quarantine Archive")` count 1.
  (UI_TEST_PLAN says "Quarantine Delete"; the product label is `Quarantine Archive` on purpose, Q-04.)
- DB count of `quarantine_delete` rows for the set == 1.

### Global: `tests/ui/test_ui_global.py`

Pages for G-01, G-02, G-06: `/dashboard`, `/unclassified`, `/quarantine`, `/audit`, `/manual/merger`,
and the set pages of `merged_basic`, `needs_review_quantity`, `quarantined_unmatched_vendor_line`.

**G-01 (RO) `test_ui_g_01_every_page_loads_without_errors`**: register `page.on("response")` (status >= 400),
  `page.on("requestfailed")`, `page.on("console")` (type `error`) before the first goto; visit each page,
  `page.wait_for_load_state("networkidle")`. Assert no entries, EXCEPT a URL ending in `/favicon.ico`
  (excluded here with a comment `# covered by G-01b`). Also visit `/static/css/theme.css`,
  `/static/js/htmx.min.js`, `/static/js/alpine.min.js`, `/static/js/app.js` with `page.request.get`: each 200.

**G-01b (pre-approved xfail) `test_ui_g_01b_a_favicon_is_served`**
`@pytest.mark.xfail(strict=True, reason="SUSPECTED PRODUCT BUG UI-G01: no favicon; every page load logs a 404 for /favicon.ico")`:
  `page.request.get(f"{base_url}/favicon.ico").status == 200`.

**G-02 (RO) `test_ui_g_02_no_request_leaves_the_app_host`**: record every `page.on("request")` URL while
  visiting the pages and opening one preview drawer (merged_basic, first `btn-preview`). Only URLs
  with scheme `http`, `https`, `ws` or `wss` count (the built-in PDF viewer may load `chrome:` /
  `chrome-extension:` / `data:` / `blob:` / `about:` URLs; ignore those). Every counted URL must have
  `urlsplit(url).netloc == urlsplit(base_url).netloc`. Assert the list of other URLs is empty.

**G-03 (RO) `test_ui_g_03_no_horizontal_scroll`**: parametrize over (width, height) in
  (1024, 768), (1366, 768), (768, 1024) and path in `/dashboard` and the `merged_basic` set page (6 params,
  ids like `1024-dashboard`). `page.set_viewport_size(...)`; goto; assert
  `page.evaluate("document.documentElement.scrollWidth") <= page.evaluate("document.documentElement.clientWidth")`.
  If one param fails, mark ONLY that `pytest.param(..., marks=pytest.mark.xfail(strict=True, reason="SUSPECTED PRODUCT BUG UI-G03-<id>: scrollWidth <n> > clientWidth <m>"))` and report the numbers.

**G-05 (RO) `test_ui_g_05_a_foreign_origin_post_is_refused`**: `merged_basic` (a merged set is immutable,
  so even a bypass changes nothing). Read DB `status, merged_output_path` before.
  `r = page.request.post(f"{base_url}/po_sets/{id}/merge", headers={"Origin": "http://evil.example"})` -> 403,
  body contains `Cross-origin request refused`. Control: same POST with `headers={"Origin": base_url}` -> 200.
  DB values after == before.

**G-06 (RO) `test_ui_g_06_every_page_has_its_own_title`**: the page list plus `/po_sets/999999/view` and
  `/dashboard?status=nonsense`; `page.title()` is non-empty for each; the list of titles has no duplicates.

**G-07 (RO) `test_ui_g_07_the_nav_aria_snapshot_is_stable`**: once, print
  `page.locator("nav.app-navbar").aria_snapshot()` on `/dashboard`, paste it into the test as a string,
  and replace the Unclassified count with a regex (`/\d+/` syntax of `to_match_aria_snapshot`), because
  other tests change that count. Assert with `expect(page.locator("nav.app-navbar")).to_match_aria_snapshot(EXPECTED)`.
  Paste the snapshot you captured into the report.

---

## 6. Things you must NOT do (common mistakes)
- Do not use `pending_no_dn` for any new data-changing test (U-04 already changes it).
- Do not assert a toast for an action that does not reload (S-25 confirm has no toast).
- Do not wait with `time.sleep` or `page.wait_for_timeout`. Use `expect(...)` and `expect_response`.
- Do not build the harness under `data/`. Use `$env:TEMP\aam_ui_test` for hand inspection.
- Do not fix product behaviour. Do not edit `static/js/app.js`.

---

## 7. Commands (PowerShell, repository root)

7.1 UI suite:
```powershell
$env:RUN_E2E = "1"
uv run python -m pytest tests/ui -o addopts= -p no:cacheprovider -W ignore -q -rxX
```
One case: add `-k ui_s_09`. Evidence on failure: `--screenshot only-on-failure --tracing retain-on-failure --output $env:TEMP\aam_ui_artifacts`.

7.2 Non-UI tests that read the templates (after step 1):
```powershell
uv run python -m pytest tests/test_business_scenarios_ui.py tests/test_dashboard.py tests/test_audit_frontend.py tests/test_web_review_fixes.py tests/test_offline_assets.py -o addopts= -p no:cacheprovider -W ignore -q
```

7.3 Build the harness by hand: `uv run python scripts/ui_test_env.py --root $env:TEMP\aam_ui_test`

7.4 Stability (all must pass):
```powershell
$env:RUN_E2E = "1"
1..3 | ForEach-Object { uv run python -m pytest tests/ui -o addopts= -p no:cacheprovider -W ignore -q -rxX }
uv run python -m pytest tests/ui/test_ui_unclassified.py tests/ui/test_ui_set_detail.py tests/ui/test_ui_scan.py tests/ui/test_ui_global.py tests/ui/test_ui_dashboard.py -o addopts= -p no:cacheprovider -W ignore -q -rxX
uv run python -m pytest tests/test_manual_merger_e2e.py -o addopts= -p no:cacheprovider -q
uv run ruff format tests/ui scripts/ui_test_env.py ; uv run ruff check tests/ui scripts/ui_test_env.py
uv run python -m pytest -q -o addopts= -p no:cacheprovider -W ignore
```
"3 identical runs" means: the same pass count, and the same xfail ids, in all three runs.

Proof that a case can fail (required for every new case): change its expected value locally, run it,
see it fail, undo the change. Say in the report which edit you used.

---

## 8. Stop conditions (stop and report; do not work around)
- A DONE test fails at step 0, or fails after your change.
- A seeded state differs from table 4.7.
- A case can only pass by editing `src/`, `static/`, or a template beyond section 3.
- Anything asks for an API key, the network, `config.yaml` or `data/`.
- Two failed attempts on one case (list it under NEEDS, continue with the next case).

---

## 9. Report (reply with exactly this, nothing else)
```
DONE:      <case ids implemented and stable>
XFAIL:     <case id> | <reason string> | <evidence: request, response status/body, DB value>
FINDINGS:  <id> | <page> | <what you saw> | <expected> | <evidence>
HARNESS:   H-03 U-01 counts manifest["held"] and depends on running before U-04/U-10 (confirm or deny)
           H-04 U-04 changes pending_no_dn, which is not marked mutable (confirm or deny)
           <anything else about the harness>
ASSUMED:   <anything you decided without being told>
NEEDS:     <missing hook / seeded state / blocked case, with the error>
FILES:     <every file changed>
TEMPLATES: <the T-numbers applied; paste `git diff --stat templates`>
RUNS:      run1 <passed/xfailed/failed>; run2 ...; run3 ...; reverse ...; manual-merger e2e ...; full suite ...; ruff ...
FAILPROOF: <case id> -> <the edit that made it fail>
D-12:      <toast text seen; anything else the Scan did>
G-07:      <the aria snapshot you captured>
```
Facts only. No praise, no guesses about causes you did not verify.
