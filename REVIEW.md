# Code Review: AAM_MERGER-FINAL — Spec vs Implementation

**Date:** 2026-08-16
**Scope:** Full repo audit against `AAM_merger_V3_SPEC.md` (§4-§13) & `AAM_merger_V3_business_logic.md`
**Depth:** deep (cross-file, import graph, DB, routes, services, flows, templates, tests, git diff)
**Files reviewed:** 24 source + 12 test files + 3 Alembic migrations + 9 templates + config/deploy/gitignore
**Baseline verified:** 88 tests pass (exit 0), `ruff check` clean, coverage 73%
**BLOCKER findings:** 10
**WARNING findings:** 22 (incl. 3 git/deploy hygiene)

> Methodology: 4 parallel adversarial review passes (services layer, API+flows+bootstrap, data model+migrations+tests, git-diff+deploy+security), then every cross-cutting finding re-verified directly against the working tree before inclusion. Prior 2026-08-15 REVIEW.md blockers re-checked against current code (status table at bottom).

## Summary

The data model + Alembic migration chain are clean and mutually consistent. Lint/format/tests are all green. The prior audit's 11 BLOCKERs are **mostly fixed** (7 confirmed fixed, 4 partial). However, the working-tree diff introduced several **silent-correctness regressions the green test suite does not catch** — most dangerous: a FR-10.2 partial-pass state that parks sets in `pending` forever, a `normalize_po_no` comma-split that silently changes grouping keys, an always-broken FR-CONC-3 "Sync running" indicator, and a `force_merge` route that reports HTTP 200 success on failure. Two findings are direct code-vs-SPEC contradictions needing a **human decision** before merge (BLOCKER-7, BLOCKER-8).

## BLOCKER

### BLOCKER-1: Per-PO lock acquisition is a TOCTOU race — concurrent 409 never fires (FR-CONC-2)
**File:** `src/app/services/locking.py:40-46`
**Issue:** `acquire_lock` does check-then-write across two non-atomic steps: `if is_locked(ps, cfg): return False` then set+commit. Two threadpool threads (double-click Force Merge) both SELECT `locked_by_action IS NULL`, both pass, both commit. Under SQLite WAL the writes serialize at commit but both requests already decided to proceed → two merges, two `audit_log` rows.
**Impact:** FR-CONC-2's "409, one merge" guarantee not actually enforced under true concurrency. `release_lock` is also unconditional (`locking.py:49-53`) — a finishing request can clear the *next* request's lock (lock-stomp).
**Fix:** Single atomic conditional update: `UPDATE po_sets SET locked_by_action=…, locked_at=… WHERE id=… AND locked_by_action IS NULL` and assert `rowcount == 1`; clear only when `locked_by_action == own action`. Add a `ThreadPoolExecutor` 2×concurrent-POST test (current test manually re-locks via SQL, `tests/test_concurrency.py:91-94`, and never races).

### BLOCKER-2: `force_merge` swallows failures and returns HTTP 200 "queued" (FR-14.8-14.10)
**File:** `src/app/api/routes/po_sets.py:164-166`
**Issue:** `except Exception as e: return {"status": "force_merge queued", …, "detail": {"error": str(e)}}`. A merge that raises (missing stored file, pypdf error, DB error) is reported to the operator as success; the HTMX swap renders nothing.
**Impact:** Audit trail and UI record "queued" for a merge that never happened — the exact silent-failure class SPEC §1 warns about.
**Fix:** Re-raise as `HTTPException(422/500, detail=str(e))`; keep lock release in `finally`; log `logger.exception`.

