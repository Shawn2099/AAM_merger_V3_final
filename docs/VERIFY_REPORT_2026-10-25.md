# VERIFY REPORT — 2026-10-25

Verification worker run per `docs/OPENCODE_VERIFY_PROMPT.md`. Report only; nothing was fixed.

Run started: 2026-10-25 14:08:29 (local)

## Summary table

| Step | Command | Exit | Result | Counts |
|---|---|---|---|---|
| 0 | `git status --short` | 0 | PASS | see Step 0 |
| 1a | `uv run ruff check .` | 1 | FAIL | 1 error (F401) |
| 1b | `uv run ruff format --check .` | 0 | PASS | 184 files formatted |
| 1c | `uv run ty check src` | 0 | PASS | all checks passed |
| 2 | `uv run pytest -q --no-cov -p no:warnings --ignore=tests/ui` | 1 | FAIL | 6594 collected; 4 failed, 0 error |
| 3 | business-logic subset | 0 | PASS | 187 tests, 0 failed |
| 4A | `tests/ui` run A | 0 | PASS | 94 tests: 91 passed, 3 xfailed |
| 4B | `tests/ui` run B | 0 | PASS | 94 tests: 91 passed, 3 xfailed |
| 4C | `tests/ui` run C (reversed file order) | 0 | PASS | 94 tests: 91 passed, 3 xfailed |
| 4D | `tests/ui/test_ui_settings.py` x3 | 0 / 0 / 0 | PASS | 20 passed each run |
| 5a | ws6 subset x3 | 0 / 0 / 0 | PASS | 20 tests (1 skipped) each run |
| 5b | PowerShell parse check (5 scripts) | n/a | PASS | 0 parse errors in all 5 |
| 5c | `install_nssm_services.ps1 -WithPrefectServer -DryRun` | 1 | FAIL | refused by `server.allowed_hosts` guard; no service plan printed |

Run completed: 2026-10-25 14:53:22 (local)

## Step 0 — Precondition

### `git status --short` (exit 0)

```
 M CLAUDE.md
 M HANDOFF_PLAN.md
 M docs/DELIVERY.md
 M docs/OPENCODE_WS1_PLAN.md
 M docs/OPUS_REVIEW_2026-10-25.md
 M docs/RUNBOOK.md
 M docs/SETTINGS_SPEC.md
 M docs/UI_TEST_PLAN.md
 M scripts/install_nssm_services.ps1
 M scripts/setup_test_rig.ps1
 M src/app/api/routes/dashboard.py
 M src/app/api/routes/po_sets.py
 M src/app/core/config.py
 M src/app/flows/sync.py
 M src/app/services/extraction.py
 M src/app/services/ingestion.py
 M src/app/services/poller.py
 M src/app/services/quarantine.py
 M src/app/services/reconciliation.py
 M src/app/services/settings_admin.py
 M templates/po_set_detail.html
 M tests/conftest.py
 M tests/test_robustness_batch_sync.py
 M tests/test_settings_page.py
 M tests/test_settings_page_more.py
 M tests/test_windows_scripts.py
 M tests/test_ws6_d21_counts.py
 M tests/ui/test_ui_set_detail.py
 M tests/ui/test_ui_settings.py
?? data/real_samples/
?? docs/OPENCODE_VERIFY_PROMPT.md
?? docs/OPENCODE_WS1_FIXES.md
?? docs/VERIFY_REPORT_2026-10-25.md
?? tests/test_s1_provider_breaker.py
?? tests/test_s2_copy_safety.py
?? tests/test_s3_startup_fallback.py
?? tests/test_s4_s5_s8_ui_truth.py
```

### Required test files present

| File | Present |
|---|---|
| `tests/test_s1_provider_breaker.py` | yes |
| `tests/test_s2_copy_safety.py` | yes |
| `tests/test_s3_startup_fallback.py` | yes |
| `tests/test_s4_s5_s8_ui_truth.py` | yes |

None missing.

### File times — start (2026-10-25 14:08:29)

| File | LastWriteTime |
|---|---|
| `src/app/services/extraction.py` | 2026-10-25 12:51:00 |
| `src/app/services/ingestion.py` | 2026-10-25 13:44:39 |
| `src/app/flows/sync.py` | 2026-10-25 13:20:33 |

_End times recorded in the "File times" section at the bottom._

## Step 1 — Lint and types

### 1a. `uv run ruff check .` — exit 1 — FAIL

One error:

```
F401 [*] `pytest` imported but unused
 --> tests\ui\test_ui_set_detail.py:6:8
  |
4 | from pathlib import Path
5 |
6 | import pytest
  |        ^^^^^^
7 | from playwright.sync_api import Page, expect
  |
help: Remove unused import: `pytest`
  |

Found 1 error.
[*] 1 fixable with the `--fix` option.
```

