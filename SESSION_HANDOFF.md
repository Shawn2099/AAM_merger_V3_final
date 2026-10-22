# Session Handover — AAM Merger V3

**Branch:** `fix/mvp-high-priority` (local commits; not merged into main/dev-simplified).
**Last commit:** `01f51f9` (description keys a PO line that has no number).
**Tests:** full suite green (4 full runs today). `ruff check` / `ruff format --check` clean. `ty check src` clean. `make business-logic` = 26 scenario tests.
**Delivery goal:** one Windows PC in the client's office is the server; office
users open `http://<server-ip>:8000/dashboard`. See `docs/DELIVERY.md`.

Read `AAM_merger_V3_PRODUCT.md` first, then `real arch v3.md` §30-§31.

---

## What was done (2026-10-22, second session — delivery readiness)

### Verified the real delivery path, not just the dev box

Cloned HEAD into a clean temp folder (`git archive` → `uv sync --no-dev` →
`config.example.yaml` → `alembic upgrade head` → boot on port 8010). Then ran a
real set (SIV-RHO-26-854 PO/DN/SI) through it: 3 documents read, 3 line items,
set `merged`, 1.1 MB packet written. This is the path the client machine takes.

### Fixes found by that exercise

1. **Daily backup** (`scripts/backup_db.py`, `tests/test_backup_script.py`)
   - Relative `paths.database_path` resolved against the process cwd; the
     Scheduled Task has no working directory. Now resolved against the project
     root. Missing `config.yaml` raises instead of guessing. Verified from
     `C:\Windows` against the live DB: 284 docs / 83 sets in the backup.
2. **Installer** (`scripts/setup_test_rig.ps1`)
   - Admin is required only when installing services/firewall/backup task;
     `-SkipServices` now works non-admin (verified on the clean clone).
   - Always syncs dependencies (a stale `.venv` is repaired), checks for
     uv/python with a real message, sets the backup task `-WorkingDirectory`,
     prints the LAN URL instead of `http://0.0.0.0:8000`.
3. **Prefect on a fresh host** (`setup_test_rig.ps1`,
   `install_nssm_services.ps1`, `tests/test_prefect_schedule.py`)
   - Nothing created `aam-merger-process-pool`; `deploy_prefect.py` failed with
     "Work pool not found", so the midnight sync was never registered.
   - Install/start services first, wait for the API, create the pool,
     register the deployment, restart the worker.
   - Every service now receives the same `PREFECT_API_URL`; without it each
     process starts its own ephemeral server.
   - `PYTHONUTF8=1` — Prefect's Rich renderer crashed on cp1252 and made
     `work-pool create` exit 1 after creating the pool. Now exit 0.
   - `prefect.schedule_timezone` (config, validated): cron is UTC by default;
     set the office zone for local midnight.
4. **Offline LAN** (`templates/base.html`, `static/js/`, test)
   - HTMX/Alpine were loaded from public CDNs; with no internet the page
     rendered but no button worked. Vendored and served locally.
5. **Force Merge audit** (`templates/po_set_detail.html`, test)
   - The modal never sent the justification the service accepts. Added a
     reason field + `hx-include`; the audit row can now carry the operator note.
6. **Reset for test rounds** (`scripts/reset_data.ps1`) — backs up, refuses
   while port 8000 listens, clears DB + folders, re-migrates; keeps config,
   `.env`, backups, logs. Verified on the clean clone.
7. **Type check** (`src/app/flows/sync.py`) — `ty check src` had 6 diagnostics
   from an unguarded `Document | None`; a vanished document is now skipped.
8. **Docs** — `docs/DELIVERY.md` (install/handover checklist) and
   `docs/TESTER_GUIDE.md` (office users). README + DEPLOY link them.

### Verified live

- Full suite green twice; ruff + ty clean (89% coverage).
- Clean-clone end-to-end merge (above).
- Backup from an unrelated cwd (above).
- Reset script leaves 0 docs / 0 sets and keeps the backup.
- Prefect pool + `aam-midnight-sync` deployment registered against a throwaway
  local server on port 4210 (stopped afterwards).
- Force Merge modal renders with the reason field on the running dev app.

---

---

## Business-logic verification pass (2026-10-22, third session)

`tests/test_business_scenarios_ui.py` now builds every verdict the product can
produce and asserts both the engine result and the page an operator reads:
merge, needs review, all six quarantine reasons, PO reference mismatch, packet
name collision, partial delivery, duplicate bytes vs duplicate content,
one-PO-many-DN/SI, customs before/after merge, the holding area, and quarantine
delete. Run it with `make business-logic` (or the file directly; 29 tests).

The one-page contract is [`docs/CORE_LOGIC.md`](docs/CORE_LOGIC.md): one PO +
n DN + n SI, per-line quantity aggregates, line number first / description only
as a fallback, quantities only, customs append. Two tests pin the "only
quantities" clause directly (`test_only_quantities_decide_the_verdict`) and the
description fallback (`test_a_numberless_vendor_row_falls_back_to_description`).

**Description now keys a PO row that has no number.** The operator clarified
that the table content identifies an item on either side. `matching.po_item_key`
uses the printed number, else `@desc:<normalised description>`; a numbered
vendor row may match a numberless PO row by description, but a number the PO
carries stays authoritative. A PO row with **neither** a number nor a
description still quarantines. The dashboard matrix uses the same keys.
Tests: `test_a_numberless_po_line_reconciles_by_description`,
`test_a_numberless_po_line_is_matched_by_its_description`,
`test_a_numbered_vendor_row_can_match_a_numberless_po_line_by_description`.
PRODUCT §2/§3, CORE_LOGIC §3 and `real arch` §33 updated.

