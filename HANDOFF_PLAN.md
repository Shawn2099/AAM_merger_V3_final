# HANDOFF + LIVE PLAN — AAM Merger V3 (hardening phase)

Living file. Any agent, model or session: read this first, then `CLAUDE.md`, `docs/CORE_LOGIC.md`,
`AAM_merger_V3_PRODUCT.md`. Update the "Status" and "Log" sections whenever you finish a chunk, and commit it.
Older state: `SESSION_HANDOFF.md` (2026-10-22, delivery readiness; still valid for deploy facts).

Last updated: 2026-10-24. Branch `fix/mvp-high-priority`, remote `origin` (push the branch; never force-push; never push main).

> **2026-10-25: the forward plan, the full decision log (D1-D13), open questions Q1-Q6 and the delegation packets (WS0-WS9) live in `docs/DEPLOYMENT_PLAN.md`. Operating model: Opus architects/reviews, DeepSeek via OpenCode does token-heavy work.**

## 1. Rules of the game (from the user)

- **Precision over recall.** Whatever is merged must be absolutely correct. Doubt about ONE document → Unclassified
  review page (operator types doc type / PO number in the UI). Wrong or inconsistent PO SET (multiple POs, over-billing,
  mismatch, letter conflict, duplicate DN/SI) → quarantine; whole-set quarantine is fine.
- **Normal-flow scope:** every input PDF = one document type + one PO. COMBINED files, multi-PO files and per-page
  fragments of one document are a **separate future module** (splitter stays off: `split_combined: false`). Do not bend
  normal-flow rules for them.
- **No new features, no extra API calls (e.g. no second read)** until the user decides. Fix bugs, report issues in md files.
- Discuss before building anything new. Ask before the real-data run if it was not already authorised.
- Commit and push regularly: by NAMING files (never `git add -A`; tree has user WIP: manual_merger, `scratch_*`,
  `batch_*_findings.md`, `prompt_improvement/`, other `*_2026-10-2x.md` reports unless told). Mixed files: only our hunks.
- Model tiers (CLAUDE.md): Haiku mechanical, Sonnet implementation, Opus review/plans; pass `model` explicitly.
- Quantities are integers scaled ×1000. Windows: use `uv run` directly; config paths use forward slashes.

## 2. What the system does now (decisions already implemented)

- Extraction: pages as **150 DPI images** (pymupdf), `reasoning.effort=high`, generic prompt P5, model `openai/gpt-6-luna`
  via OpenRouter. config: `vlm.raster_dpi`, `vlm.reasoning_effort`, `vlm.max_pages`, `request_timeout_seconds: 180`.
- PO grouping key = the **digits** of the PO number (after revision stripping). Letters are a safety check only
  (different letters on both sides → no grouping; letters differing inside one set → quarantine `po_reference_mismatch`).
  No key (held on Unclassified): no digits, ambiguous Rev, confusable letter (O/I/L/S/B/Z) inside the digit run
  (`45OO123`), non-ASCII digits. Fresh DB required (old keys not migrated): `uv run python scripts/migrate_db.py`.
- Reconcile guards: PO == DN aggregate == SI aggregate per line; `single_po_document`; letters guard;
  **`duplicate_document_number`**: two DNs (or two SIs) with the same document number AND an overlapping (or missing)
  line number are quarantined (a re-scan would double-count); pages of one document with distinct lines still sum;
  an empty same-number totals page is covered.
- Robustness fixes done: R-01, R-14, R-12, R-10 (never delete the input unless a copy exists; missing stored copy is an
  infrastructure fault), R-04 (stat failure skips one file), R-02 (absurd unit price: resolved by removing unit_price entirely; DB error ends as counted failure),
  R-05 (transient read error leaves the file for next sync), R-11.

## 3. Status (2026-10-24)

