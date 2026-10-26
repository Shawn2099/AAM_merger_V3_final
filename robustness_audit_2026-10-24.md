# Robustness audit: AAM Merger V3 pipeline (2026-10-24)

Scope: Input -> SHA dedup -> classify -> VLM extract -> group -> reconcile -> merge -> output,
attacked end to end. The rule applied:

- merged output must be exactly right;
- a doubtful document is held for the operator (Unclassified page);
- an inconsistent set is quarantined;
- nothing crashes the whole sync, merges wrongly, silently drops a file, or leaves a half-state.

## How it was tested

The new tests are only in `tests/test_robustness_*.py`. No existing file under `src/` or `tests/` was edited.

| File | What it covers |
|---|---|
| `test_robustness_harness.py` | A fake **at the HTTP layer**: `httpx.MockTransport` behind a real `openai.OpenAI`. The real `_call_vlm`, PyMuPDF rasteriser, `instructor` parser, Prefect retry envelope, SQLite, grouping, reconciliation and pypdf merge all run. Each fake PDF is identified by its page width, read back from the PNG the rasteriser sent. |
| `test_robustness_batch_sync.py` | **One** `sync_flow()` run: a good PO/DN/SI set plus about 75 hostile inputs (VLM faults, file faults, 18 adversarial sets). Each test asserts one input's outcome. This proves containment, because the good set must still merge. |
| `test_robustness_vlm_output.py` | Schema drift, quantity shapes, null rows, very large payloads, re-reads, line-number quirks. |
| `test_robustness_containment.py` | Crashes and restarts, the lock, 402/400, disk full, an unwritable output or quarantine folder, a vanished stored file, transient read errors, a stuck `processing` row, revisions, the split gate, a failing sweep. |
| `test_robustness_grouping.py` | PO-number spellings, collisions and misreads. |

Run (list the files explicitly; PowerShell does not expand the glob):
`uv run pytest -o addopts="" -q -W ignore tests/test_robustness_harness.py tests/test_robustness_vlm_output.py tests/test_robustness_batch_sync.py tests/test_robustness_containment.py tests/test_robustness_grouping.py`
gives **129 passed, 19 xfailed in about 2 min**. Ruff check and format are clean. Every xfail is
`strict=True` with an explicit `raises=`. I also ran each one with `--runxfail` to confirm it fails
on the asserted symptom and not on a harness error.

Note: R-11 (a re-read with a digit-less PO stayed attached) reproduced at the start of the audit.
It was **fixed in the working tree while the audit ran** (`extraction.py:649` now checks
`_norm(_raw)`). It is now pinned as a passing test.

---

## Ranked defect list

Severity scale:

- **Critical**: a wrong packet can be delivered.
- **High**: silent loss, or one input aborts the whole sync.
- **Medium**: a stuck or misleading state, or an operator-invisible hold.
- **Low**: cosmetic, or a diagnostic is lost.

### R-01 · Critical: one delivery note scanned twice is summed into a merge
- **Trigger:** the same paper DN is scanned twice, so the bytes differ and the SHA dedup does not
  catch it. Both copies carry the same DN number and the same PO. Example: PO line 1 = 10, DN-9
  delivers 5 and is scanned twice, SI invoices 10.
- **Observed:** both DN rows attach. The sum 5+5 = 10 equals the PO, so the set **merges**,
  although only 5 were delivered. The same happens with an SI re-issued under the same invoice
  number. `_si_numbers` de-duplicates the name (`merge.py:39-45`), so even the
  `multiple_invoice_numbers` note never fires.
- **Expected:** a repeated DN number or SI number inside one set is doubtful, so the set is held
  for review.
- **Where:** `reconciliation.py:553-572` sums every DN/SI document with no identity check.
  `grouping.resolve_unattached_documents` also attaches a document *because* its `dn_no` equals one
  already in a set (`grouping.py:514-529`).
- **Minimal fix:** in `_reconcile_po_set_inner`, before summing, check whether two DN documents
  share a non-empty `dn_no`, or two SI documents share a non-empty `si_no`. If so, return `pending`
  with a new reason `duplicate_document_number` naming the files, in the same pattern as
  `multiple_po_documents`. Add the reason text to `REASON_TEXT` and `_DETAILED_REASONS`.
