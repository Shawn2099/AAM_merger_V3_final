# Audit Resolution — AAM Merger V3 — 2026-10-23

This is the per-finding record requested after the audit. It maps **every**
finding in `AUDIT_2026-10-23.md` to the fix that landed and the regression
test(s) that now pin it. No new product features were added.

**Rule preserved:** one PO + n DN + n SI, compared line-by-line on **quantities
only**, exact scaled integers; `PO = ΣDN = ΣSI`; description is the identity
only when a line number is absent; all documents share one normalized PO key.
Price, SKU/part number and UOM never decide a verdict.

---

## P0 — merge-blocking findings

| ID | Finding | Fix | Regression evidence |
|---|---|---|---|
| P0-1 | `<form>` nested inside `<tr>` left Save & Route without a form owner | Save & Route is a `type="button"` with `hx-post` and `hx-include="closest tr"`; the row is the form scope | `tests/test_dashboard.py::test_unclassified_save_and_route_has_a_form_owner` |
| P0-2 | Prefect deployment passed a `CronSchedule` to `cron=` and failed validation; unset timezone ran UTC | `_schedule` always returns `CronSchedule`; `to_deployment(schedule=...)`; unset defaults to `Asia/Kolkata` | `tests/test_prefect_schedule.py` (5 tests) |
| P0-3 | Split parent appeared in the holding area and could be reclassified | `_holding_area_query` excludes `is_split_parent`; reclassify returns 409 | `test_audit_merge_ui.py::test_split_parent_is_not_in_the_holding_area`; `test_dashboard.py::test_reclassify_refuses_a_split_parent` |
| P0-4 | Ambiguous DN/SI could attach to whichever PO the model named first | `group_document` refuses every PO-bearing type with `ambiguous_po_number` | `test_audit_merge_ui.py::test_ambiguous_dn_is_not_attached` |
| P0-5 | Merge could write a truncated/0-page packet or crash on a corrupt source; no named quarantine | Unique temp + `os.replace` + output `FileLock`; 0-page, missing, corrupt and encrypted sources raise named errors and quarantine; no partial output | `test_audit_merge_ui.py::{test_zero_page_packet_is_refused,test_corrupt_source_writes_no_packet,test_zero_page_sources_quarantine_as_unreadable,test_allow_missing_skips_a_corrupt_member}`; `test_untested_engine_paths.py::test_missing_stored_pdf_at_merge_time_writes_nothing` |
| P0-6 | Filename interpolated into Alpine/JS could break out of an attribute | Filenames travel in escaped `data-preview-url` / `data-preview-name`; JS reads `dataset` | `test_dashboard.py::test_document_filename_is_escaped_not_interpolated_into_js` |

## P1 — correctness and immutability

