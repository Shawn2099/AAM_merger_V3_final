# Release Verification — AAM Merger V3 MVP (2026-10-23)

> **Superseded for the pilot release by
> [`docs/PILOT_RELEASE_2026-10-23.md`](./docs/PILOT_RELEASE_2026-10-23.md).**
> This file is the earlier core-product verification. Its Delete/`DELETE`
> wording and `b2c3d4e5f6a7` head predate the archive feature (`c3d4e5f6a7b8`).

**Scope:** full product, section by section, against a throwaway rig, a copy of
the live 284-document database, and real PDFs. The live database and `data/`
folder were not modified.

**Baseline:** 546 test functions across 48 files — full suite exit 0;
`ruff check .` clean; `ty check src` clean; all 8 PowerShell scripts parse
under Windows PowerShell 5.1.

**Method:** four independent audits (UI, extraction/ingestion, deployment,
concurrency) plus an end-to-end rig that drives ingestion → extraction (VLM
stubbed) → grouping → reconciliation → merge → HTTP routes, and a render pass
over every page using the real data.

---

## 1. Verdict

The **reconciliation core is sound** — no path was found that delivers a
packet whose quantities were not proven. The MVP is **ready for a fresh client
install** after the migration entry point below is used.

Two release blockers were found outside the core and are fixed in this tree:

1. **The existing database could not be upgraded by the documented command.**
   The live DB was created by `Base.metadata.create_all`, has no
   `alembic_version`, and is missing `documents.po_no_source`. A bare
   `alembic upgrade head` fails with `table po_sets already exists`; starting
   the app without migrating crashes on `no such column`.
   Fixed with `scripts/migrate_db.py` (detects the schema, writes a
   pre-migration backup, stamps the proven revision, upgrades, verifies).
   Verified on a copy of the live DB: 284 documents / 83 sets / 616 line items
   preserved, `integrity_check ok`, head `b2c3d4e5f6a7`.
2. **The documented install command left the worker in a crash loop and never
   registered the nightly sync.** It omitted `-WithPrefectServer`, so
   `PREFECT_API_URL` was unset; `prefect worker start` exits immediately and
   NSSM restarts it every 2 s. The installer now resolves the API URL before
   installing, refuses to install the worker without one, and `deploy_prefect.py`
   refuses to register into an ephemeral Prefect server.

---

## 2. Confirmed working (observed)

| Product area | Evidence |
|---|---|
| Ingestion / dedup | SHA-256 dedup; stored copies never deleted; stability poll; non-PDF, zero-byte and oversize rejected into `quarantine/_documents` with a reason; BOM-prefixed and junk-prefixed real PDFs accepted (`%PDF-` in first 1024 bytes). |
| Extraction contract | Schema fields match PRODUCT §4; `po_reference_ambiguous` survives; fail-closed without `OPENROUTER_API_KEY`; attempt cap honoured; stale `processing` reclaimed. Full pipeline run: 6 documents → 5 read, 1 failed and quarantined, non-PDF rejected, no re-read on the next run. |
| Grouping | PO mints one set; DN/SI never mint; ambiguous PO of every type is left for a human; missing PO is held, not failed; one open set per PO enforced by a partial unique index; real PO spellings collapse (`161538`, `PO 161538`, `PO, Rev # 161538,0`, `P.O.`, `P/0`, `LPO26/2360` vs `PO 26/2360`). |
| Reconciliation | PO 100 = DN 40+60 = SI 70+30 → `merged`. Partial delivery → `pending/partial_fulfillment`. Real disagreement → `mismatched`. Orphan line, zero quantity, two PO documents, empty PO, PO-reference drift → quarantine with a readable reason. Every reason code has dashboard text. |
| Merge | Packet order SI→DN→PO→SHIPPING→CUSTOMS verified by page geometry; named from the SI number, PO fallback flagged; collision fails closed; atomic temp+`os.replace`; corrupt/missing member → `packet_source_unreadable`, no raw exception; merged is terminal; input copies cleared; lock files no longer pollute `output/`. |
| Customs | Gate blocks before merge; both documents release it; append after merge rebuilds the same file, keeps `merged_at`, stays `merged`, writes `customs_append`; toggle OFF cannot remove pages; duplicate upload is idempotent; a file owned by another set returns 409. |
| Quarantine | Delete keeps stored files and writes an audit row with `po_set_id` nulled by `ON DELETE SET NULL`; a failed document is quarantined at document and set level; the documented recovery (name PO → Re-Extract) merges; a merged set is never re-quarantined by the background failure writer. |
| UI | All 9 templates render against real 284-doc data (83/83 detail pages, no Jinja errors); Save & Route is a real HTMX button; split parents cannot be attached; merged sets disable Re-Match/Re-Extract with 409; 409 messages are actionable; XSS-safe filenames; preview/download serve PDFs; action pages refresh after success; force-merge reason is visible; audit shows the operator justification; all assets local (no CDN, no Google Fonts). |
| Infra | Fresh DB `alembic upgrade head` clean; live DB copy stamp+upgrade clean; `migrate_db.py` idempotent; backup atomic, verified, collision-safe, WAL-aware; reset allow-list refuses drive roots/System32/project root/backup; config fail-fast and path resolution; Prefect `to_deployment(schedule=CronSchedule(...))` carries `Asia/Kolkata`; NSSM/uvicorn command boots and serves. |