### BLOCKER-3: Merged-output filename collision silently overwrites another set's delivered PDF (FR-14.6)
**File:** `src/app/services/merge.py:123` (auto), `:168`, `:198` (force merge)
**Issue:** `out = Path(cfg.paths.output_folder) / f"{safe}.pdf"` names output purely from the SI/invoice number with no uniqueness check against other PO Sets. Duplicate invoice numbers across sets (re-issued number, force-merge fallback to `po_no_normalized`) write to the identical path; the second merge **overwrites** the first set's output while both DB rows point at the same path.
**Impact:** Silent data loss of a delivered merged packet. Directly violates FR-14.6 "never … overwritten".
**Fix:** If target exists and belongs to a different PO Set, namespace it (`{invoice}-{po_set_id}.pdf` or numeric suffix), or 409 when the path is already claimed. Add a collision test.

### BLOCKER-4: `_write_merged` silently skips missing stored PDFs → incomplete packet marked `merged` (FR-14.1)
**File:** `src/app/services/merge.py:72-80`
**Issue:** `if not p.exists(): continue` drops any ordered document whose `stored_path` is missing; if *all* are missing, an empty 0-page PDF is still written. In every case `ps.status = merged`, `merged_at` set, no log, no audit entry.
**Impact:** A merged packet missing a required source document (DN, customs file) goes to the CA as if complete; stored copies are the permanent audit trail (business doc §4.5).
**Fix:** Abort the merge (raise; leave status unchanged or `failed`) if any ordered doc's file is absent; log + audit the missing list; never write an empty output.

### BLOCKER-5: Decoy PO code on a DN/SI mints an orphan PO Set — real set stranded in `pending` forever (FR-6.3)
**File:** `src/app/services/grouping.py:10-34` + `src/app/flows/sync.py:93,121`; check at `reconciliation.py:116-141`
**Issue:** Grouping keys solely on each doc's own `po_no_normalized`. A DN whose printed PO field is a decoy/secondary "PO Code" (business doc §6.3, §17.2) is grouped into a brand-new set containing only that DN. The genuine PO+SI set then reconciles with `agg_dn = 0` and (per BLOCKER-7's partial-pass) parks in `pending`. The FR-6.3 cross-doc quarantine check only inspects docs already inside the set, so it never sees the orphaned DN.
**Impact:** Legit merge blocked; the DN silently trapped in the wrong set with no system signal.
**Fix:** Attach a DN/SI to an existing open set containing a PO/SI whose `po_no_normalized` matches the doc's reference; treat a set containing only DN docs (no PO/SI) as quarantine/review candidate rather than silently minting a DN-only set.

### BLOCKER-6: FR-CONC-3 "Sync running" indicator is dead code, and its test falsely passes
**File:** `src/app/api/routes/dashboard.py:77-83`, `src/app/api/routes/sync.py:67-68`
**Issue:** (a) `_sync_running_state()` imports `from app.api.routes.sync import _is_sync_running` — **no such symbol exists** in sync.py (grep-confirmed); the `except Exception: return False` swallows the ImportError, so `sync_running` is always False and `dashboard.html:65` never disables the Sync button. (b) Independently, `/sync/status` uses `lock.is_locked` on a **fresh** `FileLock` instance; filelock 3.32.3 `is_locked` is per-instance (`lock_file_fd is not None`), so a second instance always reports False even while another holds the lock (empirically reproduced).
**Impact:** FR-CONC-3 not implemented; users click repeatedly and get confusing 409s; the running indicator is permanently "idle". Test `test_sync_button_htmx_and_disabled_when_running` asserts only `"sync" in r3.text` (trivially true) and sets `sync_mod._sync_running = False` — a dead attribute — so the suite is green while the feature is broken.
**Fix:** Expose a real running-state function (try `acquire(timeout=0)` / use a shared singleton `FileLock(is_singleton=True)`), call it from dashboard, assert the `disabled` attribute in the HTMX test, drop the dead `_sync_running` assignments.