| ID | Finding | Fix | Regression evidence |
|---|---|---|---|
| P1-1 | Detail matrix compared each raw PO row to summed vendor totals | One row per engine comparison key, built from `compare_po_set_lines` totals | `test_audit_merge_ui.py::test_matrix_uses_one_row_per_comparison_key` |
| P1-2 | Customs/shipping badges used `customs_doc_count` | Membership in the attached document types | `test_dashboard.py::test_customs_badges_use_attached_types_not_the_count` |
| P1-3 | Age used naive SQLite timestamp as local time | `_age_seconds` normalizes naive values to UTC | `test_audit_merge_ui.py::test_age_seconds_treats_naive_db_timestamps_as_utc` |
| P1-4 | Quarantine delete moved a same-named file with different bytes | Match on SHA-256 only | `test_audit_merge_ui.py::test_quarantine_delete_keeps_a_same_named_file_with_different_bytes`; `test_mvp_high_priority.py::test_delete_quarantined_clears_the_input_folder_copy` |
| P1-5 | Re-Extract/Re-Match mutated a delivered set | Both return 409 on `merged`; buttons disabled | `test_audit_pipeline.py::test_redo_extract_on_a_merged_set_is_refused` |
| P1-6 | Customs rebuild ran after releasing the per-PO lock; fixed temp name | Lock held through rebuild; unique temp name inside `_write_merged`; reclassify takes the lock too | `test_dashboard.py::test_customs_rebuild_runs_while_the_per_po_lock_is_held`; concurrency tests |
| P1-7 | Re-extraction left stale SI/DN/PO/ambiguity fields | Every read resets derived fields; a manual `po_no_source="manual"` survives and resolves ambiguity | `test_audit_pipeline.py::{test_a_second_read_clears_stale_numbers_and_ambiguity,test_a_second_read_preserves_an_operator_set_po}` |
| P1-8 | Multiple SI documents named the packet arbitrarily | SIs ordered by document id; first number names the packet; result carries a naming flag listing every number | `test_audit_merge_ui.py::{test_multiple_si_numbers_are_deterministic,test_multiple_si_numbers_are_flagged_on_the_result}` |
| P1-9 | An unreadable input file was retried forever | Moved to `quarantine/_documents` with a `.reason.txt` before any row exists | `test_audit_pipeline.py::test_an_unreadable_input_file_is_moved_to_quarantine` |
| P1-10 | No CSRF/Origin/Host protection | Same-origin guard on POST/PUT/PATCH/DELETE; optional `TrustedHostMiddleware`; documented residual no-auth risk | `test_audit_config_security.py` (Origin/Referer/Host tests) |
| P1-11 | A zero-line set was left silently pending | Quarantines with `po_document_has_no_line_items` / `no_line_item_evidence`; no silent pending | `test_layer2_gaps.py::test_merge_refusal_restores_prior_status`; zero-page/empty tests |

## P2 — hardening and deployment

