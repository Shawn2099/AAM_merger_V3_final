# Pre-deployment UI fix plan (2026-10-25)

Source: [`UI_EXPLORATION_2026-10-24.md`](../UI_EXPLORATION_2026-10-24.md) — a browser
pass over the seeded harness plus a Playwright global sweep. This plan covers **only the
items that should land before the client handover**. It changes no reconciliation rule, no
schema, no migration and no config key. The engine keeps producing the same packets; we
are fixing what the operator is told and what they see when something goes wrong.

Status: **proposed, nothing implemented.**

---

## 0. Decisions this plan is built on

| # | Decision | Why |
|---|---|---|
| D1 | **Do not change the Re-Extract state machine.** Fix the message, not the status transitions. | `tests/test_mvp_blockers.py:154` pins `pending` + `document_not_yet_read` when every read fails, and `:134` pins `quarantined` + `document_extraction_failed` when reads complete with failures. Restoring the old status would break a deliberate product decision. |
| D2 | **Do not pre-fill the document-type `<select>`** on `/unclassified`. Show the stored type as read-only text instead. | `tests/ui/test_ui_unclassified.py:30` deliberately asserts Route is disabled until a type is chosen — even for a row the VLM already typed DN. |
| D3 | **Friendly error pages for page routes only.** `/po_sets/{id}`, `/po_sets/{id}/detail`, preview and merged-PDF keep their JSON contracts. | `tests/test_layer2_gaps.py:698` pins JSON 404 for the API routes. |
| D4 | **Preview stays JSON for API clients**; return a small HTML page only when the request `Accept` contains `text/html` (the drawer iframe does, Playwright/TestClient does not). | `tests/test_business_scenarios_ui.py:503`, `tests/test_ingest_rejected_rows.py:78` pin the 422 JSON. |
| D5 | **Do not touch the files that are dirty right now**: `src/app/api/routes/po_sets.py`, `src/app/services/extraction.py`, `grouping.py`, `reconciliation.py`, `ingestion.py`, `poller.py`, `quarantine.py`, `sync_lock.py`, `src/app/core/config.py`, `src/app/flows/sync.py`, `templates/po_set_detail.html`, `AAM_merger_V3_PRODUCT.md`, `docs/CORE_LOGIC.md`. The whole batch below fits in clean files. | Those are someone else's in-progress work; a concurrent edit here would collide. |
| D6 | One commit per item, **test first**, revert = `git revert <sha>`. | AGENTS §4/§7. |
| D7 | No CSS/visual refactor before handover (contrast tuning, spacing, vocabulary overhaul, layout changes). | They change visible text and can churn `tests/ui` and the tester guide for no pilot benefit. |

**Files this plan will touch:** `static/js/app.js`, `templates/base.html`,
`templates/audit.html`, `templates/unclassified.html`, `templates/_quarantine_table.html`,
`src/app/api/routes/dashboard.py`, one new `templates/error.html`,
`scripts/ui_test_env.py`, `tests/ui/conftest.py`, plus tests.

---

## 1. Work items

### PF-01 — Make the UI harness genuinely provider-free (finding H-01) · P0 · 0.5 h

**Problem.** `load_config()` loads `<repo>/.env` after import (`src/app/core/config.py:394-421`,
`override=False`). `scripts/ui_test_env.py` (`build()` ~line 273, `serve()` ~line 470) and
`tests/ui/conftest.py:52` *pop* `OPENROUTER_API_KEY`, so the file puts the developer's real
key back. Any UI test that triggers extraction hits OpenRouter and costs money; S-16 cannot
be tested.

**Change.**
- `scripts/ui_test_env.py` `build()`: replace `os.environ.pop("OPENROUTER_API_KEY", None)`
  with `os.environ["OPENROUTER_API_KEY"] = ""` (an empty variable present in `os.environ`
  is not overridden by `load_dotenv(override=False)` — verified: `load_config()` then logs
  "OPENROUTER_API_KEY not set" and the value stays `''`).
- `serve()`: `env["OPENROUTER_API_KEY"] = ""` instead of `env.pop(...)`.
- `tests/ui/conftest.py` `ui_env`: build the child env dict with
  `"OPENROUTER_API_KEY": ""` instead of filtering the key out.