### BLOCKER-7: Reconciliation introduces a partial-pass state that violates SPEC FR-10.2
**File:** `src/app/services/reconciliation.py:272-291`
**Issue:** New branch: `has_unfulfilled = any(agg_dn == 0 or agg_si == 0 …)` → `ps.status = pending`, `reason = "partial_fulfillment"`. SPEC FR-10.2 (line 204): *"One failing line shall fail the entire PO Set — there is no partial-pass state."* `tests/test_reconciliation.py` codifies the contradictory behavior.
**Impact:** A PO line never delivered silently parks the whole set in `pending` forever — no `mismatched`, no quarantine, no human escalation.
**Decision required (AGENTS.md §1.1):** Either amend the SPEC (§7.6/§10) to authorize a `pending` "awaiting future deliveries" state before merging this diff, or restore fail-the-set semantics per FR-10.2. **Do not merge as-is.**

### BLOCKER-8: `normalize_po_no` comma-split deviates from SPEC §6.1 normalization rule
**File:** `src/app/services/grouping.py:4-7`
**Issue:** `first = raw.split(",")[0].strip()` before strip-non-alnum-upper changes grouping keys: `"22398, 0"` → `"22398"` (previously `"223980"`). SPEC §6.1: "strip non-alphanumeric, uppercase" — no comma rule.
**Impact:** Grouping-key collisions/merges change silently for any printed number containing a comma — a silent data merge to the CA (the exact failure mode AGENTS.md §1.1 warns about).
**Decision required:** Amend SPEC §6.1/§7 with the comma-revision rule, or revert to pure strip-non-alnum-upper. **Do not merge as-is.**

### BLOCKER-9: Silent fallback to `config.example.yaml` when `config.yaml` missing (SPEC §13.1)
**File:** `src/app/core/config.py:111-115`
**Issue:** If `config.yaml` is absent, `load_config` silently loads the committed example (dev `./data/*` paths, example model) instead of failing fast.
**Impact:** In prod, a missing config would run against example paths/credentials — exactly the silent fallback §13.1 forbids ("refuse to start … shall not fail silently").
**Fix:** Remove the example fallback (or gate behind explicit `AAM_ENV=dev`); raise clear `FileNotFoundError` with remediation text.

### BLOCKER-10: FR-10.1 anchor test is a tautology — proves nothing
**File:** `tests/test_reconciliation_anchor.py:6-12`
**Issue:** Body is `assert 100 * 1000 == (40 + 60) * 1000`; it imports **no app code**, cannot fail, and gives zero regression signal for reconciliation.
**Fix:** Call `reconcile()`/`reconcile_po_set()` against a DB fixture; keep the arithmetic only as documented expected values.

## WARNING

### W-1: COMBINED 3-section gate not re-verified at reconcile/merge time (FR-14.2)
`reconciliation.py:75-107` merges any set containing a COMBINED doc after only a non-positive-quantity check + customs gate. `has_po_section/has_dn_section/has_si_section` are validated once at extraction (`extraction.py:277-286`), stored only in `raw_extraction_json`, never read downstream. Dashboard `reclassify` (`dashboard.py:653-694`) can tag a doc COMBINED with no section evidence → immediate auto-merge of an unverified packet. **Fix:** re-read `raw_extraction_json` in the fast-path and require all three booleans true; force re-extract or quarantine for manually reclassified COMBINED docs.

### W-2: `_invoice_name` fallback deviates from FR-14.5 "no fallback naming logic"
`merge.py:24-44` chains SI `si_no` → SI `invoice_no` → *any* doc's invoice/si. For COMBINED-only sets the loose fallback is required; for standard sets it can name a file from a non-SI number. **Fix:** restrict standard sets to the SI doc; keep loose fallback only for COMBINED/force-merge paths.

### W-3: `_run_sync` swallows all flow exceptions — failed Syncs are silent (NFR-4)
`routes/sync.py:37-41` wraps `sync_flow(...)` and `lock.release` in `contextlib.suppress(Exception)` with no log. A crashed flow leaves zero evidence and the caller already returned 200 "sync started". Also `time.sleep(0.5)` in `_run_sync` (`sync.py:34`) exists only for tests — a prod-path test artifact. **Fix:** `logger.exception` in the except path; log the returned summary dict; remove the sleep and fix the test.

