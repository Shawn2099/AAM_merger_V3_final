# Worker report — pre-deploy UI fixes PF-01…PF-10

Worker: DeepSeek via OpenCode. Reviewer: Claude/Opus.
Base: `f8b2769` (worktree started clean apart from `data/real_samples/`). **Nothing committed**; `git`
was used only for `status`/`diff`.
Scope and rules: `docs/OPENCODE_PF_BRIEF.md` (including the Opus corrections, 2026-10-25).
Source of truth for behaviour: `docs/UI_PREDEPLOY_PLAN_2026-10-25.md` and
`UI_EXPLORATION_2026-10-24.md`.

Status: **all items implemented, all gates green.**

---

## 1. Final gate output (current tree)

```text
$ uv run python -m pytest -q -W ignore -o addopts= -p no:cacheprovider
6372 passed, 36 skipped, 43 xfailed in 432.81s (0:07:12)

$ RUN_E2E=1 uv run python -m pytest tests/ui -q -o addopts= -p no:cacheprovider -W ignore
run 1: 30 passed in 18.38s
run 2: 30 passed in 20.56s
run 3: 30 passed in 20.10s

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
164 files already formatted

$ uv run ty check src
All checks passed!
```

The full suite skips the 30 browser tests unless `RUN_E2E=1`; they were run separately, three
times, on the same tree. The numbers above were re-run after the concurrent edits described in
§6.

---

## 2. Items, files, and the failing test each one started with

### PF-01 — harness is genuinely provider-free (H-01)

Files: `scripts/ui_test_env.py`, `tests/ui/conftest.py`, `tests/test_ui_test_env_key.py` (new).

- `prepare_provider_free_environment()` sets `OPENROUTER_API_KEY=""` in the process;
  `provider_free_env()` returns the same for a child process. An empty string is *present*, and
  `dotenv.load_dotenv(override=False)` leaves present variables alone, so no `.env` can restore
  the developer's key.
- `build()` also writes an empty `.env` beside the harness config (defence in depth);
  `serve()` no longer pops the variable.
- `tests/ui/conftest.py` builds the child env as `{**os.environ, "OPENROUTER_API_KEY": ""}`.

Failing first: `test_the_harness_process_sees_no_provider_key` failed with
`AttributeError: module 'ui_test_env' has no attribute 'prepare_provider_free_environment'`.
With the fix the probe reports `""` and the harness logs
`OPENROUTER_API_KEY not set - VLM extraction will fail closed`; `_call_vlm` raises before any
client is built.

### PF-09 — the destructive control says Archive

Files: `templates/_quarantine_table.html`, `tests/ui/test_ui_unclassified.py` (Q-04).

- Button text `Delete` → `Archive`, `data-testid="btn-archive-row"`. The modal and the
  `quarantine_delete` audit action were already right. The reclassify refusal text
  (“Delete (archive) the set”) was **not** touched (correction 4; pinned by
  `tests/test_reclassify_scope.py:111`).
- Failing first: the cell read `"Inspect & Resolve Delete"`, and the test id did not exist.

### PF-03 — page routes never dead-end in JSON (F-04)

Files: `templates/error.html` (new), `src/app/api/routes/dashboard.py`,
`tests/test_dashboard.py`, `tests/ui/test_ui_dashboard.py` (D-07), `tests/ui/test_ui_set_detail.py` (S-17).

- New `error.html` extends `base.html`: nav, themed card, one sentence, “Back to dashboard”,
  and the code. `dashboard()` returns it with **422** for an unknown status;
  `po_set_detail_view()` with **404** for a missing set.
- Untouched: `/po_sets/{id}`, `/po_sets/{id}/detail`, `/documents/{id}/preview`,
  `/po_sets/{id}/merged_pdf`; no `po_set_id: str` guard (correction 5), so a non-integer id
  remains a framework 422.
- Failing first: both backend tests failed (JSON, wrong content type); D-07 and S-17 failed in
  Chromium against the JSON viewer.

### PF-04 — preview errors render inside the drawer (F-02)

Files: `templates/preview_error.html` (new), `src/app/api/routes/dashboard.py`,
`tests/test_ingest_rejected_rows.py`, `tests/ui/test_ui_unclassified.py` (U-07).