| ID | Finding | Fix | Regression evidence |
|---|---|---|---|
| P2-1 | Force Merge left the old mismatch reason on a merged row | Force merge overwrites `reconcile_reason` with a truthful "Force-merged by operator…" sentence | `test_audit_merge_ui.py::test_force_merge_writes_a_truthful_reason` |
| P2-2 | Force Merge could write a 0-page packet | `_write_merged` refuses 0 pages and raises `MergeEmptyError` with `reason_code="packet_source_unreadable"` | `test_audit_merge_ui.py::test_zero_page_packet_is_refused` |
| P2-3 | Route-initiated merges never cleared `input/` | Successful auto, force and route merges call `delete_input_files` for the set | `test_audit_merge_ui.py::test_merge_clears_input_copies`; `test_best_case_pipeline.py` |
| P2-4 | Two open sets per PO key; quarantine folders collided | Unique partial index `uq_po_sets_open_po_no`; `get_or_create_po_set` recovers from the insert race; quarantine folder adds the set fingerprint on collision | `test_audit_merge_ui.py::{test_unique_open_set_index_refuses_a_second_open_set,test_get_or_create_po_set_is_idempotent,test_get_or_create_po_set_recovers_from_an_insert_race,test_quarantine_folders_do_not_collide_when_a_po_number_is_reused}` |
| P2-5 | Missing FK indexes | Added `ix_documents_po_set_id`, `ix_line_items_document_id`, `ix_audit_log_po_set_id` | `test_audit_merge_ui.py::test_foreign_key_and_open_set_indexes_exist`; migration on live copy |
| P2-6 | Retired `COMMERCIAL_INVOICE` could raise `LookupError` | Migration `a1b2c3d4e5f6` updates those rows to `UNKNOWN` | Alembic upgrade verified on a copy of the live DB; migration file is the evidence |
| P2-7 | Backup overwrite, no integrity check; reset cleared arbitrary paths | Microsecond names + counter; `.partial` + integrity check + `os.replace`; reset allow-list protects `data/` and the backup | `test_audit_scripts.py` (collision, integrity, retention, reset guard) |
| P2-8 | Every 409 was reported as "locked" | JS parses the JSON `detail`; lock wording only for lock messages | `test_audit_frontend.py` (3 tests) |
| P2-9 | "Normalized:" preview used its own regex | Preview calls `/unclassified/normalize`, the server normalizer | `test_dashboard.py::test_unclassified_normalize_uses_the_server_normalizer` |
| P2-10 | Search claimed vendor filtering and reset on poll | Removed vendor claim; filter re-applied after the HTMX swap | `test_audit_frontend.py::test_search_is_reapplied_after_the_htmx_poll`; `test_dashboard_no_longer_promises_vendor_filtering` |
| P2-11 | Unconditional commit on every 2 s poll; schema creation in routes | Release commits only when a lock is cleared; schema creation added to `lifespan`; config cached | `test_audit_config_security.py::test_load_config_is_cached_but_returns_independent_copies`; full route tests |
| P2-12 | Invalid `?status=` showed an empty "All statuses" table | 422 for unknown status on both dashboard routes | `test_dashboard.py::test_invalid_status_filter_is_422` |
| P2-13 | File serving trusted DB paths; `/health` leaked paths | `_served_file` confines to configured roots; `/health` is liveness only | `test_audit_merge_ui.py::test_served_file_rejects_a_path_outside_its_root`; `test_audit_config_security.py::test_reads_are_not_blocked_by_an_origin` |
| P2-14 | Customs-off upload copy implied a rebuild | Copy now states the packet is not rebuilt when the toggle is OFF | Template diff `templates/po_set_detail.html`; UI reviewed |
| P2-15 | Manual merger used `innerHTML`; unbounded upload | `createElement`/`textContent`; 20-file and total-byte caps | `test_audit_frontend.py::test_manual_merger_builds_rows_without_innerHTML`; config-security cap tests |
| P2-16 | Config typos ignored; `combined_folder` defaulted to CWD | `extra="forbid"` on every section; all relative `paths:` values resolve against the config file's directory | `test_audit_config_security.py` config tests; `test_load_config...` |
| P2-17 | All-SKIP COMBINED retired silently | `no_document_pages` split failure; parent becomes failed and is quarantined | `test_audit_pipeline.py::test_an_all_skip_split_fails_and_quarantines_the_parent` |
| P2-18 | Re-split left stale child PDFs | Removes `<sha16>_p*.pdf` before cutting the new decomposition | `test_audit_pipeline.py::test_a_resplit_removes_stale_child_files` |
| P2-19 | `unit_price` parsed with the quantity parser | Dedicated `parse_price_scaled`; supports fractions, negatives, commas | `test_audit_merge_ui.py::test_price_parser_keeps_fractional_and_negative_rates` |
| P2-20 | Ingestion/VLM fields had no bounds | `ge=1` bounds; attempt-budget validator; positive timeout/poll fields | `test_audit_config_security.py` bounds/validator tests |
| P2-21 | Re-Extract could double-extract during sync | Refuses with 409 while `_is_sync_running()` | `test_audit_pipeline.py::test_redo_extract_refuses_while_a_sync_is_running` |
| P2-22 | Killed task reported as success on retry | Stale `processing` claim is reset and re-entered; live claim is left alone | `test_audit_pipeline.py::{test_a_stale_processing_claim_is_reclaimed_and_read,test_a_live_processing_claim_is_left_alone}` |
| P2-23 | Input sweep accepted any `*.pdf` | `%PDF-` magic + upload-size cap before a row is created; rejected file moved to quarantine with reason | `test_audit_pipeline.py` (non-PDF, oversized, real PDF) |
| P2-24 | Polling re-read YAML and re-ran `mkdir`s | Config cached by file/env stat signature; deep-copied to callers; dirs created on first load/startup | `test_audit_config_security.py::test_load_config_is_cached_but_returns_independent_copies` |
| P2-25 | `max_retries > 2` had no effect | Attempt cap derives from `extraction.max_retries` (default 3) and is validated 1..5; stale-timeout budget uses the same value | `test_audit_pipeline.py::{test_attempt_cap_follows_extraction_max_retries,test_default_attempt_cap_remains_three}`; config tests |

## Additional root-cause fixes found during verification

- **Unique-index insert race** — added the `IntegrityError` recovery test
  (`test_get_or_create_po_set_recovers_from_an_insert_race`) so a concurrent
  sync/reclassify joins the winning set instead of surfacing a 500.