### W-4: FR-8.4 conflicting-description check compares only an arbitrary pair
`matching.py:107-120`: `norm_descs` is a set; with ≥3 distinct descriptions on the same `line_item_no`, only `descs_list[0]` vs `descs_list[1]` are compared (set iteration order is arbitrary). A conflicting third description evades quarantine and quantities get summed. **Fix:** quarantine if any pairwise combination is below threshold.

### W-5: `customs_doc_count` semantics differ between writers (SPEC §6.3)
`customs.py:58-67` (`toggle_customs`) counts total CUSTOMS+SHIPPING docs (2×CUSTOMS+0×SHIPPING → 2); `dashboard.py:511-519` (upload) counts distinct required types (0/1/2). `is_blocked` requires both types present (`customs.py:31-33`). UI shows "2/2" while still `blocked_customs`. **Fix:** make `customs_doc_count` a computed count of distinct required types, consistent across writers.

### W-6: `redo_extract` can never rework a permanently-failed doc (attempt_count ≥ 3)
`po_sets.py:217` skips docs with `attempt_count < 3` false; `extraction.py:223-227` also short-circuits at the cap; no route resets the counter. A doc that failed 3 times is permanently stuck `failed` with no UI path to re-extract (reclassify changes type but leaves status `failed`). **Fix:** allow `redo_extract` to reset `extraction_attempt_count = 0` first (human intent is explicit).

### W-7: `sync_flow` never acquires the sync lock — scheduled midnight run can overlap a manual Sync (FR-4.3)
The FileLock lives only in `routes/sync.py`; the Prefect cron deployment (`scripts/deploy_prefect.py`, `0 0 * * *`) invokes `sync_flow` directly with no lock, so `POST /sync` during the midnight run is not rejected. **Fix:** acquire the same FileLock inside `sync_flow` (try-acquire; skip-or-wait) or set a Prefect deployment concurrency limit of 1.

### W-8: Status forced to `pending` immediately before `merge_po_set` (gate bypass anti-pattern)
`reconciliation.py:98-99` (COMBINED path) and `:304-305` (auto-merge path) commit `status=pending` right before calling `merge_po_set`, whose guard (`merge.py:101-105`) then can never fire in this path. If `merge_po_set` returns None (e.g. `_invoice_name` fails), the set is silently left `pending` with the prior status lost. **Fix:** don't pre-reset status; reconcile the merge result, or surface None to the caller.

### W-9: `filelock` used but not declared in `pyproject.toml`
`routes/sync.py:10` imports `filelock`; it works only because Prefect 3.x pulls it transitively. A dependency upgrade that drops it breaks the sync lock at runtime. **Fix:** add `filelock>=3.16,<4` to `[project].dependencies`.

### W-10: Retry config hardcoded, not read from config; `max_concurrent_extraction_tasks` never applied (NFR-2/3)
`flows/sync.py:18` hardcodes `retries=3, retry_delay_seconds=[2,5,15]` while `config.py:37-38` exposes `extraction.max_retries`/`retry_backoff_seconds` (tuning config does nothing). No Prefect concurrency limit is set anywhere for `prefect.max_concurrent_extraction_tasks`. `extraction.py:183` comment "we handle retries in Python loop" is stale — no such loop exists. The user-triggered `redo_extract` path calls `extract_document` with **zero** retries. **Fix:** read config into the retry policy; set pool/task concurrency from config; update the comment; add backoff to redo path (or centralize in `extract_document`).

