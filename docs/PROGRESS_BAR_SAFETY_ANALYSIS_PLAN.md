# Ingestion Progress Bar — Safety, Risk Analysis & Implementation Plan

**Target Feature:** Real-time Non-Blocking Ingestion Progress Tracker  
**Target Release:** Current Production Candidate  
**Document Status:** Pre-Implementation Safety Analysis & Risk Assessment  
**Author:** Antigravity Pairing Agent  
**Date:** 2026-10-26  

---

## Executive Summary: Is This a Big Change?

**No, this is a very small, low-risk change** in terms of footprint and blast radius, provided it is implemented purely as an observability layer without changing the underlying concurrency architecture.

| Dimension | Scope | Assessment |
|---|---|---|
| **Lines of Code** | ~90–120 lines total | Low |
| **Database Schema** | **0 changes** (no Alembic migrations, no new columns, no table locks) | Zero Risk |
| **Business Logic** | **0 changes** (no modifications to matching, reconciliation, or merge rules) | Zero Risk |
| **Concurrency / Lock Engine** | **0 changes** (`sync_lock.py` and `locking.py` remain untouched) | Zero Risk |
| **Primary Failure Modes** | Concurrency race on status file, test assertion regression | Completely Mitigated via in-memory tracking |

This change transforms a **"black box" freeze** into a **transparent, non-blocking background progress bar** without altering core orchestration.

---

## 1. What Could Possibly Break? (Failure Modes & Blast Radius Analysis)

To ensure nothing breaks in production, every potential hazard has been evaluated against the existing codebase and automated test suite:

