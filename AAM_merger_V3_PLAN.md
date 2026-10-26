# AAM Merger V3 — Execution Plan: Layer-2 purge + multi-doc split

> **REVISION 2 — 2026-09-29.** §0.3, §0.4, §2 and Step 4 are **superseded**.
> The split mechanism changed from "one VLM call → `components[]` → children born
> `valid` in the same run" to "route to `combined/` → cut child **files** → feed the
> input folder → children re-extracted fresh on the **next** run". Anything already
> written against the old text is wrong.
>
> ⚠️ **Coordination:** this document warned that another session is active and that
> overlapping edits are drift. The per-line-DN grouping association has since been
> built in `grouping.py`, which inverts §0.9's stated ordering. Confirm nobody is
> mid-execution against this file before changing it further.
>
> Status: planned, not started. Baseline: 539 tests collected, 10 failures — all in
> `test_split.py` (9) and `test_no_combined_in_engine.py` (1). Both are red-first
> tests for this work. TDD throughout.
>
> > **REVISION 3 — 2026-09-30 (review hardening, no API key available).**
> > VLM page-count live verification is **deferred** (no OPENROUTER_API_KEY in
> > this environment) — `split_combined` takes the VLM response as a plain dict
> > so all range logic is unit-testable with synthetic responses. Additional
> > hardening folded into Step 4: (a) `split_completed_at` column is required —
> > Step-1 migration only added 2 of 3 columns; (b) child filenames use a
> > 16-char SHA prefix (`<sha16>_p<i>.pdf`, `p<i>` numeric-validated) for
> > WS2016 MAX_PATH; (c) SKIP consumes pages for coverage but cuts **no file**;
> > (d) a child re-reading as COMBINED quarantines the parent as
> > `child_recombined` instead of being silently ignored (Layer 2 names no
> > types); (e) zero-line/failed children never auto-retry — operator Redo only;
> > (f) the sync file-list snapshot is load-bearing: children appearing mid-run
> > wait for the next run (pinned by test); (g) prod DB swap covers prod too
> > (backup first), not dev-only.

## 0. Settled decisions (do not relitigate during execution)

1. **Two layers.** Layer 1 = see, extract, split (`ingestion`, `extraction`,
   `sanitizer`, new `splitting`). Layer 2 = business rules over rows (`grouping`,
   `matching`, `reconciliation`, `merge`, `customs`, `quarantine`, `locking`,
   grouping/reconcile/merge sections of `flows/sync.py`, dashboard set routes).
   Manual merger stays isolated (no DB) — out of scope. **All of it ships as one
   change**; nothing is deferred to a later plan.
2. **Zero `COMBINED` in Layer 2** — code and templates. `DocType.COMBINED` stays in
   the enum as a Layer-1-only packaging marker; Layer 2 never names it. Enforced by
   `tests/test_no_combined_in_engine.py` (grep over code + templates).
3. **Multi-doc files are split in Layer 1, via the filesystem. (REVISED)**
   - Full extraction runs first, exactly as today. If it returns `COMBINED`, that is
     the trigger — **no extra VLM call for normal documents.**
   - The combined file is moved out of `input/` into `combined/`.
   - It is cut with `pypdf` into child **PDF files** written into `input/`.
   - Children are then picked up on the **next** sync and extracted **fresh**, as
     ordinary single-section documents.
   - **Why this over the old design:** a child born `valid` inherits its numbers and
     lines from the very 3-section read that produced the split. A child re-extracted
     gets the trustworthy-VLM assumption applied to one clean document. The same
     principle as retiring COMMERCIAL_INVOICE and refusing to route reconciliation
     through a degraded read. Accuracy over cost: a 3-way file costs 4 calls, not 1.
4. **Parent row stays, sterile and linked. (REVISED)**
   - Parent: `doc_type=COMBINED`, `is_split_parent=True`, full raw JSON,
     **no** `po_no` / `dn_no` / `po_set_id` / line items. Children own the data, so
     anything that scanned the parent would double-count.
   - Parent **file** is retained in `combined/` for audit; its `input/` copy is
     removed so it is never re-processed.
   - Children link via `parent_document_id`, resolved from the child filename
     (`<parent_sha>_p<i>.pdf`) → parent row looked up by SHA at ingest time.
     Filename-carried linkage is crash-safe and needs no sidecar table.
   - Re-upload of the same combined file hits the parent SHA with
     `is_split_parent=True` → skip, no duplicate children.