### W-11: `manual_merger` has no error handling, leaks temp files, no upload size cap (FR-14.11-14.13)
`manual_merger.py:24-85`: `ValueError` from bad `order` or any pypdf error → unhandled 500; `finally: pass` never unlinks the mkstemp inputs/output; `uf.file.read()` reads unbounded into memory (OOM on 2-core host). Path traversal is safe (names sanitized to alnum/`-_ .`). **Fix:** try/except → 422 with message; delete tmps in `finally` (background task for output); cap upload size / read in chunks.

### W-12: `upload_manual_doc` unbounded read + no PDF verification, writes before dedup (FR-12.2)
`dashboard.py:484` `data = file.file.read()` unbounded; no PDF content sniff (any byte blob attached as CUSTOMS/SHIPPING with `extraction_status=valid`, `dashboard.py:506`); writes to disk (`:493-495`) before the dedup check (`:497`) so a duplicate upload rewrites the stored file. **Fix:** size cap + pypdf content check + check hash before writing.

### W-13: Config does not fail fast (SPEC §13 / FR-CONFIG-1)
`main.py:54-55` wraps `load_config()` in try/except → warning only; invalid/missing config yields a running app with no logging and `/health` 500s per-request. `load_config` also swallows a missing `OPENROUTER_API_KEY` with `pass` (config.py:121-124) — comment says "warn" but nothing is logged. **Fix:** validate at startup outside try/except (raise → exit); log a warning on missing API key.

### W-14: `delete_quarantined` audit loses `po_set_id` (FR-13.6/13.7)
`quarantine.py:87-94` inserts the audit row **after** `s.delete(ps)` with `po_set_id=None` to dodge the FK; traceability lives only in `detail` JSON. **Fix:** insert audit row *before* deleting the parent (row still exists → real FK), or add `ON DELETE SET NULL`.

### W-15: `get_engine` builds a fresh engine + pool + PRAGMA listener per call
`database.py:13-28`: every route/service call re-creates the engine and re-registers the listener; with threadpool concurrency this churns pools and widens the lock race window. **Fix:** module-level engine cache keyed by `database_path`.

### W-16: Decoy-PO quarantine can false-positive after re-extraction
`reconciliation.py:117-141`: `extract_document` overwrites `doc.po_no_normalized` from the VLM on every successful extraction (`extraction.py:301-306`) while `po_set_id` stays on the originally-grouped set; the decoy check then quarantines the whole set on any VLM po-reference drift. **Fix:** log a warning (doc id + both PO values) when the branch fires; consider a minimum-confidence gate before overwriting on re-extract.

### W-17: Sync lock has no stale-timeout recovery; GET /sync/status has write side-effects
`sync.py:20-24` (GET mkdirs + creates `.sync.lock`); a hung `sync_flow` holds the file lock forever → all future syncs blocked until restart (no auto-release, unlike the POSet lock). **Fix:** stale-sync watchdog (acquired-at timestamp) or run sync as a Prefect flow that self-times-out.

### W-18: Unrestricted upload file types (no PDF allowlist)
`dashboard.py:489-491` and `manual_merger.py:31-32` accept any extension. Non-PDF payloads are persisted to `stored_documents_folder`; `PdfReader` failures surface as 422/500. Under the accepted no-auth LAN posture not exploitable, but non-compliant. **Fix:** reject unless suffix in `{".pdf"}`; store uploads with fixed `.pdf` suffix.

### W-19: `is_file_stable` reports STABLE for a never-existent file (FR-4.5)
`ingestion.py:16-21`: absent path → `sizes=[-1,-1]` → `len(set)==1` → True; caller then fails on `read_bytes`. **Fix:** return False when the file is absent.

### W-20: `_is_step_10` is all-or-nothing; `resolve_unattached_documents` matches any doc type
`matching.py:36-41`: one non-step-10 PO line disables the heuristic for the whole set → valid step-10 lines degrade to fuzzy matching. `grouping.py:67-80`: sibling query filters only on `dn_no`, no `doc_type` constraint, despite docstring claiming "an SI document"; two DNs sharing a `dn_no` can match each other. **Fix:** apply step-10 per PO line; filter the sibling query to `SI`/`PO` type; multiple distinct candidates → leave unattached rather than first-wins.

