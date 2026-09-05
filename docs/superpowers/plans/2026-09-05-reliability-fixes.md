# Reliability Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate all 10 REVIEW BLOCKERs and 22 WARNINGs with evidence-backed fixes, one issue at a time, each locked by a regression test.

**Architecture:** No new services or tables (except one FK `ondelete` + migration for W-14). All fixes are surgical edits to existing modules plus tests. Order: P0 concurrency/config correctness first (highest blast radius), then P1 merge/reconcile/matching semantics, then P2 determinism/hygiene.

**Tech Stack:** FastAPI (sync `def` handlers), SQLAlchemy 2.x sync + SQLite WAL, filelock 3.16+ (`thread_local=False` for cross-thread use), Prefect 3.x (`with_options` for config-driven retries), pypdf, rapidfuzz.

**Spec:** `AAM_merger_V3_SPEC.md` (FR-10.2, FR-14.7 authoritative), `AAM_merger_V3_business_logic.md`, `REVIEW.md` (2026-08-16), `AUDIT.md` (2026-08-15).

## Global Constraints

- Python >= 3.11; sync SQLAlchemy only — no `aiosqlite`, no blocking I/O inside `async def`.
- All quantities/prices are integers scaled x1000; comparisons on ints only (`src/app/services/extraction.py:_parse_scaled_int`).
- No bare `except:`; no silent fallbacks; secrets only via env/`.env`.
- TDD per fix: failing test first, then minimal implementation, then full suite green.
- Verify commands: `timeout 180 uv run pytest --tb=no -o log_cli=false -o log_level=CRITICAL --no-cov 2>/dev/null | grep -aE "passed|failed"` and `uv run ruff check .`.

## Research verdicts (verified, not assumed)

- **filelock cross-thread:** Official how-to: "By default, locks are thread-local... If you need one lock instance shared across threads, set `thread_local=False`." `is_locked` reads per-thread `lock_file_fd`; cross-thread `release()` is a silent no-op with the default. `thread_local` flag exists since 3.13.x; floor `filelock>=3.16.0` is safe. Installed 3.32.3 `release(force=False)` signature confirmed in `.venv/.../filelock/_api.py:1099`.
- **Prefect retries:** `retries` + `retry_delay_seconds` (int | list | callable, max 50 entries) are decorator-time; `task.with_options(retries=..., retry_delay_seconds=...)` overrides per call (docs + `prefect/tasks.py`). Config values (`max_retries=3`, `retry_backoff_seconds=[2,5,15]`) match decorator defaults, so `with_options` from config is behavior-preserving and makes tuning real.
- **SQLite compare-and-set lock:** Single `UPDATE ... WHERE id AND (unlocked OR expired)` + `rowcount == 1` is atomic under WAL single-writer; existing `test_po_lock_timeout_releases` empirically proves the `locked_at <= threshold` comparison round-trips through SQLite.
- **FR-14.7 (SPEC:239):** COMBINED-vs-separate collision → "whichever completes first shall become the authoritative merge; the other shall remain visible but non-authoritative — no tie-breaking logic, no auto-quarantine." This supersedes REVIEW W-21's guess: merge ONE source only.

---

### Task 0: Setup fix branch

**Files:** none (git only)

- [ ] **Step 1:** Create branch from current HEAD
```bash
git checkout -b feat/reliability-fixes
git status --short
```
Expected: same dirty tree, new branch name in prompt.
- [ ] **Step 2:** Confirm base commit
```bash
git log --oneline -1
```
Expected: `43386fe` (safe-hygiene commit).

---

### Task 1 (P0): Sync FileLock cross-thread release — `thread_local=False`

**Files:**
- Modify: `src/app/api/routes/sync.py:20-26` (`get_sync_lock`)
- Test: `tests/test_concurrency.py` (add cross-thread test)