---

## 3. Defects fixed in this verification pass

| # | Defect | Fix |
|---|---|---|
| 1 | Live DB not upgradeable by the documented command | `scripts/migrate_db.py`, wired into installer/run scripts; runbook updated |
| 2 | Installer installs a worker with no Prefect API → crash loop, no nightly sync | Resolve API before install, refuse without it, always pass it to the worker; `deploy_prefect.py` refuses ephemeral |
| 3 | Background failure writer could flip a merged set to `quarantined` (lost update), then the set could be deleted while its packet stayed on disk | Conditional `UPDATE … WHERE status NOT IN (merged, quarantined)` |
| 4 | A recovered (merged) set kept a stale "quarantined / could not be verified" sentence on the dashboard | Merge writes the merged sentence; naming note still appended |
| 5 | Stale-lock cleanup could clear a fresh lock (lost 409) | Cleanup release is conditional on the observed action and `locked_at` |
| 6 | Failed split parent was invisible in every view and could not be reclassified | Holding area includes failed/incomplete parents; reclassify recovers one with no children; completed parents still refused |
| 7 | A parent re-read as PO/DN/SI stayed permanently unplaceable | A non-COMBINED re-read of a never-completed split clears `is_split_parent` |
| 8 | Stale split children survived a refused re-split and were ingested as real documents | Child cleanup moved before validation |
| 9 | `P.O.`/`P/0` label entries were unreachable; `LPO` not a label; `161538 Rev 2` collided with PO `1615382`; `1,000` keyed as `1` | Raw leading-label strip + revision strip + stricter comma-revision rule |
| 10 | Quantity magnitude guard was 10× looser than PRODUCT §5 | Reject `> 1e12` |
| 11 | A BOM/junk-prefixed valid PDF was rejected | `%PDF-` anywhere in the first 1024 bytes |
| 12 | A re-read with no PO left the document attached, so drift was not checked | Detach for a non-manual read with no PO |
| 13 | A renamed duplicate of a COMBINED parent stayed in `input/` forever | Remove all input copies matching the parent hash after parking |
| 14 | `quarantine_document` said it removed the input copy but did not (direct callers) | Remove the hash-matching input copy inside the function |
| 15 | Detail page never refreshed after Merge Now / Force Merge / Customs / Re-Match / Re-Extract | `location.reload()` on a successful request |
| 16 | Force-merged set hid its own reason | Reason card shows for merged sets too ("Packet note") |
| 17 | Audit page hid the operator justification | Justification column added |
| 18 | Upload / manual-merge form errors replaced the page with raw JSON | Browser form posts redirect back with a notice; API clients keep JSON |
| 19 | Google Fonts was still fetched from the internet on a no-internet LAN | External font links removed from both pages; offline test extended |
| 20 | 422 toast dumped raw JSON; Save & Route could submit with no type | Parse `detail`; disable the button until a type is chosen |
| 21 | Force Merge on a merged set with a missing packet reported success | Refuses with the missing path |
| 22 | Sync lock leaked if the worker thread failed to start | Release on thread-creation failure |
| 23 | Recovery summary counted `extracted` as arithmetic, not the persisted row | Re-reads the document row |
| 24 | `.lock` files accumulated in `output/` beside delivered packets | Locks live in a sibling directory |
| 25 | `.env`/AAM_CONFIG_PATH divergence between backup and reset | `backup_db.py` honours `AAM_CONFIG_PATH` |
| 26 | Firewall rule allowed any profile/any address; work-pool name drifted | Scoped to Domain/Private + LocalSubnet; pool name read from config and passed to worker |
| 27 | A description-fallback match was invisible: the one place the engine guesses merged silently | Recorded as an advisory `description_match` flag (never a failure); dashboard shows an amber **Merged • review** pill, detail marks the affected lines, reason names them; status/merge outcome unchanged |