### W-21: FR-14.7 collision: COMBINED + separate docs both included in one merged packet
`merge.py:59` returns `si + dn + po + combined + shipping + customs` — if both a COMBINED and separate PO/DN/SI arrive before any merge fires, the single output duplicates content. **Fix:** when both present, prefer one source per set, keep the other visible but unmerged.

### W-22: `merge_po_set` has no reconciliation guard of its own; zero-line set can "reconcile" and merge
`merge.py:101-110` only blocks on status + `_is_blocked`; a `pending` set called directly merges. Edge: PO+SI sets where both docs extract **zero** line items pass every gate (`all_lines` empty) and merge with no numeric evidence. **Fix:** verify reconciled evidence (non-empty line items / reconciliation receipt) before merging.

## Git / Deploy / Hygiene WARNINGs (from diff + security pass)

### W-G1: `data/aam_merger.db.bak` is NOT gitignored
`.gitignore:36` has `*.db`; `.bak` doesn't match. `git check-ignore` confirms exit 1; file sits one `git add -A` away from commit. Contains vendor metadata + line items. **Fix:** add `*.bak`, `data/backup/*`, `*.db-*`.

### W-G2: root `logs/` directory is NOT gitignored
`logs/mcp_tools_py_*.log` untracked, not ignored. **Fix:** add `logs/`.

### W-G3: Schema additions not in SPEC §6; their migrations are untracked
`models.py:58` `raw_extraction_json` and `models.py:108` `locked_at` are not in SPEC §6.1/§6.3; migrations `7a8e2b1c9d0f`, `c4d5e6f7a8b9` are uncommitted (fresh clone on HEAD applies only `510f6e0fcc4e`, and `create_all` diverges from migrated DBs). **Fix:** amend SPEC §6 for the two columns, or drop them; commit migrations + `locking.py` in the same changeset as the models.

## Files Reviewed
- [x] `src/app/services/ingestion.py`, `extraction.py`, `grouping.py`, `matching.py`, `reconciliation.py`, `customs.py`, `quarantine.py`, `merge.py`, `locking.py`
- [x] `src/app/flows/sync.py`
- [x] `src/app/api/routes/sync.py`, `po_sets.py`, `dashboard.py`, `manual_merger.py`
- [x] `src/app/main.py`, `src/app/core/config.py`, `src/app/core/database.py`
- [x] `src/app/models/models.py`, `src/app/models/base.py`
- [x] `alembic/env.py` + 3 versions
- [x] `templates/*.html` (9), `static/js/app.js`, `static/css/theme.css`
- [x] `config.yaml`, `config.example.yaml`, `.env.example`, `.gitignore`, `pyproject.toml`, `Makefile`, `scripts/*`
- [x] `tests/*` (12 files)
- [x] `AAM_merger_V3_SPEC.md`, `AAM_merger_V3_business_logic.md`, `AGENTS.md`, `DEPLOY.md`

## Prior REVIEW.md (2026-08-15) BLOCKER re-check status

