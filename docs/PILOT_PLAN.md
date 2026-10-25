# Pilot Plan — AAM Merger V3 (1 week)

**Status:** IMPLEMENTED 2026-10-23 — B selected (development folder holds archived sets **and** scheduled snapshots). Archive code, migration `c3d4e5f6a7b8`, tests, installer settings and docs are in the tree; full suite and `ruff` are green.
**Baseline:** the current tree, full suite green, `ruff check .` clean, Alembic head `c3d4e5f6a7b8`.
**Frozen rule:** the application's behaviour is not otherwise changed. There is exactly **one** scoped feature — Delete becomes an archive action (section 3). That feature necessarily touches the model, migration, archive service, route guards, worklist filters, templates and tests; no unrelated behaviour is changed. Everything else is configuration, installer/ops settings, and operating rules.

---

## 1. Goal and success criteria

The pilot runs on one Windows PC on the client LAN, open to anyone on that network, and is used to collect real data for product improvement.

Success means:

1. the operator loop works end-to-end on real documents;
2. no data is lost — not in the DB, not in the logs, not in the files;
3. every status/reason is explainable from the UI and the audit log;
4. the collected DB, logs, PDFs, raw JSON and backups are complete enough to analyse afterwards.

Correctness outranks throughput. A blocked or quarantined set is an acceptable outcome; a wrong merged packet is not.

---

## 2. Data that must survive

| Data | Location | Mechanism |
|---|---|---|
| Structured data (PO sets, documents, line items, audit) | `data/aam_merger.db` | no pruning; Delete becomes archive |
| Application log | `data/logs/aam_merger.log` | `logging.backup_count: 0` |
| NSSM stdout/stderr | `data/logs/nssm-*.log` | `AppRotateFiles 0` |
| Raw extraction JSON | `raw_extraction_json` + optional `data/raw_extractions/` | DB retained |
| Source PDFs | `data/stored/`, `data/combined/` | never auto-deleted |
| Quarantine evidence | `data/quarantine/` | never auto-deleted |
| Merged packets | `data/output/` | never auto-deleted |
| Archived sets / development exports | `data/development/archived_sets/` | created by the Delete action |
| Development snapshots | `data/development/snapshots/` | daily copy of DB, logs, raw JSON and files; never pruned |
| Backups | `data/backup/` | backup task with `-RetentionDays 0` |

Residual deletion paths after this plan are explicit and limited: `delete_input_files` removes only the **input-folder copies** after a successful merge (stored/output copies remain); a failed document's input copy is moved to `quarantine/_documents`; split-page temp copies and failed packet writes clean up their own temp files; `reset_data.ps1` remains destructive and is an operating rule: **do not run it on the pilot PC**. The database, logs, stored PDFs, quarantine evidence, output packets and `data/development/` are never pruned by the app.

---

## 3. The one code change — Delete becomes Archive

### 3.1 Behaviour

Pressing **Delete** on a quarantined set:

1. Requires `status == quarantined` (unchanged).
2. Requires the per-PO lock (unchanged; second request gets 409).
3. Creates a staging folder `data/development/archived_sets/.tmp-<uuid>/`.
4. Copies every attached document's stored PDF into `files/` (`shutil.copy2`; the original `stored/` copy is **not removed** — the product rule is that `stored_path` is permanent).
5. Writes:
   - `po_set.json` — the PO Set row;
   - `documents.json` — every document row, including `raw_extraction_json`;
   - `line_items.json` — every line item;
   - `audit_log.json` — the set's audit rows;
   - `ARCHIVE.txt` — PO number, reason, justification, timestamp, counts, operator/source.
6. Renames the staging folder to `data/development/archived_sets/<po_no>_<UTC timestamp>_<po_set_id>/`.
7. Marks the rows archived, **without deleting them**:
   - `po_sets.archived_at`, `archived_by`, `archive_reason`, `archive_path`.
8. Writes an `audit_log` row (action `quarantine_delete`, detail includes `"archived": true` and the archive path).
9. Moves matching input-folder copies to `data/development/archived_sets/<...>/input/` (content-hash match, as today), so the next sync cannot resurrect the set.
10. Returns success and redirects to `/quarantine`.