5. **Page indices are PDF page indices, never printed content. (NEW — load-bearing)**
   `page_start` / `page_end` are 1-based indices into the real file, cross-checked
   against `len(PdfReader(path).pages)`. Never numbers printed on the page, and never
   PDF metadata. This is what makes ranges *verifiable* rather than merely reported.
   The VLM also returns `page_count` purely as a cross-check; pypdf is ground truth.
6. **A component is a contiguous run of ONE type. (NEW)**
   Pages form a set only while they are a continuation of the same PO / DN / SI. If a
   page changes type, the run ends and a new component begins. A page that is not a
   continuation of any of the three is **not extracted** — it is returned as a `SKIP`
   component so its pages are still accounted for.
7. **Correctness → quarantine. Every check is a hard gate.**
   - `page_count` disagreeing with pypdf → quarantine `page_count_mismatch`
   - any range outside `1..N`, or `page_start > page_end` → `page_range_invalid`
   - overlapping ranges → `page_range_overlap`
   - **incomplete coverage** — any page not claimed by a component (SKIP included) →
     `pages_unaccounted`
   - Each violation quarantines the whole parent. **Never guess a fallback range.**
   - Zero-line component → failed child row, row-state only. Never
     `quarantine_document()`: the parent file is shared across all its children.
   - SKIP components consume their pages but produce no row.
8. **DN gate.** Sets require ≥1 PO **and** ≥1 DN **and** ≥1 SI row; else `pending`
   with explicit `missing_po/dn/si_document` reasons. Enforcement chain unchanged:
   `compare_po_set_lines` → `group_by_line_no` (sums) → `compare_aggregates` (exact
   int, both pools) → verdict → customs → merge. Merge trusts status; force merge
   bypasses by operator intent + audit row.
9. **No legacy handling of any kind.** Dev DB is swapped (Step 5), not
   migrated-around. No transition reasons, no tripwires, no recovery paths for old rows.
10. **Ordering (revised):** the per-line-DN grouping association is already built
    (this session). Layer-2 purge and the split land after it and must not delete the
    branches it depends on.

## 1. Principles

- No legacy path, no workarounds, no transition scaffolding.
- TDD: failing test first for every behaviour change.
- The PDF is ground truth. The model's report is a claim to be verified, never a fact.
- Full suite green at the end; everything outside the enumerated test fallout passes
  unmodified.

## 2. Files NOT touched

`matching.py`, `customs.py` (code), `po_sets.py`, `api/routes/sync.py`, `main.py`,
`manual_merger.py`, `sanitizer.py`, `limits.py`, `database.py`, `locking.py`,
`sync_lock.py`, `static/*`.

> **REVISED:** `ingestion.py` moved from this list to **touched**. The combined→`combined/`
> routing and the child→parent linkage both live there. A new `paths.combined_folder`
> config key is added (`core/config.py` + both YAMLs).

## 3. Step 1 — schema migration (additive, lands first)