| # | Blocker | Status | Evidence |
|---|---|---|---|
| B-1 | FR-14.6 post-merge grouping | **FIXED** | `grouping.py:22-27` filters `status != merged`; test `test_grouping_post_merge_starts_new_set` |
| B-2 | COMBINED 3-section gate | **PARTIAL** | extraction gate added (`extraction.py:277-286`) but not re-verified at merge → new W-1 |
| B-3 | Prefect retry envelope | **FIXED** (caveat) | `flows/sync.py:18` `@task(retries=3, [2,5,15])`; verified retries fire; no failing-VLM e2e test → W-10 |
| B-4 | FR-6.3 PO decoy | **FIXED** (gap) | cross-doc check `reconciliation.py:116-141`; orphan-set gap → new BLOCKER-5 |
| B-5 | FR-4.8 input clearing | **FIXED** | `ingestion.py:97-108` hash-scan catches deduped renames |
| B-6 | ×1000 scaling | **FIXED** | `_parse_scaled_int` `extraction.py:95-111`; verbatim prompt; regression test |
| B-7 | alembic.ini path | **FIXED** | `alembic/env.py:24-28` overrides via `load_config` |
| B-8 | sync concurrency guard | **PARTIAL** | FileLock in `routes/sync.py:18-24`; cron overlaps (W-7); status broken (BLOCKER-6) |
| B-9 | lock uses updated_at | **FIXED** | `locked_at` column `models.py:108-110`; migration `7a8e2b1c9d0f`; timeout test |
| B-10 | force_merge releases early | **PARTIAL** | lock held across merge (`po_sets.py:154-168`), but TOCTOU acquire → BLOCKER-1 |
| B-11 | customs gate consistency | **PARTIAL** | `is_blocked` correct (`customs.py:31-33`); `customs_doc_count` writer-inconsistent → W-5 |

## Recommended next steps
1. **Human decisions first:** BLOCKER-7 (FR-10.2 partial-pass) and BLOCKER-8 (comma normalization) — amend SPEC or revert; both are code-vs-SPEC contradictions (AGENTS.md §1.1).
2. **Fix silent-correctness blockers before deploy:** BLOCKER-1 (atomic lock), BLOCKER-2 (force_merge error path), BLOCKER-3/4 (merge overwrite/missing-file), BLOCKER-6 (Sync status).
3. **Add the missing integration tests:** Prefect-harness VLM-retry (fail twice then succeed → 3 attempts → valid), true concurrent 2×POST lock race, `/sync/status` running=true during active sync.
4. **Close hygiene gaps before commit:** W-G1/W-G2 gitignore, W-G3 commit migrations together, W-9 declare `filelock`.
5. **Re-run real-sample validation** (business doc §17 STS/IRE/Ensign) once the VLM path has an integration test, per SPEC §1 rule 3.

## Addendum 2026-09-05 — reliability-fixes branch (verified, all green)

Branch `feat/reliability-fixes`, 17 commits. **116 passed, ruff clean, coverage 76%, single alembic head `00628c4756fc`.** Every fix below has a regression test that failed before and passes after (or locks already-fixed behavior). Full plan: `docs/superpowers/plans/2026-09-05-reliability-fixes.md`.