- Defence in depth: `build()` writes `root/.env` with `OPENROUTER_API_KEY=`.
- Remove the temporary `data/ui_test/.env` workaround from the exploration (the rebuild
  creates its own).

> Implementation note: the empty string must be set **in Python** (`os.environ[...] = ""`).
> In PowerShell 7 `$env:OPENROUTER_API_KEY=""` removes the variable instead of setting it
> empty, so a shell-level workaround silently does nothing.

**Tests.**
- New `tests/test_ui_test_env_key.py`: load a config from a `ui_test` temp dir with the
  env var set to `""` and assert `os.getenv("OPENROUTER_API_KEY")` is falsy after
  `load_config()`, and that `extraction._call_vlm(...)` raises `RuntimeError`.
- Optional assertion in `tests/ui/conftest.py`: the served app reports no provider key.

**Acceptance.** With the harness served, `data/ui_test/logs/aam_merger.log` contains
`OPENROUTER_API_KEY not set`; no outbound request is made by a Re-Extract.

**Risk.** Test-only. No product impact. **Rollback:** revert.

---

### PF-02 — Honest action reports: Re-Extract and Merge Now (finding F-01/F-17) · P0 · 1.5 h

**Problem.** `static/js/app.js` answers `POST …/redo_extract` with
`store.success('⚡ Document extraction queued.')` regardless of the per-document result.
The route already returns `extractions: [{doc_id, status | error}]`
(`src/app/api/routes/po_sets.py:504`). Merge Now reports only "still mismatched" and drops
the server's `detail`/`reason`.

**Change (JS only).**
- `redo_extract` branch: parse the body and report the real outcome —
  `failed.length === 0` → `success("Re-read N document(s).")`;
  `0 < failed < all` → `warning("Read X of N; Y failed: <first error>")`;
  all failed → `error("Extraction failed for all N documents: <first error>")`.
- `merge` branch: put the server's explanation in the toast:
  `body.detail || body.reason || 'open the set for the reason'` for the
  quarantined/pending/other branches.
- Do **not** change the route's response shape (`status: "redo_extract_complete"` is
  asserted in `tests/test_failed_document_recovery.py:212`).

**Tests.**
- New `tests/ui/test_ui_set_detail.py::test_ui_s_16_reextract_without_a_provider_key_reports_failure`
  (needs PF-01): open `quarantined_unmatched_vendor_line`, click Re-Extract, expect a
  `.toast-error`/`warning` containing "failed", and assert the toast is still on screen
  after the reload (needs PF-07).
- Amend plan S-16 text (see PF-10): the set **may** become `pending` (pinned behaviour);
  the requirement is an honest message.

**Acceptance.** No success toast when nothing was read; no success toast when the set did
not merge. **Risk.** Low — one JS file, no server change.

**Dependency.** PF-07 should land first (or in the same commit), otherwise the reload eats
the new message.

---

### PF-03 — Friendly pages instead of raw JSON dead ends (finding F-04) · P0 · 1.5 h

**Problem.** `GET /dashboard?status=nonsense` → `{"detail":"Unknown status filter…"}` (422);
`GET /po_sets/999999/view` → `{"detail":"POSet 999999 not found"}` (404); a non-integer id →
raw Pydantic JSON. No title, no nav, no way back.

**Change.**
- New `templates/error.html` extends `base.html`: a card with an icon, heading, one-sentence
  message, `Back to dashboard` button; `{% block title %}Not found — AAM Merger V3{% endblock %}`
  (parametrise the title).
- `dashboard()` (~`dashboard.py:316`): instead of `raise HTTPException(422, …)`, return
  `_templates.TemplateResponse(request, "error.html", {code:422, message:"That status filter
  is not recognised."}, status_code=422)`.
- `po_set_detail_view()` (~`dashboard.py:388`): `ps is None` → render the same template with
  404, `"That PO Set does not exist."`, status 404. A non-integer path id stays a FastAPI 422
  (framework-level) — acceptable, or add a `po_set_id: str` route guard if wanted.
- Leave `/po_sets/{id}`, `/po_sets/{id}/detail`, `/documents/{id}/preview`,
  `/po_sets/{id}/merged_pdf` exactly as they are (D3).

**Tests.**
- `tests/ui/test_ui_dashboard.py::test_ui_d_07_unknown_status_filter_shows_a_page` — status
  422, `nav` present, no JS error, title non-empty, link back to `/dashboard` works.