**DONE** — migration `d4e5f6a7b8c9_split_parent_columns.py`, columns at
`models.py:84-88`, index `ix_documents_parent_document_id`.
Still to do: fix two drifted comments (no logic) — `po_reference_ambiguous` ("never
attached", not "quarantined") and `LineItem.dn_no`, whose "attachment reverification"
claim is now true but should say which function reads it
(`grouping._anchor_from_po_line_ref`).

## 4. Step 2 — failing tests first

- `tests/test_split.py` — 9 tests, already red. Rewrite to the revised design:
  combined routed to `combined/`; child **files** land in `input/`; child **rows** do
  not exist until the next run; `parent_document_id` resolved from filename; sterile
  parent with no line items; parent file kept in `combined/` and absent from `input/`.
  Ranges validated against real pypdf page count — add cases for count mismatch,
  out-of-bounds, overlap, incomplete coverage, and a mid-run type change splitting one
  component into two.
- `tests/test_no_combined_in_engine.py` — 1 test, already red.
  Currently only `dashboard.py:687-692` (the reclassify guard) leaks.
- `tests/test_verify_user_claims.py` — missing-DN → `pending/missing_dn_document`;
  missing-SI likewise; DN-short → `partial_fulfillment` with `vendor_quantity: 0`;
  DN-over/disagree → `mismatched`; detail matrix absent-pool renders ❌; reclassify
  `COMBINED` → 422; unclassified lists `failed` non-UNKNOWN docs.
  > Note: `test_reclassify_combined_rejected` (currently passing) and decision 2
  > contradict each other — the guard it asserts must be **deleted**, and the test
  > inverted to expect 422 from the unclassified path only.

## 5. Step 3 — Layer-2 purge + DN gate

| File | Exact change |
|---|---|
| `services/reconciliation.py` | delete `combined_unverified`; delete the COMBINED fast path; gate → PO+DN+SI-required + `missing_*` reasons; `dn_source = dn_docs`, `si_source = si_docs`; add `REASON_TEXT` entries |
| `services/merge.py` | delete naming exception, `COMBINED` order token, exclusivity block, stale docstrings |
| `services/quarantine.py` | report PO-only quantities |
| `flows/sync.py` | `_ANCHOR_TYPES = ("PO",)`; filter `is_split_parent == False` out of reconciliation sweeps |
| `api/routes/dashboard.py` | delete pool fallback; matrix absent pool = unmet; **delete the reclassify COMBINED guard `:687-692`**; unclassified → `UNKNOWN OR failed` |
| `core/config.py` + both YAMLs | drop `COMBINED` from the `legal_order` validator; add `paths.combined_folder` |
| `services/customs.py`, templates | delete comment/badge/option |
| Tests | delete COMBINED fixtures across reconciliation/merge/customs; rewrite dashboard expectations; fix reason assertions |
| Gate | full suite green except split tests |

## 6. Step 4 — extraction split (REVISED)

**`services/splitting.py` (new module, Layer 1)**

1. `split_combined(parent_path, response, cfg) -> list[Path]`
   - `real_pages = len(PdfReader(path).pages)` — ground truth
   - validate per §0.7; any failure → `quarantine_document()` + reason, return `[]`
   - cut each component with `PdfWriter` to `combined/<parent_sha>_p<i>.pdf`
   - move those files into `input/`
2. **Schema**: `_VLMComponent(doc_type: Literal[PO,DN,SI,SKIP], document_number,
   po_reference, po_reference_ambiguous, page_start, page_end, line_items[])` and
   `page_count: int` on the response.
3. **Prompt**: multi-doc step added to the combined branch only — enumerate contiguous
   same-type runs with their real PDF page indices. Explicit instruction: these are
   positions in this file, not numbers printed on the page.
4. **`services/extraction.py`**: on `document_type == "COMBINED"`, persist the sterile
   parent, hand off to `split_combined`, and **do not** persist the 3-section union of
   line items. Delete the 3-section gate and the COMBINED number fallbacks.
5. **`services/ingestion.py`**: route `combined/`-bound files; resolve
   `parent_document_id` for children from the `<parent_sha>_p<i>.pdf` filename,
   skipping any document that is itself a split parent.
6. **Loop guard** (three parts, all required):
   - a child (`parent_document_id` non-null) never re-enters the splitter, even if it
     re-reads as COMBINED — instead the **parent** quarantines as
     `child_recombined` with the child filename (a COMBINED child is otherwise
     invisible: Layer 2 names no types, so ignoring it strands the set forever)
   - nothing already inside `combined/` is ever re-split
   - a failed child (`extraction_status == failed` with `parent_document_id`
     non-null) never auto-retries on sync sweeps — operator Redo/Re-extract only,
     so one blank page cannot burn 3 VLM calls every night
7. **Crash recovery**: `split_completed_at` on the parent is authoritative. If absent,
   children are re-derived from the parent SHA rather than trusted to exist.
   (`split_completed_at` is a real column — Step-1 migration only added 2 of 3;
   Step 3b adds it. Without the column this section is unimplementable.)
   Child filenames are `<sha16>_p<i>.pdf` (16-char SHA prefix, `p<i>` strictly
   numeric) for WS2016 MAX_PATH; SKIP components consume pages for coverage but
   cut **no file**. The input file list is snapshotted at run start: children
   landing mid-run wait for the **next** run (pinned by test).
8. `services/grouping.py`: attach sweeps skip `is_split_parent == True`.

## 7. Step 5 — DB swap + live verify

1. Stop app + worker. 2. Rename `data/aam_merger.db`, `.db-wal`, `.db-shm` →
   `*_old_<date>.db.bak` (sidecars MUST move — a fresh DB replays a stale WAL).
3. Optionally clear `data/stored/` and `data/combined/`; leave `data/input/` to
   re-ingest through the new path. 4. `alembic upgrade head`, restart.
5. Verify: synthetic edge matrix (bad ranges, overlap, gaps, type change mid-run,
   re-split idempotency) **plus one real vendor multi-doc PDF split end to end across
   two sync runs** before calling it done. VLM prompt quality tuning is a later track;
   structural correctness is this change's gate.
