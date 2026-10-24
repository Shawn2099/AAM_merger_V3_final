# Brief for the worker (DeepSeek via OpenCode): pre-deploy UI fixes PF-01..PF-09

Source of truth for WHAT to build: `docs/UI_PREDEPLOY_PLAN_2026-10-25.md` (items PF-01..PF-10) and the evidence in
`UI_EXPLORATION_2026-10-24.md`. This brief only fixes the scope, the order and the rules. The reviewer (Claude/Opus) decides
pass/fail; you do not commit.

## Reviewer decisions (these override the plan where they differ)
1. APPROVED, in this order, one logical change at a time: PF-01, PF-09, PF-03, PF-04, PF-05, PF-06, PF-07 together with PF-02, PF-08 last, PF-10 docs.
2. The plan's section "D5 / do not touch dirty files" is OBSOLETE: the tree is committed (905d974). You may edit `templates/po_set_detail.html` if a change needs it, but prefer the files the plan lists.
3. Plan D1 stands: do NOT change the Re-Extract status machine; fix the message only. Plan D2 stands: never pre-select the type `<select>`.
4. Archive needs NO justification (leave optional). Rename the button to Archive (PF-09).
5. The lock-timeout note at the end of the plan is already handled (default is now 900; a shorter value is raised with a warning). Do nothing.
6. Everything in plan section 5 (out of scope) stays out of scope. Do not "improve" anything not listed.
7. No change to reconciliation, grouping, matching, merge, models, migrations or config keys. HTTP status codes and JSON contracts listed in plan section 3 must not change.

## Rules
- Test first: for each item write/extend the test, see it fail for the stated reason, then fix, then see it pass.
- Allowed paths: `static/js/app.js`, `templates/**`, `src/app/api/routes/dashboard.py`, `scripts/ui_test_env.py`, `tests/**`, `docs/UI_TEST_PLAN.md`. Anything else: stop and report.
- Never weaken or delete an existing test to get green. If an existing test pins the behaviour you are changing, stop and report it.
- No network, no API keys, no real data (`data/aam_merger.db`, `data/input*`, `config.yaml`, `.env` are off limits; PF-01 must make that true for the harness).
- Do not use `git` except `git status`/`git diff`. Do not commit.
- Line endings: do not change them (the repo is LF via `.gitattributes`; some working-tree files may show CRLF, leave each file as you found it). Python escapes in heredocs get mangled; write files with your editor tool.
- Module names under `tests/` must be unique (`tests/ui/test_ui_<area>.py`).
- Windows PowerShell; repo root; `uv run ...`. UI tests: `$env:RUN_E2E="1"; uv run python -m pytest tests/ui -q -o addopts= -p no:cacheprovider -W ignore`.

## Done means (report all of it)
- After each PF item: the targeted tests, then at the end the full suite (`uv run python -m pytest -q -W ignore -o addopts= -p no:cacheprovider`), `tests/ui` three times in a row, `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check src`. Paste the final summary lines.
- A list of files changed per item, assumptions you made, and anything in the product that looks wrong (finding, not a fix).

## Corrections from the Opus review of this plan (2026-10-25, binding; they override the plan text)
If you already built an item differently, fix it to match.
1. **PF-07 event:** in htmx 1.9.12 an `HX-Refresh` response reloads the page BEFORE `htmx:beforeSwap` fires. Use an `htmx:beforeOnLoad` listener. Read the message with `DOMParser` and `textContent`; never `innerHTML`/`x-html`. Persist a toast in `sessionStorage` only for actions that are followed by a reload, and restore only when `type` is one of `success`, `info`, `warning`, `error`.
2. **PF-02 / S-16:** `quarantined_unmatched_vendor_line` is also used by S-04 on a session-scoped harness, so the S-16 test must use its OWN seeded scenario in `scripts/ui_test_env.py` (otherwise S-04 breaks under "x3 / reverse order"). Re-Extract with the default retry backoff [2, 5, 15] can take over a minute: set the harness backoff to 0 or give the test a long timeout. A failed extraction entry is `e.error || e.status !== 'valid'` (the route emits both shapes, `po_sets.py:496-505`). Only the last `else` branch of the Merge Now toast drops the server's `detail`/`reason` (`app.js:166-180`); fix that branch only. The merged toast must show the file NAME only, never `merged_output_path`.
3. **PF-05:** `manual_status_change` audit rows have no "note" key and new shapes will appear later. For any unknown JSON shape use the action label as the summary; the page must never fail on a detail it cannot parse.
4. **PF-09:** rename the button only. Leave the reclassify refusal text ("Delete (archive) the set", `dashboard.py` ~1482) alone; `tests/test_reclassify_scope.py:111` pins "Delete".
5. **PF-03:** a non-integer set id stays a framework 422. Do NOT add a `po_set_id: str` guard.
6. **PF-08:** restore focus only if `document.contains(trigger)` (the quarantine table is re-rendered every 3 s); handle Shift+Tab; skip disabled and hidden elements.
7. Stale reference: dotenv loading is at `src/app/core/config.py:440-465`.
8. The plan's "do not touch dirty files" rule is obsolete (see above); `po_set_detail.html` may be edited.