- **Product question:** the docs do not cover this case. Per CLAUDE.md, confirm it with the owner
  before implementing.
- **Test:** `test_robustness_batch_sync.py::test_same_dn_number_twice_in_one_set_is_not_summed_into_a_merge`

### R-14 · High (can become Critical): a misread character inside a PO number creates a different, valid-looking key
- **Trigger:** the VLM transcribes `PO 4500123` as `PO 45OO123` (letter O for zero), or
  `4500l23`, or uses full-width or Arabic-Indic digits.
- **Observed:** `_alnum_key` keeps the ASCII digits and treats the letters as "letters":
  - `45OO123` becomes key `45123`, letters `OO`;
  - `4500l23` becomes `450023`;
  - `PO ４５00 12345` becomes `0012345`, because non-ASCII digits are dropped.

  Letters on one side only are compatible, so the DN attaches to the **open set of an unrelated
  PO 45123**. When that set was only waiting for its DN and the line and quantity agree, it
  **merges with another PO's delivery note**. This is reproduced in the test.
- **Expected:** a number that cannot be read consistently is held (`missing_po_number`).
- **Where:** `grouping.py:117-134` (tokenise and keep) and `grouping.py:153` (digits only).
- **Minimal fix:** in `normalize_po_no`, return `""` in either case:
  - a token contains a letter *between* two digits;
  - the raw string contains any non-ASCII digit.

  The documented forms (`60213576 OPG`, `P213899`, `LPO26/2360`, `TOGA-PO-25-26-163`) carry their
  letters only as a prefix, suffix or separate token, so they are unaffected. Check codes such as
  `3049PO123` (the prompt's few-shot) against the real vendor inventory first. That is a product
  decision.
- **Test:** `test_robustness_grouping.py::test_dn_with_misread_po_number_does_not_complete_an_unrelated_set`

### R-10 · High: silent file loss when the stored copy is gone
- **Trigger:** a document's `stored/<sha>.pdf` disappears and cannot be rewritten. Examples: the
  storage folder is unavailable, a share is remapped, a cleanup script runs.
- **Observed:**
  1. The re-ingest rewrite swallows the error (`ingestion.py:170-175`).
  2. `_call_vlm` raises `FileNotFoundError` (`extraction.py:343-344`), which is classed as a
     **content** error.
  3. After 3 attempts the document is failed. `quarantine_document` copies nothing because the
     stored file is missing (`quarantine.py:313`), but it still deletes the input copy
     (`quarantine.py:319-326`). `_quarantine_broken_document` deletes it again (`sync.py:154`).

  **No copy of the PDF exists anywhere.** The report still says the file "has been removed from
  the input folder".
- **Expected:** the bytes are never deleted unless a copy has been made.
- **Minimal fix:**
  - (a) In `quarantine_document`, when the stored file is missing, copy from the input file. Never
    unlink the input unless `folder/name` now exists. In `_quarantine_broken_document`, drop the
    second unlink.
  - (b) Treat a missing `stored_path` as an infrastructure fault so the document stays pending.
- **Tests:** `test_robustness_containment.py::test_document_bytes_survive_somewhere_when_storage_is_broken`,
  `::test_quarantine_document_with_missing_stored_copy_removes_the_input_copy`

### R-04 · High: one file whose stat() fails aborts the whole sync
- **Trigger:** any input file whose `stat()` raises inside the stability poll. Examples: access
  denied, a broken SMB handle, an over-long path.
- **Observed:** `_poll_stability` (`sync.py:612`) runs outside the per-file `try`. The exception
  leaves `sync_flow`. No file is ingested and no set is reconciled that night.
- **Expected:** the file is skipped and counted; the rest of the sync runs.
- **Minimal fix:** in `_poll_stability`, wrap the call:
  `lambda f: (f, _safe_stable(f, ...))`, where `_safe_stable` catches `OSError`, logs it and
  returns `False`.
- **Test:** `test_robustness_containment.py::test_one_unstat_able_file_does_not_abort_the_sync` (xfail with `raises=PermissionError`)

### R-02 · High: a long number misread as a unit price leaves the document stuck in `processing` for good
- **Trigger:** the VLM returns a `unit_price` of about 9.2e15 or more (SQLite INTEGER tops out at about 9.22e18, and the value is scaled by 1000), for example a barcode or a
  concatenated column misread into the price cell.
- **Observed:**
  1. `parse_price_scaled` has no bound (`sanitizer.py:76-97`).
  2. The ×1000 value overflows SQLite INTEGER at flush.
  3. The except branch (`extraction.py:1026-1052`) commits on a session that needs a rollback, so
     it raises `PendingRollbackError`.
  4. The row stays `processing` with **attempt_count 0**.

  The set shows "not yet read". Inferred from the reclaim path (only one extraction was run): every sync after the 900 s reclaim
  makes another VLM call, and the document is never failed or quarantined. Prices are documentation only, yet they block the
  document.
- **Expected:** a bad price never blocks a document. A DB error at commit ends in a counted failure.
- **Minimal fix:** in `parse_price_scaled`, return 0 when `abs(val) > Decimal("1e12")`. In the
  except branch of `extract_document`, call `s.rollback()` and re-`get` the row before marking it
  failed.
- **Tests:** `test_robustness_containment.py::test_absurd_unit_price_does_not_leave_document_stuck_in_processing`
  (xfail) and `::test_absurd_unit_price_observed_state` (pins the half-state)

### R-05 · Medium-High: a transient read lock moves a good file out of the pipeline
- **Trigger:** antivirus or a backup tool holds the PDF while the sync reads it, so
  `read_bytes()` raises `PermissionError` once.
- **Observed:** `_quarantine_unreadable` (`ingestion.py:139-142` → `84-106`) **moves the good file**
  to `quarantine/_documents` with no Document row. It is never read and never shown on the
  Unclassified page, and its set waits forever.
- **Expected:** a transient error leaves the file for the next sync.
- **Minimal fix:** on a read or stat `OSError`, leave the file in place and re-raise. Quarantine only
  content verdicts (no `%PDF`, over the cap, still incomplete after `_RECENT_WRITE_SECONDS`).
- **Test:** `test_robustness_containment.py::test_transient_read_error_leaves_a_good_file_for_the_next_sync`

### R-03 · Medium: ingest-rejected files never reach the Unclassified page
- **Trigger:** a non-PDF file with a `.pdf` name, a truncated PDF (older than 5 minutes), a
  password-protected PDF, a file over the size cap, or an unreadable file. All of these are
  exercised by tests; the size cap is tested with `limits.MAX_UPLOAD_BYTES` patched small.
- **Observed:** the file is moved to `quarantine/_documents/<name>_<uuid>.pdf` plus `.reason.txt`
  with **no Document row**, so the only trace is on disk. The sync counts it as an error, but the
  operator page shows nothing. This contradicts the stated rule. CORE_LOGIC §6b also describes
  corrupt or encrypted files taking the counted failed-extraction path, which is the
  pymupdf-level check; the pypdf-level ingest gate refuses them first.
- **Minimal fix:** either insert a `failed` Document row (`doc_type=UNKNOWN`, reason in
  `raw_extraction_json`) for permanent rejects, or list `quarantine/_documents/*.reason.txt` on the
  Unclassified page.
- **Tests:** `test_robustness_batch_sync.py::test_rejected_file_is_visible_to_the_operator[*]`;
  `test_robustness_containment.py::test_file_over_the_size_cap_is_rejected_before_a_row_exists` (pins the observed behaviour)

### R-16 · Medium: re-sending an identical PO after its set merged mints an empty phantom set
- **Trigger:** byte-identical PO, DN and SI files dropped into input again after their set merged.
- **Observed:**
  - The SHA dedup returns the merged PO row, but `group_document` still calls
    `get_or_create_po_set(create=True)` (`grouping.py:285-289`).
  - `_find_open_set` skips merged sets, so a **new empty set** is minted. It shows "No purchase
    order document in this set yet" and holds the open-set key.
  - The three re-sent inputs are never cleared, because Phase 2 skips merged sets. They are
    re-hashed every night.
  - The sync reports `errors: 0`.
- **Minimal fix:** at the top of `group_document`, `if doc.po_set_id is not None: return doc.po_set_id, None`.
  In the sync loop, when an already-read document belongs to a merged set, delete the input copy
  by hash.
- **Tests:** `test_robustness_containment.py::test_resent_identical_po_after_merge_does_not_mint_a_phantom_set`
  (xfail) and `::test_resent_identical_files_after_merge_observed_state`

### R-06 · Medium: a provider-wide HTTP 400 condemns the whole night's batch
- **Trigger:** the OpenRouter request is invalid for every document. Examples: a bad `vlm.model`
  id, an unsupported parameter, a provider schema change. I could not confirm offline which status
  OpenRouter returns for each of these.
- **Observed:** 400 is not in `_INFRA_STATUS_CODES` (`extraction.py:813`). Every document is read
  3 times, set to `failed`, quarantined and removed from input. Recovery is a per-document
  operator Re-Extract. There is no circuit breaker.
- **Minimal fix:** a breaker in the sync loop. After K consecutive documents (for example 3) fail
  with the same 4xx status, stop extracting, refund those attempts and leave the rest `pending`.
  Alternatively, treat a 400 whose message names the model or parameters as infrastructure.
- **Test:** `test_robustness_containment.py::test_a_provider_wide_400_does_not_condemn_the_whole_batch`

### R-09 · Medium: a quarantine-folder failure leaves a set quarantined with no reason, permanently
- **Trigger:** the quarantine share is offline or full when a set fails.
- **Observed:** reconcile commits `status=quarantined` before calling `quarantine_copy`
  (`reconciliation.py:368-370` and every other quarantine branch). The copy raises,
  `_persist_reason` never runs, and every later sweep returns early for a quarantined set
  (`reconciliation.py:326-339`). The result is a quarantined set with `reconcile_reason = None`.
- **Minimal fix:** route every quarantine branch through one helper that commits the status
  together with the reason text, then calls `quarantine_copy` in a `try`/`except` that only logs.
- **Test:** `test_robustness_containment.py::test_quarantine_folder_failure_still_records_the_reason`

### R-08 · Medium-Low: a pypdf error while *writing* the packet escapes reconcile
- **Trigger:** a damaged source PDF opens and its pages add, but lazy object resolution fails in
  `writer.write`. This was fault-injected; I could not build a real file that does it.
- **Observed:** only the read loop is wrapped in `MergeSourceError`. `writer.write(str(tmp))`
  (`merge.py:276`) is not. The `PdfReadError` escapes `reconcile_po_set`, the set is left
  `pending` with no reason code, and it is retried every night.
- **Minimal fix:** wrap `writer.write` in `try`/`except Exception` and raise `MergeSourceError`,
  after the existing `OSError` handling.
- **Test:** `test_robustness_containment.py::test_pypdf_error_during_packet_write_quarantines_the_set`

### R-07 · Medium-Low: an infrastructure failure at write time permanently quarantines a consistent set
- **Trigger:** disk full, the output share offline, or a 60 s output-name lock timeout.
  `filelock.Timeout` subclasses `TimeoutError`, which is an `OSError`.
- **Observed:** `except (MergeSourceError, OSError)` (`reconciliation.py:778-796`) gives
  `packet_write_failed` and the set is **quarantined**. Quarantine is sticky under sweeps, so after
  the disk is fixed the set stays quarantined until an operator acts. Nothing wrong is delivered:
  no partial or tmp packet is left and the inputs are kept. Both are pinned.
- **Minimal fix:** for a plain `OSError` (not `MergeSourceError`), leave the set `pending` with
  reason `packet_write_failed`, so the next sweep retries it. Keep quarantine for source errors.
- **Test:** `test_robustness_containment.py::test_set_recovers_after_disk_full_is_fixed`

### R-15 · Low (fault-injected): unguarded sweep steps can abort the sync
- **Where:** `reclaim_stale_processing` (`sync.py:312`, `596`), `resolve_unattached_documents` and
  `attach_unattached_to_open_sets` (`sync.py:434`, `438`), and the open-set and customs queries
  (`sync.py:460`, `488`) are not wrapped.
- **Observed:** any exception there aborts the sync before any set is reconciled. Example:
  `database is locked` past the busy timeout. I found no single-document input that triggers it.
- **Minimal fix:** wrap each step in `try`/`except`, `logger.warning(...)`, `errors += 1`.
- **Test:** `test_robustness_containment.py::test_a_failing_attach_sweep_does_not_stop_reconciliation`

### R-12 · Low: the "no line items" reason does not name the file
`vendor_document_has_no_line_items` builds a detail naming the empty DN or SI, but the code is
missing from `_DETAILED_REASONS` (`reconciliation.py:193`). The stored reason is the generic
sentence.
- **Fix:** add the code to the set.
- **Test:** `test_robustness_batch_sync.py::test_dn_without_lines_reason_names_the_file`

### R-13 · Low (only with `split_combined: true`): the split-gate reason is overwritten
`_handle_combined` writes `split_failed:page_count_mismatch`. In the same sync,
`_quarantine_broken_document` rewrites the same `QUARANTINE.txt` with "attempt cap reached".
The report also claims 3 attempts, although the VLM was called once.
- **Fix:** skip `quarantine_document` in `_quarantine_broken_document` when the document is already
  quarantined, or append to the report instead of overwriting it.
- **Test:** `test_robustness_containment.py::test_failed_split_gate_reason_survives_in_the_report`

### R-17 · Low: zero-byte files and subfolder files are skipped invisibly
A zero-byte `.pdf` fails `is_file_stable` (`ingestion.py:33`) before the ingest rejection can run.
Any PDF in a subfolder of `input/` is never listed (`find_input_pdfs`, `ingestion.py:231`). Neither
gets a Document row, an error count or (for subfolders) a log line, so neither shows on the
Unclassified page. Nothing is deleted, but a vendor drop into a subfolder is never processed.
This is the same class as R-03.
- **Minimal fix:** log and count both. Give a zero-byte file older than `_RECENT_WRITE_SECONDS`
  the R-03 treatment. Either scan subfolders or warn about them in the sync summary.
- **Test:** `test_robustness_batch_sync.py::test_skipped_input_is_visible_to_the_operator[*]`

### R-18 · Low: test and dev runs write raw extractions into the repository
`extract_document` dumps raw JSON into the CWD-relative `data/raw_extractions` whenever that folder
exists (`extraction.py:984-997`). This bypasses the conftest path redirect, so any test that
reaches `extract_document` from the repo root writes there.
- **What I did:** my first runs wrote about 96 files. I deleted the 86 I could attribute to these
  tests by filename and content. About 10 ambiguous `*_combined.json` and `cae8748682056ba8_*`
  files from the same window were left, because existing tests write the same names. The harness
  now `chdir`s to `tmp_path`.
- **Minimal fix:** derive the dump folder only from `cfg.paths` and gate it on an explicit setting.

### Other findings (no xfail test)
- **No `audit_log` row for automatic quarantines (observed and pinned).** In the batch run about
  12 sets and 15 documents were quarantined, and the `audit_log` table stayed **empty**. An engine
  quarantine of a set, or a document quarantine, writes only `QUARANTINE.txt`. `AuditAction` has no
  quarantine value, so the database has no record of when or why a set entered quarantine. Only
  `reconcile_reason` holds that, and it is later overwritten. This contradicts CLAUDE.md, which says
  `quarantine.py` "writes audit rows"; that is true only for archive, force-merge and customs
  append. Pinned by `test_robustness_batch_sync.py::test_no_audit_row_is_written_for_any_automatic_quarantine`.
- **Outage amplification.** On a dead or hung provider, each document costs **8 HTTP calls per
  sync**: 4 Prefect tries in the ingest loop, then 4 more when recovery step 1 re-extracts the
  still-pending document. At a 60 s timeout that is about 8 minutes per document, so a 200-document
  night on a hung endpoint does not finish. A circuit breaker (see R-06) fixes both. Pinned in
  `test_provider_fault_amplification_per_document`.
- **The sync summary counts a document held in `processing` by another worker as `extracted`**
  (`sync.py:649`). Cosmetic.

---

## Scenario matrix (observed outcome, then verdict)

"Held" means valid, unattached and listed on the Unclassified page. "Doc-quarantined" means the
row is `failed`, the file is copied to `quarantine/_documents/<sha>/` with `QUARANTINE.txt`, and
the input copy is removed. No case below wrote an audit_log row (checked for the batch run). In every case the sync continued
unless stated.

### VLM output

| Input | Observed | OK? |
|---|---|---|
| `document_type` UNKNOWN, all else null | held, UNKNOWN | yes |
| `document_type` null / "INVOICE" / lowercase "po" | schema fail, 3 calls, doc-quarantined | yes (recall cost) |
| explicit `null` in `line_items` / `po_reference_ambiguous` / `components` / `page_count` | schema fail, doc-quarantined | yes, but loses readable docs; a lenient before-validator would help |
| PO with null number | held `missing_po_number` | yes |
| UNKNOWN or SKIP carrying the good set's PO number | held, never attached | yes |
| COMBINED (split off, the default) | COMBINED valid, unattached, file quarantined whole | yes |
| ambiguous=true **and** `po_reference` filled | held (ambiguous wins) | yes |
| `"4500012345/46"` without the ambiguity flag | keyed as one long number, held | yes |
| prose reply / truncated tool JSON / truncated HTTP body / HTML body with status 200 | content fail, 3 calls, doc-quarantined | yes |
| `line_item_no` as float 1.5; `quantity` as bool; `po_reference` as a JSON number | schema fail, doc-quarantined | yes |
| quantity `-5`, `0`, `1e13`, `1e20`, `1O`, `10 EA`, `1 200`, `1.234,56`, `0.001`, `2.555`, `½`, NaN, Infinity | stored as 0, set quarantined `non_positive_quantity` | yes (some are false quarantines) |
| quantity `2.5`/`2.50`/`2.500`, `1,200.00`, JSON 12.5, Arabic-Indic `١٠` | parsed exactly; set merges | yes |
| `5.000` meaning 5000 (European) | read as 5 | documented limitation (PRODUCT §8-L) |
| unit_price of about 9.2e15 or more | **stuck `processing`** | **no, R-02** |
| every field of a row null, ×3 | 3 rows kept with qty 0 → quarantine | yes |
| 2,000 rows; a 100k-character description; unknown extra fields | stored / ignored | yes |
| duplicate line no. on the DN summing to the PO | merges | yes (by design) |
| duplicate line no. **on the PO** (`1: BOLT 5`, `1: WASHER 5`) against DN `1: BOLT 10` | merges | accepted-by-design risk (CORE_LOGIC §3 sums both sides); worth a second look |
| repeated table header returned as a row | qty 0 → quarantined | yes |
| numberless TOTAL row on the PO | set **pending "Waiting on more deliveries" forever** | safe but misleading |
| numberless TOTAL row on the DN | orphan → quarantined | yes |
| vendor rows with descriptions only | description fallback, merged with an amber note | yes |
| PO 10/20 vs DN 1/2 (ERP step numbering) | quarantined | documented (§7) |
| vendor line no. that the PO lacks | orphan → quarantined | yes |
| PO or DN with an empty line list | quarantined | yes |
| re-read now ambiguous / now digit-less | detached | yes (R-11 fixed during the audit) |

### Provider faults

| Fault | Observed | OK? |
|---|---|---|
| 402 key limit mid-sync | doc pending, attempt not counted; set pending; merges on the next sync once the key is restored | yes |
| 500 / ReadTimeout | pending, 0 attempts, 8 calls per sync | yes (see amplification) |
| 400 | content fail → quarantined | **R-06** if provider-wide |

### Files

| Input | Observed | OK? |
|---|---|---|
| zero-byte `.pdf` | skipped every night: no row, no error count, stays in input | R-17 (nothing lost, but invisible) |
| `input/sub/*.pdf` | never scanned, no log | R-17 |
| file over the size cap | moved to quarantine with a reason; no row | R-03 |
| `notes.txt` | ignored | yes |
| non-PDF named `.pdf` | moved to `quarantine/_documents` with a reason; **no row** | R-03 |
| truncated PDF, under 5 minutes old | left in input, error counted, retried | yes |
| truncated PDF, over 5 minutes old | moved to quarantine, no row | R-03 |
| password-protected PDF | same as truncated (pypdf refuses at ingest) | R-03 |
| 35 pages (over `vlm.max_pages`) | refused before any HTTP call, doc-quarantined after 3 counted attempts | yes |
| byte-identical copy under a long unicode name with spaces | one row (first name in sort order wins); both copies cleared at merge | yes |
| file deleted between poll and ingest | error counted, sync continues | yes |
| file still growing | skipped, picked up next sync | yes |
| transient `PermissionError` on read | **good file moved out** | **R-05** |
| `stat()` raising during the poll | **whole sync aborts** | **R-04** |
| stored copy gone and storage broken | **file deleted everywhere** | **R-10** |
| identical files re-sent after merge | phantom empty set; inputs stuck | **R-16** |
| PO revision (different bytes) before merge | 2 PO documents → pending `multiple_po_documents` | yes |
| PO revision after merge | a fresh pending set; the packet is untouched | yes |
| split on, failing page-count gate | parent failed, no child cut, quarantined | yes (reason lost: R-13) |

### Grouping

| Input | Observed | OK? |
|---|---|---|
| `PO 4500123`, `P.O.No.4500123`, `PO#…`, `Order No: …`, `… Rev 2`, `…, 0`, NBSP, ZWSP, en or em dash, `4.500.123`, `4,500,123`, a 400-digit PO | same key; a 400-digit PO merges, packet name capped at 100 characters | yes |
| `N/A`, `OPG`, `4500123 REV`, `… Rev 2 Rev 3`, `PO: 4500123 (Rev.1)` | no key → held | yes (the last is a false hold) |
| `0045001234` vs `45001234`; `4500123-1` | different keys → DN waits | safe, recall cost |
| `12/345` vs `123/45` | same key and set (digit-only by design) | design risk; reconciliation is the only guard |
| `TOGA-PO-25-26-163` vs `ARM-…` | DN held as a letters conflict; set pending | yes |
| `45OO123`, `4500l23`, full-width digits | **shorter valid key; attaches to an unrelated set; can merge** | **R-14** |

### Containment, crash and merge-time faults

| Fault | Observed | OK? |
|---|---|---|
| sync lock already held | `skipped`, nothing touched | yes |
| `processing` row older than `stale_processing_seconds` (crash) | reclaimed, re-read, merged | yes |
| fresh `processing` row (another worker) | left alone, counted as "extracted" | yes (cosmetic) |
| output folder is a file | quarantined `packet_write_failed`, no packet | yes, but see R-07 |
| ENOSPC at merge | quarantined, no partial or tmp file, inputs kept | yes, but sticky (R-07) |
| pypdf error at write | set left pending, no reason | R-08 |
| stored file vanished before merge | quarantined `packet_source_unreadable`, no packet | yes |
| quarantine folder unwritable | quarantined with no reason | R-09 |
| attach sweep raises | sync aborts | R-15 |

---

## What I could not exercise (be careful with these)

- **The real Prefect process work pool.** Every test runs the flow in-process with an ephemeral
  Prefect server. Process launch, worker death mid-task and pool concurrency were simulated only
  through DB state (a stale `processing` row).
- **Real OpenRouter semantics.** The status codes, the error bodies and which failures return 400
  vs 402 vs 429 are assumed. HTTP-level truncation (connection cut mid-body) was approximated by a
  200 with a malformed body. A real `RemoteProtocolError` should land on the infrastructure path,
  but that is untested.
- **Real Windows share-mode locks and SMB behaviour.** Locks were simulated with a monkeypatched
  `PermissionError`, and disk full with a patched `PdfWriter.write`. No real ACL or read-only
  share was used.
- **A real concurrent Force Merge or a concurrent route plus sync.** Not run. Only the inter-process
  sync lock was checked.
- **Real vendor PDFs, image-only scans and multi-page tables with repeated headers.** These were
  represented by blank PDFs plus a scripted VLM reply. Whether the real model emits the patterns
  above (for example a header row, an O/0 misread or explicit nulls) is a model question, not
  tested here.
- **Customs append under fault.** Not attacked beyond reading the code.
- **Real large files and real 200-page rasterisation memory.** The size cap was hit only with the
  limit patched small, and only the page-count gate was hit because it refuses before rendering.
- **Code under review elsewhere.** `src/` was being edited by another agent during the audit (R-11
  was fixed mid-run). Line numbers are as of the end of the audit. Re-run the suite after that
  work lands: strict xfails turn into XPASS failures when a defect is fixed.