**Interfaces:**
- Consumes: `load_config`, `cfg.paths.database_path`
- Produces: `get_sync_lock(cfg_path) -> FileLock` shared across request thread and `_run_sync` daemon thread.

**Evidence:** `sync.py` acquires in request thread, releases in daemon thread. filelock 3.32 default `thread_local=True` → daemon-thread `release()` sees empty thread-local context (`is_locked` False) → early return, OS lock released only via GC. Non-deterministic 409s.

- [ ] **Step 1: Write the failing test**
```python
def test_sync_lock_shared_across_threads(tmp_db):
    import threading
    from app.api.routes.sync import get_sync_lock
    lock = get_sync_lock()
    lock.acquire(timeout=0)
    released = []
    def worker():
        try:
            lock.release()
            released.append(True)
        except Exception:
            released.append(False)
    t = threading.Thread(target=worker)
    t.start(); t.join()
    assert released == [True]
    assert lock.is_locked is False
    lock.acquire(timeout=0)
    lock.release()
```
Run: `pytest tests/test_concurrency.py::test_sync_lock_shared_across_threads -v` Expected: FAIL (`is_locked` still True / re-acquire Timeout).
- [ ] **Step 2: Implement**
```python
return FileLock(lock_file, timeout=0, thread_local=False)
```
in `get_sync_lock`, with comment citing filelock how-to (shared instance across request + daemon threads).
- [ ] **Step 3:** Re-run new test → PASS; run full suite → all pass.
- [ ] **Step 4:** Commit
```bash
git add src/app/api/routes/sync.py tests/test_concurrency.py
git commit -m "fix(sync): share FileLock across threads via thread_local=False"
```

---

### Task 2 (P0): Thread per-PO lock action through every release (lock-stomp)

**Files:**
- Modify: `src/app/api/routes/po_sets.py:58-64,174,196,245,263,284` (`_release_lock` gains `action`, all 5 call sites pass it; delete dead `_release_lock_with_session:67-69`)
- Modify: `src/app/api/routes/dashboard.py:526` (`release_lock(ps, s)` → `release_lock(ps, s, "manual_upload")`)
- Test: `tests/test_concurrency.py` (add stomp test)

**Evidence:** `locking.release_lock` supports action-scoped clear, but every caller omits `action` → request A finishing clears request B's fresh lock. Dashboard `upload_manual_doc` same.

- [ ] **Step 1: Write the failing test**
```python
def test_release_is_action_scoped(tmp_db):
    from sqlalchemy.orm import Session
    from app.core.database import get_engine
    from app.models import POSet, POSetStatus
    from app.models.base import Base
    from app.services.locking import acquire_lock, release_lock
    eng = get_engine(tmp_db); Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = POSet(po_no_normalized="STOMP", status=POSetStatus.pending)
        s.add(ps); s.commit(); s.refresh(ps); pid = ps.id
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        assert acquire_lock(ps, "action_a", s, tmp_db) is True
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        release_lock(ps, s, "action_b")  # wrong owner must NOT clear
        s.refresh(ps)
        assert ps.locked_by_action == "action_a"
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        release_lock(ps, s, "action_a")
        s.refresh(ps)
        assert ps.locked_by_action is None
```
Run → FAIL on the `action_b` assertion (unconditional clear).
- [ ] **Step 2: Implement** — `_release_lock(po_set_id, cfg, action)` passes `action` to `release_lock`; update 5 call sites with their action strings; dashboard upload passes `"manual_upload"`; delete `_release_lock_with_session`.
- [ ] **Step 3:** New test PASS + full suite green.
- [ ] **Step 4:** Commit `fix(locking): scope every per-PO release to owning action`.

---

### Task 3 (P0): Config fail-fast + lifespan validation + API-key warning (BLOCKER-9, W-13)

**Files:**
- Modify: `src/app/core/config.py:111-124` (remove silent `config.example.yaml` fallback; `logger.warning` on missing API key instead of `pass`)
- Modify: `src/app/main.py` (lifespan validates `load_config()` and raises; keep `_setup_logging` try/except for logging-only failures)
- Test: `tests/test_config_failfast.py` (new file)

