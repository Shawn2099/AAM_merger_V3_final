# Test-Suite Hardening — Handover

**Date:** 2026-11-03
**Branch:** `test/suite-hardening-2026-11-03` (created from `main` at `d6b905a`)
**Status:** complete; pushed to the new branch, not merged to `main`.

Read these two files first:

1. [`TEST_SUITE_AUDIT_FINDINGS_2026-11-02.md`](./TEST_SUITE_AUDIT_FINDINGS_2026-11-02.md) — every finding, with severity and evidence.
2. [`TEST_SUITE_REMEDIATION_PLAN_2026-11-02.md`](./TEST_SUITE_REMEDIATION_PLAN_2026-11-02.md) — the plan, execution status and deferred items.

---

## 1. What changed

The work is **test infrastructure and test correctness only**. No product behavior changed; the merged-set removal feature was deliberately not touched (except its documentation, which was already aligned to the code in the preceding step).

| Area | What changed |
|---|---|
| Hermetic bootstrap | `tests/conftest.py` always installs a session `AAM_CONFIG_PATH` built from `config.example.yaml`, forces `allowed_hosts=["*"]` and `poll_enabled=False` for no-arg loads, and no longer forces `split_combined=True`. |
| Temp cleanup | Session dir is removed after disposing SQLAlchemy engines; `mkdtemp()` leaks in `test_prop_reconcile`, `test_reconciliation` and `test_synthetic_spec` were removed. |
| Prefect teardown | `atexit.unregister(server.stop)` + neutralise the server object after `taskkill`. The slow suite now exits cleanly (228 s) instead of hanging. |
| UI isolation | Release directory gets its own `base_url`/`context`; parent `manifest` is function-scoped. `pytest tests/ui` now passes (previously 24 failures). |
| UI process cleanup | `taskkill /F /T`; `empty_ui_env` now has a teardown. |
| Stale tests | Deleted `test_verify_user_claims.py` (temporary duplicate) and `test_split_delivery_defect.py` (stale premise, contradicted by the e2e file). |
| Vacuous assertions | Repaired PO-no-lines status, merge immutability (bytes/mtime), Force Merge audit (set id + justification), sync-button disabled state, set-aside row/DB, zero-state KPI values, human `count() >= 0`. |
| ERP matrices | Removed 17 obsolete global-renumbering xfails and the product-rejected 3-decimal/UOM xfails; description gaps are named `test_known_limitation_*`; simulator uses the configured line fallback. |
| Real samples | D7264 is a named `test_known_limitation_*`; the ground-truth matrix excludes it; the file is documented as reconciliation-only. |
| Ingestion | Real SHA-256 in the delete test; added same-name/different-bytes survival test. |
| Settings | Unwritable-config failure is forced (no Windows skip); B-11 uses the committed example. |
| Installer | Real NSSM installer test is skipped with a disposable-VM reason. |
| Gate | `pytest-timeout`, `slow` markers, `test-fast`/`test-ui`/`test-release`, release marker, Windows visual guard, and `.github/workflows/tests.yml`. |

---

## 2. Verification (observed, not claimed)

Run from the repo root on this Windows PC:

```text
pytest tests --ignore=tests/ui -m "not slow"   PASS
pytest tests --ignore=tests/ui -m slow         PASS (228 s, exits cleanly)
pytest tests/ui (RUN_E2E=1)                    235 passed, 1 xfailed
pytest tests/ui --ignore=tests/ui/release      113 passed
pytest tests/ui/release                        100% (1 xfail)
production-like allowed_hosts probe            PASS (6/6)
clean-clone/AAM_CONFIG_PATH backup probe       PASS
fast-suite coverage                            91% (CI floor 85%)
ruff check src scripts tests                   PASS
```

The combined UI command that previously produced **24 failures** now produces **0**. The Prefect teardown that previously hung now exits in seconds; one soak module runs in ~57 s and the full slow suite in 228 s.

---

## 3. How to run it

```powershell
# from C:\Users\malliath\Desktop\aam_merger
uv sync --all-groups

# fast loop (no soak/WS6/UI)
uv run pytest -q -m "not slow"

# full non-UI (includes slow)
uv run pytest -q --ignore=tests/ui

# UI suites must run in separate processes
$env:RUN_E2E = "1"
uv run pytest tests/ui --ignore=tests/ui/release -q
uv run pytest tests/ui/release -q
```

`pytest-timeout` is set to 300 s per test. `--strict-config` and `--strict-markers` are on. CI is **Windows-only**: one Windows job for lint + non-UI (85% coverage floor) and one Windows UI release job. There is no Linux job.

---

## 4. Deferred / decision-needed

These are documented in the findings file and are **not** silently fixed:

1. Startup fallback vs fail-fast (`test_s3_startup_fallback` / `test_st_19` vs PRODUCT §8).
2. “Merged • review” copy (`PRODUCT` vs `test_naming`).
3. Force Merge justification policy (`§6` vs `§7b`).
4. `max_concurrent_extraction_tasks` fixed at 3 vs accepted 1–10.
5. A committed real-vendor PDF corpus and a true extraction-fidelity regression.
6. NSSM service start/health, 11:30 backup task, Prefect process-pool registration, backup restore — need a disposable Windows host.
7. Manual-merger UI source-text tests and static accessibility checks (E2E/axe remain the real guards).
8. Linux visual baselines (visual tests are Windows-only; the baselines are the Windows reference).

---

## 5. Next session checklist

1. Read `AGENTS.md`, then this file, then the findings/plan.
2. `git status` must be clean; you are expected on `test/suite-hardening-2026-11-03`.
3. If the branch is merged/rebased, `main` may have moved — re-run the verification commands above before changing anything.
4. Do not touch merged-set removal behavior without the owner; the doc now matches the code by explicit decision.
5. When adding a test, keep the existing patterns: real rows/PDFs where practical, `tmp_path`, no developer `config.yaml`, explicit `split_combined` only in split tests, `slow` marker for soak/Prefect/UI.
6. Before committing: `ruff check src scripts tests` and the fast suite. Before a release: the full non-UI suite and both UI suites.
