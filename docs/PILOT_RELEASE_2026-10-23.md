# Pilot Release Verification — 2026-10-23

**Scope:** Delete→Archive feature, archive-aware schema, no-pruning operations,
and the pilot source package. This is the release note for client testing on a
real dataset and hardware.

## 1. What changed

Delete on a quarantined PO Set now **archives** instead of deleting:

- every `po_sets`, `documents`, `line_items` and `audit_log` row is kept;
- every stored PDF is copied to
  `data/development/archived_sets/<PO>_<UTC>_<set id>/files/`;
- `po_set.json`, `documents.json` (with `raw_extraction_json`),
  `line_items.json`, `audit_log.json`, `manifest.json` and `ARCHIVE.txt` are
  written beside the copies;
- content-matched input copies are moved into `.../input/` so the next sync
  cannot resurrect the set;
- the set and its documents are marked `archived_at` and disappear from the
  dashboard/quarantine worklists; direct detail URL shows a read-only banner;
- a second Delete is idempotent (no second export, no row change).

Two uniqueness rules were changed because keeping rows would otherwise block
normal work:

| Rule | New predicate |
|---|---|
| `uq_po_sets_open_po_no` | `status != 'merged' AND archived_at IS NULL` |
| `ix_documents_sha256_hash` | `archived_at IS NULL` (partial unique) |

The second is the important one: re-uploading the exact same PDF after an
archive creates a **new active document**; the archived row keeps its hash for
audit but no longer reserves it.

## 2. Observed verification

| Check | Result |
|---|---|
| Full test suite | `828 tests collected`, exit 0 |
| Lint | `ruff check .` — all checks passed |
| Types | `ty check src` — all checks passed |
| PowerShell syntax | all `scripts/*.ps1` parse under Windows PowerShell 5.1 |
| Migration on live-DB copy | revision `c3d4e5f6a7b8`; `documents=284`, `po_sets=83`, `line_items=616`; `integrity_check ok` |
| Archive on real quarantined set | PO `50119501`, 4 docs: 4 rows kept, 4 files copied, 4 originals still present, manifests + `ARCHIVE.txt` present |
| App smoke test on migrated live DB | `/health`, `/dashboard`, `/quarantine`, `/unclassified`, quarantined detail and merged detail all HTTP 200 |
| Development snapshot task | DB backup + 285 stored PDFs + logs copied to `data/development/snapshots`; copy-only |
| Local DB migration | `data/aam_merger.db` at `c3d4e5f6a7b8`; pre-migration backup written |

## 3. Operations set for the pilot

- `logging.backup_count: 0` — one growing app log, never rotated away.
- NSSM `AppRotateFiles 0` for web, worker and Prefect server — set by the
  installer, so re-installs do not revert it.
- `AAMMerger-DailyBackup` at 01:00 with `-RetentionDays 0 -BackupDir
  data\development\snapshots\db`.
- `AAMMerger-DevelopmentSnapshot` at 01:30; `scripts/development_snapshot.ps1`
  copies logs, raw JSON, stored, quarantine, output and combined with
  `robocopy /E /XO` (never deletes).
- `PREFECT_HOME=data\prefect` on all services.
- `data\raw_extractions` is created by the installer so raw JSON files exist.
- Do **not** run `scripts\reset_data.ps1` on the pilot PC.

## 4. Package

`dist/aam-merger-pilot-20261023.zip` — source tree, no `.env`, no
`config.yaml`, no `data/`, no `.venv`. SHA-256 is in
`dist/aam-merger-pilot-20261023.sha256`.

## 5. Not verified here

These require the client's actual PC, network and OpenRouter quota:

- live VLM extraction (the release pass uses stubs/fixtures);
- NSSM service install and firewall on the client host;
- real LAN access from a second PC;
- real multi-user concurrency and a reboot during a sync.

The Day-1 checklist in `docs/PILOT_PLAN.md` covers those on the pilot machine.