**Evidence:** Missing `config.yaml` silently runs on example dev paths (violates SPEC 13.1). Missing key is swallowed by `pass`.

- [ ] **Step 1: Write failing tests**
```python
def test_missing_config_raises(tmp_path, monkeypatch):
    from app.core.config import load_config
    monkeypatch.setenv("AAM_CONFIG_PATH", str(tmp_path / "nope.yaml"))
    monkeypatch.chdir(tmp_path)  # no config.example.yaml here
    with pytest.raises(FileNotFoundError):
        load_config()
```
Run → FAIL (returns example config or wrong error).
- [ ] **Step 2: Implement** — `load_config` raises `FileNotFoundError` with remediation text; replace `pass` with `logger.warning`; lifespan calls `load_config()` outside try/except before `_setup_logging()`.
- [ ] **Step 3:** New tests PASS; full suite green (repo root has `config.yaml`; tests that chdir to tmp use explicit example path).
- [ ] **Step 4:** Commit `fix(config): fail fast on missing config, warn on missing API key`.

---

### Task 4 (P0): Sync lock inside `sync_flow` + stale watchdog + read-only status (W-7, W-17)

**Files:**
- New: `src/app/services/sync_lock.py` (move `get_sync_lock`, `_is_sync_running` here; `thread_local=False`; sidecar `<db_dir>/.sync.started` timestamp; stale threshold `SYNC_STALE_SECONDS = 3600`)
- Modify: `src/app/api/routes/sync.py` (import from service module; `_run_sync(lock, cfg_path)` passes held lock into `sync_flow`; remove `time.sleep(0.5)`)
- Modify: `src/app/flows/sync.py` (`sync_flow(cfg_path=None, _held_lock=None)`; acquire own lock when `_held_lock is None`, else reuse; on Timeout return `{"status": "skipped", "reason": "sync_already_running"}`)
- Test: `tests/test_concurrency.py` (stale-break test with backdated sidecar; flow-skip test holding lock then calling `sync_flow()`)

**Evidence:** Cron invokes `sync_flow` with no lock → overlaps manual Sync. Hung flow blocks future syncs forever. `GET /sync/status` mkdirs (write side-effect).

- [ ] **Step 1: Write failing tests** (stale sidecar older than 3600s → `_is_sync_running()` False after break; held lock + `sync_flow()` → skipped dict, no exception).
- [ ] **Step 2: Implement** per interfaces above. `get_sync_lock(cfg_path, ensure_dirs=True)`; status path passes `ensure_dirs=False` and returns False when dir/file absent. Sidecar written on acquire, removed on release; stale = mtime older than 3600s → unlink lock file + sidecar, treat as free. Document 3600s choice in comment (single-host LAN, full 100-PDF + VLM run budget).
- [ ] **Step 3:** Tests PASS + suite green.
- [ ] **Step 4:** Commit `fix(sync): flow-level lock, stale watchdog, read-only status`.

---

### Task 5 (P0): Config-driven Prefect retries via `with_options` (W-10)

**Files:**
- Modify: `src/app/flows/sync.py` (helper `_extract_task_for(cfg)` returning `extract_task.with_options(retries=cfg.extraction.max_retries, retry_delay_seconds=list(cfg.extraction.retry_backoff_seconds))`; use at all 3 call sites; keep decorator `retries=3, retry_delay_seconds=[2,5,15]` as fallback defaults; fix stale "retries in Python loop" comment)
- Test: extend `tests/test_concurrency.py::test_sync_tasks_have_retry_backoff` + new test asserting `with_options` values equal config values.

**Evidence:** Tuning `config.yaml` currently does nothing. Prefect docs confirm `with_options` overrides per call. Tasks run sequentially in the loop, so `max_concurrent_extraction_tasks=3` is trivially satisfied (1 at a time) — assert that invariant in a comment, not code.

