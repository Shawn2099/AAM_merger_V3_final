# WS6 plan: regression, concurrency, restart and soak tests (worker: DeepSeek v4.1 flash)

Written 2026-10-25 by the architect (Claude Opus). This replaces `docs/OPENCODE_WS6_BRIEF.md`.
It is your complete work order. Do the steps in order. If something does not work as written, stop
and report (section 10). Do not improvise a different design.

The expectations below (tick results, run counts, call counts, reasons) were derived from reading the
code; nothing has been executed yet. If reality differs, report it; never adjust an expectation to fit.

Read first: `CLAUDE.md` ("Conventions that bite"), `docs/CORE_LOGIC.md`, `docs/DEPLOYMENT_PLAN.md`
(WS6, D2, D5, D10, D21), then these files, which you will call:
`tests/test_robustness_harness.py` (make_cfg, write_pdf, line, extraction, HttpVLM, run_sync, sets),
`tests/test_poller.py`, `tests/test_concurrency.py`, `tests/test_sync_lock_heartbeat.py`,
`src/app/services/poller.py`, `src/app/services/sync_lock.py`, `src/app/services/locking.py`,
`src/app/flows/sync.py`, `src/app/services/merge.py` (`_write_merged`, `force_merge`),
`src/app/api/routes/po_sets.py` (`force_merge`, `merge_now`, `_acquire_lock`), `src/app/main.py` (`lifespan`).

---

## 1. Hard rules

1. **Allowed files (new files only):** `tests/_ws6_helpers.py`, `tests/test_ws6_poller_soak.py`,
   `tests/test_ws6_concurrency.py`, `tests/test_ws6_restart_recovery.py`,
   `tests/test_ws6_d2_original_pages.py`, `tests/test_ws6_d21_counts.py`, `scripts/soak_d21_timing.py`.
   Forbidden: `src/`, `templates/`, `static/`, `alembic/`, `config*.yaml`, `.env`, `data/`, every existing test.
2. **No git** (no add, commit, stash, checkout, reset, push).
3. **Canned VLM only, no network.** Every test uses `make_cfg(tmp_path, monkeypatch)` and either
   `CannedVLM` (section 3) or `HttpVLM` (D2 only). Never read `config.yaml` or `data/`. Never set a real key.
4. **Never weaken an assertion.** No changed expected value, no loosened comparison, no retry loop
   around an assertion, no new `skip`. If the product misbehaves, mark the test
   `@pytest.mark.xfail(strict=True, reason="SUSPECTED PRODUCT BUG WS6-<nn>: <one line>")` and report
   evidence. Pre-approved xfail: WS6-01 only (section 5.2). Every other xfail needs evidence.
5. **Time is simulated, never slept on.** Use `os.utime` for file times, a Core `update()` to backdate
   `documents.created_at` / `documents.updated_at` / `po_sets.locked_at`, `os.utime` for the sync
   sidecar, and `SyncPoller(clock=...)` for poller backoff. The only real waiting allowed: (a)
   `threading.Event.wait(timeout)` on a gate you control, (b) the stability poll inside
   `scripts/soak_d21_timing.py`. No `time.sleep` in tests.
6. **Ban list** (CLAUDE.md): no bare `except:`, no hardcoded Windows paths, no manual SQL strings in
   tests (use SQLAlchemy `update()`/`select()`), integers x1000 for quantities.
7. **Determinism:** 3 runs give identical results (same passed / xfailed / skipped test ids). A race
   test asserts invariants that hold whoever wins, never which thread wins.
8. **Slow work is opt-in by environment variable**, because `@pytest.mark.slow` does NOT deselect
   anything here (addopts has no `-m`). Use
   `pytest.mark.skipif(os.getenv("RUN_SOAK") != "1", reason="set RUN_SOAK=1 to run the WS6 soak")`.
   Every test that runs by default must finish in under 60 s on its own (measure with `--durations=0`).
9. ruff check and ruff format clean on every new file.

---

## 2. Order of work