Regression tests: `tests/test_release_fixes.py` (13 tests),
`tests/test_migrate_db.py` (5 tests) and `tests/test_description_match.py`
(9 tests), plus updates to the existing UI, normalisation, split and dashboard
suites.

### Description-fallback review tag (client decision, 2026-10-23)

A vendor row may be attached to a PO line by description when no usable line
number exists. That is a guess, so it is now visible:

- the engine emits a `description_match` flag per fallback link (advisory, not
  an identification/quantity failure);
- a merged set keeps `merged` but the dashboard shows an amber
  **Merged • review** pill and `description match ×N`;
- the detail page marks the affected matrix rows and lists the exact vendor row
  and PO line;
- `reconcile_reason` names the lines so the warning survives;
- `po_sets.description_match_count` stores the count for the list view.

Exact-number matches stay green and untagged. The tag never changes the
verdict; it tells the CA where to look.

---

## 4. Remaining risks (accepted or requiring a human)

1. **Live VLM extraction was not exercised in this pass.** The `.env` in this
   tree holds a placeholder, and no real vendor PDFs are committed. The
   historical DB shows 282/284 documents extracted successfully with the same
   prompt, so the path is proven in principle; the first client run must be
   watched. `EXTRACTION_PROMPT_AUDIT.md` lists quality issues that remain
   (5 false "ambiguous" replacement notes, 14 non-item PO pages, step-10
   numbering) — all fail-safe quarantines, not wrong merges.
2. **A real NSSM service install was not performed** (needs admin). Scripts
   parse, the uvicorn command boots and serves, and the Prefect deployment was
   registered against a real local server. Follow `docs/DELIVERY.md` §4 and
   check both services plus `prefect deployment inspect aam-midnight-sync`.
3. **A crash between packet write and DB commit** can leave a complete,
   unowned packet; the next attempt quarantines as `packet_naming_failed` and
   an operator must move the file. Fail-closed, manual recovery.
4. **A hard-killed sync** makes `/sync/status` report running for up to one
   hour while the OS lock is already free (the button stays disabled;
   `POST /sync` would succeed). Availability only.
5. **`server.port` in `config.yaml` is not the bind port** — the installer
   `-Port` parameter is. Documented.
6. **`max_concurrent_extraction_tasks` is not enforced** (extraction is
   serial) — documented in PRODUCT §10.
7. **No authentication** — accepted LAN risk; the audit log is accountability,
   not access control.

---

## 5. Client handover commands

Fresh install (single host):

```powershell
.\scripts\setup_test_rig.ps1 -OpenFirewall -ScheduleBackup -WithPrefectServer
```

Existing database (this repo's validated 284-doc DB, or any `create_all`-era
DB):

```powershell
.\scripts\backup_db.ps1 -RetentionDays 30
python scripts\migrate_db.py
```

`migrate_db.py` prints the stamped revision and refuses to report success
unless the columns the code needs are present. Run it before starting the web
service after any upgrade.