- `tests/ui/test_ui_set_detail.py::test_ui_s_17_unknown_set_shows_a_friendly_page` — 404,
  nav, message, no stack trace.
- Backend: `tests/test_dashboard.py` add
  `test_unknown_status_filter_renders_html_with_422` (asserts `text/html` + 422), and one
  for `/view` 404.

**Acceptance.** Both URLs render the app shell with the right status code.
**Risk.** Low–medium: only the two page handlers. Keep the status codes.

---

### PF-04 — Preview errors inside the drawer, not raw JSON (finding F-02) · P0 · 1.5 h

**Problem.** The drawer iframe points at `/documents/{id}/preview`. A rejected-on-arrival
file returns 422 JSON, which Chrome renders as a JSON viewer, clipped inside the drawer.
Same for a missing file or a non-PDF blob.

**Change.**
- `preview_document(doc_id: int, request: Request)` (add `request`; ~`dashboard.py:626`):
  for the three failure cases (doc missing, `ingest_reject_reason`, `_served_file` failure /
  not `%PDF`), if `_wants_html(request)` (helper already at `dashboard.py:683`) return
  `TemplateResponse("preview_error.html", …)`, otherwise keep raising the same
  `HTTPException` with the same detail (D4).
- New `templates/preview_error.html`: a **standalone** page (no nav, no sidebar — it renders
  inside the drawer iframe) with the app's theme, a "This file cannot be previewed" heading,
  the reason, and a note that the rest of the set is unaffected. Reusing `error.html` would
  put the whole app shell inside the iframe; do not.

**Tests.**
- `tests/test_ingest_rejected_rows.py`: extend `test_preview_says_why_instead_of_a_404`
  with an html-Accept request: 422, `text/html`, body contains "cannot be previewed" and the
  reason; the existing `*/*` call still gets JSON.
- `tests/ui/test_ui_unclassified.py::test_ui_u_07_previewing_a_failed_file_explains_itself`
  — open the preview for `rejected_on_arrival`, assert the iframe's response is
  `text/html` and the drawer text contains the reason (not `{"detail"`).

**Acceptance.** No raw JSON is ever rendered in the drawer. **Risk.** Low; additive
branch on `Accept`.

---

### PF-05 — Make the audit page readable (finding F-03) · P0 · 2 h

**Problem.** `templates/audit.html:55` prints `e.detail` (a multi-KB JSON string with
literal `\n`) in a `<code>` cell. Four quarantine rows make the page unreadable; action
names fall through to raw enums (`:43`).