### Risk 1: Test Suite Regressions on Filesystem Assertions (CRITICAL FINDING)
* **The Hazard:** In [`tests/test_concurrency.py:171`](file:///c:/Users/malliath/Desktop/aam_merger/tests/test_concurrency.py#L171), the test strictly asserts:
  ```python
  new = {p.name for p in tmp_path.iterdir()} - before
  assert new <= {".sync.lock", ".sync.started"}, f"unexpected filesystem writes: {new}"
  ```
  If progress were tracked by writing a `.sync.progress` file to the database directory, this existing security guard would immediately **FAIL**.
* **Mitigation:** Progress is tracked **strictly in memory** within the Python process via a thread-safe singleton (`threading.Lock` + dataclass). Because `sync_flow` runs in a daemon thread inside the FastAPI application process, in-memory tracking requires **zero disk writes** and avoids all filesystem assertion tests.

### Risk 2: Schema Compatibility of `/sync/status`
* **The Hazard:** Existing callers and tests expect `/sync/status` to return HTTP 200 with JSON:
  - [`tests/test_dashboard.py:368`](file:///c:/Users/malliath/Desktop/aam_merger/tests/test_dashboard.py#L368): `assert "running" in r2.text.lower()`
  - [`tests/test_concurrency.py:161`](file:///c:/Users/malliath/Desktop/aam_merger/tests/test_concurrency.py#L161): `assert "running" in r_status.json()`
  - [`static/js/app.js:464`](file:///c:/Users/malliath/Desktop/aam_merger/static/js/app.js#L464): `btn.disabled = !!state.running;`
* **Mitigation:** The response envelope remains 100% backwards-compatible:
  ```json
  {
    "running": true,
    "detail": "Sync already running",
    "total": 500,
    "current": 142,
    "current_file": "INV_2026_094.pdf",
    "percent": 28,
    "phase": "extracting",
    "extracted": 139,
    "errors": 3,
    "elapsed_seconds": 1240,
    "eta_seconds": 3180
  }
  ```
  When idle, it returns the exact same object as before (`{"running": false, "detail": "idle"}`).

### Risk 3: Progress Tracker Crashing the Ingestion Flow
* **The Hazard:** An uncaught exception in progress recording (e.g. division by zero on empty file lists, or unexpected types) could crash `sync_flow`, stopping the scan.
* **Mitigation:** All calls to the progress recorder in `sync.py` are wrapped in fail-safe try/except blocks (`with contextlib.suppress(Exception):`). Even if the progress system fails completely, the sync flow runs to completion uninterrupted.

### Risk 4: UI Freezing, Blocking Overlays, or Modal Takeovers
* **The Hazard:** Accidentally introducing a modal overlay, CSS backdrop, or pointer-events block that stops operators from interacting with the page.
* **Mitigation:** The progress bar is strictly an **inline dashboard card** and an **unobtrusive top-banner indicator**. It contains no backdrops, no modals, and no z-index hijacking. Operators can freely navigate, inspect PO sets, filter tables, and download merged PDFs.

### Risk 5: Client Polling Storm / Performance Degradation
* **The Hazard:** Repeated high-frequency requests overwhelming the server or causing Alpine/HTMX DOM re-render thrashing.
* **Mitigation:** The client already polls `/sync/status` every 5 seconds in [`static/js/app.js:475`](file:///c:/Users/malliath/Desktop/aam_merger/static/js/app.js#L475). Instead of adding a second timer, we simply hook into this existing poll. Serving an in-memory dictionary takes `< 1 millisecond` and consumes near-zero CPU.

---

## 2. Accessibility of Available PO Sets & Downloads

The table below confirms what remains accessible while 500 files are being scanned:

| Action / Feature | URL / Endpoint | State During 500-File Scan | Impact of Progress Bar |
|---|---|---|---|
| **Download Merged PDF** | `GET /po_sets/{id}/merged_pdf` | **100% Available** (never blocked) | Operators can download any completed packet without delay |
| **Inspect PO Set Details** | `GET /po_sets/{id}/view` | **100% Available** | Full read-only view of line items, docs, status |
| **Slide-over PDF Drawer** | `GET /documents/{id}/preview` | **100% Available** | Operators can preview existing PDFs |
| **Dashboard Search & Filter**| `GET /dashboard?status=...` | **100% Available** | Status filtering and PO number search fully work |
| **Standalone PDF Merger** | `GET /manual/merger` | **100% Available** | Independent tool; completely unaffected |
| **Audit Log Viewing** | `GET /audit` | **100% Available** | Audit inspection fully works |
| **Mutating Unmerged Set** | `POST /po_sets/{id}/merge` | **Guarded with 409** | Guarded to prevent merging before missing docs arrive |

---

## 3. Architecture & Data Flow

```
                      FastAPI Application Process
  ┌─────────────────────────────────────────────────────────────────┐
  │                                                                 │
  │   Background Daemon Thread (_run_sync)                          │
  │   └── flows.sync.sync_flow                                      │
  │        ├── Poll files -> SyncProgressTracker.start(total=500)   │
  │        ├── Loop file 1..500:                                    │
  │        │    ├── Ingest & VLM extract                            │
  │        │    └── SyncProgressTracker.update(current=idx, file=f) │
  │        └── Finish -> SyncProgressTracker.finish()               │
  │                                                                 │
  │   In-Memory Singleton: SyncProgressTracker                      │
  │   └── Thread-safe state (zero filesystem writes)                │
  │                                                                 │
  │   Web Request Handler                                           │
  │   └── GET /sync/status ──> Reads SyncProgressTracker            │
  │                                                                 │
  └─────────────────────────────────┬───────────────────────────────┘
                                    │ JSON response
                                    ▼
                          Operator Browser (UI)
  ┌─────────────────────────────────────────────────────────────────┐
  │  • Navbar Pill: "⚡ Syncing: 28% (142/500)"                     │
  │  • Dashboard Card: Progress bar + filename + ETA timer          │
  │  • Table / Download Buttons: Fully interactive & clickable      │
  └─────────────────────────────────────────────────────────────────┘
```

---

## 4. Detailed Component Changes

### Component 1: `src/app/services/sync_progress.py` (New File, ~45 lines)
- Defines `SyncProgressTracker` with thread-safe lock.
- Methods:
  - `start_sync(total_files: int)`
  - `update_progress(current_idx: int, current_filename: str, status: str)`
  - `get_status() -> dict`
  - `finish_sync(summary: dict)`
  - `reset()`
- Zero disk writes, zero DB queries.

### Component 2: `src/app/flows/sync.py` (~15 lines modified)
- In `_sync_flow_locked()`:
  - Call `start_sync(len(files))` once stable files are discovered.
  - In `for idx, f in enumerate(files, 1):`, call `update_progress(idx, f.name, ...)`.
  - In `finally:`, call `finish_sync()`.
- Guarded with `suppress(Exception)` so progress recording can never interrupt execution.

### Component 3: `src/app/api/routes/sync.py` (~10 lines modified)
- Update `sync_status()` endpoint:
  ```python
  @router.get("/sync/status")
  def sync_status():
      running = _is_sync_running()
      data = {"running": running, "detail": "Sync already running" if running else "idle"}
      if running:
          data.update(get_sync_progress())
      return data
  ```

### Component 4: `templates/dashboard.html` & `static/css/theme.css` (~40 lines modified)
- Add non-blocking progress tracker widget above the PO table:
  - Shown automatically when `syncState.running` is true.
  - Displays:
    - Percentage bar (`width: X%`) with smooth CSS transition.
    - Counter: `File 142 of 500`.
    - Current document name: `INV_2026_094.pdf`.
    - Elapsed time & estimated remaining time.
    - Count of extracted vs flagged docs.

### Component 5: `static/js/app.js` (~20 lines modified)
- Hook into existing `refreshSyncState()`:
  - Store progress payload into Alpine store or update progress DOM elements directly.
  - When sync completes, transition smoothly to a temporary success badge (`"✅ 500 files processed"`) for 8 seconds before resetting.

---

## 5. Verification & Testing Strategy

Before claiming completion, verify against:
1. **Existing Test Suite**:
   Run `pytest tests/test_concurrency.py tests/test_dashboard.py tests/test_sync_lock_heartbeat.py` to confirm zero regressions on lock assertions.
2. **New Dedicated Unit Tests** (`tests/test_sync_progress.py`):
   - Verify `start_sync`, `update_progress`, and `finish_sync` transitions.
   - Verify ETA calculation with varied document counts.
   - Verify thread safety under concurrent reads and writes.
   - Verify `/sync/status` JSON contract when idle vs running.
3. **Manual Browser Verification**:
   - Trigger a batch sync, verify the progress bar renders cleanly.
   - Click "View Set" on an existing PO Set while the sync runs.
   - Click "📥 PDF" to download an existing merged PDF while the sync runs.
   - Verify no modals or overlays obstruct the screen.

---

## 6. Verdict & Sign-Off

**Verdict:** This plan is safe, surgical, and avoids touching the fragile concurrency architecture or database models. It completely addresses the operator's visibility and anxiety issues without breaking any existing guarantees.
