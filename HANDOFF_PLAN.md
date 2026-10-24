# HANDOFF + LIVE PLAN — AAM Merger V3 (hardening phase)

Living file. Any agent, model or session: read this first, then `CLAUDE.md`, `docs/CORE_LOGIC.md`,
`AAM_merger_V3_PRODUCT.md`. Update the "Status" and "Log" sections whenever you finish a chunk, and commit it.
Older state: `SESSION_HANDOFF.md` (2026-10-22, delivery readiness; still valid for deploy facts).

Last updated: 2026-10-24. Branch `fix/mvp-high-priority`, remote `origin` (push the branch; never force-push; never push main).

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

## 7. Deferred topic: extraction validation / hallucination defence (decided 2026-10-24: NOT now, discuss in a later session)

- Today only the pydantic shape (all value fields are `str | None`), the strict quantity parser, and cross-document agreement protect against a bad extraction. Nothing checks a value against the document itself.
- **The user does not trust embedded PDF text, at least for DN documents.** Do NOT use the text layer as ground truth or as a hard check. Evidence: of 200 docs, 111 have a text layer and 89 are scans; doc 1 (DN2182.pdf) text layer says `LPO2612360`, the image says `LPO26/2360` (model was right); doc 199 text layer `GDN'RH0'26-796` vs printed `GDN-RHO-26-796` (model right). No hallucination was found on the text-layer half of the corpus.
- Candidates to discuss later: format invariants in one validator; replay audit script over stored extractions (read-only); second independent read for scans/held docs (adds an API call, needs the user's decision, would catch I-1); image-based verification only. Nothing here is approved.
