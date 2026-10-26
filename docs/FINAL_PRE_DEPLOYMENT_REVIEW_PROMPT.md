# Claude Opus Final Pre-Deployment Code Review & Go/No-Go Audit Brief

**Document:** `docs/FINAL_PRE_DEPLOYMENT_REVIEW_PROMPT.md`  
**Date:** 2026-10-26 / 2026-10-27  
**Target Release:** AAM Merger V3 MVP Deployment  
**Auditor Target Model:** Claude Opus (Claude 3.5 / 5.5 Opus)

---

## 1. Executive Summary for the Operator

This brief is designed to be provided directly to Claude Opus for the final, independent pre-deployment code review and Go/No-Go decision prior to rolling out AAM Merger V3 to production on Windows Server 2016.

### Which Branch to Inspect?
- **Primary Release Branch to Review:** **`deployed/aam-2026-10-27`**  
  *Rationale:* This is the clean, deployment-only release tree containing strictly the runtime engine (`src/`), UI (`templates/`, `static/`), database migrations (`alembic/`), deployment automation scripts (`scripts/`, `deploy.bat`), bundled service wrapper (`tools/nssm/nssm.exe`), and operator guides.
- **Reference / Test Suite Branch:** **`main`** or **`dev`**  
  *Rationale:* Holds the complete test suite (6,696 tests passed, 0 failures), developer evaluations (`pattern_lab/`, `prompt_improvement/`), and full commit history.

---

## 2. Copy-Paste Prompt for Claude Opus

Copy and paste the entire block below directly into Claude Opus:

```markdown
# TASK: Final Pre-Deployment Code Review & Go/No-Go Decision for AAM Merger V3

You are acting as the Principal Systems Architect and Staff Software Auditor conducting the final, binding code review and Go/No-Go deployment assessment for AAM Merger V3.

Repository: https://github.com/Shawn2099/AAM_merger_V3_final
Target Release Branch: `deployed/aam-2026-10-27`
Full Test Suite Baseline: `main` / `dev` (at commit `396faab`)
Target Host: Windows Server 2016 Datacenter (2 vCPUs, 128 GB RAM, LAN binding 0.0.0.0:8000, local NTFS disk storage, NSSM Windows Services, SQLite WAL).

---

### Non-Negotiable Product Architecture & Business Rules (Source of Truth: `AAM_merger_V3_PRODUCT.md`)
1. Reconcile Matching Invariant:
   - Quantities are strictly integers scaled ×1000 (never floats).
   - Three-way exact aggregate match: PO quantity == sum(DN delivered quantities) AND PO quantity == sum(SI invoiced quantities).
   - line_items table has STRICTLY NO `part_no`, NO `line_type`, and NO `uom`. These were deliberately retired on 2026-09-29. Quantities and normalised `line_item_no` are the ONLY reconciliation signals.
   - Missing SI documents or orphan vendor lines must block auto-merge and quarantine/hold the set.
2. Single-Host SQLite WAL Safety:
   - Database engine is synchronous SQLAlchemy with `busy_timeout=30000`, `PRAGMA journal_mode = WAL`, and `PRAGMA synchronous = NORMAL`.
   - SQLite WAL files MUST reside on local NTFS disk (`C:\AAM\...`), NEVER on SMB/UNC shares due to Windows shared memory (.shm) locking semantics.
   - All file locks use `thread_local=False` so locks acquired in FastAPI request threads can be released across background worker threads.
3. Windows Service Architecture:
   - Three services managed by NSSM: `AAMMerger-Web` (FastAPI / Uvicorn), `AAMMerger-Worker` (Prefect process worker), and `AAMMerger-PrefectServer` (local Prefect Server orchestration).
   - NSSM starts processes in `C:\Windows\System32`. Paths in `config.py` are strictly anchored to the directory of `config.yaml`, never CWD.
4. Input Copy Stability & Dedup:
   - 30-second minimum file age stability check (`MIN_FILE_AGE_SECONDS = 30`).
   - Uses `max(st.st_mtime, st_birthtime)` on Windows to prevent partially copied SMB files from passing as "old".
   - Input files are only cleared when a PO Set reaches the terminal `merged` state or when broken documents are quarantined into `quarantine/_documents/`.
   - Partial unique indices (`WHERE archived_at IS NULL`) ensure active files are deduplicated while allowing operators to re-ingest previously archived sets.
5. Concurrency & Security:
   - Compare-and-swap (CAS) database locks with HTTP 409 Conflict protection on all state-changing endpoints (`/force_merge`, `/remove_set`, `/redo_extract`, `/redo_match`).
   - `OriginGuardMiddleware`, `BodySizeLimitMiddleware` (100MB upload bound), and `TrustedHostMiddleware` enforced.

---

### Verification Baseline Already Completed
1. Pytest Full Suite:
   - 6,696 passed, 243 skipped, 44 xfailed, 0 failures across the entire codebase.
   - Overall statement coverage: 91% (Matching: 99%, Reconciliation: 97%, Grouping: 97%, Config: 96%, Extraction: 90%, Quarantine: 89%, Sync: 87%, Database/Locking: 100%).
2. Deployment Simulation:
   - `scripts/deploy_production.ps1 -DryRun` executed cleanly with exit code 0 through all 9 installation phases.
   - Automated health polling on port 4200 (up to 60s) for Prefect Server startup.
   - Automated creation of `aam-merger-process-pool` work pool.
   - Bundled NSSM 2.24 64-bit binary in `tools/nssm/nssm.exe`.
3. Pre-Flight Bugfixes Applied:
   - Fixed `NameError: name 'Path' is not defined` at `src/app/api/routes/po_sets.py:661`.
   - Fixed PowerShell variable interpolation `$($prefCode):` in deployment scripts.
   - Fixed Prefect server start sequence in `scripts/update_production.ps1`.
   - Added `path_separator = os` to `alembic.ini`.

---

### Audit Instructions: What You Must Review & Answer

Please thoroughly audit the repository files—especially:
- `src/app/core/config.py` & `src/app/core/database.py`
- `src/app/models/models.py`
- `src/app/services/ingestion.py`, `poller.py`, `extraction.py`, `reconciliation.py`, `matching.py`, `quarantine.py`, `merge.py`, `locking.py`, `sync_lock.py`
- `src/app/flows/sync.py`
- `src/app/api/routes/po_sets.py`, `dashboard.py`, `manual_merger.py`
- `scripts/deploy_production.ps1`, `install_nssm_services.ps1`, `verify_deployment.ps1`, `manage_services.ps1`

Provide your assessment in the following structured format:

1. EXECUTIVE VERDICT:
   - State clearly: [GO] or [CONDITIONAL GO] or [NO-GO].
2. ARCHITECTURAL INVARIANT AUDIT:
   - Confirm whether any retired logic (part_no, line_type, SKU rescue, price extraction) has leaked into the codebase.
   - Confirm integer scaled x1000 quantity comparisons.
   - Confirm partial unique index and dedup loop guards (ensure no infinite token burn loops on failed or combined PDFs).
3. WINDOWS SERVER 2016 HOST SAFETY:
   - Verify NSSM path anchoring, environment variables, and log redirection.
   - Verify SQLite WAL behavior on local storage vs SMB constraints.
   - Verify Windows MAX_PATH (260 char) safety in quarantine and merge folder paths.
4. CONCURRENCY & LOCK CONFLICT HANDLING:
   - Verify 409 Conflict handling on double-actions (e.g. concurrent Force Merge or Remove Set).
   - Verify sync sweep lock interaction with UI user actions.
5. BLOCKERS (IF ANY) OR CONDITIONS FOR DEPLOYMENT:
   - List any mandatory operational prerequisites that the human operator must execute before flipping the switch tomorrow morning.
6. FINAL RECOMMENDATION & SIGN-OFF.
```