Note: `tests/ui/test_ui_set_detail.py` is listed as modified (`M`) in `git status`.

### 1b. `uv run ruff format --check .` — exit 0 — PASS

```
184 files already formatted
```

### 1c. `uv run ty check src` — exit 0 — PASS

```
All checks passed!
```

## Step 2 — Full backend suite (no browser)

Command: `uv run pytest -q --no-cov -p no:warnings --ignore=tests/ui`
Output redirected to `$env:TEMP\pt2.out`.

- **Exit code: 1** — FAIL
- Collected (via `--collect-only -q`): 6594 tests across 106 files
- pytest printed **no final tally line** in this configuration — the output ends at the `short test summary info` FAILED list. Counts below are from the dot/x/s progress characters and the FAILED list, not from a summary line.
- 4 FAILED, 0 ERROR.

Note: a first attempt of this same command was killed by my 600 s harness timeout after pytest had already printed the FAILED list (3 failures, the `test_ingestion_truncated_pdf.py` one absent). The command was re-run once as instructed (exit 1, 4 failures). See "Flaky".

### Failures (Step 2)

**1. `tests/test_ingestion_truncated_pdf.py::test_is_file_stable_accepts_steady_nonempty_file`**

```
tests\test_ingestion_truncated_pdf.py:119: in test_is_file_stable_accepts_steady_nonempty_file
    assert is_file_stable(p, interval=1, count=3) is True
E   AssertionError: assert False is True
E    +  where False = is_file_stable(WindowsPath('C:/Users/malliath/AppData/Local/Temp/pytest-of-innovizta_2/pytest-2080/test_is_file_stable_accepts_st0/steady.pdf'), interval=1, count=3)
```

Passes alone: **yes** (`exit=0`). Order-dependent and not reproducible in run 1 of the suite.

**2. `tests/test_s3_startup_fallback.py::test_start_on_broken_file_uses_newest_valid_backup`**

```
tests\test_s3_startup_fallback.py:52: in test_start_on_broken_file_uses_newest_valid_backup
    assert any("20261002T000000Z" in r.getMessage() for r in caplog.records)
E   assert False
E    +  where False = any(<generator object test_start_on_broken_file_uses_newest_valid_backup.<locals>.<genexpr> at 0x0000023C71C232A0>)
```

Passes alone: **yes** (`exit=0`). The full file `tests/test_s3_startup_fallback.py` also passes alone (6 passed, exit 0).

**3. `tests/test_s3_startup_fallback.py::test_skips_invalid_newest_backup`**

```
tests\test_s3_startup_fallback.py:63: in test_skips_invalid_newest_backup
    assert any("20261001T000000Z" in m for m in messages)
E   assert False
E    +  where False = any(<generator object test_skips_invalid_newest_backup.<locals>.<genexpr> at 0x0000023C71C228E0>)
```

Passes alone: **yes** (`exit=0`).

**4. `tests/test_s3_startup_fallback.py::test_error_logged_once_across_repeated_loads`**

```
tests\test_s3_startup_fallback.py:100: in test_error_logged_once_across_repeated_loads
    assert len(errors) == 1
E   assert 0 == 1
E    +  where 0 = len([])
```

Passes alone: **yes** (`exit=0`).

All three `test_s3_startup_fallback.py` failures are `caplog`-based assertions that see no records at all when the whole suite runs, while the same tests pass in isolation and as a file. Cause unknown (something in the full-suite run suppresses/redirects the captured log records).

## Step 3 — Business-logic subset

Command: `uv run pytest -q --no-cov -p no:warnings tests/test_business_scenarios_ui.py tests/test_reconciliation.py tests/test_edge_reconciliation.py tests/test_matching.py tests/test_grouping.py tests/test_merge.py tests/test_customs.py tests/test_quarantine.py tests/test_dashboard.py tests/test_best_case_pipeline.py tests/test_untested_engine_paths.py`
Output redirected to `$env:TEMP\pt3.out`.

- **Exit code: 0** — PASS
- 187 tests ran (progress characters: 72 + 72 + 43), 0 failed, 0 error, no FAILED/ERROR lines.
- No final tally line printed; the last lines are a Prefect "Stopping temporary server" notice (written to stderr, PowerShell surfaces it as a `NativeCommandError` block) and a test-emitted `--- Error logging to API ---` / `All connection attempts failed` message. Neither is a test failure.

## Step 4 — Browser suite (Chromium, `RUN_E2E=1`)

All four runs used `-rxX` so XFAIL/XPASS reasons are reported. No final tally line is printed by this pytest setup; test counts are from the progress characters.

### Run A — `uv run pytest tests/ui -q --no-cov -p no:warnings` — exit 0 — PASS

- 94 tests: 91 passed, 3 xfailed, 0 failed, 0 error, 0 xpassed.

