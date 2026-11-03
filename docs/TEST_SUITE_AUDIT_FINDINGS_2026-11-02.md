# Test-Suite Audit — Findings

**Date:** 2026-11-02
**Branch:** `main` at `d6b905a`
**Scope:** all tests under `tests/` (123 modules, 1,373 test functions, 6,981 collected cases), the test bootstrap, UI harness, pytest configuration, and the docs that describe them.
**Method:** four domain subagents (reconciliation, extraction/ingestion, API/UI, ops/config) plus direct verification on this checkout. No product code was changed during the audit.

> **Source-of-truth note:** the merged-set removal behavior was resolved separately: the code is treated as the source of truth and `AAM_merger_V3_PRODUCT.md` §8 N was updated. This audit does **not** propose changing that behavior.

---

## 1. Executive summary

The suite is large and mostly green:

```text
pytest tests --ignore=tests/ui
  ~6,981 cases, no failures/errors observed, 44 xfailed, 13 skipped, ~18 min

pytest tests/ui (parent only, RUN_E2E=1)
  113 passed

pytest tests/ui (parent + release, RUN_E2E=1)
  24 failed, 210 passed, 1 xfailed (403.54s)
```

It has genuinely strong areas: the real-PDF best-case pipeline, the HTTP-layer VLM fake behind the real `openai`/`instructor` stack, real thread/barrier Force Merge races, restart recovery, copy safety, migration upgrade, config fail-fast, and the business-scenario UI matrix.

It is **not** production-grade as a whole because:

1. The documented UI gate command is broken by test-environment mixing (24 failures).
2. The default run is not hermetic: it can read the developer/production `config.yaml`, and a production-like `allowed_hosts` makes 21+ tests fail with 400s.
3. 235 UI cases are skipped by default; there is no CI and `--cov-fail-under=0`.
4. Several green tests are tautologies or stale premises, and two files assert contradictory behavior.
5. The suite leaks temp data (~678 MB observed) and UI/Prefect processes, and Prefect teardown adds ~2 minutes per run.
6. Deployment is not actually tested: no NSSM service start, no 11:30 backup task, no Prefect process-pool registration, no backup restore.

---

## 2. Severity summary