- `preview_document(doc_id, request)` now returns a standalone page for
  `Accept: text/html` in the three failure cases (missing row, rejected on arrival, missing or
  non-PDF file). Other clients keep the same `HTTPException` detail and status (`*/*` still
  gets JSON).
- `preview_error.html` is deliberately standalone (no nav): it loads inside the drawer iframe.
- Failing first: `test_preview_renders_a_page_for_a_browser_accept_header` failed on the JSON
  content type. The existing JSON-contract test stayed green.
- Note: U-07 was written immediately after this backend test had failed and pinned the same
  behaviour end-to-end through Chromium.

### PF-05 — audit page is readable (F-03)

Files: `src/app/api/routes/dashboard.py`, `templates/audit.html`, `tests/test_dashboard.py`.

- `_AUDIT_ACTION_LABELS` names every `AuditAction`; `_audit_detail()` parses JSON and returns
  `{}` on anything malformed; `_audit_summary()` produces one line per action
  (quarantine reason + folder/document, packet name, archive folder, appended documents,
  mapping rules, placement note); `_audit_entries()` builds the view models.
- Per correction 3: an unknown or unparseable shape falls back to the **action label** as the
  summary, and the whole summary is guarded by `try/except` so a future row can never break the
  page. `manual_status_change` does not require a `note`.
- `audit.html`: badge uses the human label, the summary is the visible cell, and the raw JSON is
  collapsed behind `<details><summary>Raw JSON</summary><pre>…</pre></details>` (escaped by
  Jinja; no `|safe`). Timestamp, PO Set, Justification and Initiator columns unchanged.
- Failing first: the new backend test found no summary, no `<details>`/`<pre>`, and the raw
  multi-KB report still in the cell.

### PF-06 — ambiguous row marker + stored type (F-06/F-05)

Files: `templates/unclassified.html`, `tests/ui/test_ui_unclassified.py` (U-06, U-11).

- A per-row badge “⚠️ multiple PO numbers — cannot be placed automatically” plus a one-line
  note, only for `po_reference_ambiguous`.
- Below the type `<select>`: “Stored type: X” for any type other than `UNKNOWN`. The select is
  still never pre-selected (D2; U-03 stays green).
- Failing first: U-06 and U-11 failed (no marker, no stored-type line).

### PF-07 — the action result survives the reload (F-07/F-10)

Files: `static/js/app.js`, `tests/ui/test_ui_set_detail.py` (S-08 extended, S-15),
`tests/ui/test_ui_unclassified.py` (U-09), `scripts/ui_test_env.py` (`held_reclassify_no_po`).

- `store.add(..., remember)` parks `{message, type, path, t}` in `sessionStorage` **only for
  actions followed by a reload** (toggle customs unless `append_pending`, redo match, redo
  extract, merge, force merge, the `HX-Refresh` path).
- `alpine:initialized` replays it once, only on the same pathname+search, only within 15 s, and
  only when `type` is `success|info|warning|error`.
- Per correction 1: the `HX-Refresh` message is read in an **`htmx:beforeOnLoad`** listener
  (htmx 1.9.12 reloads before `beforeSwap`), with `DOMParser` + `textContent` and no
  `innerHTML`. A bare `<tr>` is discarded by the HTML parser, so the fragment is parsed inside
  `<table><tbody>…</tbody></table>`; only its text is used.
- Duplicate icons removed from the messages that repeated the type icon (`❌`, `⚠️`); the
  template’s icon is the single one.
- Failing first: S-08 found no toast after the reload; S-15 saw
  `'\u274c\n\u274c Validation error: …'`; U-09 saw no message survive the refresh (which also
  exposed the `<tr>` parsing bug).

### PF-02 — honest Re-Extract and Merge Now reports (F-01/F-17)

Files: `static/js/app.js`, `tests/ui/test_ui_set_detail.py` (S-16),
`scripts/ui_test_env.py` (`reextract_target`, zero backoff).

- Re-Extract reads `extractions` and reports read/failed counts; failed is
  `item.error || item.status !== 'valid'` (correction 2), with the first error (or failed
  status) in the message. Nothing read → error; partial → warning; all read → success.
- Merge Now: only the final `else` gained `detail || reason` (correction 2). The merged toast
  now shows the **file name only** (`merged_output_path` split on both separators), never a
  path.
