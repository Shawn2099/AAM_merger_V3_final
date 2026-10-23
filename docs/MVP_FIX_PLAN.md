# MVP blocker fix plan (branch `fix/mvp-high-priority`)

Written 2026-10-23. Source: fresh-eyes code review, then an Opus plan that verified each
claim against the code. All three blockers were confirmed. Test DB data is disposable.

## Decisions (user)

- A set is formed ONLY by PO, DN and SI documents (customs/shipping are the separate
  add-ons). An UNKNOWN/COMBINED document never attaches to a set unless an operator
  reclassifies it. (Settles plan decision A2.)
- Manual merger is out of scope for these commits; its files stay uncommitted.
- Correctness first; no new features. Ambiguity means quarantine or ask the user.

## 1. Verdicts

| # | Verdict | Evidence |
|---|---|---|
| A1 | Confirmed | `reconciliation.py` ~492-496 checks only that the whole pool has lines. An empty DN beside a full one is packed unchecked. |
| A2 | Confirmed; remedy was misplaced | No attach path filters by type (`group_document`, `attach_unattached_to_open_sets`, `resolve_unattached_documents`); PO-reference check (~375) skips UNKNOWN. Guard belongs in reconcile, NOT `_ordered_docs` (`force_merge` uses it and may legitimately pack UNKNOWN). `test_layer2_gaps.py:295` stays unchanged. |
| A3 | Confirmed | Reconcile never reads `extraction_status`; a failed re-read keeps old lines (`extraction.py:626` deletes old lines only on success). Concrete path: `redo_extract` (`po_sets.py` ~487-490). |
| A4 | Confirmed | `merge_po_set` opens a new session and re-reads `ps.documents`; Merge Now does not check `_is_sync_running`, so sync attach sweeps can hit the window too. |
| B | Confirmed | Toggle and reclassify swallow a failed customs rebuild; nothing persisted. Upload reports honestly but also persists nothing. |
| C | Confirmed, worse than reported | Prefect 3.8.3 records deploy-time cwd as `path` with no pull steps. The process worker copies the whole tree (incl. `data/` and the SQLite DB) into a temp dir and runs there. With default `./data` paths every scheduled run works on a throwaway copy: silent loss. |
| Re-Match/Re-Extract "no rollback" | Mostly resolved by A3 | A still-broken set re-quarantines or pends with a fresh reason. No rollback to build. |

## 2. Fix designs

**Guard order (pin with a test).** In `_reconcile_po_set_inner`, new guards run before the
missing-side checks: (a) validity, (b) unclassified, then existing checks.