| ID | Severity | Finding | Status |
|---|---|---|---|
| F-01 | CRITICAL | `pytest tests/ui` mixes parent and release databases/servers: 24 failures / 210 passed | Fix planned |
| F-02 | CRITICAL | Merged-set removal contradicts PRODUCT §8 N | **Resolved by user decision:** code is source of truth; doc updated |
| F-03 | CRITICAL | Tests read the ambient `config.yaml`; production-like `allowed_hosts` makes 21+ tests fail with 400 | Fix planned |
| F-04 | HIGH | No CI, no coverage gate (`--cov-fail-under=0`), UI/release opt-in | Fix planned |
| F-05 | HIGH | UI test servers leak (8 uvicorn processes observed; stale servers from 27 Oct still running) | Fix planned |
| F-06 | HIGH | Global conftest forces `split_combined=True` and `MIN_FILE_AGE_SECONDS=0`, hiding production defaults | Fix planned |
| F-07 | HIGH | Session temp dir is never removed; 1,708 session dirs + 8,537 `sndprop_*` dirs / ~678 MB leaked | Fix planned |
| F-08 | HIGH | Pytest teardown hangs after summary (17s test → 141s wall; Prefect `server.stop()` after `taskkill`) | Fix planned |
| F-09 | HIGH | Clean-clone backup tests fail or pass for the wrong reason under `AAM_CONFIG_PATH` precedence | Fix planned |
| F-10 | HIGH | `test_installer_real_run_refuses_foreign_config_path` runs the real NSSM installer without `-DryRun` | Fix planned |
| F-11 | HIGH | Stale/contradictory `test_split_delivery_defect.py` vs `test_split_delivery_e2e.py` | Fix planned |
| F-12 | HIGH | Tautological/vacuous tests across reconciliation, merge, dashboard, UI | Fix planned |
| F-13 | HIGH | `test_real_samples.py` is not extraction; it inserts rows by hand and uses blank PDFs | Fix planned (rename + limitation split) |
| F-14 | HIGH | 17 `test_erp_line_formats.py` xfails encode obsolete global renumbering and contradict the passing test | Fix planned |
| F-15 | HIGH | ERP simulator already diverges from the real engine | Fix planned (differential/fallback fix) |
| F-16 | HIGH | `test_synthetic_spec` skips on non-merge, so a false-quarantine regression is green | Fix planned |
| F-17 | HIGH | No deployment coverage: NSSM start, 11:30 backup, Prefect process pool, restore | Deferred / environment |
| F-18 | HIGH | `test_concurrency.py::test_po_lock_409` weak/duplicate; real test is in `test_ws6_concurrency.py` | Fix planned |
| F-19 | HIGH | `max_concurrent_extraction_tasks` accepts 1–10 while AGENTS says fixed at 3 | Decision needed |
| F-20 | MEDIUM | Cross-test imports (~30) and 29 audit/review modules (403 tests) | Partial / deferred |
| F-21 | MEDIUM | Source-text/CSS guards used as behavior tests | Partial |
| F-22 | MEDIUM | Hardcoded `742+60`/`802`, `/tmp`, exact CSS/copy, seeded values | Fix planned / partial |
| F-23 | MEDIUM | Settings `test_b_11` reads the developer `config.yaml`; `test_b_09` scans source text | Fix planned |
| F-24 | MEDIUM | `test_preflight_audit_fixes.py` creates CWD-relative `data/` | Fix planned |
| F-25 | MEDIUM | Manual-merger UI tests are source-string snapshots | Deferred (E2E exists) |
| F-26 | MEDIUM | Static accessibility tests do not test accessibility | Deferred (axe is opt-in) |
| F-27 | MEDIUM | Visual pixel baselines are platform/font-sensitive | Fix planned (platform guard) |
| F-28 | MEDIUM | Windows read-only-config test skips on Windows | Fix planned (force PermissionError) |
| F-29 | MEDIUM | Startup fallback, “Merged • review”, Force Merge justification contracts contradict docs/tests | Decision needed |
| F-30 | MEDIUM | Python 3.12 test venv vs 3.11 project/production; no matrix | Fix planned (CI matrix) |
| F-31 | LOW | Stale docstrings/names (“midnight”, temporary verify file, xfail claims) | Fix planned |
| F-32 | LOW | Unused `_mtime`, dead mocks, one-value parametrization, `print`-only test | Fix planned |
| F-33 | LOW | No `pytest-timeout`, `filterwarnings=error`, `--strict-config` | Fix planned (timeout/strict) |

---

## 3. Verified evidence

### 3.1 Suite baseline

- `pytest tests --ignore=tests/ui`: no `F`/`E` characters through 98%; the final 8 modules re-ran as **56 passed, 1 skipped**.
- `pytest tests/ui --ignore=tests/ui/release` with `RUN_E2E=1`: **113 passed**.
- `pytest tests/ui` with `RUN_E2E=1`: **24 failed, 210 passed, 1 xfailed**; the failures are cross-environment fixture mixing, not product failures.
- `pytest tests/ui/release/test_contract.py::test_every_page_loads_without_errors`: **6 passed** in 25s.

### 3.2 Production-config reproduction

With a temp config copied from `config.example.yaml` and `server.allowed_hosts: ["aam-server"]`:

```text
pytest tests/test_styled_404.py
4 failed: assert 400 == 404
```

`app.main:450` creates the app at import time; `app.main:346` reads `allowed_hosts` from the ambient config. `tests/conftest.py:187` deliberately lets a developer `config.yaml` win.

### 3.3 Clean-clone backup reproduction

