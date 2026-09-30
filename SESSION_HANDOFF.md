# Session Handover — AAM Merger V3

**Date:** 2026-09-29
**Branch:** `dev-simplified`
**State:** 521 tests, 0 failing. `ruff check` and `ruff format --check` clean.
**Last commit:** `52c5c1f` (working tree has uncommitted changes — see §7)

Read this first, then `AAM_merger_V3_PRODUCT.md` for the product contract.

---

## 1. What was done this session

Four things: fixed a reporting bug, stripped dead code, removed a document
type, and investigated an enhancement that turned out to be **not** what
anyone assumed.

### 1.1 The sync summary reported permanent data loss as a clean run

**The bug.** With one unreadable PDF among four documents:

```
FLOW SUMMARY : {'processed': 4, 'extracted': 4, 'errors': 0, ...}
corrupt.pdf  type=UNKNOWN status=failed attempts=3
```

The document really was `failed` in the database. The run was reported as
fully successful.

**Cause.** Prefect marks a task `COMPLETED` whenever it returns any Python
object (Prefect v3 docs, "Task return values"). `extract_document` returns
*normally* once a document has exhausted its attempt cap
(`src/app/services/extraction.py:268-277`) — a terminal failure. The sync
loop counted errors from task **exceptions**, so the final "successful" retry
erased the failure. `extracted` was derived as `processed - errors` and
inherited the same error.

**Fix.** Count from the persisted row, which is the only record of what
happened:
- `src/app/flows/sync.py` — new `_persisted_extraction_status()`; both
  extraction call sites gained an `else` branch that re-reads the row and
  counts it as an error if `failed`
- `src/app/services/extraction.py` — the cap exit now logs at ERROR with the
  filename and attempt count (it was previously **silent**)
- Also fixed: `extracted = processed - errors` could go **negative**, because
  `processed` only increments after ingestion succeeds while `errors` also
  counts ingestion failures. Now clamped with a comment.

Corroborated externally: Oracle's batch-recovery guide says to "treat the
database tables that will be updated as the final point of truth… regardless
of log messages."

### 1.2 Broken documents are now quarantined

Per the product rule — *anything broken or unconfirmable is quarantined*:

- **The document** — input-folder copy removed, file copied to
  `quarantine/_documents/<name>/` with a `QUARANTINE.txt` reason. This stops
  a real loop: `ingest_file` copies to `stored/` and leaves the original in
  input, and input is only cleared on merge — so a dead PDF was re-hashed,
  de-duplicated to the same dead row, and re-counted as an error **every
  night, forever**. The `stored/` copy is kept.
- **The PO Set**, if the document belongs to one — an unconfirmable set must
  not sit in an apparently-normal `pending` state.

A set with a *genuinely absent* document stays `pending`, not quarantined —
the vendor may not have sent it yet, and an incomplete set never merges
either way.

### 1.3 Failed documents are visible

- `templates/unclassified.html` — new Extraction column: red `Failed` badge
  with `n/3 attempts`, plus `Read` / `Reading…` / `Not read`. Header now says
  "N awaiting classification" with a separate red "N failed" badge.
- `src/app/api/routes/dashboard.py` — `unclassified_view` computes
  `failed_count`.
- PO Set detail page now lists its **related files** with extraction state,
  so a set stuck on `pending` says which file is missing.
  (`src/app/api/routes/po_sets.py`, `_doc_status_badge()`.)

### 1.4 `COMMERCIAL_INVOICE` removed entirely

5 code sites, 4 UI sites, 3 test files. `DocType` is now 7 types.
`DocType("COMMERCIAL_INVOICE")` raises `ValueError`.

Verified no DB migration needed: live `data/aam_merger.db` had **0** such
rows, and the column is `VARCHAR(18)` with no CHECK constraint.

### 1.5 Duplicate-hash upload was a silent no-op

Uploading bytes already owned by **another** PO Set fell through the dedup
branch: HTTP 302 to a page still showing the customs gate unsatisfied. The
gate could never clear and the operator couldn't tell a working upload from a
discarded one.

Now: **409** naming the owning PO Set. Same-file-same-set is idempotent and
redirects with a `?notice=` the detail page renders.

### 1.6 Dead code removed (GitNexus-verified)