- D1 respected: no change to the state machine. S-16 uses its **own** scenario
  `reextract_target` (mutable, quarantined), so S-04 is unaffected in any order (correction 2).
- Failing first: the pre-fix branch always emitted `⚡ Document extraction queued.`, which cannot
  satisfy the “Extraction failed” assertion. (The first S-16 run failed on a test-side
  response-body race; the test now asserts the toast, and the response shape stays pinned by
  the backend tests.)

### PF-08 — modals take and trap focus (F-08)

Files: `static/js/app.js`, `tests/ui/test_ui_global.py` (new, G-04).

- A `MutationObserver` on backdrop class changes remembers the trigger, moves focus to the
  dialog’s textarea (or first visible control, or the dialog itself with `tabindex="-1"`), and
  restores focus on close only if `document.contains(trigger)` (correction 6). Tab and
  Shift+Tab cycle inside the open dialog; disabled, hidden and `aria-hidden` elements are
  skipped; Escape (unchanged handler) closes and the observer restores.
- The observer installs on `DOMContentLoaded` when `document.body` does not exist yet — the
  first attempt threw `Failed to execute 'observe' on 'MutationObserver': parameter 1 is not of
  type 'Node'`, which the JS-error fixture caught.
- Failing first: G-04 found the textarea `inactive` after opening the modal.

### PF-10 — documentation

Files: `docs/UI_TEST_PLAN.md`.

- D-07, S-15, S-16, S-17, U-06, U-07, U-09, U-11, Q-04 and G-04 marked DONE with what each
  now checks; S-16 rewritten to the **pinned** engine behaviour (a failed re-read leaves the set
  `pending` with `document_not_yet_read`; the requirement is an honest message, not “set
  unchanged”); new scenarios (`reextract_target`, `held_reclassify_no_po`) and the new
  `data-testid`/`.toast-<type>` hooks documented.
- `AAM_merger_V3_PRODUCT.md` §8 and `SESSION_HANDOFF.md` were **not** edited: they are outside
  the brief’s allowed paths. If the reviewer wants the D1 limitation recorded there, that is a
  separate, deliberate edit.

---

## 3. Corrections from the Opus review — compliance

| # | Correction | Where |
|---|---|---|
| 1 | `HX-Refresh` via `htmx:beforeOnLoad`; `DOMParser` + `textContent`; persist only for reloading actions; replay only for the four types | `static/js/app.js` (`htmx:beforeOnLoad`, `rememberToast`, `replayRememberedToast`, `TOAST_TYPES`) |
| 2 | S-16 owns its own scenario; backoff 0 (or long timeout); failed = `error || status !== 'valid'`; only the last Merge Now `else` gains detail/reason; merged toast shows the name only | `scripts/ui_test_env.py`, `tests/ui/test_ui_set_detail.py`, `static/js/app.js` |
| 3 | Unknown audit shapes summarise as the action label; a detail that cannot be parsed never breaks the page | `_audit_entries` / `_audit_summary` in `dashboard.py` |
| 4 | Rename the button only; leave the reclassify refusal text alone | `_quarantine_table.html`; no edit near `dashboard.py`’s “Delete (archive)” text |
| 5 | Non-integer set id stays a framework 422; no `str` route guard | `po_set_detail_view` |
| 6 | Restore focus only if `document.contains(trigger)`; Shift+Tab; skip disabled/hidden | `app.js` (`visibleFocusables`, `releaseModalFocus`, Tab handler) |
| 7 | Stale dotenv line reference | no code change needed (behaviour verified by PF-01’s probe) |

---

## 4. Decisions and deviations the reviewer should check

1. **Existing test changed (not weakened):**
   `test_ui_q_01_quarantine_page_lists_exactly_the_quarantined_sets` now derives its expectation
   from the `db` fixture instead of the build-time manifest. The mandated S-16 re-extracts a
   quarantined set, so the old “only `archive_target` changes” assumption was order-dependent.
   The assertion is still exact set equality (and now also catches unexpected rows). No test
   that pins product behaviour was modified.
2. **Case-ID collisions avoided:** the refused-force-merge test is **S-15** (the plan’s row for
   it), the archive-label test is **Q-04** (Q-02 is “row reason / Inspect”), and the stored-type
   test is **U-11** (U-05 is the PO-key preview).