- **Cards done (evening 2026-10-24):** T1 (suite green), T2 quarantine report/filenames/audit row (12d4950), T3 config + DELIVERY
  (3f954b1), T4 Prefect test-server cleanup (3f954b1, conftest fixture), T5 R-03/R-06/R-16 (938188f), T11 licence note (in
  DELIVERY, decision still the client's), T12 manual merger committed after Opus review (ea2f511; review defects 1+2 fixed).
  Repair commit 6bb1384: 3586491 had been pushed with SYNTAX ERRORS in config.py, quarantine.py and a misplaced
  `line_number_fallback` block in config.example.yaml (partial-hunk staging). Fixed. **Lesson: after staging hunks, always
  `git checkout-index -a --prefix=<tmp>/` and run the tests against that tree before committing.**
- **T6 done except two decisions:** R-08, R-09, R-15, R-18 fixed (bf7a294). Opus review of 6bb1384/12d4950/938188f found the R-06
  breaker quarantining before the trip, document-specific 400s able to halt every night, shared breaker state, rejected rows
  reclassifiable/previewable/uploadable, and unsafe quarantine filenames: all fixed in bf7a294 (see PRODUCT section 8 R-06 entry).
  `make business-logic` files: 183 passed. Full suite at bf7a294: 6309 passed, 6 skipped, 46 xfailed.
- **T8 UI accuracy audit (2026-10-24, on a COPY of the migrated live DB, GET-only, the real app via TestClient):** 7 pages + 57 set
  pages rendered; 805 independent checks (DB and files vs what the page shows: every dashboard row's PO/status/reason/doc count/PDF link, KPI cards,
  tab counts, nav badges, per-set matrix totals vs line_items, attached-document tables, Unclassified and Quarantine lists, merged packet page
  counts vs their source PDFs) -> all pass. 200/200 document previews and 12/12 merged downloads serve the exact file. One real gap found and
  FIXED: pending sets hid vendor lines that match no PO line (sets 14, 22, 23, 52: quantities in the DB shown nowhere); they are now listed as
  "Not on the PO" notes. Not covered: browser-side behaviour (Alpine/HTMX, drawer, buttons: the opt-in Playwright tests are skipped) and
  visual layout; do a 10-minute click-through on the target PC. Known display quirks: qty-0 numberless PO rows read `0 0 0 Match`; sets whose
  stored status predates the line-number fallback (e.g. 48) show a stale status next to a live matrix that now matches (clears on a re-run).
- **R-17 decided (2026-10-24, second answer):** subfolders ARE read now; zero-byte files stay skipped (a future module may use them).
- **Still open (T13 build D1; T14 done):** T7 retest corpus + fresh-DB run (uses the paid provider; needs the user's go-ahead), T8 operator UI
  walk-through, T9 cleanup (list below, user decides), T10 final branch review + gitnexus refresh (`node .gitnexus/run.cjs analyze`).
- **Decisions made by the user (2026-10-24 night):** R-07 -> a packet-write I/O failure leaves the set `pending` and is retried
  (implemented); R-17 -> subfolders are read, zero-byte files skipped (implemented, PRODUCT section 8);
  licences (pymupdf AGPL) -> later; the real-corpus retest runs LAST, on a NEW DB, once all development and testing are done and
  the assistant is confident; scratch cleanup approved and done (T9: files archived to `Desktop/aam_merger_scratch_backup_20261024.zip`
  then deleted; the four local data folders and `scratch_*` are now in `.gitignore`).
  Still undecided: R-13 (only with `split_combined: true`), I-1 second read, "1 of 6" PO pages, extraction validation (section 7).
- **Live DB `data/aam_merger.db` was migrated** to e8f9a0b1c2d3 on 2026-10-24 (backup `data/backup/aam_merger_premigrate_20261024_*.db`).
  The real retest still uses a NEW DB, as decided.
- **History:** the live DB was at revision a9b0c1d2e3f4 (had `line_items.unit_price`, no `po_sets.line_mapping_rule`); migrated as above.
  `scripts/requeue_line_mapping.py` dry run (would change sets 39,40,41,42,43,47,48,49,50,51; 39,40,42,48,50 merge) before any new real run, or start a fresh DB.
  Its 24 pending sets are explained: page-fragment sets ("...-pages-3.pdf", the out-of-scope fragments module), genuine partial
  deliveries, and sets still waiting for a DN.
- Windows note: Application Control (WDAC) blocked the venv python for a while on this PC; it works again. If `uv run`
  fails with os error 4551, see the Code Integrity event log (not an antivirus issue).

- **Line-number fallback implemented, Opus-reviewed 2026-10-24** (LINE_NUMBER_FALLBACK_PLAN.md rev 2): rules x10/div10/add_rel/strip_rel per pool,
  `matching.line_number_fallback` switches, `po_sets.line_mapping_rule` + migration `e8f9a0b1c2d3`, audit `line_mapping_applied`,
  `scripts/requeue_line_mapping.py` (dry run by default; `--apply` re-runs exactly the sets whose verdict changes). Replay of the real run on a DB copy: merged 39,40,42,48,50.
- Real end-to-end run on a fresh DB (200 files; old DB archived in `data/prev_run_20261024/`; input backup in
  `data/input_corpus_backup/`): **12 merged (11 verified correct, 1 incomplete packet), 3 mismatched, 18 quarantined**,
  no wrong merge. Reports: `REAL_RUN_ISSUES_2026-10-24.md` (index), `REAL_RUN_MERGED_VERIFY_2026-10-24.md`,
  `REAL_RUN_DIAGNOSIS_2026-10-24.md`, `robustness_audit_2026-10-24.md`.
- Full suite last green: 6138 passed (before the R-02/04/05/10 fixes; re-running). `ty check src` clean.
- Committed and pushed (T1 done): 395a7a0 grouping/digit key/guards, 254bba3 extraction images, 339f821 robustness. Opus review still owed (T10).
- Open from commit split: PRODUCT section 8 entry "input copy is removed even when the quarantine copy fails" contradicts R-10 and must be updated; other unstaged PRODUCT entries (documents_changed_during_merge, blocked_customs, circuit breaker, nine section 8 entries) go with T12/T5.

## 4. Plan as task cards (unambiguous; execute in order unless marked parallel-safe)

Executor key: **CLAUDE** = Claude Code (Opus/Sonnet, judgement + reconcile/grouping/quarantine logic);
**OC** = OpenCode with DeepSeek (well-specified, mechanical or test-driven work). Every card: work ONLY in the listed
files, make the acceptance commands pass, DO NOT commit, DO NOT touch other files or user WIP, report files changed +
command output. CLAUDE reviews every OC result before it is committed. Never run the real sync from a card unless it says so.

Global acceptance for every code card: `uv run ruff check <files>`, `uv run ruff format --check <files>`,
`uv run ty check src`, plus the card's pytest targets (`uv run pytest -o addopts="" -q -W ignore <targets>`).

| ID | Task | Executor | Parallel-safe |
|---|---|---|---|
| T1 | Verify R-02/04/05/10 fixes: full suite green, fix the 1 failure seen at ~8% of the run, commit + push (A whole files, B own hunks) | CLAUDE | no |
| T2 | Quarantine explanation file + original filenames + audit rows (details below) | CLAUDE (design) then OC may add tests | no (touches quarantine.py) |
| T3 | Config + docs for new VLM keys (below) | OC | yes |
| T4 | Kill leaked test servers + stop the leak (I-9) (below) | OC | yes |
| T5 | Medium robustness: R-03, R-06, R-16 (one card each; below) | CLAUDE for R-06 and R-16 (touch sync/grouping), OC for R-03 tests | after T2 |
| T6 | Lower robustness: R-09, R-08, R-07, R-15, R-17, R-18 | OC with CLAUDE review | after T5 |
| T7 | Stitched whole-document retest corpus + fresh-DB run + verify against PDFs | CLAUDE (stitch script may be OC) | after T5 |
| T8 | Operator UI walk-through with real run data (Unclassified, reclassify, quarantine, force merge) | CLAUDE | after T7 |
| T9 | Cleanup list: propose which `scratch_*`, `batch_*`, stray reports to delete/gitignore; ASK the user; do not delete | OC proposes, user decides | yes |
| T10 | **Opus code review** of the whole branch diff + retest results; `make business-logic`; gitnexus refresh + `detect_changes` | CLAUDE (Opus) | after T8 |
| T11 | pymupdf AGPL licence check; write result in docs/DELIVERY.md | OC (research) + user decision | yes |
| T12 | **Manual merger** (user-tested, KEEP, never discard): Opus review, `tests/test_manual_*.py`, check vs single-doc/single-PO scope, commit by named files + push. Files: `manual_merger.py` (route), `manual_merge.py`, `http_errors.py`, `templates/manual_merger.html`, `tests/test_manual_*`, its hunks in `main.py`, `base.html`, `dashboard.html`, `limits.py`, `config.py` (ManualMergeConfig), `quarantine.py`, `conftest.py`, `config.example.yaml`, SPEC FR-14, PRODUCT 6.2, CLAUDE.md tiers section | CLAUDE (Opus review) | no |

### T2 detail (user-approved 2026-10-24)
`QUARANTINE.txt` for sets AND single documents, one layout, English only, in `src/app/services/quarantine.py`
(`_write_quarantine_report`, `quarantine_copy`, `quarantine_document`):
1. "What happened" sentence from `reconciliation.REASON_TEXT` plus the existing detail.
2. Table of the set's documents: original filename, doc type, document number (DN/SI/PO as printed), PO number as
   printed, original input path when known (`documents.original_path` or equivalent; if no such column, omit, do not invent).
3. "What to do" line per reason code (one sentence each; unknown code → generic sentence).
4. Quarantined PDFs keep their ORIGINAL filename (append `_2`, `_3` on a clash) instead of the sha hash.
5. Same text stored in the `audit_log` row written at quarantine time (automatic quarantines currently write no row).
Acceptance: new tests that quarantine a set and a single document and read `QUARANTINE.txt`, the copied filenames and
the audit row back; `tests/test_quarantine.py` and `tests/test_business_scenarios_ui.py` stay green.

### T3 detail
Files: `config.example.yaml`, `docs/DELIVERY.md`, `README.md` config section if it lists `vlm`. Add under `vlm:`
`request_timeout_seconds: 180`, `raster_dpi: 150`, `reasoning_effort: "high"` (and `max_pages` if documented in
`VLMConfig`), each with a one-line comment (valid ranges are in `src/app/core/config.py`). Keep YAML valid:
`uv run python -c "from app.core.config import load_config; load_config('config.example.yaml')"` must pass.
Add a DELIVERY checklist line: re-run `scripts/deploy_prefect.py`; check the OpenRouter key limit; one midnight run on
the target machine.

### T4 detail
Find which tests start Prefect's temporary server (log line "Stopping temporary server on http://127.0.0.1:...";
orphans show as `uvicorn --app-dir ...site-packages --factory prefect...`). Make the test fixtures set Prefect to a
non-server mode or stop the server at session end (e.g. `PREFECT_SERVER_ALLOW_EPHEMERAL_MODE`/ephemeral settings in
`tests/conftest.py`). Acceptance: after one full `pytest` run no new `uvicorn --factory` processes remain
(PowerShell: `Get-CimInstance Win32_Process | ? CommandLine -match 'uvicorn.*--factory'`). First kill the current orphans
(only those matching that pattern, never `app.main:app`).

### T5 detail
- R-03: files rejected at ingest (non-PDF named .pdf, truncated, password-protected, over size cap) must create a
  Document row so they appear on the Unclassified page with a reason. Files: `ingestion.py`, tests.
- R-06: HTTP 400 from the provider that is not tied to the document (bad model id, bad request shape) must not fail and
  quarantine the night's batch: after N consecutive 400s in one sync, stop calling the VLM, leave remaining documents
  pending, log one clear error. Files: `extraction.py`/`flows/sync.py`.
- R-16: identical PO/DN/SI files re-sent after their set merged must not create an empty extra set; they are duplicates
  and must leave the input folder or be reported. Files: `grouping.py`, `flows/sync.py`.

## 5. Open issues (short; details in the reports above)

- I-1 one-digit PO misread on a continuation page (set 8) – needs the second read (user decision pending).
- Business question: POs printed "1 of 6" supplied as page 1 only – are the remaining PO pages expected?
- I-8 overflow-text rows on POs stored as numberless qty-0 lines (none in merged sets) – observe.
- I-9 test suite leaks Prefect temporary uvicorn servers (dozens of orphan processes).
- Vendors' own numbering (10/20/30, `1-1`) quarantines sets as unmatched lines – correct holds.

## 6. Log (newest last; add a line per finished chunk: date, what, commit sha)

- 2026-10-24 task cards T1-T11 written; OC/DeepSeek assignment proposed (CLAUDE keeps reconcile/grouping/quarantine/sync logic and all reviews).
- 2026-10-24 real run, verification, R-01/R-14/R-12 + dup-guard refinement, R-02/04/05/10 fixes (uncommitted until step 1).
- 2026-10-24 T1 done: commits 395a7a0, 254bba3, 339f821 pushed to origin/fix/mvp-high-priority. T12 (manual merger) added.

- 2026-10-24 evening: repaired broken push (6bb1384); T2 12d4950; manual merger ea2f511; T3/T4/T11 3f954b1; T5 938188f.

- 2026-10-24 night: live DB migrated; R-07 done (pending on write failure); T9 cleanup done; Opus review findings + R-08/R-09/R-15/R-18 fixed, bf7a294.

## 6a. Handover-readiness checklist (what is and is not done for an MVP handover)

Done and verified in the suite: reconciliation rule (PO == DN == SI per line, integers x1000), digit-keyed PO grouping with letters
guard, duplicate-document guard, line-number fallback (x10, div10, -1 suffix), image-based extraction config, quarantine report +
audit rows + original filenames, ingest rejects visible, 400 breaker, manual merger tool, DELIVERY checklist + licence note.
NOT done (needs the user or the target machine): pymupdf licence choice (later); real-corpus retest on a NEW DB (T7, last);
operator UI walk-through (T8); one midnight Prefect run on the target Windows Server (DELIVERY section 8 item 9); T10 final
review + gitnexus refresh; production-phase items in 6b.

### T9 cleanup (DONE 2026-10-24, user approved; kept here as the record)
- Safe to delete (throwaway): `scratch_*.py` (36 files) and `scratch_dump/` (184 MB), `append_report.py`, `batch_inspector.py`,
  `prepare_audit_batches.py`, `inspect_data.py`, `ordered_audit_docs.json`, `batch_02..30_findings.md` (merge their content into
  `AUDIT_FINDINGS_REPORT.md` first if still wanted).
- Keep and commit (they are referenced by this plan): `LINE_NUMBER_FALLBACK_PLAN.md`, `REAL_RUN_*_2026-10-24.md`,
  `robustness_audit_2026-10-24.md`, `MANUAL_MERGER_AUDIT_2026-10-23.md`, `PDF_DB_COMPARISON_2026-10-23.md`.
- Large data, keep outside git: `data/input_corpus_backup/` (67 MB, the real corpus: do not lose), `data/prev_run_20261024/` (117 MB old DB +
  outputs), `pattern_lab/` (11 MB), `prompt_improvement/` (5 MB, evals behind the P5 prompt). Suggest adding all four plus `scratch_*` to `.gitignore`.

## 6c. UI test harness (built 2026-10-24) and decisions log

**UI test harness (committed):** `scripts/ui_test_env.py` builds a throwaway environment through the real pipeline code
with a canned VLM reply (no network, no cost), `tests/ui/` holds 20 passing Playwright reference tests,
`data-testid` hooks were added to the templates (additive), `docs/UI_TEST_PLAN.md` is the case matrix (~45 TODO cases),
and `docs/OPENCODE_UI_TEST_BRIEF.md` is the brief + rules for DeepSeek via OpenCode. Run:
`$env:RUN_E2E="1"; uv run python -m pytest tests/ui -o addopts= -p no:cacheprovider -W ignore -q`.
Next step: hand the brief to DeepSeek, then review its report and triage the "suspected product bug" findings.

**Decisions taken by the user (2026-10-24, late):**

| # | Topic | Decision | Status |
|---|---|---|---|
| D1 (D18: NOT BUILT in this release, owner skipped it 2026-10-25; manual merger + supervised first week cover it) | I-1: one-digit PO misread (phantom set, packet missing a page) | **Do not merge a suspected misread. Hold it and let the operator choose**: assign the page to the correct PO (type the right PO number), mark "not a misread", or Force Merge. No second read, no extra API calls. Design and risks below. | Decided; NOT built (T13) |
| D2 | POs printed "1 of 6" with only page 1 supplied | If the supplied PDF holds everything the program needs (PO number, line items, quantities), **match and merge it**. The packet uses the **original PDF pages** (the stored upload), never the rasterised PNGs sent to the model (`merge.py` reads `stored_path` with pypdf; images only go to the VLM). | Decided; no code change (confirm in T10) |
| D3 | R-13 (split-gate reason overwritten, only with `split_combined: true`) | Defer to the future module for combined files / fragments. | Decided |
| D4 | Extraction validation (hallucination defence, section 7) | **Not in the initial MVP.** Revisit after real-world testing; build in the next phase once proper data exists. | Deferred by decision |
| D5 | Production run schedule / folder polling | **Poll the input folder on a short interval; each tick processes ALL waiting files, oldest first. Nothing is skipped; an overlapping tick is queued (coalesced into one follow-up pass). The midnight sweep stays as a SWITCHABLE option. Each processed file's set is reconciled and merged immediately (optional settle time).** Details and waiting-time options below. | **BUILT 2026-10-24 (T14): 1-minute poll, queue, settle time, nightly switch; see PRODUCT section 8 and `tests/test_poller.py`. No parallel extraction (user: no big refactor now).** |
| D6 | pymupdf licence | Later. | Deferred |

### D1 design notes (T13) - read before building
**Investigation on the real run (57 sets, read-only, 2026-10-24):**
- A bare "PO numbers differ by exactly one digit" rule finds 3 pairs: sets 2/3 (50116501 / 50119501), 41/43 (90139566 / 90139766) and 8/9 (4500051195 / 4500057195). Only 8/9 is the real misread. The other two are legitimate separate POs, each with its own PO + DN + SI: **2 of 3 would be false positives.**
- Refined rule: set A holds ONLY PO documents (no DN, no SI) AND A's number is one digit from a set B that already has a DN or SI. On this corpus that flags **exactly set 9 (vs 8) and nothing else: 0 false positives.** The earlier idea "A's line numbers continue B's" is dropped: the phantom page had NO extracted lines at all.
- Remaining false-positive case: a legitimate PO that arrives before its DN/SI while a sequential neighbour is ready to merge. Use the settle time (the hold is evaluated only after the settle time has passed) and the operator's "not a misread" action. Small sample (57 sets): re-check on the next real corpus.
- Effect: B does not merge; the page says "possible misread: PO 4500057195 looks like 4500051195" for both sets.
- Operator actions needed (new): (1) "this page belongs to PO X" = move the document to the right set (today there is NO UI path that moves an attached document; reclassify refuses it), (2) "not a misread" = dismiss the hold (a flag on the set), (3) Force Merge (exists).
- Pin with tests named `test_known_limitation_*` for any case left unguarded (PRODUCT section 8).

### D5 design notes (T14) - read before building
Measured on the real run (data/logs/real_sync_20261024.out): 200 documents in 33 min sequentially; median 8.9 s, p90 16 s per document. A typical set (PO+DN+SI) is about 30 s of work.
- Switches (config, later editable in the UI): `schedule.poll_enabled`, `schedule.poll_interval_seconds`, `schedule.nightly_enabled` (+ existing time/timezone), `schedule.merge_settle_minutes` (0 = merge as soon as a set reconciles).
- "Never skip": today a second sync returns `sync_already_running`. Change to: a request while a sync runs records "run again" and the running sync starts one more pass when it ends. One follow-up pass covers every file that arrived meanwhile (a pass takes all waiting files), so N queued ticks collapse into one without skipping a file. (Manual Scan still answers 409 while a sync runs: the button is disabled and the poller picks the work up anyway.)
- Immediate merge: correctness is unchanged (same rule PO == DN == SI), but timing matters when a PO's documents arrive minutes apart: a set can merge as soon as what has arrived agrees (e.g. PO page 1 + matching DN/SI before PO page 2). `merge_settle_minutes` (suggest 5-10) merges a set only after its newest file is that old. Decide the default with the user.
- Waiting-time levers (cheap to expensive): shorter poll (a folder listing costs nothing and makes no API calls; 1 min instead of 5 cuts the average wait from 2.5 min to 30 s); 3-4 parallel extractions (same API cost, about 3x throughput: 200 documents in ~11 min instead of 33); call extraction in-process instead of one Prefect task launch per file; folder watcher (event-driven) instead of polling; lower DPI or reasoning effort (NOT recommended: accuracy is the priority).
- Prefect schedule is registered by `scripts/deploy_prefect.py`; re-run after any change.

## 6d. Opus review of the whole branch (2026-10-24) and what was done

Three Opus reviewers (core engine, pipeline, web/config/tests) read the branch against `origin/main` and proved their findings with probes. Fixed in the commit after `736fdc6` (tests in `tests/test_core_review_fixes.py`, `test_pipeline_review_fixes.py`, `test_web_review_fixes.py`):

| Sev | Finding | Fix |
|---|---|---|
| CRITICAL | A PO-less DN that shares a number with another vendor's DN was attached on the number alone and the set's PO was copied onto it, so a foreign DN could complete a set and merge | Such a document is marked `po_no_source='dn_number'`; the set holds (`placed_by_number_only`) until an operator presses "Confirm it belongs here" (audited) or Force Merges |
| HIGH | Duplicate-DN guard missed `01` vs `1` line keys, number-less copies, `DN-0077` vs `DN-077` | Same normalisation as matching; number-less documents are compared among themselves; leading zeros ignored |
| HIGH | Reclassify attached a document before taking the set lock (set mid Force Merge) | 409 before anything is written; honest notice if the lock turns busy later |
| HIGH | Per-PO lock (300 s) could be cleared during a 742 s extraction; value 0 allowed | Default 900, `ge=60`, validator requires it to exceed worst-case extraction |
| HIGH | HTTP-400 breaker: refunded documents were quarantined at once; the poller retried and condemned 9 documents in 4 passes | Refunded documents are deferred too; poller pauses 1 h after `vlm_halted`; no error retries then |
| HIGH | Stored copy could be deleted when a data folder sat inside input | Config refuses nested folders; cleanup never deletes a stored copy |
| MED | Stability/OSError left files unhandled; Prefect-down committed the snapshot; renamed copy of a failed document stayed; offline input share aborted the whole flow; sidecar race; orphan sidecar blocked actions 20 min; buttons enabled during a sync; KPI cards stale; UI tests order-dependent; poller could start from a dev config | All fixed (see PRODUCT section 8 "operating limits") |
| LOW | x10 re-keys a vendor key equal to a PO key | Kept (intended); documented and pinned `test_known_limitation_x10_rekeys_...` |
| NOT FIXED (decided/noted) | Origin guard compares host names only; upload has no size middleware; unused config keys; downgrade of a create_all DB below d4e5; DNS rebinding exposes writes; per-minute O(N) re-hash of stuck input; split-parent cleanup matches by name | Documented in PRODUCT section 8; DELIVERY step 6 (pin `allowed_hosts`) is now mandatory |

## 6b. NEXT PHASE (user request 2026-10-24, to be DISCUSSED later; nothing here is approved or started)

After the handoff cards above are finished, move to **production deployment: final decisions, then full-fledged testing.**
Items the user wants decided/built (record only):

1. **All configuration editable from the UI** (a settings page), not by editing `config.yaml` by hand. Open questions for the
   discussion: which keys are safe to edit live vs need a restart (`manual_merge` limits already need one); validation and
   rollback of a bad value (the app fails fast on invalid config today); who may edit (LAN, no login yet); audit row per change;
   secrets (OPENROUTER_API_KEY) stay out of the UI/file; paths must stay inside the data root; Prefect deployment captures
   settings, so a schedule change must re-register the deployment.
2. **Auto-run timings finalised:** the midnight Prefect cron (today `prefect.*` in config + `scripts/deploy_prefect.py`),
   timezone, whether a second daytime run is wanted, and what happens when a run overlaps the next one.
3. **Folder polling finalised:** today a sync only runs on the cron or a manual Scan; there is no continuous watcher. Decide
   cron-only vs a polling interval, plus the file-stability check (`ingestion.stability_poll_*`) and the recent-write window
   (`_RECENT_WRITE_SECONDS = 300`).
4. **Then full-fledged testing** on the target machine: fresh DB, real corpus, midnight run, concurrent Force Merge, restart
   and power-loss recovery, backup/restore, the operator UI walk-through (T8) and the Opus branch review (T10).

Related decisions already waiting: pymupdf AGPL (buy / replace rasteriser / accept), second-read extraction validation (section 7),
whether POs printed "1 of 6" supplied as page 1 only should be held, and I-1 (one-digit PO misread on a continuation page).

## 7. Deferred topic: extraction validation / hallucination defence (decided 2026-10-24: NOT now, discuss in a later session)

- Today only the pydantic shape (all value fields are `str | None`), the strict quantity parser, and cross-document agreement protect against a bad extraction. Nothing checks a value against the document itself.
- **The user does not trust embedded PDF text, at least for DN documents.** Do NOT use the text layer as ground truth or as a hard check. Evidence: of 200 docs, 111 have a text layer and 89 are scans; doc 1 (DN2182.pdf) text layer says `LPO2612360`, the image says `LPO26/2360` (model was right); doc 199 text layer `GDN'RH0'26-796` vs printed `GDN-RHO-26-796` (model right). No hallucination was found on the text-layer half of the corpus.
- Candidates to discuss later: format invariants in one validator; replay audit script over stored extractions (read-only); second independent read for scans/held docs (adds an API call, needs the user's decision, would catch I-1); image-based verification only. Nothing here is approved.