| Removed | Evidence |
|---|---|
| `effective_dn_no()`, `clear_input_if_merged()` | zero callers, `DEFINES` edge only |
| `merge._is_blocked()` | byte-for-byte duplicate; call site now uses `customs.is_blocked` |
| `BackupConfig` + `backup.*` | inert, NFR-6 is a runbook item |
| `paths.unclassified_folder` | never read |
| `vlm.provider` | never read |
| `matching.sanity_description_threshold`, `fuzzy_margin`, `enable_sku_rescue` | retired v20.5 matcher |

Also corrected a `merge.py` docstring claiming `Filename = <invoice_no>_<po_no>`
— the code has never produced a two-part name.

Cleaned from **both** `config.example.yaml` and the real `config.yaml`.

---

## 2. THE IMPORTANT FINDING — two of my claims were wrong

Read this before acting on anything in §4.

### 2.1 Split deliveries are ALREADY handled correctly

I claimed a PO awaiting a second delivery note is quarantined as a supply
shortage, and that this was indistinguishable from a genuine shortfall. I was
wrong. `reconcile_po_set` handles it at `src/app/services/reconciliation.py:445-455`:

```python
qty_flags = [f for f in flags if f.get("type") == "quantity"]
has_real_disagreement = any((f.get("vendor_quantity") or 0) > 0 for f in qty_flags)
if not has_real_disagreement:
    ps.status = POSetStatus.pending
    return {"status": "pending", "reason": "partial_fulfillment", ...}
```

Measured end to end:

| Scenario | Status | Reason |
|---|---|---|
| DN-2 **not yet sent** | `pending` | `partial_fulfillment` |
| DN-2 **sent but short** | `mismatched` | quantity disagreement |

The zero-quantity signal is the discriminator. **I generalised from the
matching layer to the whole system without checking reconciliation above it.**
The end-to-end test is what caught this.

Consequence: **do not add `line_items.dn_no` as a reconciliation or matching
key.** The correctness property is already guaranteed; adding a VLM-read
string there risks false quarantines for no gain.

### 2.2 Two errors found in my own test code

- `Model.query` — legacy SQLAlchemy 1.x. This codebase is 2.x (`Session.query`).
- A regex cleanup removed a variable still in use.
- An earlier Hypothesis strategy raised `ValueError` inside `draw`, which
  Hypothesis treats as filterable, not a strategy rejection.

---

## 3. The enhancement to continue — per-line DN association

**This is the live work item. The fix is NOT implemented.**

### 3.1 The corrected model (user observed real vendor docs)

- The **PO** carries a per-row delivery-note reference — it tells you which
  delivery note will cover each line.
- The **DN** carries its own number as the **document number**.

An earlier draft of this analysis had it backwards. The corrected model
**inverts the lookup** and makes it much stronger:

| | Wrong (rejected) | Correct |
|---|---|---|
| Take | orphan's *per-line* values | orphan's *own* `documents.dn_no` |
| Match against | other docs' `dn_no` | **PO line items** with that value |
| Anchor type | another DN — often itself unattached | **a PO — always attached** |

The anchor is the key point: only PO/COMBINED mint sets
(`src/app/flows/sync.py::_ANCHOR_TYPES`), so a PO in the system is in a set
by construction. My original version could only anchor on another DN, which
is frequently unattached — an unreliable anchor.

### 3.2 The target scenario

Set A's PO prints `GDN-100` against lines 1–2 and `GDN-200` against line 3.
A DN arrives whose document number **is** `GDN-100`, but with no printed PO
number. It has everything needed and no way to use it: `po_no_normalized` is
empty, so grouping has no key. It sits unattached forever.

**Fix location:** `resolve_unattached_documents()` in
`src/app/services/grouping.py`, after the existing `if doc.dn_no:` block fails.

**Intended rule:**
1. Only when `documents.dn_no` is present (fallback — never override a value)
2. Query `line_items.dn_no == doc.dn_no`, narrowed to PO documents
3. Attach **only** if all matching POs resolve to exactly one set
4. Otherwise leave unattached (ambiguous, or unresolvable)
5. Association/grouping **only** — never reconciliation, never matching
6. Runs **last**, after the PO path has already failed

**Why it's safe:** a misattachment still has to pass PO vs DN vs SI quantity
reconciliation, so it quarantines rather than silently merging. This
asymmetry is why it is acceptable here and why §2.1's version was not.