```
................x.....xx................................................ [ 76%]
......................                                                   [100%]
```

### Run B — same command again — exit 0 — PASS

- 94 tests: 91 passed, 3 xfailed, 0 failed. Byte-identical XFAIL list to Run A.

### Run C — reversed file order — exit 0 — PASS

File order passed to pytest: `tests/ui/test_ui_unclassified.py tests/ui/test_ui_settings.py tests/ui/test_ui_set_detail.py tests/ui/test_ui_global.py tests/ui/test_ui_dashboard.py`

- 94 tests: 91 passed, 3 xfailed, 0 failed.

```
.....................................................................x.. [ 76%]
...xx.................                                                   [100%]
```

### Run D — `tests/ui/test_ui_settings.py` three times in a row — exit 0 / 0 / 0 — PASS

| Run | Exit | Tests | Result |
|---|---|---|---|
| D1 | 0 | 20 | all passed |
| D2 | 0 | 20 | all passed |
| D3 | 0 | 20 | all passed |

No failure in any of the three.

### XFAIL / XPASS (identical in runs A, B, C)

| Marker | Test | Reason |
|---|---|---|
| XFAIL | `tests/ui/test_ui_global.py::test_ui_g_01b_a_favicon_is_served[chromium]` | SUSPECTED PRODUCT BUG UI-G01: no favicon; every page load logs a 404 for /favicon.ico |
| XFAIL | `tests/ui/test_ui_global.py::test_ui_g_03_no_horizontal_scroll[chromium-768-dashboard]` | SUSPECTED PRODUCT BUG UI-G03-768-dashboard: scrollWidth 830 > clientWidth 768 |
| XFAIL | `tests/ui/test_ui_global.py::test_ui_g_03_no_horizontal_scroll[chromium-768-set]` | SUSPECTED PRODUCT BUG UI-G03-768-set: scrollWidth 830 > clientWidth 768 |

**XPASS: none.** No strict-xpass marker needs removing on the basis of these runs.

## Step 5 — Extra checks

### 5a. ws6 subset, three runs in a row — exit 0 / 0 / 0 — PASS

Command: `uv run pytest -q --no-cov -p no:warnings tests/test_ws6_concurrency.py tests/test_ws6_d21_counts.py tests/test_ws6_d2_original_pages.py tests/test_ws6_poller_soak.py tests/test_ws6_restart_recovery.py`

| Run | Exit | Tests | Result |
|---|---|---|---|
| 1 | 0 | 20 (1 skipped) | all others passed |
| 2 | 0 | 20 (1 skipped) | all others passed |
| 3 | 0 | 20 (1 skipped) | all others passed |

Progress line identical in all three runs: `.....s..............  [100%]`. No failure, no difference between runs.

### 5b. PowerShell parse check — error count 0 — PASS

| Script | Parse errors |
|---|---|
| `scripts/setup_test_rig.ps1` | 0 |
| `scripts/install_nssm_services.ps1` | 0 |
| `scripts/manage_services.ps1` | 0 |
| `scripts/backup_db.ps1` | 0 |
| `scripts/verify_backup.ps1` | 0 |

### 5c. `.\scripts\install_nssm_services.ps1 -WithPrefectServer -DryRun` — exit 1 — FAIL (refused before the service plan)

Exact output:

```
ERROR: server.allowed_hosts in C:\Users\malliath\Desktop\aam_merger\config.yaml is missing or contains '*' (accepts any Host header). Set it to this PC's name and static IP, e.g. ["aam-server", "10.0.0.5"].
Nothing was installed. (Lab installs only: re-run with -AllowAnyHost.)
```

The required command exits 1 at the `server.allowed_hosts` guard, so the Web/Worker `DependOnService` lines are **not** produced by this command as specified. Not verified by the specified command.

Extra (not in the prompt, same dry run, adds `-AllowAnyHost`): `.\scripts\install_nssm_services.ps1 -WithPrefectServer -DryRun -AllowAnyHost` → **exit 0**, nothing changed. In that plan:
- `AAMMerger-Worker`: `[DRY-RUN] nssm set AAMMerger-Worker DependOnService AAMMerger-PrefectServer` → "Depends on: AAMMerger-PrefectServer" — as required.
- `AAMMerger-Web`: no `DependOnService` line at all in its block — as required (no dependency).
- Also printed: `[DRY-RUN] not running as Administrator; a real install would stop here`, `[DRY-RUN] NSSM not found; a real install would download NSSM 2.24 into tools\nssm`, Binding `0.0.0.0:8000`, Work Pool `aam-merger-process-pool`.

## Failures