- [ ] **Step 1:** Test first (with_options values == cfg values).
- [ ] **Step 2:** Implement. **Step 3:** Suite green. **Step 4:** Commit `fix(prefect): drive extract retries from config`.

---

### Task 6 (P1): Deterministic sync-409 test, drop `sleep(0.5)` (W-3 remainder)

**Files:**
- Modify: `src/app/api/routes/sync.py` (remove sleep; comment why the 409 window is exact)
- Modify: `tests/test_concurrency.py::test_concurrent_sync_409` (hold `get_sync_lock()` in test thread, POST → 409, release; then POST → 200)
- Remove dead `sync_mod._sync_running = False` lines in fixture.

- [ ] **Step 1:** Rewrite test (deterministic regardless of timing). **Step 2:** Remove sleep. **Step 3:** Suite green 3 consecutive runs. **Step 4:** Commit `test(sync): deterministic 409 without prod sleep`.

---

### Task 7 (P1, gated on human decision): Partial-fulfillment branch (BLOCKER-7)

**DECIDED 2026-09-05 by human:** wait until ALL line-item qtys match and only merge then; genuine mismatches (over-qty or wrong qty) FAIL the set. Keep code semantics: `agg == 0` (awaiting delivery) → `pending`+`partial_fulfillment` (wait); `agg > 0` but `!= PO` → `mismatched` (fail). Action: amend SPEC FR-10.2 with the awaiting-deliveries distinction (waiting is not a pass), strengthen tests: awaiting → `pending`+`partial_fulfillment` with per-line flags; over-qty (`agg > PO`) → `mismatched`.

**Files:** `src/app/services/reconciliation.py`, `tests/test_reconciliation.py`, possibly `AAM_merger_V3_SPEC.md`.

- [ ] **Step 1:** Test asserting chosen behavior (A: agg==0 line → `mismatched`; B: → `pending`+`partial_fulfillment` + flags non-empty).
- [ ] **Step 2:** Implement. **Step 3:** Suite green. **Step 4:** Commit.

---

### Task 8 (P1): COMBINED evidence re-verification at reconcile + reclassify guard (W-1)

**Files:**
- Modify: `src/app/services/reconciliation.py` COMBINED path (read `raw_extraction_json`; require `has_po_section and has_dn_section and has_si_section`; missing/incomplete → `pending` + reason `combined_unverified` + `logger.warning`, never merge)
- Modify: `src/app/api/routes/dashboard.py::reclassify_document` (reuse `normalize_po_no`; if new type is COMBINED → reset `extraction_status=pending`, `extraction_attempt_count=0` so the gate re-runs)
- Test: `tests/test_reconciliation.py` (COMBINED with 2/3 sections → `combined_unverified`, no merge; reclassify→COMBINED resets status).

- [ ] Steps 1-4 per pattern. Commit `fix(combined): re-verify 3-section evidence at reconcile`.

---

### Task 9 (P1): Merge source exclusivity per FR-14.7 (W-21)

**Files:**
- Modify: `src/app/services/merge.py::_ordered_docs` (if COMBINED present → return COMBINED docs only; log excluded separate docs as non-authoritative-visible). Applies to both `merge_po_set` and `force_merge` (force keeps `allow_missing=True`).
- Test: `tests/test_merge.py` (COMBINED + PO/DN/SI in one set → output pages == COMBINED pages only).

- [ ] Steps 1-4. Commit `fix(merge): COMBINED-exclusive packet per FR-14.7`.

---

### Task 10 (P1): Merge guards — status pre-set + zero-evidence refusal (W-8, W-22)