With `AAM_CONFIG_PATH` pointing at a temp config (the clean-clone condition created by `tests/conftest.py:177-195`):

```text
pytest tests/test_backup_script.py
F.
FAILED test_relative_database_path_resolves_against_project_root
FileNotFoundError: Database file not found at ...\aam_cfg_probe2\database_path
```

`test_missing_config_is_an_error_not_a_silent_default` passed for the wrong reason (missing DB, not missing config).

### 3.4 Prefect teardown

```text
pytest tests/test_ws6_poller_soak.py::test_ws6_soak_small_real_prefect_path
test body 17.14s; wall clock 140.7s
last log: "Stopping temporary server ... All connection attempts failed"
```

### 3.5 Temp leakage

```text
aam-merger-tests-*: 1,708 dirs / 113.5 MB
sndprop_*:          8,537 dirs / 564.9 MB
```

`tests/conftest.py:54` says the session dir “is removed when the session ends”; `pytest_sessionfinish` only kills Prefect.

### 3.6 UI environment mixing

`tests/ui/conftest.py:144-147` defines session-scoped `base_url` from the parent `ui_env`; `tests/ui/release/conftest.py:67-80` overrides `ui_env` but not `base_url`. Once `browser_context_args` (`tests/ui/conftest.py:164-181`) is cached for the parent, release tests use the parent URL while their DB/manifest point at the release env.

### 3.7 UI server leak

After the combined UI run: 8 `app.main:app` uvicorn processes remained. `tests/ui/conftest.py:82-108` `empty_ui_env` has no teardown; `_stop()` (`:134-142`) terminates the venv trampoline, not the real child. `tests/ui/release/conftest.py:83-91` uses the same pattern. Stale servers from 27 Oct were still listening on `54198` and `50700`.

---

## 4. Findings detail

### F-01 (CRITICAL) — combined UI run mixes environments