If any copy or write fails, the staging folder is removed, **no row is changed**, and the route returns 422 with the reason. If the DB commit fails after the folder is complete, the archive folder is left in place (no data loss) and the error is returned; retrying is safe.

### 3.2 Development folder layout

All development-collection artifacts go under `data/development/`:

```
data/development/
  archived_sets/
    <PO>_<UTC timestamp>_<po_set_id>/
      files/                 copied stored PDFs
      input/                 moved input copies (hash-matched)
      po_set.json
      documents.json         includes raw_extraction_json
      line_items.json
      audit_log.json
      ARCHIVE.txt

  snapshots/                 written by the daily ops task, never pruned
    db/
      aam_merger_<timestamp>.db
    logs/
      aam_merger.log, nssm-*.log
    raw_extractions/
      *.json
    files/
      stored/                incremental copy
      quarantine/            incremental copy
      output/                incremental copy
```

The live DB, logs and PDF folders stay in their configured runtime locations. The daily snapshot task copies (never moves) them into `data/development/snapshots/`; the archive action writes the exported set under `archived_sets/`. Nothing in `data/development/` is ever pruned.

### 3.3 Database changes

One Alembic revision after `b2c3d4e5f6a7`:

- `po_sets.archived_at` (nullable datetime, indexed)
- `po_sets.archived_by` (nullable text)
- `po_sets.archive_reason` (nullable text)
- `po_sets.archive_path` (nullable text)
- `documents.archived_at` (nullable datetime, indexed)

Two unique rules must change, because keeping rows otherwise breaks the
workflow:

- `uq_po_sets_open_po_no` becomes `WHERE status != 'merged' AND archived_at IS NULL`,
  so an archived PO can form a new set.
- `ix_documents_sha256_hash` becomes a **partial** unique index
  `WHERE archived_at IS NULL`, so the same bytes can be ingested again after
  the old set is archived.

No existing row is changed. `migrate_db.py` gains a synthetic archive marker,
the new head and verification of both index predicates.

### 3.4 Active-view filters

Add `archived_at IS NULL` everywhere a set or document is treated as active:

- dashboard list and status counts, quarantine list/table;
- `get_or_create_po_set` (including the `IntegrityError` re-query);
- recovery Phase-2 sweep, pending-document sweep, stale-processing reclaim;
- attach sweeps (`attach_unattached_to_open_sets`, `resolve_unattached_documents`,
  `_anchor_from_po_line_ref`);
- ingestion/upload/split-parent dedup by `sha256_hash`;
- upload/reclassify target checks, and all set-level action routes through
  `_acquire_lock` (archived = 409 read-only);
- PO Set JSON list and archive idempotency.

The detail page stays reachable by direct URL for a read-only archived view with
a banner; the template hides action controls, the upload form, the Delete card
and the Force Merge modal. Archived sets are hidden from worklists, not deleted.

### 3.5 UI

- Update 2026-10-25: a second action, **Move to quarantine and remove set**, deletes a set's rows after copying its files to `quarantine/_removed_sets/` (decision 2026-10-25). It is not an archive; see PRODUCT section 8, N.
- Keep the button labelled **Delete**. The confirmation text states: “This moves the set to `data/development/archived_sets` and keeps the database records. Nothing is destroyed.” The behaviour is archive, not deletion.
- Show a notice after success: the archive folder path.
- Add an “Archived” count or filter if cheap; not required for the pilot.

### 3.6 Tests

New/updated tests (implemented):

- `test_archive_keeps_rows_manifest_and_hides_from_worklists`
- `test_archive_allows_a_new_set_for_the_same_po`
- `test_archived_document_does_not_block_identical_reingest`
- `test_archive_copy_failure_leaves_rows_untouched`
- `test_archive_moves_input_copies_only_on_content_match`
- `test_archived_rows_do_not_reserve_the_po_or_the_content_hash` (migration)
- updated `test_delete_keeps_files`, audit-detail and UI delete tests to assert
  archive semantics rather than row deletion.

---

## 4. Configuration and operations (no other code changes)