| Step | File | Stop if |
|---|---|---|
| 0 | Baseline: `uv run python -m pytest tests/test_poller.py tests/test_concurrency.py tests/test_sync_lock_heartbeat.py -o addopts= -p no:cacheprovider -q -W ignore`. Record pass count. | any fails |
| 1 | `tests/_ws6_helpers.py` (section 3), plus a 1-test smoke file run (5.1's small case) | the canned VLM is never called (`calls` stays empty) |
| 2 | `test_ws6_concurrency.py` (section 6) | |
| 3 | `test_ws6_restart_recovery.py` (section 7) | |
| 4 | `test_ws6_d2_original_pages.py` (section 8) | |
| 5 | `test_ws6_poller_soak.py` (section 5) | |
| 6 | `test_ws6_d21_counts.py` + `scripts/soak_d21_timing.py` (section 9) | |
| 7 | 3 identical runs, RUN_SOAK runs once, ruff, full suite (section 10) | |

Two failed attempts at one test: stop on it, report it under NEEDS with the error, continue.

---

## 3. `tests/_ws6_helpers.py` (write exactly this, then add only small helpers you need)

```python
"""Shared helpers for tests/test_ws6_*.py. Not a test module."""

from __future__ import annotations

import hashlib
import threading
from collections import Counter
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import AuditLog, Document, POSet
from app.services import extraction


class SimulatedCrash(BaseException):  # noqa: N818 - a process death, not an error
    """Raised inside the VLM call to simulate the process dying mid-extraction.

    BaseException on purpose: the product's `except Exception` blocks must not
    catch it, exactly as they could not catch a killed process.
    """


def payload(kind: str, number: str | None, po_ref: str | None, lines: list[dict]) -> dict:
    """The dict `extraction._call_vlm` returns (same shape as scripts/ui_test_env.py)."""
    return {
        "document_type": kind,
        "document_number": number,
        "po_no_raw": number if kind == "PO" else po_ref,
        "po_reference": None if kind == "PO" else po_ref,
        "po_reference_ambiguous": False,
        "vendor_name": "ACME",
        "components": [],
        "line_items": lines,
    }


class CannedVLM:
    """Replaces `extraction._call_vlm`; keyed by SHA-256 (the stored file is `<sha>.pdf`)."""

    def __init__(self, monkeypatch):
        self.payloads: dict[str, dict] = {}
        self.calls: Counter[str] = Counter()
        self.crash_on: dict[str, int] = {}  # sha -> the call number that raises SimulatedCrash
        monkeypatch.setattr(extraction, "_call_vlm", self)

        def _no_network(**kwargs):
            raise AssertionError("a WS6 test tried to build a real OpenAI client")

        monkeypatch.setattr(extraction, "OpenAI", _no_network)

    def add(self, path: Path, body: dict) -> str:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        self.payloads[sha] = body
        return sha

    def __call__(self, stored_path, doc_type, cfg):
        sha = Path(stored_path).stem
        self.calls[sha] += 1
        if self.crash_on.get(sha) == self.calls[sha]:
            raise SimulatedCrash(sha)
        return dict(self.payloads[sha])


def plain_extract_task_for(cfg):
    """`flows.sync._extract_task_for` without the Prefect task wrapper.

    Same extraction code (`extract_task.fn` -> `extract_document`); only the
    Prefect task-run bookkeeping is skipped, so 500 documents fit in a test
    and a SimulatedCrash is not intercepted by the Prefect engine.
    """
    from app.flows.sync import extract_task

    return lambda doc_id, cfg_path=None: extract_task.fn(doc_id, cfg_path=cfg_path)


def bind_poller_config(monkeypatch, cfg) -> None:
    """`run_poller_sync` reads the settle time from `app.core.config.load_config()`."""
    monkeypatch.setattr("app.core.config.load_config", lambda path=None: cfg)


def backdate_documents(cfg, seconds: float, column: str = "created_at") -> None:
    from datetime import UTC, datetime, timedelta

    when = datetime.now(UTC) - timedelta(seconds=seconds)
    with Session(get_engine(cfg)) as s:
        s.execute(update(Document).values({column: when}))
        s.commit()


def output_pdfs(cfg) -> list[Path]:
    return sorted(p for p in Path(cfg.paths.output_folder).iterdir() if p.suffix.lower() == ".pdf")


def hidden_output_files(cfg) -> list[Path]:
    return sorted(p for p in Path(cfg.paths.output_folder).iterdir() if p.name.startswith("."))


def audit_count(cfg, po_set_id: int, action: str) -> int:
    with Session(get_engine(cfg)) as s:
        return s.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.po_set_id == po_set_id, AuditLog.action == action
            )
        )


def set_row(cfg, po_no: str) -> POSet:
    with Session(get_engine(cfg)) as s:
        ps = s.scalars(select(POSet).where(POSet.po_no_normalized == po_no)).one()
        s.expunge(ps)
        return ps


class Gate:
    """Blocks the first call of a wrapped function until `release()`."""

    def __init__(self):
        self.entered = threading.Event()
        self.released = threading.Event()

    def wrap(self, real):
        def gated(*args, **kwargs):
            if not self.entered.is_set():
                self.entered.set()
                assert self.released.wait(30), "the test never released the gate"
            return real(*args, **kwargs)

        return gated

    def release(self):
        self.released.set()
```
Imports in test files: `from _ws6_helpers import ...` and `from test_robustness_harness import make_cfg, write_pdf, line, run_sync, sets, HttpVLM, extraction`
(sibling imports work as in `tests/test_poller.py`). `AuditLog.action` is an enum. If `== "force_merge"`
does not compare in SQLAlchemy, pass `AuditAction.force_merge` instead and say so in the report.

A standard fixture block to put in each test file:
```python
@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    c = make_cfg(tmp_path, monkeypatch)
    # config.example.yaml has a 5-minute settle time; a poller-started sync would
    # then hold every set. Tests that want the settle rule set it themselves (5.3).
    c.schedule.merge_settle_minutes = 0
    monkeypatch.setattr("app.flows.sync._extract_task_for", plain_extract_task_for)
    # `with TestClient(app)` runs main.lifespan, which calls main's own load_config():
    # without this it would read the developer's config.yaml (rule 3).
    monkeypatch.setattr("app.main.load_config", lambda *a, **k: c)
    return c


@pytest.fixture()
def vlm(monkeypatch):
    return CannedVLM(monkeypatch)


def drop_set(cfg, vlm, po: str, width: float, dn_qty="10", si_qty="10", po_qty="10", names=None):
    """Write PO, DN, SI for one set into the input folder; return {kind: (path, sha)}."""
    names = names or {"PO": f"PO-{po}.pdf", "DN": f"DN-{po}.pdf", "SI": f"SI-{po}.pdf"}
    out = {}
    inp = Path(cfg.paths.input_folder)
    for i, (kind, qty) in enumerate((("PO", po_qty), ("DN", dn_qty), ("SI", si_qty))):
        path = write_pdf(inp / names[kind], width + i)
        number = po if kind == "PO" else f"{kind}-{po}"
        sha = vlm.add(path, payload(kind, number, None if kind == "PO" else po, [line("1", qty, "BOLT")]))
        out[kind] = (path, sha)
    return out
```
Widths must be unique across all documents of one test (that is what makes the bytes, and the SHA, unique).

---

## 4. Facts about the product you will rely on (verified by the architect)

- `find_input_pdfs` returns files sorted by **path**, not by time (`ingestion.py:316`), and the recovery
  pass reads pending documents with no ORDER BY. "Oldest first" (D5, PRODUCT, poller docstring) is not
  implemented. See 5.2.
- A per-PO action (`force_merge`, `merge`, `redo_match`, ...) returns **409** while any sync holds the
  sync lock (`_acquire_lock` checks `_is_sync_running()` first), and 409 "already in progress" while
  another action holds that set's lock.
- `POST /sync` returns 409 "Sync already running" while the lock is held. `SyncPoller.tick()` never
  returns 409: it returns `"queued"` and runs on a later tick.
- The sync's reconcile sweep **skips** a set whose per-PO lock is live (`_reconcile_with_lock_guard`),
  it does not wait and does not raise.
- Force Merge on an already merged set returns 200, writes no new packet and adds a `force_merge`
  audit row with `"already_merged": true` (merge.py `force_merge`).
- A per-PO lock left by a dead request is honoured until `concurrency.po_set_lock_timeout_seconds`
  (900 s, D10) and then cleared by the next acquire or page view. This is the design, not a bug.
- The web service's startup (`main.lifespan`) acquires and releases the sync lock once, which removes
  an orphan sidecar. A nightly-worker crash while the web service keeps running is NOT cleaned that
  way: the sidecar looks alive for up to 1200 s (`SYNC_STALE_SECONDS`), unless a Scan runs.
- A document left `processing` by a dead worker is reclaimed only when its `updated_at` is older than
  `extraction.stale_processing_seconds` (900 s), at the start of a sync.
- The packet is written from the stored original PDF (`_write_merged` reads `doc.stored_path` with
  pypdf). The PNGs sent to the model are never stored.

---

## 5. `tests/test_ws6_poller_soak.py`

### 5.1 `test_ws6_soak_every_document_once_every_set_merged_once` (default: 20 sets / 100 docs; RUN_SOAK=1: 100 sets / 500 docs)
- `SETS = 100 if os.getenv("RUN_SOAK") == "1" else 20`. `cfg.schedule.poll_enabled = True`,
  `cfg.schedule.merge_settle_minutes = 0`. Call `bind_poller_config(monkeypatch, cfg)`.
- Per set i (po = `f"45{i:08d}"`), five documents, quantities chosen so only the SUM reconciles:
  PO lines 1:10, 2:6; DN-A (number `DN-{po}-A`) 1:4, 2:6; DN-B (`DN-{po}-B`) 1:6;
  SI-A (`INV-{po}-A`) 1:5, 2:6; SI-B (`INV-{po}-B`) 1:5. DN/SI `po_ref = po`. Widths `200 + 5*i + k`, k=0..4.
- Arrival: `rng = random.Random(20261025)`; shuffle the 5*SETS documents; split into 10 batches
  ("minutes"). File name `f"{rng.getrandbits(32):08x}.pdf"` (unique; keep a set to avoid repeats).
  After writing batch k, set each file's mtime `BASE + 60*k + j` with `os.utime(path, (t, t))`
  (`BASE = 1_760_000_000`).
- Poller: `poller = SyncPoller(load_cfg=lambda: cfg, clock=lambda: 0.0)` (real lock, real
  `run_poller_sync`, real Prefect flow; extraction task unwrapped by the fixture).
  After each batch: `assert poller.tick() == "ran"`.
- Re-sends: after the last batch, copy the bytes of 5 documents of already merged sets to 5 new names,
  `assert poller.tick() == "ran"`. Then `assert poller.tick() == "idle"`.
- Assertions (all against the DB / disk):
  1. Non-archived document rows == 5*SETS; distinct `sha256_hash` == 5*SETS (re-sends made no row).
  2. Every document: `extraction_status == valid` and `po_set_id` not NULL.
  3. Every set: status `merged`; `merged_output_path` values all distinct; `len(output_pdfs(cfg)) == SETS`;
     `hidden_output_files(cfg) == []`; every packet has exactly 5 pages (`len(PdfReader(p).pages)`).
  4. `vlm.calls[sha] == 1` for every one of the 5*SETS SHAs (no document read twice).
  5. The input folder holds no PDF (merged sets' inputs and the re-sends are removed).
  6. No `force_merge` audit row exists. `poller.runs == 11` (10 batches + the re-send pass; an idle tick does not count).
- Runtime: record it. If the default (20 sets) takes over 60 s, lower the default to 10 sets and say so.

### 5.2 Ordering (the D5 "oldest first" gap)
Two small tests, 3 PO-only files (no DN/SI needed), names `a.pdf`, `b.pdf`, `c.pdf`, mtimes
`c` oldest, `b`, `a` newest. Record the order of `ingest_file` calls:
```python
order = []
real = ingestion.ingest_file
monkeypatch.setattr(ingestion, "ingest_file", lambda src, cfg_: (order.append(Path(src).name), real(src, cfg_))[1])
```
(`_sync_flow_locked` imports `ingest_file` from `app.services.ingestion` at call time, so the patch applies.)
Run one `run_sync()`.
- `test_ws6_input_order_today_is_path_order`: `assert order == ["a.pdf", "b.pdf", "c.pdf"]`. Passes today;
  pins current behaviour.
- `test_ws6_input_order_is_oldest_first`:
  `@pytest.mark.xfail(strict=True, reason="SUSPECTED PRODUCT BUG WS6-01: input files are read in path order, not oldest first as D5/PRODUCT state (find_input_pdfs sorts by path)")`,
  `assert order == ["c.pdf", "b.pdf", "a.pdf"]`.
These two are an OWNER DECISION (fix the code or fix the docs). Do not change product code.

### 5.3 `test_ws6_soak_settle_wake_merges_everything_in_one_pass`
- 5 sets, all files in one batch, `merge_settle_minutes = 5`, `bind_poller_config`.
- tick -> `"ran"`; every set `pending` with reason starting `SETTLING_PREFIX`; tick -> `"idle"`.
- `backdate_documents(cfg, 600)`; tick -> `"ran"`; every set `merged`; tick -> `"idle"`.

### 5.4 `test_ws6_soak_small_real_prefect_path` (default)
- 3 sets, the fixture WITHOUT the `_extract_task_for` patch (build `cfg` with `make_cfg` directly in this
  test, set `merge_settle_minutes = 0` and `poll_enabled = True`, call `bind_poller_config`), one tick,
  all 3 merged, each SHA called once. This proves the unwrapped path in 5.1 behaves
  like the real Prefect path. Record its runtime and the per-document cost (runtime / 9).

---

## 6. `tests/test_ws6_concurrency.py` (Force Merge + poller + Scan: exactly one wins)

Common setup: `cfg`, `vlm` fixtures; `bind_poller_config(monkeypatch, cfg)`. Build ONE mismatched set:
`drop_set(cfg, vlm, "4500011111", 300, dn_qty="7")`, then `run_sync()`, then assert its status is `mismatched`.
HTTP client: `from app.main import app`; `with TestClient(app) as client:` (lifespan runs; poll stays off).
Join background `/sync` threads with `_sync_run_threads` / `_join_new_sync_threads` copied from
`tests/test_concurrency.py` (copy, do not import). JUST = `"WS6: operator accepts 7 of 10 delivered."`.

### 6.1 `test_ws6_force_merge_holds_the_set_while_poller_scan_and_a_second_merge_try`
- `gate = Gate(); monkeypatch.setattr(merge, "_write_merged", gate.wrap(merge._write_merged))`
  (`import app.services.merge as merge`). The gate blocks INSIDE force_merge, while the per-PO lock and
  the output-name lock are held: a real mid-write.
- Thread A calls the route function directly:
  `from app.api.routes import po_sets as po_routes`;
  `results = []`; target `lambda: results.append(_call(po_routes.force_merge, ps_id, justification=JUST))`
  where `_call` returns `("ok", value)` or `("http", e.status_code, e.detail)` for `HTTPException`.
- `assert gate.entered.wait(15)`. While A is inside:
  1. DB `locked_by_action == "force_merge"`.
  2. `client.post(f"/po_sets/{id}/force_merge", data={"justification": JUST})` -> 409, text contains `already in progress`.
  3. `client.post(f"/po_sets/{id}/merge")` -> 409. `client.post(f"/po_sets/{id}/redo_match")` -> 409.
  4. Poller: write one new unrelated PDF (a DN with no PO number, own width, `vlm.add` it);
     `SyncPoller(load_cfg=lambda: cfg).tick() == "ran"`; afterwards the set is still `mismatched` and
     `merged_output_path` is NULL (the sweep skipped the locked set).
  5. Scan: `before = _sync_run_threads(); r = client.post("/sync")` -> 200; `_join_new_sync_threads(before)`;
     the set is still `mismatched`, `merged_output_path` NULL.
- `gate.release()`; `thread.join(30)`; `assert not thread.is_alive()`; `results == [("ok", ...)]` with
  value `["status"] == "merged"`.
  If thread A instead ends with an error such as `database is locked` (a sync wrote while the Force
  Merge was mid-write), that is a product finding (WS6-02): keep the assertion, mark the test
  `xfail(strict=True, reason="SUSPECTED PRODUCT BUG WS6-02: ...")` and paste the traceback.
- Final: status `merged`; `len(output_pdfs(cfg)) == 1`; `hidden_output_files(cfg) == []`; the packet has 3
  pages; `audit_count(cfg, id, "force_merge") == 1` and its justification == JUST; `locked_by_action` NULL.

### 6.2 `test_ws6_a_running_sync_refuses_force_merge_and_scan_and_queues_the_poller`
- `holder = sync_lock.acquire_sync_lock()` (`import app.services.sync_lock as sync_lock`).
- `client.post(force_merge)` -> 409, text contains `A sync is currently running`;
  `client.post(f"/po_sets/{id}/redo_extract")` -> 409; `client.post("/sync")` -> 409, text contains `Sync already running`.
- Write a new PDF (canned); `poller = SyncPoller(load_cfg=lambda: cfg)`; `poller.tick() == "queued"`; `poller.runs == 0`.
- `sync_lock.release_sync_lock(holder)`; `poller.tick() == "ran"`; `poller.runs == 1`.
- `client.post(force_merge, data={"justification": JUST})` -> 200; one packet; set `merged`.

### 6.3 `test_ws6_two_simultaneous_force_merges_write_one_packet` (no gate)
- Build 10 mismatched sets in one `run_sync()` (po `45000200{i:02d}`, widths 400+10*i).
- For each set: `barrier = threading.Barrier(2)`; two threads each `barrier.wait(10)` then
  `_call(po_routes.force_merge, ps_id, justification=JUST)`.
- Invariants per set (whoever wins):
  1. At least one result is `"ok"`; every other result is `("http", 409, <detail containing "already in progress">)`.
  2. Exactly one packet file for the set (`merged_output_path` exists; no other output file named after this set's SI number).
  3. `audit_count(cfg, id, "force_merge") == number of "ok" results`; exactly ONE of those audit rows
     has a detail WITHOUT `"already_merged"`.
  4. The packet has 3 pages; `hidden_output_files(cfg) == []` after all rounds.
- If a result is anything else (for example `("http", 500, "Force merge failed; see the server log.")`
  caused by `database is locked`), that is finding WS6-02 as in 6.1: keep the assertion, mark the test
  `xfail(strict=True, ...)` only if it happens in all 3 runs, otherwise report it as a non-deterministic
  finding with the outcomes of each run. Never add a retry.

---

## 7. `tests/test_ws6_restart_recovery.py`

### 7.1 `test_ws6_a_lock_left_by_a_dead_request_is_honoured_then_expires` (D10 design)
- One mismatched set (as in 6). Set `locked_by_action="force_merge"`, `locked_at=now-30s` (Core update).
- `client.post(force_merge, data=JUST)` -> 409 `already in progress`; `run_sync()`; status still `mismatched`.
- Backdate `locked_at` to `now - (cfg.concurrency.po_set_lock_timeout_seconds + 5)`.
- `client.get(f"/po_sets/{id}")` -> 200 and `is_locked` false; then `client.post(force_merge, ...)` -> 200; one packet.

### 7.2 Orphan sync sidecar
Create the residue of a dead holder by hand (no live OS lock):
`lock_file = sync_lock._lock_file(); lock_file.write_text(""); sc = sync_lock._sidecar_for(lock_file); sc.write_text("0")`,
then `os.utime(sc, (t, t))` with `t = time.time() - age`.
- `test_ws6_web_restart_clears_an_orphan_sidecar`: age 60. `sync_lock._is_sync_running()` is True.
  `TestClient(app)` WITHOUT `with` (no lifespan): force_merge -> 409 `A sync is currently running`.
  Then `with TestClient(app) as client:` (lifespan runs): `_is_sync_running()` is False, `sc` does not
  exist, force_merge -> 200.
- `test_ws6_a_scan_clears_an_orphan_left_by_a_crashed_nightly_worker`: age 60, inside
  `with TestClient(app) as client:` create the residue AFTER startup (the web service did not restart).
  `_is_sync_running()` True; force_merge -> 409. `client.post("/sync")` -> 200; join the sync threads;
  `_is_sync_running()` False; force_merge -> 200. (Record: without a Scan the operator waits up to
  1200 s. Owner decision O-3.)
- `test_ws6_an_old_orphan_sidecar_is_ignored`: age `sync_lock.SYNC_STALE_SECONDS + 60` (and
  `cfg.extraction.stale_processing_seconds` stays 900 so the threshold is 1200): `_is_sync_running()` False,
  force_merge -> 200 with no restart and no Scan.

### 7.3 `test_ws6_crash_mid_extraction_loses_no_document`
- Two complete sets, files named so path order is known: `a_po1.pdf b_dn1.pdf c_si1.pdf d_po2.pdf e_dn2.pdf f_si2.pdf`
  (set 1 po `4500030001`, set 2 po `4500030002`).
- `vlm.crash_on[sha_of_e_dn2] = 1`.
- Crash: `lock = sync_lock.acquire_sync_lock()`; `with pytest.raises(SimulatedCrash): app.flows.sync._sync_flow_locked(None)`;
  then `lock.release()` directly (NOT `release_sync_lock`: a dead process leaves its sidecar).
- Crash state: `e_dn2.pdf` doc is `processing`; no row for `f_si2.pdf`; the sidecar exists.
- Restart: `with TestClient(app):` (lifespan) -> `_is_sync_running()` False. Then a fresh
  `SyncPoller(load_cfg=lambda: cfg)` (a restarted service has an empty watcher): `tick() == "ran"`.
- After restart pass: set 1 `merged`; `f_si2.pdf` read (`valid`); `e_dn2.pdf` still `processing`; set 2
  `pending` with reason `A document in this set has not been read yet`.
- `tick() == "idle"` (no new file; documented: the poller does not wake for a stuck document. Owner decision O-4).
- `backdate_documents(cfg, cfg.extraction.stale_processing_seconds + 5, column="updated_at")`; `run_sync()`
  (the nightly run / a manual Scan).
- Final: both sets `merged`; one non-archived row per input file (6); `vlm.calls[e] == 2`, every other SHA 1;
  `len(output_pdfs(cfg)) == 2`; input folder empty.

### 7.4 `test_ws6_a_stray_temp_file_from_a_killed_write_does_not_block_the_merge`
- Before the first sync, create `output/.SI-4500040001.deadbeef.tmp` with bytes `b"partial"`.
- `drop_set(...)` for po `4500040001`; `run_sync()`; status `merged`; packet readable, 3 pages.
- Report whether the `.tmp` file still exists afterwards (do NOT assert either way; it goes in FINDINGS
  as an observation: the output folder should hold only delivered packets).

### 7.5 `test_ws6_an_orphan_packet_from_a_crash_before_commit_quarantines_the_set`
- Simulates power loss after `os.replace` and before the DB commit. Pre-create
  `output/SI-4500050001.pdf` with bytes `b"%PDF-1.4 orphan"` (the name the set will want: the SI number).
- `drop_set(...)` for po `4500050001`; `run_sync()`: status `quarantined`, reason contains `already exists`;
  the orphan file's bytes are unchanged (CORE_LOGIC 6: never overwrite).
- Delete the orphan file; `client.post(f"/po_sets/{id}/redo_match")` -> 200; status `merged`; 3 pages.

---

## 8. `tests/test_ws6_d2_original_pages.py` (D2: "1 of 6", packet uses the original pages)

Uses `HttpVLM` (real rasteriser, real parser; only the HTTP bytes are fake) and `make_cfg` WITHOUT the
`_extract_task_for` patch (also add `monkeypatch.setattr("app.main.load_config", lambda *a, **k: cfg)`
if the test opens a TestClient; 5.4 needs the same line). Build PDFs with PyMuPDF so the pages have real content:
```python
import pymupdf

def text_pdf(path: Path, width: float, texts: list[str], height: float = 842) -> Path:
    doc = pymupdf.open()
    for t in texts:
        page = doc.new_page(width=width, height=height)
        page.insert_text((40, 60), t, fontsize=12)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path
```
Widths (HttpVLM dispatch keys; they survive the 150-dpi round trip): PO 401, DN 503, SI 607.

### 8.1 `test_ws6_a_one_of_six_po_merges_from_the_original_pdf_pages`
- PO: one page, text `"PURCHASE ORDER 4500060001  Page 1 of 6"`; DN one page; SI one page.
  HttpVLM responses: PO `extraction("PO", "4500060001", None, [line("1","10"), line("2","5")])`,
  DN/SI the same lines with `po_ref="4500060001"`.
- Before the sync: `sha_in = {name: sha256(bytes)}` for the three input files.
- `run_sync()`. Assertions:
  1. Set `merged` (a "1 of 6" PO is not held when its lines reconcile: D2).
  2. Each document's `sha256_hash` equals its input file's SHA, and its `stored_path` bytes hash to it.
  3. Packet `PdfReader`: 3 pages; widths in order `[607, 503, 401]` (SI, DN, PO legal order), each
     `float(page.mediabox.width)` equal to the source page's.
  4. For each packet page, `extract_text()` equals the source page's `extract_text()` (test-only check
     that the page is the original, not a re-rendered image).
  5. No packet page has an image XObject:
     ```python
     def images(page):
         xo = (page.get("/Resources") or {}).get("/XObject") or {}
         return [k for k, v in xo.items() if v.get_object().get("/Subtype") == "/Image"]
     ```
     `images(p) == []` for every packet page (the sources have none, so any image came from a PNG).
  6. `HttpVLM.calls` has exactly one call per width (the PNGs went to the model once).

### 8.2 `test_ws6_a_two_page_partial_po_keeps_both_pages_in_order`
- PO with two pages (`"Page 1 of 6"`, `"Page 2 of 6"`), same checks: packet 4 pages, the two PO pages
  are the last two, texts in source order.

---

## 9. D21: cost of pending input files

### 9.1 `tests/test_ws6_d21_counts.py` (counts only, never seconds)
Parametrize `n_pending` over `0, 50` (default) and `200` (only with RUN_SOAK=1, as a separate param with
the skipif mark). `n_pending` files = `n_pending // 2` sets of PO + SI with no DN (each waits
`missing_dn_document`, so its files stay in input). Use `cfg`/`vlm` fixtures (stability 0 s x 1).
- Cold pass: `run_sync()`; all those sets `pending`.
- Warm pass: add ONE new PO file for a new set. Wrap with counters (monkeypatch, call through to the real function):
  `app.services.ingestion.ingest_file`, `app.services.reconciliation.reconcile_po_set`,
  `app.services.grouping.resolve_unattached_documents`, `app.services.grouping.attach_unattached_to_open_sets`,
  `app.flows.sync._poll_stability` (count the files it is given). Run `run_sync()`.
- Assert: VLM calls in the warm pass == 1; ingest calls == n_pending + 1; stability files == n_pending + 1;
  reconcile calls == n_pending // 2 + 1 (each open set exactly once); non-archived document rows grew by
  exactly 1; every old set is still `pending` with an unchanged reason; input PDFs == n_pending + 1.
- Idle check: `p = SyncPoller(load_cfg=lambda: cfg, run_sync=fake, acquire=lambda: "LOCK")` with a counting
  `fake` that returns `{"errors": 0}`: first tick `"ran"` (empty watcher), second tick `"idle"`, `fake.calls == 1`.
  (An unchanged input folder never starts a sync: D21.)

### 9.2 `scripts/soak_d21_timing.py` (the timing table; not a test)
- CLI: `uv run python scripts/soak_d21_timing.py --root <dir whose name contains "soak"> --counts 0 50 200`.
  Wipe `--root` only if its name contains `soak` (refuse otherwise, like `ui_test_env.py`).
- Setup without pytest: `sys.path.insert(0, str(REPO / "src"))`; `os.environ["OPENROUTER_API_KEY"] = ""`;
  load `config.example.yaml`, point every `cfg.paths.*` and `cfg.manual_merge.temp_folder` into `--root`,
  KEEP the production stability settings (2 s x 2), `cfg.schedule.poll_enabled = False`;
  `setattr(module, "load_config", lambda *a, **k: cfg)` for `app.flows.sync`, `app.services.sync_lock`,
  `app.api.routes.dashboard`, `app.api.routes.po_sets`, `app.api.routes.sync`; replace
  `extraction._call_vlm` with a canned function as in `CannedVLM` and `extraction.OpenAI` with one that raises;
  replace `app.flows.sync._extract_task_for` with `plain_extract_task_for`; run the flow as `sync_flow.fn()`.
- For each count: fresh sub-root, cold pass, then the warm pass of 9.1 with timing wrappers
  (`time.perf_counter`) around `_poll_stability`, `ingest_file` (sum), the two grouping sweeps (sum),
  `reconcile_po_set` (sum), and the whole pass.
- Print one table:
  `pending | stability_s | ingest_s | grouping_s | reconcile_s | total_s | vlm_calls`.
  Expected order of magnitude for stability: `ceil((n+1)/8) * 4 s` (8 parallel workers, 2 s x 2), so about
  104 s for 200. Write nothing outside `--root`. Run it once at step 7 and paste the table in the report.

---

## 10. Commands (PowerShell, repository root)
```powershell
uv run python -m pytest tests/test_ws6_concurrency.py -o addopts= -p no:cacheprovider -q -W ignore -rxXs --durations=0
1..3 | ForEach-Object { uv run python -m pytest tests/test_ws6_poller_soak.py tests/test_ws6_concurrency.py tests/test_ws6_restart_recovery.py tests/test_ws6_d2_original_pages.py tests/test_ws6_d21_counts.py -o addopts= -p no:cacheprovider -q -W ignore -rxXs --durations=0 }
$env:RUN_SOAK = "1"; uv run python -m pytest tests/test_ws6_poller_soak.py tests/test_ws6_d21_counts.py -o addopts= -p no:cacheprovider -q -W ignore -rxXs --durations=0; Remove-Item Env:RUN_SOAK
uv run python scripts/soak_d21_timing.py --root $env:TEMP\aam_d21_soak --counts 0 50 200
uv run ruff format tests/_ws6_helpers.py tests/test_ws6_*.py scripts/soak_d21_timing.py ; uv run ruff check tests scripts
uv run python -m pytest -q -o addopts= -p no:cacheprovider -W ignore
```
Proof that a test can fail (required for each test): change one expected value locally, see it fail,
undo it. Name the edit in the report.

Stop conditions: a baseline test fails; a test can only pass by editing `src/`; anything asks for a key,
the network, `config.yaml` or `data/`; a race test gives different outcomes in different runs (that is a
finding: report the outcomes, do not add retries).

---

## 11. Report (reply with exactly this)
```
DONE:      <test ids, passing in 3 identical runs>
XFAIL:     <test id> | <reason> | <evidence>
FINDINGS:  WS6-<nn> | <what> | <expected> | <evidence: DB value, response, file listing>
OBSERVED:  7.4 stray .tmp after merge: <exists / gone>
ASSUMED:   <anything decided without being told>
NEEDS:     <blocked test + error>
FILES:     <files created>
RUNS:      run1/run2/run3 <passed, xfailed, skipped ids identical? yes/no>; RUN_SOAK run <result>; full suite <result>; ruff <result>
RUNTIME:   <test id: seconds> (from --durations=0), and 5.4 per-document cost
D21:       <the table printed by scripts/soak_d21_timing.py>
FAILPROOF: <test id> -> <edit that made it fail>
```
Facts only.

---

## 12. Owner decisions this plan surfaces (architect's notes; the worker does not act on them)
- **O-1 (WS6-01) "Oldest first" is not implemented**; files are read in path order. Either change the
  code (sort the batch by mtime: one line in `find_input_pdfs` or `_sync_flow_locked`, plus the swap of the
  two tests in 5.2) or correct D5 / PRODUCT / the poller docstring. Correctness is unaffected within a pass
  (DN/SI before their PO attach in the same pass's recovery step); it matters only for fairness under load.
- **O-2 Force Merge on an already merged set returns 200** and adds an audit row; a stale second browser
  tab shows "Force merge completed". UI_TEST_PLAN section 4.3 expected a 409. Accept (idempotent) or change.
- **O-3 Crashed nightly worker leaves a fresh sidecar**: with the web service still up, every action is
  refused for up to 1200 s unless the operator presses Scan. Accept and put "press Scan" in the runbook,
  or add a startup/periodic orphan check in the worker.
- **O-4 A document stuck in `processing`** after a crash is re-read only by a sync that starts after
  900 s; the poller does not wake for it, so with no new file it waits for the nightly run. Accept and
  document, or let the poller wake when a `processing` claim becomes stale.
- **O-5 A stray `.tmp`** from a killed write may remain in the output folder (7.4 tells us).