| # | Finding | Status 2026-09-05 | Evidence |
|---|---|---|---|
| BLOCKER-1 | Per-PO lock TOCTOU + lock-stomp | **FIXED** | atomic conditional UPDATE kept; every route release now action-scoped; `test_atomic_acquire_race` (2 threads → exactly one winner, stable 5/5) + `test_route_release_passes_action` |
| BLOCKER-2 | force_merge 200-on-failure | **Already fixed in tree** | raises 422 with log; locked by existing tests |
| BLOCKER-3 | Merge filename collision | **Already fixed in tree** | `-{po_set_id}` namespacing in `_resolve_output_path` |
| BLOCKER-4 | Missing-file silent skip | **Already fixed in tree** | auto path raises/returns None; `test_zero_evidence_auto_merge_refused` |
| BLOCKER-5 | Decoy-PO orphan sets | **FIXED** | DN/SI/UNKNOWN attach-only (`create` flag); `attach_unattached_to_open_sets` never mints; unknown keys wait indefinitely in unclassified (human decision) |
| BLOCKER-6 | Sync status dead code | **Already fixed in tree** | `_is_sync_running` try-acquire; **new bug found**: cross-thread `release()` was GC-dependent → fixed with `thread_local=False` + regression test |
| BLOCKER-7 | Partial-pass vs FR-10.2 | **DECIDED + SPEC amended** | Human: await-delivery (`agg==0`) waits `pending/partial_fulfillment`; nonzero mismatch (incl. over-qty) fails; FR-10.2 text amended; over-qty test added |
| BLOCKER-8 | Comma-split normalization | **Already reverted in tree** | pure strip-non-alnum-upper == SPEC §6.1; no decision needed |
| BLOCKER-9 | Example-config fallback | **FIXED** | `load_config` raises; lifespan validates; API-key warning; `test_config_failfast.py` |
| BLOCKER-10 | Anchor tautology | **Already fixed in tree** | real `reconcile_po_set` fixture test |
| W-1 | COMBINED re-verification | **FIXED** | reconcile re-reads section evidence (`combined_unverified` hold); reclassify→COMBINED forces re-extract; red-green proven via stash |
| W-2 | Invoice-name fallback | **FIXED** | `_invoice_name(loose=False)` standard sets name strictly from SI; COMBINED/force keep loose fallback; `test_standard_set_names_strictly_from_si` |
| W-3 | Sync swallow + sleep | **FIXED** | exceptions logged; deterministic 409 test; sleep removed (stable 3×) |
| W-4 | Pairwise conflict check | **FIXED** | all-pairs `itertools.combinations`; pre-fix detection hash-order flaky (missed 6/8 seeds), post-fix stable 8/8 |
| W-5 | customs_doc_count writers | **Already fixed in tree** | both writers count distinct types |
| W-6 | redo_extract attempt cap | **Already fixed in tree** | resets count to 0 |
| W-7 | Flow holds no lock | **FIXED** | `services/sync_lock.py`; flow acquires or skips; route passes held lock |
| W-8 | Status pre-set strand | **FIXED (corrected)** | forward-progress pending kept (load-bearing for stale-sweep), restore-on-refusal added — suite caught the naive removal |
| W-9 | filelock undeclared | **FIXED** | `filelock>=3.16.0` in pyproject + lock |
| W-10 | Retry hardcoded | **FIXED** | `_extract_task_for` via `with_options` from config + test |
| W-11 | manual_merger errors/tmps | **FIXED** | 422 error mapping; BackgroundTask tmp cleanup (3/3 tmps proven gone); 100MB stream cap; `.pdf` allowlist |
| W-12 | Upload write-before-dedup | **FIXED** | dedup-check before write (poison-file test); `%PDF` + page sniff; idempotent 302 |
| W-13 | Config not fail-fast | **FIXED** | see BLOCKER-9 |
| W-14 | Audit loses po_set_id | **FIXED** | audit-first + `ON DELETE SET NULL` migration `00628c4756fc` (chain + downgrade verified on scratch DB) |
| W-15 | Engine per call | **Already fixed in tree** | module-level cache; committed |
| W-16 | Decoy false-positive | **FIXED** | warning log with doc id + both PO values |
| W-17 | No stale-sync recovery | **FIXED** | sidecar watchdog 3600s (human decision); read-only status |
| W-18 | Upload type allowlist | **FIXED** | `.pdf` suffix enforced + stored with fixed `.pdf` name (both upload paths) |
| W-19 | Stability on missing file | **Already fixed in tree** | returns False |
| W-20 | Step-10 all-or-nothing | **FIXED** | per-line mapping (FR-8.1a confirmed real pattern, recorded in SPEC); ambiguous anchors wait |
| W-21 | COMBINED+separate double-merge | **FIXED** | COMBINED-exclusive packet per FR-14.7 + 1-page test |
| W-22 | No merge guard | **FIXED** | zero-evidence auto-merge refusal (force_merge bypass by design) |
| W-G1/G2 | gitignore gaps | **FIXED** | committed on safe-hygiene branch |
| W-G3 | Untracked migrations | **FIXED** | locking.py + all 3 migrations committed with models |

**Still open:** real-sample VLM validation (needs API balance). Everything else is fixed and test-locked.