| Change | Where | Exact setting |
|---|---|---|
| Stop log deletion | `config.example.yaml` / `config.yaml` | `logging.backup_count: 0` |
| Keep log level manageable | `config.yaml` | `logging.level: INFO` |
| Stop NSSM rotation | `install_nssm_services.ps1` | `AppRotateFiles 0` / `AppRotateOnline 0` (persists across re-install) |
| Stop backup pruning | `setup_test_rig.ps1` | backup task gets `-RetentionDays 0` |
| Put DB snapshots in the development folder | `setup_test_rig.ps1` | backup task gets `-BackupDir "<project>\data\development\snapshots\db"` |
| Daily copy of logs and files | new task `AAMMerger-DevelopmentSnapshot` at 01:30 | `scripts/development_snapshot.ps1` uses `robocopy /E /XO` for logs, `raw_extractions`, `stored`, `quarantine`, `output`, `combined` (copy newer only, never delete) |
| Let the app write raw JSON files | installer | creates `data\raw_extractions` |
| Keep Prefect history with the data | `install_nssm_services.ps1` | `PREFECT_HOME=<project>\data\prefect` |
| Never reset pilot data | operating rule | do not run `scripts/reset_data.ps1` |
| Manual backups go to development | operating rule | `backup_db.ps1 -BackupDir ...\data\development\snapshots\db -RetentionDays 0` |

`backup_count: 0` is verified against CPython: `RotatingFileHandler.doRollover()` skips the rename/delete block entirely when `backupCount <= 0` and reopens the same file in append mode. Nothing is removed; the file grows.

Snapshot task shape (no application code, copy-only):

```powershell
# DB — existing script, retargeted, no retention
.\scripts\backup_db.ps1 -BackupDir "$PWD\data\development\snapshots\db" -RetentionDays 0

# Logs + raw JSON + PDF folders — incremental copy, never delete
robocopy data\logs           data\development\snapshots\logs           /E /XO /R:1 /W:1
robocopy data\raw_extractions data\development\snapshots\raw_extractions /E /XO /R:1 /W:1
robocopy data\stored         data\development\snapshots\files\stored   /E /XO /R:1 /W:1
robocopy data\quarantine     data\development\snapshots\files\quarantine /E /XO /R:1 /W:1
robocopy data\output         data\development\snapshots\files\output   /E /XO /R:1 /W:1
robocopy data\combined       data\development\snapshots\files\combined /E /XO /R:1 /W:1
```

`/E` copies subdirectories, `/XO` copies only newer files, `/R:1 /W:1` stops a locked file from hanging the task; neither deletes destination content. `scripts/development_snapshot.ps1` runs exactly this and the DB backup, and the installer registers it at 01:30. The disk cost of B is roughly one extra copy of `stored/`, `quarantine/`, `output/` and `combined/` plus daily DB/log growth — expected for a development corpus. Keep an off-machine copy of `data/development/` as well; it lives on the same disk as the live DB.

---

## 5. Pre-flight

- [ ] Confirm the archive semantics in section 3 (especially: keep DB rows, copy stored files, keep originals).
- [ ] Confirm `data/development/` as the archive and snapshot location.
- [ ] Record the data drive's free space on Day 1 for the growth estimate.
- [ ] Confirm the OpenRouter key/quota and that the server has internet.
- [ ] Confirm Prefect local server and `PREFECT_API_URL`.
- [ ] Have one real complete PO/DN/SI set for the smoke test.
- [ ] Run `migrate_db.py` on a copy of the client DB and verify the 284-document dataset (if that is the starting DB).
- [ ] Confirm the current `data/aam_merger.db` is migrated before the app is started; it currently has no `alembic_version` and lacks `po_no_source` / `description_match_count`.
- [ ] Confirm `data/development/archived_sets/` and `data/development/snapshots/` are on the same local disk and writable.

---

## 6. Day-by-day

### Day 1 — Implement archive, install, baseline
- Implement section 3 and its tests; run the full suite.
- Add and test the Alembic revision; update `migrate_db.py`.
- Install on the pilot PC; run `migrate_db.py` on the real DB copy, then the pilot DB.
- Apply section 4 settings; create `data\development\archived_sets` and `data\development\snapshots`; create `data\raw_extractions`; retarget the backup task; add the 01:30 snapshot task; set NSSM rotation off.
- Verify `/health`, dashboard from a second PC, services Running, Prefect deployment registered.
- Drop the real set; Scan; confirm merged packet and page order.