| # | Test | Exit in full suite | Passes alone | Related `git status` entries |
|---|---|---|---|---|
| 1 | `tests/test_ingestion_truncated_pdf.py::test_is_file_stable_accepts_steady_nonempty_file` | failed in suite run 2, passed in suite run 1 | yes | `M src/app/services/ingestion.py`, `M tests/conftest.py` |
| 2 | `tests/test_s3_startup_fallback.py::test_start_on_broken_file_uses_newest_valid_backup` | failed in suite runs 1 and 2 | yes | `?? tests/test_s3_startup_fallback.py` (new, uncommitted) |
| 3 | `tests/test_s3_startup_fallback.py::test_skips_invalid_newest_backup` | failed in suite runs 1 and 2 | yes | `?? tests/test_s3_startup_fallback.py` (new, uncommitted) |
| 4 | `tests/test_s3_startup_fallback.py::test_error_logged_once_across_repeated_loads` | failed in suite runs 1 and 2 | yes | `?? tests/test_s3_startup_fallback.py` (new, uncommitted) |
| 5 | `ruff check` F401 `import pytest` | n/a (lint) | n/a | `M tests/ui/test_ui_set_detail.py` |
| 6 | `install_nssm_services.ps1 -DryRun` (config guard) | n/a | n/a | `M scripts/install_nssm_services.ps1`, `M scripts/setup_test_rig.ps1` |

Exact error text: see the Step 2 failure blocks above. All four pytest failures are order-/suite-dependent: each passes alone, and the whole `tests/test_s3_startup_fallback.py` file passes alone (6 passed, exit 0). Cause of the dependency: unknown.

`tests/test_ingestion_truncated_pdf.py` is not in `git status`, so that file itself is unmodified; `is_file_stable` lives in `src/app/services/ingestion.py` (modified).

## XPASS / changed xfails

No XPASS in any run. The three XFAILs below are unchanged and still xfail in runs A, B and C:

- `test_ui_g_01b_a_favicon_is_served[chromium]` — SUSPECTED PRODUCT BUG UI-G01: no favicon; 404 for /favicon.ico on every page load.
- `test_ui_g_03_no_horizontal_scroll[chromium-768-dashboard]` — SUSPECTED PRODUCT BUG UI-G03-768-dashboard: scrollWidth 830 > clientWidth 768.
- `test_ui_g_03_no_horizontal_scroll[chromium-768-set]` — SUSPECTED PRODUCT BUG UI-G03-768-set: scrollWidth 830 > clientWidth 768.

## Flaky

1. **Step 2 full backend suite, run 1 vs run 2.** Run 1 (my 600 s harness timeout killed the wrapper, but pytest had already printed its FAILED list): 3 failures — only the three `test_s3_startup_fallback.py` tests. Run 2 (completed, exit 1): 4 failures — the same three plus `test_ingestion_truncated_pdf.py::test_is_file_stable_accepts_steady_nonempty_file`. The `is_file_stable` failure appeared only once and passes alone.
2. All repeated runs that did complete were stable: UI runs A/B/C identical (94 tests, same 3 xfails), UI run D 20/20 passed three times, ws6 subset 20 tests (1 skipped) identical three times.
3. No other differences observed.

## File times (Step 0 start vs end)

| File | Start (14:08:29) | End (14:53:22) |
|---|---|---|
| `src/app/services/extraction.py` | 2026-10-25 12:51:00 | 2026-10-25 12:51:00 |
| `src/app/services/ingestion.py` | 2026-10-25 13:44:39 | 2026-10-25 13:44:39 |
| `src/app/flows/sync.py` | 2026-10-25 13:20:33 | 2026-10-25 13:20:33 |

No change in any of the three during the run. `git status --short` at the end is identical to the start listing in Step 0 (same 29 modified, same 8 untracked entries), so no source file moved while the suite ran.

## Not run / could not run

- **Step 5c as specified** did not reach the service plan (exit 1 on the `server.allowed_hosts` guard). The Web/Worker `DependOnService` behaviour was instead observed with `-AllowAnyHost` added (exit 0, dry run), recorded above.
- **pytest tally lines** ("N passed, M failed") are not printed in this repo's configuration for any run (`-q --no-cov -p no:warnings`); only the progress characters and the `short test summary info` FAILED/XFAIL lists are available. Counts above are derived from those.
- **Nothing else was skipped.** Browser runs used Chromium only; no other browser engine was exercised.
- **Not attempted (out of scope of the prompt):** live Prefect server / NSSM service start, real vendor PDF extraction against a live LLM, and any concurrent Force-Merge 409 test against a running server.

## Run environment

- Repo: `C:\Users\malliath\Desktop\aam_merger`, Windows, PowerShell, Python via `uv run`.
- Only file written by this worker: `docs/VERIFY_REPORT_2026-10-25.md`. No fixes applied, no git state changes.
- Run window: 2026-10-25 14:08:29 → 14:53:22 local.
- `.env` was not read or printed.