**Change.**
- `audit_log()` (~`dashboard.py:990`): build a per-entry view model:
  `label` (human name for every `AuditAction` value: Force Merge, Quarantine Archive,
  Customs Appended, Manual Status Change, Line Mapping Applied, Quarantined),
  `summary` (short, from the parsed JSON: for `quarantined` → the reason text and the
  document count; for `force_merge` → the packet name; for `quarantine_delete` → the archive
  folder's final segment; for `line_mapping_applied` → "line numbers mapped by rule x10
  (DN, SI)"; for `manual_status_change` → the note), and `detail` (raw, unchanged).
  Parsing in the route, never in the template; never `|safe`.
- `audit.html`: Action column uses `label`; Event Detail shows `summary` with the raw JSON in
  `<details><summary>Raw JSON</summary><pre>{{ e.detail }}</pre></details>`; keep the
  Timestamp, PO Set, Justification and Initiator columns; cap cell width with wrapping.
- Do not change the DB rows or the audit service.

**Tests.**
- `tests/test_dashboard.py`: new `test_audit_page_summarises_event_detail` — a seeded
  `quarantined` row renders a short summary, contains `<details`, and the raw JSON is still
  present inside it; a `force_merge` row shows "Force Merge".
- Existing `tests/ui/test_ui_unclassified.py::test_ui_a_01` must stay green (it filters a
  row containing the seeded justification).

**Acceptance.** The audit page is readable at 1024 px; no `<code>` cell with the whole
report in the open. **Risk.** Low; read-only route + template.

---

### PF-06 — Flag the ambiguous document per row, and show the stored type (finding F-06/F-05) · P0 · 1 h

**Problem.** `DN-AMBIG.pdf` has `po_reference_ambiguous = 1` but its row looks like every
other one; only the aggregate "1 need a PO number" badge exists. Separately, after saving a
type the select re-renders at `-- Select Type --` although the DB holds `DN`.

**Change (template only, `templates/unclassified.html`).**
- In the Document cell, when `doc.po_reference_ambiguous`: a red badge
  `⚠ multiple PO numbers — needs a person` plus a one-line note
  ("Nothing will attach this automatically; type the PO number to place it.").
- In the type cell, **below** the select, a muted line
  `Stored type: {{ doc.doc_type.value }}` when the stored type is not `UNKNOWN`
  (never pre-select the control — D2).
- Do not touch the `HX-Refresh` behaviour of the no-PO response (that is how the row is
  re-rendered); PF-07 turns its message row into a toast before the refresh, so the
  explanation survives.

**Tests.**
- `tests/ui/test_ui_unclassified.py::test_ui_u_06_ambiguous_document_is_marked_on_its_row`
  — the `held_ambiguous_po` row contains "multiple PO numbers", other rows do not.
- `tests/ui/test_ui_unclassified.py::test_ui_u_05b_stored_type_is_visible_without_enabling_route`
  — `held_dn_no_po` shows "Stored type: DN" and Route is still disabled (guards U-03).

**Acceptance.** Both texts visible; U-03 unchanged. **Risk.** Zero (additive template text).

---

### PF-07 — Keep the action result on screen (finding F-07/F-10) · P0 · 1.5 h

**Problem.** Success toasts are in-memory; every action reloads and wipes them. Toasts also
duplicate the template's icon (`❌ ❌ Validation error…`).

**Change (`static/js/app.js`, optionally `templates/base.html`).**
- In `store.add(...)`: `sessionStorage.setItem('aam.lastToast', JSON.stringify({message,type,t:Date.now()}))`
  (wrapped in try/catch).
- On `alpine:initialized` (after the store exists): read the key, drop it, and if it is
  younger than ~15 s re-add it; then remove the key. This makes one toast survive exactly
  the reload the action triggers.
- Register an `htmx:beforeSwap` listener: when the response carries
  `HX-Refresh: true` **and** contains a message cell (`td[colspan]`) — the no-PO reclassify
  response — push that cell's text into the toast store, so PF-06's explanation is not wiped
  by the refresh.
- Remove the leading type emoji from the messages that duplicate the icon
  (`'❌ Validation error: …'`, `'⚠️ Not merged …'`, `'⚠️ Network connection failed…'`, …),
  or remove the icon spans in `base.html` — not both.
- Do not use sessionStorage for anything else.

**Tests.**
- `tests/ui/test_ui_set_detail.py::test_ui_s_08_customs_toggle_turns_the_requirement_on_and_off`
  (existing) — extend it to assert a toast is visible after the reload.
- `tests/ui/test_ui_set_detail.py` S-16 (PF-02) — the failure toast survives the reload.
- A no-JS-error assertion already exists in `tests/ui/conftest.py`.

**Acceptance.** After toggling customs / force merge / re-extract, the operator sees one
message on the reloaded page; no double icon. **Risk.** Low; if a stale toast ever appears
on an unrelated page, shorten the age window.

---

### PF-08 — Modals take and trap focus (finding F-08) · P0.5 · 2 h

**Problem.** Opening Force Merge leaves focus on the trigger and Tab walks the page behind
the backdrop (preview buttons, upload input, doc-type select). `aria-modal="true"` is set.

**Change — in `static/js/app.js` only** (do not edit `po_set_detail.html`, it is dirty, D5):
- `MutationObserver` on `document.body` (`attributes`, `attributeFilter: ['class'],
  subtree: true`): when a `.modal-backdrop` gains `.open`, remember
  `document.activeElement`, focus the first `textarea`/focusable inside `.modal-dialog`
  (give the dialog `tabindex="-1"` if absent if nothing is focusable); when it loses `.open`,
  restore focus to the remembered trigger.
- Keydown `Tab` handler: if a `.modal-backdrop.open` exists, cycle focus among its visible
  focusables only. `Escape` already removes `.open` (app.js:193) — the observer restores
  focus.
- Same observer covers the quarantine-table Delete modal and both modals on a set page.

**Tests.**
- New `tests/ui/test_ui_global.py::test_ui_g_04_force_merge_modal_takes_and_traps_focus` —
  open the modal, `expect(page.locator(...textarea)).to_be_focused()`, Tab N times and
  assert every focused element is inside the modal, Escape returns focus to the trigger.

**Acceptance.** Focus starts inside the modal, never leaves it while open, returns on close.
**Risk.** Low–medium (global key handler); guard it strictly on an open modal.

---

### PF-09 — Stop calling archive "Delete" (finding in §5 of the exploration) · P0.5 · 5 min

**Change.** `templates/_quarantine_table.html:38`: button text `Delete` → `Archive`
(the modal already says "Archive Quarantined PO Set?" and the audit action is
`quarantine_delete`). Add `data-testid="btn-archive-row"` for the future Q-03 test.
No test currently asserts this label.

**Risk.** Zero. **Acceptance.** The quarantine table's destructive control says Archive and
opens the same modal.

---

### PF-10 — Documentation alignment · P0 · 0.5 h

- `docs/UI_TEST_PLAN.md`: rewrite **S-16** to the decision in D1 — "Re-Extract with no
  usable provider shows a failure message; the set may go to `pending` because its
  documents were reset (pinned by `tests/test_mvp_blockers.py`)". Mark U-06, U-07, D-07,
  S-17 as covered by PF-03/04/06. Add the missing `data-testid` gaps to the "NEEDS" note
  (quarantine row Archive, preview buttons, toast container).
- `AAM_merger_V3_PRODUCT.md` §8: one line stating that a Re-Extract/Re-Match resets the
  previous verdict on purpose (keep the guard, record the limitation) — **only if the
  product owner confirms D1**.
- `SESSION_HANDOFF.md`: short "2026-10-25 pre-deploy UI pass" entry after the work lands.

---

## 2. Order, dependencies, estimate

```
PF-01 (harness key) ──► PF-02 (re-extract honesty) ──► PF-07 (toast survives reload)
        │                                                │
        ├──► PF-03 (error page) ──► PF-04 (preview error)─┤
        ├──► PF-05 (audit)                               │
        ├──► PF-06 (ambiguous + stored type)             │
        └──► PF-09 (Archive label)                       └──► PF-08 (modal focus)
                                                              PF-10 (docs)
```

| Phase | Items | Effort |
|---|---|---|
| 1 | PF-01, PF-09 | 0.6 h |
| 2 | PF-03, PF-04 (shared `error.html`) | 3 h |
| 3 | PF-05, PF-06 | 3 h |
| 4 | PF-07, PF-02 | 3 h |
| 5 | PF-08 | 2 h |
| 6 | New UI tests for D-07/S-17/U-06/U-07/G-04, PF-10, full verification | 4 h |
| **Total** | | **~1.5 dev-days** (or one long day without PF-08) |

If time is short, ship PF-01…PF-07 + PF-09 and defer PF-08 (focus) — it is the only
non-core item.

---

## 3. What can break, and the pinned contracts to respect

| Contract | Pinned by | How this plan keeps it |
|---|---|---|
| `/po_sets/{id}`, `/po_sets/{id}/detail` 404 stays JSON | `tests/test_layer2_gaps.py:698-700` | PF-03 touches only `/view` |
| `/documents/{id}/preview` 422 JSON for non-browser clients | `tests/test_business_scenarios_ui.py:503`, `tests/test_ingest_rejected_rows.py:78` | PF-04 branches on `Accept: text/html` |
| `redo_extract` returns `status: "redo_extract_complete"` + per-doc list | `tests/test_failed_document_recovery.py:212`, `tests/test_layer2_gaps.py:736` | response shape unchanged; JS reads existing keys |
| Re-Extract leaves the set `pending` when reads fail | `tests/test_mvp_blockers.py:154` | D1 — no engine change |
| Route disabled until a type is chosen | `tests/ui/test_ui_unclassified.py:30` | D2 — no pre-fill |
| Reason text on the set page (first 40 chars) | `tests/ui/test_ui_set_detail.py:40` | no reason strings touched |
| Audit page contains `audit-row` + justification | `tests/ui/test_ui_unclassified.py:74` | PF-05 keeps both columns |
| `/audit` GET 200, POST 405 | `tests/test_dashboard.py:190-200` | no route method change |

**Also risk-checked:** no migration, no config key, no DB write, no change to
`reconciliation.py` / `merge.py` / `grouping.py`. The worst regression class is a wrong
HTTP status code or a missing toast — covered by the tests above.

**Dirty tree.** Do not start PF-01…PF-10 until the current `src/` edits are committed (or
at least green). If they must run in parallel, this batch touches none of the dirty files
(`templates/po_set_detail.html` is deliberately avoided by PF-08).

---

## 4. Verification gate (definition of done)

Run from the repo root after all items:

```powershell
$env:RUN_E2E = "1"
uv run python -m pytest -q -o addopts=                                   # whole suite
uv run python -m pytest tests/ui -q -o addopts= -p no:cacheprovider      # UI suite, 3×
uv run python -m pytest tests/test_manual_merger_e2e.py -q -o addopts=   # merge PDFs tool
uv run python -m pytest tests/test_business_scenarios_ui.py -q -o addopts=
uv run ruff check . ; uv run ruff format --check .
```

Plus one manual pass on a freshly built harness (`scripts/ui_test_env.py --root data/ui_test`):

1. Dashboard → a bad `?status=xyz` link shows the error page; Back works.
2. Set → Re-Extract: a failure message stays on screen after the reload; the set's state
   is whatever the engine decided (documented, not "restored").
3. Set → toggle customs / force merge: one toast visible after the reload, no double icon.
4. Unclassified → the ambiguous row is marked; the stored type is shown; Route stays
   disabled until a choice is made.
5. Unclassified → preview of `fake-not-a-pdf.pdf` shows a readable message in the drawer.
6. Audit → four quarantine rows are readable, raw JSON collapsed.
7. Keyboard only: open Force Merge, verify focus is inside, Tab does not escape, Escape
   returns focus.
8. Quarantine table → the button says Archive.

The release is **not** done until `docs/UI_TEST_PLAN.md` reflects S-16 (PF-10) and the
harness logs `OPENROUTER_API_KEY not set` (PF-01).

**Related pre-deploy check (not UI, not in this batch).** While verifying PF-01 I saw the
app log `concurrency.po_set_lock_timeout_seconds (300s) is shorter than the worst-case
extraction of one document (742s); using 802s`. `config.example.yaml` ships 300 s, so the
client's `config.yaml` should set that value explicitly (or the example should be raised),
otherwise the app silently overrides it at startup. Worth one line in the delivery checklist.

---

## 5. Explicitly out of scope (post-deploy backlog)

Fix these only after the pilot is stable; each is a visible change and none block handover:

- F-09 accessibility sweep (labels on the dashboard filter and the upload form, contrast
  token changes) — Lighthouse 0.84/0.81 today.
- F-11 `pending` enum in the document table → "Not read".
- F-12 wording of the upload refusal (`only .pdf uploads accepted`).
- F-13 turn "Service Online" into a status pill (not a link to `/health`).
- F-14 the duplicated "Preview one, then one…" subtitle.
- F-15 human labels for every audit action (partially covered by PF-05).
- F-16 scan outcome (last-scan timestamp + result on the dashboard; needs a status field).
- F-17 Merge-Now copy (partially covered by PF-02).
- F-18 "no matching PO sets" message for the client-side search.
- F-19 quarantine poll interval (3 s → 10 s) or pause during interaction.
- F-20 absolute server paths on the archived/set pages.
- Vocabulary unification: the `#status-filter` options currently print raw enums
  (`blocked_customs`) next to tabs that say "Awaiting Customs".
- Collapse the action bar on merged sets (a finished job should look finished).
- Search by DN/SI number in addition to PO.

---

## 6. Open questions for the product owner

1. **D1 confirmation** — is "a failed Re-Extract resets the set to `pending`" intended?
   If yes, S-16 is amended as described. If no, this becomes an engine change with its own
   plan (and `test_mvp_blockers.py` must change deliberately).
2. **Archive justification** — Force Merge asks for a reason; Archive does not. Require one
   (≥20 chars, like Force Merge) or leave it optional? Not needed for handover, but decide
   before the CA sees the audit page.
3. **PF-08 placement** — implementing focus handling in `app.js` (no template edit) is
   acceptable, or do you prefer it in `po_set_detail.html` once that file's current edit
   lands?