**Exit:** full suite green; app runs on the migrated DB; a real set merges; no automatic deletion settings remain.

### Day 2 — Core correctness
Happy path; duplicate file; missing PO/DN/SI; short DN; short SI; orphan vendor line; zero quantity; two PO documents; PO-reference drift; ambiguous PO.
Record each status and reason; confirm no wrong merge.

### Day 3 — Operator actions and edges
Force Merge with/without justification; second Force Merge; Delete (archive) on a quarantined set; Delete refused on a non-quarantined set; Re-Extract; Re-Match; Merge Now; customs toggle; upload both documents; duplicate upload; upload owned by another set; customs append after merge; split combined PDF; failed extraction + recovery; two users at once (409); Re-Extract during sync (409).

### Day 4 — No pruning, recovery, data integrity
- Snapshot DB row counts and file counts; run a day of testing; confirm nothing was removed.
- Confirm old logs and all backups remain.
- Confirm the snapshot task ran: `data/development/snapshots/` contains a DB backup, logs, raw JSON and file copies.
- Restore a backup from `data/development/snapshots/db/` into a scratch folder and start the app against it.
- Reboot mid-sync; confirm recovery and no duplicate line items.
- Record free disk and growth estimate.

### Day 5 — Client dry run and handover
- Client repeats the 20-scenario script unaided.
- Hand over `docs/TESTER_GUIDE.md`, `docs/DELIVERY.md`, the retention rules, backup/restore steps, and the archive explanation.
- Explain the amber **Merged • review** tag: description fallback is intentional, advisory, and does not change the verdict.
- Sign-off on scenarios.

---

## 7. Acceptance

- Full suite green, including the new archive tests; `ruff` clean.
- `migrate_db.py` on the live-DB copy preserves all rows; `integrity_check ok`.
- App boots and serves the dashboard on the pilot DB.
- All Day-2/3 scenarios observed passing.
- Archive: rows remain, `data/development/archived_sets/<...>/` contains files + JSON + `ARCHIVE.txt`, the set leaves the quarantine list, audit row exists.
- Development snapshots: `data/development/snapshots/` contains the DB, logs, raw JSON and file copies; a second run adds without deleting.
- No automatic deletion: logs and backups still present after the test window.
- Backup restore works.
- Client can repeat the test script.

---

## 8. Known pilot risks

- No authentication; anyone on the LAN can act. Archive protects data; it is not access control.
- Live VLM extraction was not exercised in the release pass; watch the first real run.
- A crash between packet write and DB commit can leave an unowned packet; recovery is manual.
- A hard-killed sync keeps `/sync/status` “running” for up to one hour; `POST /sync` may still succeed.
- No pruning means unbounded growth; disk capacity must be watched. B adds roughly one extra copy of `stored/`, `quarantine/` and `output/` plus daily DB/log snapshots.
- `max_concurrent_extraction_tasks` is not enforced; extraction is serial.
- Archive is the only code change; a missed `archived_at IS NULL` filter could show an archived set in a worklist. The test list covers the known queries.

---

## 9. Decisions and verification

Resolved and implemented:

1. Archive keeps the DB rows and **copies** stored PDFs; originals stay in `stored/`.
2. Location: `data/development/archived_sets/` for archived sets, `data/development/snapshots/` for daily copies.
3. The button stays labelled **Delete**, with archive behaviour.
4. **B selected:** DB, logs, raw JSON, `stored/`, `quarantine/`, `output/` and `combined/` are copied into `data/development/snapshots/` daily (copy-only, never delete).
5. Disk size needs no pre-set threshold; growth is recorded on Day 1.
6. The review found two release blockers the original draft missed — both unique indexes still treated archived rows as active. Both are fixed in migration `c3d4e5f6a7b8` and pinned by tests.
7. Migration was run on a copy of the 284-document live DB: `documents=284`, `po_sets=83`, `line_items=616`, `integrity_check ok`, revision `c3d4e5f6a7b8`.
8. Full test suite and `ruff check .` / `ty check src` are green.

Remaining are the pilot's real-hardware/live-VLM steps (Day 1 install and smoke test); they cannot be exercised on this development machine.
