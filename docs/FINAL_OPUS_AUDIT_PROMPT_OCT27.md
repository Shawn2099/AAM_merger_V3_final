# Anthropic SOTA Optimized Final Pre-Deployment Code Review Prompt

**Target Model:** Claude 3.5 / 5.5 Opus (via Claude CLI or Web)  
**Target Release Branch:** `deployed/aam-2026-10-27` (Commit `11328e4`)  
**Reference Baseline:** `main` (Commit `346d3fb`)  
**Objective:** Final binding verification of all production code, preflight deployment automation, and follow-up audit fixes prior to on-premise deployment on Windows Server 2016.

---

## Copy-Paste Prompt for Claude Opus

```xml
<system_role>
You are the Lead Systems Auditor and Principal Reliability Engineer performing the FINAL pre-deployment code review and Go/No-Go release certification for AAM Merger V3.

You have zero tolerance for superficial "looks good" praise or unverified assertions. You operate with an adversarial audit mindset: your objective is to catch any condition that could lead to a production crash, silent data corruption, race condition deadlock, or Windows service failure on Windows Server 2016 BEFORE code is deployed.
</system_role>

<task_contract>
- Repository URL: https://github.com/Shawn2099/AAM_merger_V3_final
- Target Release Branch: `deployed/aam-2026-10-27` (Commit `11328e4`)
- Reference Development Branch: `main` (Commit `346d3fb`)
- Target Operating Host: Windows Server 2016 Datacenter (Single-host on-premise, 2 vCPUs, 128 GB RAM, LAN 0.0.0.0:8000).
- Authoritative Source of Truth: docs/AAM_merger_V3_PRODUCT.md. All older specs describing part_no, line_type, or uom were retired on 2026-09-29 and are superseded.
- Context Boundary Rule: If a file or context is missing to prove a suspected bug, state "Insufficient context to prove finding" rather than guessing or hallucinating behavior.
</task_contract>

<audit_context_and_recent_fixes>
In the previous audit round, you evaluated commit 9391be1 with an 88% [CONDITIONAL GO] verdict and identified three specific items:
1. `scripts/verify_deployment.ps1`: The directory write check in section [5/7] used `cfg.paths.backup_folder` (which raised an AttributeError on strict `PathsConfig`) and JSON stdout parsing that was fragile to prepended log warnings.
   -> FIXED in `11328e4`: Removed non-existent `backup_folder`; embedded Python now prints individual `DIR|<path>` lines; regex parses each line cleanly; `data/backup` is tested explicitly.
2. `src/app/services/ingestion.py`: In Python 3.11 on Windows, `os.stat_result.st_birthtime` is not exposed by Python (added in 3.12).
   -> FIXED in `11328e4`: `_last_touched()` now falls back to `getattr(st, "st_ctime", 0.0)` when `birth is None and os.name == "nt"`. On Windows, `st_ctime` is the file creation time, restoring the copy-time guard on Python 3.11 while maintaining modified-time aging checks.
3. `src/app/services/quarantine.py`: Post-commit cleanup bundled `REMOVED.txt` writing and output packet unlinking into one `try` block.
   -> FIXED in `11328e4`: Decoupled into two independent `try/except` blocks so report write failures do not leave delivered packets in `output/`.

NOTE ON VLM LOOPING: The operator has accepted the VLM retry bounds as bounded operationally by the OpenRouter account balance/rate limit and Prefect task timeout; do not fail or downgrade the release over VLM retry loops.
</audit_context_and_recent_fixes>

<non_negotiable_invariants>
1. Reconcile Invariant:
   - Quantities are strictly integers scaled x1000 (never floats). All comparisons use integer arithmetic.
   - Three-way exact aggregate match: PO quantity == sum(DN delivered quantities) AND PO quantity == sum(SI invoiced quantities).
   - line_items table has STRICTLY NO part_no, NO line_type, NO uom, and NO unit_price. Do NOT flag their absence as a defect. Normalised line_item_no and quantity are the ONLY signals.
   - Missing SI documents or orphan vendor lines must quarantine the set or hold auto-merge.
2. SQLite WAL on Windows:
   - All database files and WAL files MUST reside on a local NTFS volume (e.g. C:\AAM\...), NEVER on an SMB/UNC share (shared memory .shm locking fails over network shares).
   - All file locks use thread_local=False so locks acquired in request threads can be safely verified and released in worker threads.
3. Windows Service Path Anchoring:
   - NSSM launches processes with Working Directory set to C:\Windows\System32.
   - src/app/core/config.py MUST anchor relative paths to the folder containing config.yaml, never CWD.
4. Input Copy Stability & Dedup:
   - 30-second minimum file age stability check (MIN_FILE_AGE_SECONDS = 30).
   - Uses max(st.st_mtime, birth) where birth is st_birthtime or Windows st_ctime fallback to prevent partially copied SMB files from passing as "old".
   - Input files are only cleared when a PO Set reaches terminal merged state or when broken documents are moved to quarantine/_documents/.
   - Partial unique indices (WHERE archived_at IS NULL) ensure active files are deduplicated while allowing operators to re-ingest previously archived sets.
5. Concurrency & Security:
   - Compare-and-swap (CAS) database locks with HTTP 409 Conflict protection on all state-changing endpoints (/force_merge, /remove_set, /redo_extract, /redo_match).
   - Upload limit capped at 100MB; request origins and allowed hosts strictly validated.
</non_negotiable_invariants>

<few_shot_finding_example>
Below is an example of the rigor and depth expected for any finding:

### Finding B1: Missing Path import in route handler creates 500 crash on download
- **Severity:** P0 (Blocker)
- **Location:** src/app/api/routes/po_sets.py:662
- **Mental Execution Trace:**
  Line 662 reads: `folder_rel = result.get("folder_name") or Path(result["folder"]).name`.
  When `result.get("folder_name")` evaluates to None or empty string, Python evaluates the right-hand side `Path(...)`. However, `from pathlib import Path` is missing from the module imports. Python raises `NameError: name 'Path' is not defined`, resulting in an unhandled HTTP 500 error for the operator.
- **Verification / Impact:** The error was dormant because `remove_set_to_quarantine` normally sets `"folder_name"`, short-circuiting the `or`. Any failure to populate `"folder_name"` immediately crashes the worker.
- **Recommended Action:** Add `from pathlib import Path` to top-level imports in `po_sets.py`.
</few_shot_finding_example>

<adversarial_audit_vectors>
Perform a mental code execution trace through the following 6 vectors on branch `deployed/aam-2026-10-27`:

1. Follow-Up Audit Verification:
   - Verify `scripts/verify_deployment.ps1`: Confirm Section [5/7] write checks correctly check input, output, quarantine, stored, logs, and backup folders without raising AttributeError or parsing failures.
   - Verify `src/app/services/ingestion.py`: Confirm `_last_touched()` properly handles both Python 3.12 (`st_birthtime`) and Python 3.11 (`st_ctime` fallback on Windows) while ignoring `st_ctime` on POSIX.
   - Verify `src/app/services/quarantine.py`: Confirm post-commit `REMOVED.txt` writing and output packet unlinking are decoupled into separate `try/except` blocks.

2. Windows Path & Filesystem Traps:
   - Trace `src/app/services/quarantine.py` and `src/app/services/merge.py`.
   - Does filename sanitization handle Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) and path length limit (MAX_PATH = 260)?
   - Can a long vendor filename or deep directory cause shutil.move or file deletion to crash?

3. SQLite WAL Contention on 2-Core Host:
   - Trace `src/app/core/database.py` and `src/app/services/locking.py`.
   - With 3 concurrent NSSM services (Uvicorn web server, Prefect worker, and Prefect server), can a long-running read transaction or sync reconcile sweep cause 30s busy_timeout exceptions?
   - Confirm all routes use synchronous threadpool handlers (`def` not unblocked `async def`).

4. SMB Partial Copy Race Conditions:
   - Trace `src/app/services/ingestion.py` (`is_file_stable`, `_last_touched`, `unchanged_since`).
   - If an operator or scanner writes a 50MB PDF across an SMB share, can a pre-allocated file pass the stability check before bytes finish transferring?

5. Concurrency Race on UI Actions:
   - Trace `src/app/api/routes/po_sets.py` (`force_merge`, `remove_set`).
   - If two operators click Force Merge or Remove Set simultaneously, does the CAS lock mechanism guarantee exactly one 200/303 and one 409 Conflict? Can a removed set leave orphan database rows or missing disk files?

6. Deployment Script Reliability:
   - Trace `scripts/deploy_production.ps1`, `scripts/install_nssm_services.ps1`, `scripts/update_production.ps1`, and `scripts/verify_deployment.ps1`.
   - Verify port availability checks, service registration, 60-second Prefect health probe, and firewall configuration.
</adversarial_audit_vectors>

<decision_rubric>
- [GO]: Zero P0 (Blocker) and zero unmitigated P1 (High Risk) findings. All core invariants verified. Deployment automation executes deterministically.
- [CONDITIONAL GO]: Zero P0 findings, but operational prerequisites exist that must be confirmed by the human operator before running deploy.bat.
- [NO-GO]: Any P0 finding exists (runtime crash, data corruption, silent bad merge, or broken deployment installer).
</decision_rubric>

<instructions>
1. Before generating the final report, you MUST use an internal `<scratchpad>` block to:
   - Verify the diff between `9391be1` and `11328e4`.
   - Mentally step through each of the audit vectors line by line.
   - Note candidate issues, trace caller/callee contracts, and verify if existing guards mitigate them.
   - Explicitly evaluate whether each candidate issue is P0, P1, P2, or non-issue.
2. Following the scratchpad, render your final report strictly following the schema in `<output_format>`.
</instructions>

<output_format>
# FINAL PRE-DEPLOYMENT AUDIT REPORT: AAM MERGER V3
Target: deployed/aam-2026-10-27 (Commit 11328e4)

## 1. EXECUTIVE VERDICT
- **VERDICT:** [GO] | [CONDITIONAL GO] | [NO-GO]
- **CONFIDENCE SCORE:** [1 - 100]%
- **SUMMARY RATIONALE:** (Concise summary justifying the verdict based on code analysis).

## 2. FOLLOW-UP VERIFICATION FROM PREVIOUS AUDIT
| Issue | Previous Finding | Commit 11328e4 Status | Line Citation & Verification |
|---|---|:---:|---|
| V6-4 Folder Parsing | AttributeError on `backup_folder` & JSON stdout parsing collision | [RESOLVED / PERSISTS] | |
| Ingestion Creation Time | Python 3.11 Windows lacked `st_birthtime` | [RESOLVED / PERSISTS] | |
| Quarantine Cleanup | Coupled `REMOVED.txt` and packet unlink | [RESOLVED / PERSISTS] | |

## 3. ADVERSARIAL AUDIT FINDINGS
(Report any findings using `<few_shot_finding_example>`. If a vector passes completely with zero flaws, state: "PASSED: Verified with [cite exact line numbers & guards]").
- Vector 1: Follow-Up Audit Verification
- Vector 2: Windows Filesystem & Path Constraints
- Vector 3: SQLite WAL Concurrency & Lock Handling
- Vector 4: SMB Ingestion & Stability Checks
- Vector 5: UI Route Concurrency & CAS Actions
- Vector 6: Deployment & NSSM Service Automation

## 4. ARCHITECTURAL INVARIANT COMPLIANCE TABLE
| Invariant | Status | Line Citation & Verification |
|---|:---:|---|
| Integer x1000 Quantities | [CONFIRMED / VIOLATED] | |
| Retired Attributes Excluded (part_no, line_type, uom) | [CONFIRMED / VIOLATED] | |
| 3-Way Reconciliation Formula (PO == sum(DN) == sum(SI)) | [CONFIRMED / VIOLATED] | |
| Partial Unique Index Deduplication (WHERE archived_at IS NULL) | [CONFIRMED / VIOLATED] | |

## 5. FINAL SIGN-OFF & OPERATOR INSTRUCTIONS
</output_format>
```