- **0-page source reason** — `MergeEmptyError` now carries a `reason_code`;
  a source with no readable pages quarantines as `packet_source_unreadable`,
  not the misleading `no_line_item_evidence`.
- **Sync summary undercount** — `flows/sync.py` now counts `extracted`
  explicitly, so an input rejected before a row exists is counted as an error
  without reducing the number of documents actually extracted.
- **Route merge input cleanup** — `merge_po_set` and `force_merge` now clear
  the set's input copies, not just the nightly sync.
- **Test-fixture root cause** — several tests used `PdfWriter().write(...)`
  with zero pages as a "valid PDF"; the new 0-page refusal exposed them. They
  now write a real one-page PDF, so the tests exercise merge behavior rather
  than the empty-packet guard.
- **All relative config paths** — resolved against the config file, not the
  service working directory (`C:\Windows\System32` under NSSM).

## Verification evidence (this run)

- **785 tests collected; full suite green** (`pytest`).
- **Coverage: 90%** (`3019` statements, `271` missed, `872` branches, `112`
  partial). Every function in `src/app` is executed by at least one test;
  there are **zero** zero-coverage functions and no module below 75%.

### Per-module coverage (function-level run)

| Module | Coverage | Module | Coverage |
|---|---:|---|---:|
| `core/database.py` | 100% | `services/locking.py` | 100% |
| `models/models.py` | 100% | `services/retry.py` | 100% |
| `services/matching.py` | 98% | `core/config.py` | 95% |
| `services/grouping.py` | 95% | `main.py` | 94% |
| `services/quarantine.py` | 94% | `services/reconciliation.py` | 94% |
| `services/customs.py` | 93% | `services/merge.py` | 92% |
| `services/sanitizer.py` | 92% | `services/splitting.py` | 92% |
| `services/sync_lock.py` | 88% | `api/routes/dashboard.py` | 87% |
| `services/extraction.py` | 83% | `api/routes/manual_merger.py` | 83% |
| `flows/sync.py` | 81% | `services/ingestion.py` | 81% |
| `api/routes/po_sets.py` | 81% | | |

- `ruff check .` — clean. `ty check src` — clean.
- **GitNexus:** index refreshed to 2,253 nodes / 4,605 edges / 103 flows;
  `check` reports **0 cycles, 0 components**. `impact` identifies
  `_reconcile_po_set_inner`, `merge_po_set`, `get_or_create_po_set` as HIGH and
  `load_config` as CRITICAL (centrality, not defects); the affected flows are
  exactly the ones covered by the full suite. `detect_changes` was run before
  the re-index and its findings were reviewed.
- **Alembic:** `f7a8b9c0d1e2` (po_no_source) and
  `a1b2c3d4e5f6` (indexes + open-set uniqueness + `COMMERCIAL_INVOICE` cleanup)
  applied successfully to a **copy** of the live DB. The live DB was not
  modified.
- **Core business invariants exercised:** PO 100 = DN 40+60 = SI 30+70 →
  merged; unequal quantity → mismatched; extra vendor line → quarantined;
  price/description differences with matching line numbers → merged; multiple
  PO documents → quarantined; customs OFF → merged; customs ON → blocked;
  CUSTOMS + SHIPPING append → packet rebuilt; force merge audited; merged set
  immutable; duplicate PO after merge creates a separate set.

## Residual items (not code defects)

1. **Revision normalization is still a business decision** (`161538 Rev 2`
   vs `161538,2`). No policy was invented.
2. **External deployment checks not run here:** a real NSSM service install
   and a real `deploy_prefect.py` apply against the office Prefect server.
3. **Existing live DB has no `alembic_version`** because it was created with
   `create_all`. Before deploying this tree: back up, stamp to
   `e6f7a8b9c0d1`, then `alembic upgrade head`.
4. **Real browser pass** is replaced by rendered-page assertions plus the
   Node-executed JS tests; a final human click-through is still recommended
   before the first live delivery.