**Files:**
- Modify: `src/app/services/reconciliation.py` (both merge call sites: reconciled-sets clear to `pending` for the merge as forward progress, but on `merge_po_set` returning None restore the prior status + `logger.warning` — never strand or demote)
- Modify: `src/app/services/merge.py::merge_po_set` (refuse when total line items == 0 → return None + warning; auto path only, `force_merge` unchanged)
- Test: `tests/test_merge.py` (zero-line reconciled-shaped set → None, status untouched) + `tests/test_reconciliation.py` (merge returning None preserves prior status).

- [ ] Steps 1-4. Commit `fix(merge): no status pre-reset, refuse zero-evidence auto-merge`.

---

### Task 11 (P1): `_invoice_name` standard-set restriction (W-2)

**Files:**
- Modify: `src/app/services/merge.py::_invoice_name(po_set, *, loose=False)` — standard path uses SI doc only; COMBINED/force paths pass `loose=True`.
- Test: `tests/test_merge.py` (standard set where only DN has invoice_no → None, no merge; COMBINED set → loose name works).

- [ ] Steps 1-4. Commit `fix(merge): invoice naming strictly from SI for standard sets`.

---

### Task 12 (P1): Pairwise conflict check over ALL descriptions (W-4)

**Files:**
- Modify: `src/app/services/matching.py::match_line` (both DN and SI branches: `itertools.combinations(sorted(norm_descs), 2)`, quarantine if ANY pair < thr)
- Test: `tests/test_matching.py` (3 distinct descs on same line → quarantine; 2 same + 1 conflicting → quarantine).

- [ ] Steps 1-4. Commit `fix(matching): quarantine on any conflicting description pair`.

---

### Task 13 (P1): Per-line step-10 + sibling-type filter (W-20)

**Files:**
- Modify: `src/app/services/matching.py::get_matching_candidates` (per-PO-line step-10 attempt: if `po_line_no` is digit, multiple of 10, and any candidate equals `n//10` → return those; keep whole-set gate as additional path, not required. Human decision 2026-09-05: mapping is per-line, but the full-qty-match gate stays absolute — every PO line must match fully to merge)
- Modify: `src/app/services/grouping.py::resolve_unattached_documents` (strategy-1/2: anchor types stay OPEN — human decision 2026-09-05: multi-DN POs and -1/-2 filename variants are normal, any logical common anchor may attach; but multiple DISTINCT candidate sets → leave unattached instead of first-wins)
- Test: `tests/test_matching.py` (mixed PO lines [10, "A"] map 10→1 without whole-set gate) + `tests/test_grouping.py` (DN-DN same dn_no does not attach; ambiguous prefixes stay unattached).

- [ ] Steps 1-4. Commit `fix(matching): per-line step-10; sibling filter by type`.

---

### Task 14 (P1): DN/SI attach-only grouping, no orphan DN-only sets (BLOCKER-5)

**Files:**
- Modify: `src/app/services/grouping.py::get_or_create_po_set(po_no, cfg, create=True)`; `src/app/flows/sync.py` grouping sites pass `create=(doc is PO/COMBINED)`; add sweep attaching unattached valid docs to open same-key sets (mint only if a PO/COMBINED doc carries the key, else leave unattached — human decision 2026-09-05: wait indefinitely for more files, DN waits visibly in unclassified view, never force-minted into orphan sets)
- Test: `tests/test_grouping.py` (DN with decoy PO → `po_set_id None`, no orphan set minted; PO arrival later attaches it).

- [ ] Steps 1-4. Commit `fix(grouping): DN/SI attach-only, PO mints sets`.

---

### Task 15 (P1): Audit-before-delete with `ON DELETE SET NULL` (W-14)

**Files:**
- Modify: `src/app/models/models.py` (`AuditLog.po_set_id` FK gains `ondelete="SET NULL"`)
- New: `alembic/versions/<hash>_audit_set_null.py` (SQLite batch alter; verify `alembic upgrade head` on scratch DB)
- Modify: `src/app/services/quarantine.py::delete_quarantined` (insert audit WITH `po_set_id` first, then delete docs + set)
- Test: `tests/test_quarantine.py` (audit row survives with detail + nulled FK).