### Defects found and fixed

1. **The Quarantine page showed no reason.** It listed PO number, status and
   doc count only. Added a "Why" column (page and HTMX fragment) and replaced
   the stale subtitle (it named retired matcher concepts) with the real
   reasons. Both routes now pass `reconcile_reason`.
2. **A quarantined set could show a fully green matrix with no explanation.**
   The detail page only re-ran the line comparison; it never reproduced the
   guards that run before it (two PO documents, PO reference mismatch, packet
   naming). Added a verdict banner ("Why this set is quarantined / needs review
   / blocked / waiting") and a note that a quarantined set does not merge even
   when every line matches.
3. **The dashboard holding-area badge counted only `UNKNOWN` documents.**
   Live it showed **2** while the page held **37**. One `_holding_area_query`
   now feeds the dashboard badge, KPI count, quarantine and detail views, and
   the page itself. Verified live: 37 = 37.
4. **`multiple_po_documents` / `po_reference_mismatch` persisted a generic
   sentence.** The detail (which names the offending files) is now stored; the
   PO-reference-mismatch result was not even returning its detail. New
   quarantines say which files; existing rows keep their old sentence until a
   Re-Match.
5. **Naming the PO on a held document attached it but never re-reconciled the
   set** — the operator had to wait for the next scan. The reclassify route now
   runs the engine once, outside the write session (SQLite writer overlap).
6. **A blank `stored_path` made the merge open the cwd** (`Path("")` "exists")
   and crash with `PermissionError`. Treated as a missing file: auto-merge
   refuses, the set keeps its status, the sweep survives.
7. The Unclassified page subtitle said every document there "could not be
   identified"; it also holds read-but-unplaced documents. Corrected.

### Customs append after the merge (client decision, supersedes FR-14.6)

The client's answer: customs defaults **off**, but it must be possible to
switch it on **after** delivery and append the two documents to the packet
already written. The earlier "merged refuses the toggle (409)" rule (defect #7
in the previous session) is **superseded**.

- `toggle_customs` accepts a merged set; status stays `merged` (never
  `blocked_customs` — that was the original reporting defect).
- New `merge.append_customs_to_merged_packet`: rebuilds the packet from all
  attached documents in legal order, temp file + atomic replace, `merged_at`
  untouched, idempotent, `audit_log.action = customs_append`.
- Upload route rebuilds once both CUSTOMS and SHIPPING are present on a merged
  set; toggle route rebuilds if both were uploaded while the requirement was
  off.
- Dashboard badge "Customs append pending" on merged sets with the requirement
  on and fewer than two documents; detail page says the upload rebuilds the
  packet.
- `AuditAction.customs_append` added (column is VARCHAR(20), no migration).
- `prefect.schedule_timezone` now defaults to `Asia/Kolkata` in
  `config.example.yaml` (IST midnight).
- PRODUCT §3, §6, §6.1 updated; `real arch v3.md` §32 added; tester guide and
  delivery runbook updated.

Tests: `test_customs_documents_can_be_appended_after_merge`,
`test_customs_docs_uploaded_before_the_requirement_is_switched_on_rebuild_on_toggle`,
`test_the_customs_toggle_is_allowed_on_a_merged_set`,
`test_toggle_allowed_on_a_merged_set_and_status_stays_merged`.

### Verified live on the real 83-set database

- Quarantine page: 18 rows, 18 reasons; fragment too.
- `?status=mismatched`: 3 rows with numbers ("delivered 400 of 4200").
- Detail pages: quarantine banner (set 7), needs-review banner + numbers (set 1).
- Dashboard nav badge 37 == holding area 37 ("33 waiting to be placed", 2 failed).

---

## What is NOT done / next steps
1. **Real NSSM install not exercised on this machine** (needs admin + Windows
   services). Scripts parse clean and the Prefect steps were verified against a
   real server, but the first client install should follow
   `docs/DELIVERY.md` §4 and check both services and the midnight deployment.
2. **Flaky test** `tests/test_concurrency.py::test_po_lock_timeout_releases`
   did not reproduce today (green in isolation and in two full runs). No root
   cause; watch it on the first full run on the client machine.
3. **`-pages-N` layout** still quarantines 5 sets (multiple PO docs per set) and
   causes page-boundary mismatches. Operator/input-naming issue.
4. **33 held documents** (PO-less DNs) still need a human to name their PO.
5. **Scheduled sync timezone** — set `prefect.schedule_timezone` in the client
   `config.yaml` before handover if they expect local midnight. Default is UTC.
6. **Push** — the three commits are local only. Push to
   `fix/mvp-high-priority` when asked; never merge to main/dev-simplified.
7. `EXTRACTION_PROMPT_AUDIT.md` is untracked user work — left alone.

---

## Key facts

- Branch `fix/mvp-high-priority`; 13 commits ahead of `dev-simplified`
  (`5ea8f53`) after this session.
- App running: `http://localhost:8000` (console Uvicorn on this dev box).
- DB: `data/aam_merger.db` — 284 docs, 83 sets, 616 line items, 33 held,
  2 failed. Fresh backup `data/backup/aam_merger_20261022_160548.db` (284/83).
- `.env` at repo root (dev key). The client gets a separate tester key; `.env`
  is gitignored and is not in the release zip.
- Git identity: Shawn <shawnachirappuram@gmail.com>.
- Do not run `git add -A` on the dirty tree.