---

## 3. Reference Data & Context for the Auditor

### Key Files in `deployed/aam-2026-10-27`
```text
├── alembic/                       # Database migration versions
├── alembic.ini                    # Alembic config (path_separator = os)
├── config.example.yaml            # Validated template config
├── data/                          # Placeholder directory structure (.gitkeep)
├── deploy.bat                     # Administrator one-click deployment entry point
├── docs/
│   ├── AAM_merger_V3_PRODUCT.md   # Authoritative source of truth for product behavior
│   ├── BATCH1_CHANGES_2026-10-25.md
│   ├── CORE_LOGIC.md              # Reconcile & matching reference
│   ├── CUSTOMS_OPERATIONS.md      # Customs toggle & append operations
│   ├── DELIVERY.md                # Client delivery runbook
│   ├── RUNBOOK.md                 # Production operations guide
│   └── SETTINGS_SPEC.md           # Admin settings schema
├── pyproject.toml                 # Dependencies (FastAPI, Prefect, Uvicorn, SQLAlchemy)
├── scripts/
│   ├── deploy_production.ps1      # 9-stage automated deployment script
│   ├── install_nssm_services.ps1  # NSSM service installer (Web, Worker, PrefectServer)
│   ├── manage_services.ps1        # Service status / stop / start / restart
│   ├── update_production.ps1      # Safe production updater with auto-migrations
│   ├── verify_deployment.ps1      # Post-deployment health verification audit
│   ├── backup_db.ps1              # SQLite WAL online vacuum backup
│   └── migrate_db.py              # Schema migration runner
├── src/app/
│   ├── api/routes/                # Dashboard, PO Sets, Settings, Sync, Manual Merger
│   ├── core/                      # Config, Database engine, Logging, Limits
│   ├── flows/sync.py              # Prefect 3.x orchestration & 2-phase reconcile sweep
│   ├── models/models.py           # Document, LineItem, POSet, AuditLog
│   └── services/                  # Ingestion, Poller, Extraction, Grouping, Matching,
│                                  # Reconciliation, Customs, Merge, Quarantine, Locking
├── static/                        # Offline CSS, JS, fonts (no external CDN required)
├── templates/                     # Server-rendered Jinja2 templates
├── tools/nssm/nssm.exe            # Bundled 64-bit NSSM 2.24 executable
└── uv.lock                        # Pinned reproducible dependency lockfile
```

---

## 4. Current Test & Static Analysis Baseline

- **Pytest Suite:** 6,696 passing tests, 0 failures, 243 skipped (browser E2E opt-in), 44 xfailed.
- **Linter Check:** `uv run ruff check src` passes with 0 errors.
- **Formatter Check:** `uv run ruff format --check src` passes.
- **Git Status:** Clean working tree, all changes committed and synchronized across `main`, `dev`, and `deployed/aam-2026-10-27`.

This brief is complete and self-contained for Claude Opus review.