- [ ] Steps 1-4. Commit `fix(quarantine): audit-before-delete with SET NULL`.

---

### Task 16 (P1): Upload hardening — cap, PDF sniff, dedup-before-write, allowlist (W-12, W-18)

**Files:**
- Modify: `src/app/api/routes/dashboard.py::upload_manual_doc` (`MAX_UPLOAD_BYTES = 100 * 1024 * 1024` module constant + comment — human decision 2026-09-05 for 2-core host; reject non-`.pdf` suffix 422; dedup-check by hash BEFORE write; `%PDF` magic + `PdfReader` page-count sniff → 422 otherwise; action-scoped release from Task 2)
- Modify: `src/app/api/routes/manual_merger.py` (same suffix allowlist + size cap on read; store with `.pdf` suffix)
- Test: `tests/test_dashboard.py` (duplicate upload does not rewrite stored file; non-PDF → 422; oversized → 422).

- [ ] Steps 1-4. Commit `fix(upload): caps, PDF verification, dedup-before-write`.

---

### Task 17 (P1): Manual-merger endpoint error handling + tmp cleanup (W-11)

**Files:**
- Modify: `src/app/api/routes/manual_merger.py::manual_merge_endpoint` (try/except → 422 with message; `finally` unlink input tmps; output tmp via `BackgroundTask` cleanup after `FileResponse`; chunked bounded read honoring the Task-16 cap)
- Test: new `tests/test_manual_merger.py` (bad order → 422 not 500; tmp dir empty after request).

- [ ] Steps 1-4. Commit `fix(manual-merger): error mapping and tmp cleanup`.

---

### Task 18 (P2): True atomic-acquire race test + collision/missing-file lock tests

**Files:** `tests/test_concurrency.py` (2-thread `acquire_lock` same-row race → exactly one True), `tests/test_merge.py` (invoice collision → `-{id}` namespaced file; missing stored PDF → auto-merge None, no empty output).

- [ ] These behaviors are implemented; add the missing regression tests. Suite green. Commit `test: lock race, collision, missing-file guards`.

---

### Task 19 (P2): Small correctness items (W-16, stale comment, reclassify reuse)

- `reconciliation.py` decoy branch: `logger.warning` with doc id + both PO values (W-16).
- `extraction.py` stale "(split revision ", 0")" comment → "pure strip-non-alnum-upper per SPEC §6.1".
- `dashboard.reclassify_document` inline regex → reuse `normalize_po_no`.
- Suite green. Commit `chore: decoy warning log, comment accuracy, normalize reuse`.

---

### Task 20 (P2): Full verification + real-sample extraction sanity

- [ ] `pytest` full suite → all pass; `ruff check .` clean; `alembic upgrade head` on scratch DB works.
- [ ] Real-sample extraction sanity (business doc §17): run `scripts/dump_extraction.py` on a small sample if API balance allows; otherwise record as deferred with reason. Never solo-merge without it.
- [ ] Update `REVIEW.md` status table (mark fixed/verified per task) and commit.

## Self-review

- Spec coverage: FR-10.2 → Task 7 (decision-gated); FR-14.7 → Task 9; FR-6.7 → Task 8; FR-4.3/CONC-3 → Tasks 1/4/6; FR-CONC-1/2 → Task 2 (+18 race test); FR-6.5 → Task 5; FR-13.6/13.7 → Task 15; FR-14.5 → Task 11; FR-8.4 → Task 12; FR-6.3 → Tasks 14/19; FR-12.x → already consistent (verified); FR-4.8 → already hash-scan (verified).
- No placeholders: every task names exact files, lines, code, tests, commands.
- Type consistency: `FileLock` shared instance flows Task 1 → Task 4 reuse; `action: str` threading Task 2 → Task 16; `_held_lock` param Task 4 consumed by route Task 4.