### 3.3 Tests already written

`tests/test_per_line_dn_association.py` — 6 tests, all passing:

| Test | Status |
|---|---|
| `test_orphan_dn_with_its_own_number_cannot_attach_today` | proves the gap |
| `test_po_anchor_is_guaranteed_to_be_attached` | proves the anchor is sound |
| `test_intended_rule_single_matching_po_anchor` | **spec only** — flip when built |
| `test_intended_rule_refuses_when_po_anchors_span_two_sets` | **spec only** — flip when built |
| `test_intended_rule_refuses_when_no_po_mentions_the_number` | **spec only** — flip when built |
| `test_a_wrong_attach_cannot_produce_a_silent_merge` | bounds the risk |

The three marked *spec only* currently assert the rule's inputs, not the
engine's behaviour. **They must be inverted when the fallback is built** —
they should fail before the change and pass after.

Helper already in place: `_po_docs_referencing(session, dn_no)`.

### 3.4 Other proof files kept as no-regression bars

- `tests/test_split_delivery_defect.py` — 5 Hypothesis properties on the
  matching layer. **Property 1 asserts current behaviour and will need to
  change if the reconciliation layer is ever altered.** Properties 3 and 4
  are the control and the verdict-comparison.
- `tests/test_split_delivery_e2e.py` — 4 tests through the real engine,
  asserting `pending` / `partial_fulfillment`. These encode §2.1 and will
  fail loudly if anyone breaks the partial-delivery behaviour.

### 3.5 Caveat

**No real vendor PDFs exist in the repo.** All samples are synthetic, and
0 of 6 DN fixtures have any `dn_no` populated. The design is inferred from
the prompt's own wording (`extraction.py:66`) plus the user's observation,
not from data in the repo.

---

## 4. Open items needing a decision

1. **`line_items.dn_no` comment is wrong.** `src/app/models/models.py:104`
   says *"Used for attachment reverification only"* — no code reads it. If
   §3 is built, the comment becomes true; if not, fix the comment.

2. **`SKIP` in the VLM enum** (`extraction.py:117`) is emitted by the VLM
   but is not a `DocType`, so it silently becomes `UNKNOWN`. Every cover
   sheet and T&C page lands in the unclassified queue looking unreadable.
   Real behaviour — changing it alters classification.

3. **`test_verify_user_claims.py`** was broken externally and repaired
   mid-session. Its `test_duplicate_hash_manual_upload_is_reported_not_silent`
   was asserting the *old bug*; inverted to pin the 409 fix.

---

## 5. Verified-not-dead (do not remove)

Findings from reference counting that looked dead but are not:

| Item | Why it lives |
|---|---|
| `work_pool_name` | read by `src/app/main.py:91` (`/health`) |
| `customs_doc_count` | read by `templates/po_set_detail.html:57-58` |
| logging config keys | read in `main.py:34-36` via `getattr` |
| `po_reference_ambiguous` | safety flag preventing wrong-item merges |
| `is_blocked` | **CRITICAL** — 6 flows, gates auto-merge |
| `COMMERCIAL_INVOICE` | removed; but `documents.dn_no` / `line_items.dn_no` columns stay |

**Methodology note:** GitNexus `impact` over-reports on classes — it resolved
`BackupConfig` to its file and attributed all 10 files importing `config.py`.
Confirm with a Cypher `DEFINES`/`CALLS` query before trusting a class-level
verdict.

---

## 6. Still-running nuisance

Dead config keys were load-bearing in **5 test files** that set
`unclassified_folder` / `cfg.backup.folder`. Removing them broke those
fixtures, which masked the fact that nothing in `src/` ever read them. Fixed.
Watch for the same pattern if more config is removed.

---

## 7. Suggested next steps

1. Build the per-line DN association (§3) and flip the three *spec only*
   tests to assert real behaviour
2. Run the full suite — 521 tests, expect 0 failures
3. `gitnexus analyze --skip-git .` to refresh the graph
4. Decide the three open items in §4

## 8. Commands

```powershell
cd "final\AAM_merger_V3_final"
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --tb=line
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
gitnexus analyze --skip-git .
```

Full-text/BM25 search in GitNexus is disabled (LadybugDB FTS extension needs
network). Structural analysis unaffected.