**A3.** For every attached non-CUSTOMS/SHIPPING doc: any `failed` -> `quarantined`,
`document_extraction_failed`; any `pending`/`processing` -> `pending`,
`document_not_yet_read` (next sync's pending recovery picks it up). Detail names the files.

**A2.** (b) Any attached doc whose type is not PO/DN/SI/CUSTOMS/SHIPPING -> `quarantined`,
`unclassified_document_in_set` (defence for legacy rows). Prevention: `_ATTACHABLE`
allowlist in all three grouping paths so UNKNOWN stays in Unclassified; PO number stays on
the row. Update CLAUDE.md "Anchoring" and CORE_LOGIC §2 in the same commit.

**A1.** `empty = [d for d in vendor_docs if not d.line_items]`, name only those files; keep
reason code `vendor_document_has_no_line_items`.

**A4.** `merge_po_set(..., expected_doc_ids: frozenset[int] | None = None)`. Inside
`_output_lock`, just before writing, run a fresh `select(Document.id)` for the set
(`archived_at IS NULL`); if it differs raise `MergeStaleError`. Reconcile passes
`{d.id for d in docs}`, catches the error, returns `pending` with reason
`documents_changed_during_merge`. Defer "take the lock first" in reclassify
(`acquire_lock` commits the caller's session; needs a restructure).

**Checklist for every new reason code:** `REASON_TEXT`, `_DETAILED_REASONS` (for
`document_extraction_failed`, `unclassified_document_in_set`), PRODUCT §3.1 row,
CORE_LOGIC §4 row, a `test_business_scenarios_ui` scenario asserting page text.

**B.** Add `po_sets.customs_append_pending BOOL server_default 0` (Alembic revision, marker
in `scripts/migrate_db.py` map, update `tests/test_migrate_db.py`). Rejected: deriving it
from audit timestamps (timezone fragility, false positives).
- Set on a merged set with toggle on and customs count 2: `toggle_customs` route, upload
  `rebuild_flag`, reclassify attaching CUSTOMS/SHIPPING to a merged set.
- Clear inside `append_customs_to_merged_packet` (same commit as the audit row) and when
  the toggle is switched off.
- Retry: new step after Phase 2 in `_recovery_flow_locked` (Phase 2 excludes merged sets).
  Query merged, non-archived sets with the flag; call `append_customs_if_complete` under
  the per-set lock (`sync_customs_append`).
- Toggle response: `{packet_rebuilt:false, append_pending:true, append_error:<scrubbed>}`,
  still 200. `app.js:146` shows a warning toast: "Customs requirement saved, but the merged
  packet was NOT rebuilt: ... It will retry on the next sync."
- Reclassify: failure redirects with that notice instead of only logging.
- Badge: `merged and has_customs_toggle and (customs_doc_count < 2 or customs_append_pending)`
  -> "Customs append pending". Detail page: "Merged • customs not yet in packet".

**C.** `deploy_prefect.py`: derive `ROOT` from `__file__`, `os.chdir(ROOT)` before
`to_deployment`; pass `job_variables={"working_dir": str(ROOT), "env": {"AAM_CONFIG_PATH":
str(ROOT/"config.yaml")}}` and `parameters={"cfg_path": ...}` (working dir == `path`, so no
copy). Add `AAM_CONFIG_PATH` to installer `$extraEnv`. DELIVERY.md and DEPLOY.md:
`Set-ExecutionPolicy -Scope Process Bypass` and `Get-ChildItem -Recurse *.ps1 | Unblock-File`.

**Existing merged sets never reopen.** Ship a read-only query listing merged sets with an
attached UNKNOWN/COMBINED doc, a 0-line DN/SI, or a PO/DN/SI that is not `valid`; those
packets need manual review. Upgrade note: open sets with failed/UNKNOWN docs flip to
quarantined on first sync after upgrade.

## 3. Tests (new file `tests/test_mvp_blockers.py`)

Port the scratchpad repros with inverted assertions (drop the hard-coded `sys.path` line):
`test_doc_attached_during_merge_refuses_and_pends`, `test_attached_unknown_quarantines_set`,
`test_empty_dn_beside_full_dn_quarantines`, `test_failed_doc_lines_never_reconciled`.

Add: `test_pending_doc_holds_set_pending`,
`test_redo_extract_failure_does_not_merge_stale_lines`,
`test_unknown_with_po_no_is_not_attached_by_any_sweep`,
`test_guard_order_validity_and_unknown_before_missing_side`,
`test_customs_append_failure_is_persisted_and_retried_by_sync` (port of `repro_customs.py`),
`test_toggle_reports_append_failure`, `test_badge_shows_when_append_pending`,
`test_deployment_sets_working_dir_and_config_path` (in `test_prefect_schedule.py`), the §8
`test_known_limitation_*` tests, and one business-scenario page-text test per new code.

Churn: `extraction_status` defaults to `pending`, so factories omitting it now yield pending
sets. Default them to `valid`: likely `test_mvp_high_priority._add_doc`,
`test_audit_pipeline._add_doc`, `test_prop_reconcile`, `test_per_line_dn_association`,
`test_reclassify_scope`, `test_layer2_gaps._doc`. Update `test_migrate_db` for the column.

## 4. Commit order

Run `node .gitnexus/run.cjs impact <sym> --direction upstream --repo .` before editing each
symbol; `detect-changes --scope all` before each commit; name files explicitly.

1. Failing repro tests, xfail strict (Haiku, low).
2. A3 + A1 + guard order; symbols `_reconcile_po_set_inner`, `REASON_TEXT`,
   `_DETAILED_REASONS`; fix factories (Opus, high).
3. A2: `group_document`, `attach_unattached_to_open_sets`, `resolve_unattached_documents`,
   reconcile guard; update CLAUDE.md and CORE_LOGIC (Opus, medium).
4. A4: `merge_po_set`, `MergeStaleError`, reconcile catch (Opus, medium).
5. B: `POSet` model, migration, `append_customs_to_merged_packet`, `toggle_customs`,
   `reclassify_document`, `_upload_manual_doc_impl`, `_recovery_flow_locked`, template,
   `app.js` (Opus, high).
6. C: `deploy_prefect.deploy`, installer env, docs (Haiku with review, medium).
7. Small fixes (Haiku, low): quarantine table `hx-trigger="every 3s
   [!document.querySelector('#quarantine-table .modal-backdrop.open')]"`;
   `install_nssm_services.ps1:168` and `uninstall_nssm_services.ps1:57` wrap the `2>$null`
   call in a local `$ErrorActionPreference='Continue'`; `reset_data.ps1` calls the backup
   script with `-RetentionDays 0`.
8. Docs: PRODUCT §3.1 and §8 plus pinned limitation tests (Haiku, low).

## 5. Deferred (each needs a PRODUCT §8 entry + `test_known_limitation_*`)

- M: provider-outage circuit breaker (401/402 burns a full run); extend existing entry.
- M: Re-Extract can overwrite an operator's doc_type (fails safe once guard (b) exists).
- L: reclassify attaches before taking the lock (small residual window after A4).
- M: DN-number match has no vendor check (`test_known_limitation_dn_number_collision_attaches`).
- L: missing audit rows (toggle, reclassify, redo_*, upload); `allowed_hosts '*'`; manual
  Scan needs the Prefect server; input copy deleted when quarantine copy fails (stored copy
  survives); sanitizer accepts `1e2` and `1_000`.

## 6. Final gate

- `uv run pytest -q > full.txt` and `uv run pytest tests/test_business_scenarios_ui.py -q > bl.txt`
  (Makefile `/tmp` cache breaks `make` on Windows)
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check src`
- `uv run python scripts/migrate_db.py` on a copy of the rig DB
- Re-run `deploy_prefect.py`; confirm deployment `working_dir` and `path` equal the repo;
  `prefect deployment run "sync-flow/aam-midnight-sync"`; confirm real DB row counts and
  output packets changed and no `*prefect` temp copy exists.
- Run the merged-set review query.

## Status (update this as work lands)

- 2026-10-23 DONE (local + pushed): plan doc `22dd016`; A3/A1 reconcile guards `6b00f9c`;
  A2 grouping allowlist `42a046d` (+ `tests/test_mvp_blockers.py`, 19 tests; suite green).
- NEXT, in order (nothing started on these):
  1. A4 merge snapshot guard (section 2 "A4").
  2. C Prefect `working_dir` + `AAM_CONFIG_PATH` + execution-policy docs, and the small
     script/UI fixes (commit order items 6-7). Independent of A4; may run in parallel.
  3. B persisted `customs_append_pending` + sync retry + honest UI (after A4).
  4. PRODUCT §3.1/§8 docs + `test_known_limitation_*` for the deferred list.
  5. Final gate (section 6), including a real `prefect deployment run` on the rig.
- Working method: Sonnet/Haiku subagents implement test-first, Opus reviews; run GitNexus
  impact before editing a symbol and detect-changes before commit; name files in commits;
  redirect pytest output to a file and use `--no-cov` (piping hangs). The PRODUCT.md has
  unrelated uncommitted manual-merger hunks: stage only your own hunk (`git apply --cached`).
- Manual merger files are uncommitted work in progress by the user: do not commit them.
  Scratch/audit files at repo root are never committed.