3. **Two harness entries added**, each owned by one mutable test:
   `SCENARIOS["reextract_target"]` (quarantined, 7000017) and
   `HELD["held_reclassify_no_po"]` (`DN-HOLD.pdf`). Both are documented in `UI_TEST_PLAN.md`.
4. **Harness backoff** is `[0, 0, 0]`: identical retries and code path, no real sleeping.
5. **U-07** was added right after PF-04’s backend test failed; it pins the same behaviour through
   a real Chromium `Accept` header.
6. **Line endings** were left as found (LF in the index; some working-tree files are CRLF and
   git prints its normalisation warning). `ruff format` touched only files already in this
   change.

---

## 5. Assumptions

- An empty-string environment variable is the reliable way to beat `dotenv(override=False)`;
  verified in a clean subprocess, not only in-process.
- The drawer iframe sends `Accept: text/html`; API clients do not. Tests pin both sides.
- A short-lived toast (≤15 s, same URL) is the right lifetime for “one reload”.
- Unknown audit shapes should still render (label as summary) rather than fail the page.
- `held_reclassify_no_po` is the only held document used for the `HX-Refresh` message test.

---

## 6. State of the tree and concurrent work

`git status` after the work:

```text
 M docs/UI_TEST_PLAN.md
 M scripts/ui_test_env.py
 M src/app/api/routes/dashboard.py
 M src/app/api/routes/po_sets.py          # NOT mine (see below)
 M static/js/app.js
 M templates/_quarantine_table.html
 M templates/audit.html
 M templates/unclassified.html
 M tests/test_dashboard.py
 M tests/test_ingest_rejected_rows.py
 M tests/ui/conftest.py
 M tests/ui/test_ui_dashboard.py
 M tests/ui/test_ui_set_detail.py
 M tests/ui/test_ui_unclassified.py
?? data/real_samples/                     # pre-existing
?? templates/error.html                   # new (PF-03)
?? templates/preview_error.html           # new (PF-04)
?? tests/test_customs_replace.py          # NOT mine (see below)
?? tests/test_ui_test_env_key.py          # new (PF-01)
?? tests/ui/test_ui_global.py             # new (PF-08)
```

`src/app/api/routes/po_sets.py` gained an 84-line `remove_supporting_document` route and
`tests/test_customs_replace.py` appeared (both timestamped 01:12, during final verification).
They are **not part of this packet**; I did not edit or review them. The final gates in §1 were
re-run after they landed and include their 10 tests.

---

## 7. Product findings (reported, not fixed)

1. **Merge Now cannot show a reason for a mismatched set.** `merge_now()` returns
   `reason: null` there because `reconcile_po_set()`’s normal return carries only
   status/flags/output. PF-02 now prefers `detail || reason`, but both are absent, so the toast
   still says “open the set for the reason”. Fixing it needs `po_sets.py`, outside the allowed
   paths (F-17 residue).
2. **A failed Re-Extract still resets the set to `pending`** (D1, pinned by
   `tests/test_mvp_blockers.py`). The operator is now told every read failed, but a failed
   action changing state remains a product decision for after the pilot.
3. **A successful reclassify that attaches a document** keeps a green row message and no toast
   (the action does not reload, so nothing is parked); the message disappears on the next poll.
4. **Audit raw JSON still contains absolute paths** (F-20, out of scope); the visible summary
   shows only the folder’s final segment.
5. Everything in plan §5 stays out of scope: F-09…F-20, vocabulary unification, the
   `#status-filter` enum labels, collapsing the action bar on merged sets, DN/SI search.

---

## 8. How to re-run

```powershell
$env:RUN_E2E = "1"
uv run python -m pytest tests/ui -q -o addopts= -p no:cacheprovider -W ignore   # 30 tests
uv run python -m pytest -q -W ignore -o addopts= -p no:cacheprovider            # full suite
uv run ruff check . ; uv run ruff format --check . ; uv run ty check src
```

Manual harness pass (all findings were also exercised by tests): build and serve
`uv run python scripts/ui_test_env.py --root data/ui_test --serve`, then check a bad
`?status=`, an unknown set id, Re-Extract with no key, the audit page, the ambiguous row, the
rejected-file preview, the Archive label and keyboard-only modal use.