- `tests/ui/conftest.py:144-147`, `:164-181`
- `tests/ui/release/conftest.py:67-80`
- Symptoms: expected 41 rows page showed 38; a seed written to the parent DB while the page read the release server; a force-merge target consumed by a release test.
- Fix: separate parent/release runs, or give the release directory its own `base_url`, `context`, and `page` fixtures (do not share pytest-playwright's session context).

### F-02 (CRITICAL) — merged-set removal contract

- Resolved by user decision: code is source of truth.
- `AAM_merger_V3_PRODUCT.md` §6/§8 N, `docs/TESTER_GUIDE.md`, `docs/BATCH1_CHANGES_2026-10-25.md` updated.
- No code/test change is part of this remediation.

### F-03 (CRITICAL) — non-hermetic config

- `src/app/main.py:450`, `:346`
- `tests/conftest.py:187`
- `tests/conftest.py:101-122` only redirects paths inside the repo; absolute production paths are left untouched.
- Fix: always provide a session test config (`AAM_CONFIG_PATH`), force `allowed_hosts=["*"]`, `poll_enabled=False`, and paths under the session dir; monkeypatch `app.main.load_config` in TestClient fixtures where needed.

### F-04 (HIGH) — no CI / no gate

- `.github/` absent.
- `pyproject.toml:89` `--cov-fail-under=0`.
- `tests/ui/conftest.py:29-37` skips all UI unless `RUN_E2E=1`.
- `pyproject.toml:91-93` declares `slow`/`release` markers that are never applied; `pytest -m release` collects 0.
- `Makefile:13-14` runs plain `pytest`.

### F-05 (HIGH) — UI server leak

- `tests/ui/conftest.py:82-108`, `:134-142`
- `tests/ui/release/conftest.py:83-91`
- Fix: `try/finally` for every env; `taskkill /F /T` on Windows; close stdout; fall back to `kill()`.

### F-06 (HIGH) — production defaults overridden

- `tests/conftest.py:139-141` forces `split_combined=True`; production default is `False` (`src/app/core/config.py:97`, `config.example.yaml`).
- `tests/conftest.py:148-174` forces `MIN_FILE_AGE_SECONDS=0`; production is `30` (`src/app/services/ingestion.py:29`).
- No test asserts the real `MIN_FILE_AGE_SECONDS`; only out-of-scope `test_combined_quarantine.py:82` asserts `ExtractionConfig().split_combined is False`.
- Fix: remove the global `split_combined=True` (set it explicitly in split tests); add default assertions.

### F-07 (HIGH) — temp leakage

- `tests/conftest.py:54-55` claims cleanup that does not exist.
- `tests/test_prop_reconcile.py:66`, `tests/test_reconciliation.py:52`, `tests/test_synthetic_spec.py:704` use `mkdtemp()` without cleanup.
- Fix: `shutil.rmtree(_SESSION_DIR, ignore_errors=True)`; replace `mkdtemp()` with `tmp_path`/`TemporaryDirectory`.

### F-08 (HIGH) — Prefect teardown hang

- `tests/conftest.py:198-235` calls `server.stop()` after force-killing the process; observed ~2 minutes per Prefect run.
- Fix: reset/skip `server.stop()` after the process tree is killed, or bound it; add `pytest-timeout`.

### F-09 (HIGH) — backup tests and `AAM_CONFIG_PATH`

- `scripts/backup_db.py:90-100` gives `AAM_CONFIG_PATH` precedence.
- `tests/conftest.py:177-195` sets it on a clean clone.
- `tests/test_backup_script.py:56-71` and `tests/test_audit_scripts.py` fixtures write their own `config.yaml` but do not clear the env var.
- Fix: `monkeypatch.delenv("AAM_CONFIG_PATH")` in the fixtures; add an env-precedence test.

### F-10 (HIGH) — real installer in a unit test

- `tests/test_windows_scripts.py:180-186` calls `_installer(..., dry=False)`.
- Only `scripts/install_nssm_services.ps1:120-123` prevents a real install.
- Fix: pass `-ProjectDir tmp_path` / use a fake `nssm`, or skip the real run.

### F-11 (HIGH) — contradictory split-delivery files

- `tests/test_split_delivery_defect.py` header: “BEFORE any fix… there is no fix yet”.
- `tests/test_split_delivery_e2e.py` header: “This file OVERTURNED the hypothesis… the defect does not reach the reviewer.”
- `test_split_delivery_defect.py:174-186` builds its own `projected` list and asserts `dn_no` absent; `:284-311` has `assert partial == verdict_short or partial[0] == "flags"` (always true).
- Fix: delete the stale defect file; keep the e2e record.

### F-12 (HIGH) — tautologies / vacuous tests

| Test | Problem |
|---|---|
| `tests/test_split_delivery_defect.py:174-186` | asserts its own list, not production |
| `tests/test_split_delivery_defect.py:284-311` | `or partial[0] == "flags"` is always true |
| `tests/test_per_line_dn_association.py:205-220` | asserts its own fixture rows |
| `tests/test_edge_reconciliation.py:180-185` | allows `pending` **or** `quarantined`; source is deterministic |
| `tests/test_edge_reconciliation.py:195-199` | empty test |
| `tests/test_untested_engine_paths.py:586` | status is one of all four statuses |
| `tests/test_untested_engine_paths.py:695-697` | `assert os and time and POSet` |
| `tests/test_merge.py:266` | `_mtime` captured, never used |
| `tests/test_merge.py:307-334` | named `writes_audit` but no audit query |
| `tests/test_merge.py:491-501` | only two HTTP 200s on an ineligible set |
| `tests/test_dashboard.py:359-372` | `"running" in JSON` is always true; unjoined sync thread |
| `tests/test_dashboard.py:311-318` | `any(...) or len(entries) >= 1` always true; no `po_set_id`/justification |
| `tests/ui/test_ui_batch1_audit_findings.py:145-151` | only asserts the URL is still `/quarantine` |
| `tests/ui/release/test_zero_states.py:61-66` | only checks visibility, not zero |
| `tests/ui/release/test_human.py:32` | `count() >= 0` |
| `tests/ui/release/test_contract.py:48-113` | presence, not visibility/enabled |

### F-13 (HIGH) — “real samples” is not extraction

- `tests/test_real_samples.py:402-426` writes blank PDFs; `:445-462` inserts rows by hand; `:475` only calls `reconcile_po_set`.
- `data/real_samples/` is gitignored and unused.
- `:350-369` D7264 comment says ground truth is `quarantined`, but `"expect": "merged"` and the test is named `test_real_po_set_matches_ground_truth`.
- Fix: rename the file to a reconciliation-samples test; split D7264 into `test_known_limitation_*`; add a real extraction regression separately (deferred).

### F-14 (HIGH) — obsolete ERP xfails

- `tests/test_erp_line_formats.py:47-84`: 17 pairs asserted **distinct** in the passing test and **equal** in the strict xfail block.
- PRODUCT §2/§8 says step-10 is a per-pool fallback, not global normalization.
- Fix: delete the xfail block; keep the distinct-key test.

### F-15 (HIGH) — ERP simulator drift

- `tests/test_erp_simulated_pipeline.py:70-142` reimplements the verdict gate and calls `compare_po_set_lines` without the line fallback.
- `:411-421` expects `quarantined` for “vendor line 1 vs SAP 10”, while the engine applies `x10` and merges (`reconciliation.py:896-899`); the file admits this at `:697`.
- Fix: pass the configured fallback / use the real comparison; update the golden case.

### F-16 (HIGH) — synthetic-spec skip

- `tests/test_synthetic_spec.py:173` skips when `status != "merged"`.
- A regression that quarantines every known-good set yields four skips and a green suite.
- Fix: assert the known-good sets merge; keep the independent recomputation.

### F-17 (HIGH) — deployment not tested

- No NSSM service start/health.
- No 11:30 backup task (`deploy_production.ps1:383`, `setup_test_rig.ps1:302`).
- No Prefect `--type process`, `work_pool_name`, or no-`PREFECT_API_URL` refusal.
- No backup restore.
- Only one migration downgrade step.

### F-18 (HIGH) — weak 409 test

- `tests/test_concurrency.py:189-208`: one call, then manually writes the lock, then `r1.status_code in (200, 409)`.
- Real thread/barrier test exists in `tests/test_ws6_concurrency.py:113-208`.
- Fix: assert 200 then 409, or remove the duplicate.

### F-19 (HIGH) — max concurrency drift

- `PrefectConfig(max_concurrent_extraction_tasks=10)` returns 10.
- AGENTS §5 says fixed at 3.
- Decision needed: tighten to `Literal[3]`/`ge=3, le=3` and add a test, or amend AGENTS.

### F-20–F-33 (MEDIUM/LOW)

- Cross-test imports: ~30 (`from test_business_scenarios_ui import cfg, client`, `from test_robustness_harness import ...`, etc.).
- 29 audit/review modules / 403 tests; some duplication (`test_reconciliation_anchor.py` vs `test_reconciliation.py`, `test_backup_script.py` vs `test_audit_scripts.py`).
- Source-text/CSS guards: `test_no_combined_in_engine.py`, `test_accessibility_labels.py`, `test_settings_page_more.py:350`, `test_extraction.py:441`, `test_manual_merger_ui.py`.
- Hardcoded: `742 + 60` / `802` (`test_web_review_fixes.py:108`, `test_settings_page_more.py:512`), `/tmp` (`test_packet_customs_facts.py:52`), exact CSS/copy, seeded PO/filenames.
- Settings: `test_b_11` reads the real `config.yaml`, `test_b_09` scans source; `test_b_01` skips on Windows.
- `test_preflight_audit_fixes.py:26-44` omits `combined_folder`/`archive_folder`, creating `data/` in the repo.
- Manual-merger UI: source-string snapshots.
- Accessibility: static CSS checks; real axe is opt-in.
- Visual: nine PNG baselines, threshold `0.1`, no platform guard.
- Startup fallback, “Merged • review”, Force Merge justification: product/test contradictions; decision needed.
- Python 3.12 venv vs 3.11 project/production.
- Stale: “midnight” in `test_prefect_schedule.py`; `test_verify_user_claims.py:1` “Temporary verification”; `test_batch1_audit_findings.py` claims xfails but has none; `print`-only `test_two_si_docs_different_invoice_numbers`.
- No `pytest-timeout`; `pytest` summary then teardown hang.

---

## 5. Tests that are genuinely strong

- `tests/test_business_scenarios_ui.py` — engine verdict and operator pages for every verdict.
- `tests/test_manual_merger.py` — real order, encryption, limits, temp cleanup.
- `tests/test_upload_body_limit.py` — limit before multipart parsing, including chunked bodies.
- `tests/test_best_case_pipeline.py` — real PDFs, real `_call_vlm`, real merge.
- `tests/test_robustness_harness.py` + `test_robustness_batch_sync.py` — HTTP-layer fake behind the real stack.
- `tests/test_ws6_concurrency.py`, `test_ws6_restart_recovery.py`, `test_ws6_d2_original_pages.py`, `test_ws6_poller_soak.py`.
- `tests/test_s1_provider_breaker.py`, `test_s2_copy_safety.py`.
- `tests/test_prop_layer2.py`, `test_prop_reconcile.py`, `test_prop_sanitizer.py`, `test_split_delivery_e2e.py`.
- `tests/test_migrate_db.py` — fresh/legacy DB, downgrade with FKs.
- `tests/test_config_failfast.py`, `test_audit_config_security.py`, `test_ui_test_env_key.py`.
- `tests/ui/release/test_a11y.py`, `test_moves_on_disk.py`, `test_release_dashboard.py`.

---

## 6. Deferred / decision-needed

These are documented but **not** changed by this remediation, because they need a product decision or an environment not available here:

1. **Merged-removal code** — user decision: code is source of truth; doc updated.
2. **Startup fallback** — PRODUCT says refuse; `test_s3_startup_fallback.py`/UI test assert “start on newest backup”.
3. **“Merged • review”** — PRODUCT vs `test_naming.py`/template.
4. **Force Merge justification** — §6 “when supplied” vs §7b “needs a reason”; tests disagree.
5. **`max_concurrent_extraction_tasks`** — AGENTS fixed 3 vs code 1–10.
6. **Real vendor PDF corpus** — `data/real_samples/` is local-only; no committed extraction fixture.
7. **NSSM service start / 11:30 backup / Prefect process pool / backup restore** — needs a disposable Windows host.
8. **Manual-merger UI source-text** — full behavioral rewrite is larger than this remediation; E2E exists.
9. **Static accessibility** — real axe remains opt-in; default suite can only keep lint-level checks.
10. **Visual snapshots** — platform/font baselines need a decision on the reference platform.

---

## 7. Remediation status (2026-11-03)

Implemented in this pass (test/docs only; the merged-set removal behavior is untouched):

| Area | Result |
|---|---|
| UI environment isolation | `pytest tests/ui` now **235 passed, 1 xfailed, 0 failed** (parent + release in one process). Parent-only: 113 passed. Release-only: 100% (1 xfail). |
| Release marker | `-m release` now selects the release directory (it previously selected zero tests). |
| Hermetic config | The suite always installs a session `AAM_CONFIG_PATH` built from `config.example.yaml`; no-arg loads force `allowed_hosts=["*"]` and `poll_enabled=False`. A production-like `allowed_hosts` no longer breaks `TestClient`. |
| Backup fixture isolation | `project` fixtures clear `AAM_CONFIG_PATH`; the clean-clone backup probe passes. |
| Production defaults | `split_combined=True` is no longer forced globally; split tests set it explicitly. The example config's `split_combined=False` is asserted. |
| Temp cleanup | `_SESSION_DIR` is removed after disposing cached SQLAlchemy engines; `mkdtemp()` leaks in `test_prop_reconcile`, `test_reconciliation` and `test_synthetic_spec` are gone. |
| Prefect teardown | Prefect's `atexit` stop callback is unregistered and the server object is neutralised after its process tree is killed. The slow suite now exits in 228 s instead of hanging. |
| UI/Prefect process leak | UI `_stop`/`_stop_env` use `taskkill /F /T`; `empty_ui_env` has a teardown. |
| Stale/contradictory tests | `test_verify_user_claims.py` and `test_split_delivery_defect.py` deleted; split-delivery record is now the e2e file. |
| Vacuous assertions | Fixed: PO-no-lines status, merge immutability bytes/mtime, Force Merge audit filter+justification, sync-button disabled state, set-aside row/DB, zero-state KPI values, human `count() >= 0`. |
| ERP xfails | 17 obsolete global-renumbering xfails deleted; product-rejected 3-decimal/UOM xfails deleted; description gaps converted to named `test_known_limitation_*`. |
| ERP simulator | Uses the configured line fallback; the SAP step-10 golden case now expects `matched`. |
| Synthetic known-good | Known-good invariant sets must merge; a false quarantine can no longer be a green skip. |
| Ingestion safety | Delete test uses a real SHA-256 and adds a same-name/different-bytes survival test. |
| Settings | Unwritable-config failure is forced (no Windows skip); B-11 uses the committed example, not the developer config. |
| Installer safety | The real NSSM installer test is skipped with a disposable-VM reason. |
| Gate hygiene | `pytest-timeout` added; `slow` markers on WS6/soak/UI; `test-fast`/`test-ui`/`test-release` Make targets; Windows-only CI workflow added (non-UI + UI release). |

Still open / deferred (product decision or environment):

- Startup fallback vs fail-fast (`PRODUCT` vs `test_s3_startup_fallback` / `test_st_19`).
- �Merged � review� copy (`PRODUCT` vs `test_naming`).
- Force Merge justification policy (`�6` vs `�7b`).
- `max_concurrent_extraction_tasks` fixed at 3 vs accepted 1�10.
- Real vendor PDF corpus and a true extraction-fidelity regression.
- NSSM service start/health, 11:30 backup task, Prefect process-pool registration, backup restore (need a disposable Windows host).
- Manual-merger UI source-text tests and static accessibility checks (E2E/axe remain the real guards).
- Visual baselines are now Windows-only (the reference platform); a Linux baseline set is not committed.

### Coverage

Measured on the fast non-UI suite: **91% total** (`src`). CI enforces **85%** (`--cov-fail-under=85` on the full non-UI job); the local `addopts` stays at 0 so a single-file test run is not forced to cover the whole app.

### Final verification (2026-11-03)

```text
pytest tests --ignore=tests/ui -m "not slow"   PASS (6,000+ cases; 1 skip)
pytest tests --ignore=tests/ui -m slow         PASS (228 s wall; exits cleanly)
pytest tests/ui (RUN_E2E=1)                    235 passed, 1 xfailed
pytest tests/ui --ignore=tests/ui/release      113 passed
pytest tests/ui/release                        100% (1 xfail)
production-like allowed_hosts probe            PASS (6/6)
clean-clone/AAM_CONFIG_PATH backup probe       PASS
fast-suite coverage                            91% (CI floor 85%)
```

The combined UI command that previously produced **24 failures** now produces **0**. The Prefect teardown that previously hung now exits in seconds; a single soak module ran in 57 s wall and the full slow suite in 228 s.
