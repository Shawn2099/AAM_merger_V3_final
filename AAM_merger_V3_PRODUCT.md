# AAM Merger V3 — Product Definition

**Status:** Authoritative. This document describes the product as it is built
and as it is intended to behave.

**Supersedes:** `AAM_merger_V3_SPEC.md` for the reconciliation, matching and
merge sections, and `AAM_merger_V3_business_logic.md` §8, §10, §11 wherever the
two disagree. Where this document and the older specs conflict, **this
document wins** — the older specs describe a richer matcher that was
deliberately retired. They are kept for history and for the vendor research
they contain, not as a description of current behaviour.

**Decision of record:** 2026-09-29. The simplified reconciliation core is the
product, not a scaffold. The feature set that used to surround it has been
removed from the code, not merely disabled.

---

## 1. What this system does

Input folder → SHA-256 dedup → VLM extraction → grouping by PO number →
reconciliation by line number → automatic merge into one PDF per PO Set →
output folder, with a web dashboard for the human reviewer.

It automates the mechanical quantity check. It does not replace the CA. Every
merge it performs is a claim that quantities agree, and §8 lists precisely what
that claim does and does not cover.

---

## 2. The rule, in full

This is the whole matching and reconciliation algorithm. There is no second
implementation anywhere in the codebase.

```
1. Group BOTH sides by line_item_no, summing each group.
   Line numbers are normalised first: whitespace stripped, leading zeros
   removed ('01' -> '1'). Alphanumeric forms survive verbatim ('1a', '1-1').

2. A vendor row with no usable line number may fall back to its description,
   and only then. It never overrides a real line number.

3. For every PO group, PO quantity must equal the vendor group in the DN pool
   AND, independently, in the SI pool. Exact scaled integers. No tolerance.

4. A vendor group with no PO counterpart is an orphan: quarantine the set.
   A delivered line that resolves nowhere cannot be ignored.

5. A PO line with no line number at all is unresolvable: quarantine the set.

6. Zero or negative anywhere: quarantine the set. Never evaluate it as a
   normal case.

7. Anything that fails: quarantine the whole set. There is no partial pass.
```

**Two different questions, two different answers.** This is the distinction
most worth being precise about:

- **Matching** — how a vendor row is attached to a PO line — uses exactly
  **two** signals: `line_item_no` (primary) and, for a row carrying no usable
  number, its `description` (fuzzy, threshold from `matching.fuzzy_description_threshold`).
- **Reconciling** — whether the numbers agree, i.e. the pass/fail test —
  uses **quantity only**.

So "quantities are the only signal" means *no price, no part number, no UOM, no
positional order and no ERP step-numbering participate in the decision*. It
does **not** mean matching is number-only: the description fallback in step 2
above is a real matching signal, and it is the only one besides the number.
§7 explains why each removed input was removed; §8 states what that costs.

### Where it lives

| Function | File | Role |
|---|---|---|
| `group_by_line_no` | `services/matching.py` | steps 1, 2, 4, 5 |
| `_best_desc_key` | `services/matching.py` | the description fallback in step 2 |
| `compare_aggregates` | `services/matching.py` | step 3's comparison, orphan reporting |
| `compare_po_set_lines` | `services/reconciliation.py` | the whole comparison, one entry point |
| `reconcile_po_set` | `services/reconciliation.py` | applies status transitions and merges |

`compare_po_set_lines` is called by **both** the engine and the PO Set detail
view. The dashboard cannot disagree with the engine, because it runs the same
code. This was a real defect before: the detail page carried a private copy of
an older algorithm and could show "Match" on a set the engine had quarantined.

---

## 3. Status model

Exactly five states, unchanged:

| Status | Meaning | Leaves the set by |
|---|---|---|
| `pending` | not evaluated yet, or awaiting a delivery | next sync, or Redo matching |
| `mismatched` | both sides reported a line and the quantities disagree | a human, or new data |
| `quarantined` | a line could not be identified, a quantity is non-positive, the documents disagree on the PO number, or the packet could not be named | a human |
| `blocked_customs` | customs toggle on, CUSTOMS + SHIPPING not both attached | uploading the missing document |
| `merged` | packet written; permanently closed | nothing — terminal |

The complete status-to-reason mapping is in **§3.1**.

**Partial delivery vs disagreement.** When a line fails, the distinction is
whether the vendor said anything about it:

- vendor reported **nothing** for a PO line → `pending`, reason
  `partial_fulfillment`. The set is waiting for a delivery that may still come.
- vendor reported **something** that differs → `mismatched`. Two documents
  disagree and a human has to decide which is right.

This is a deliberate narrowing of the original "no partial pass" rule. It is
implemented in `reconcile_po_set`, not in the comparison, because only the
engine knows the set's history.

**Every terminal state leaves a readable reason** on `po_sets.reconcile_reason`,
written by `reconciliation.explain`. Every reason code the reconciler can emit
must exist in `REASON_TEXT`; an unmapped code renders as "Awaiting further
processing", which understates a quarantine as a wait. If you add a reason
code, add the text in the same change.

### 3.1 Every status this engine can produce

Verified against `reconcile_po_set` — this is the complete list, not an
approximation.

| Status | Reason code | Trigger |
|---|---|---|
| `pending` | `missing_po_document` / `missing_dn_document` / `missing_si_document` | a required side has no document row yet — not yet evaluable |
| `pending` | `partial_fulfillment` | a PO line the vendor reported nothing about |
| `mismatched` | *(none)* | both sides reported a line and the quantities differ |
| `quarantined` | `non_positive_quantity` | any quantity zero, negative, or unparseable |
| `quarantined` | `po_reference_mismatch` | a document's PO number disagrees with its set's |
| `quarantined` | `po_line_missing_line_item_no` | a PO line has no line number to compare on |
| `quarantined` | `multiple_po_documents` | the set holds more than one PO document (config `reconciliation.single_po_document`) |
| `quarantined` | `unmatched_vendor_line` | a vendor row resolved to no PO line (see 3.2) |
| `quarantined` | `po_document_has_no_line_items` | the PO document was read but contains no line items |
| `quarantined` | `packet_naming_failed` | merged packet could not be named unambiguously |
| `blocked_customs` | *(none)* | customs toggle on, CUSTOMS + SHIPPING not both attached |
| `merged` | *(none)* | reconciled, customs gate clear, packet written — terminal |

`REASON_TEXT` carries every reason code the reconciler can emit (twelve, in
`services/reconciliation.py`). `mismatched`, `blocked_customs`
and `merged` pass no reason because the status already says everything a
reviewer needs.

> Retired 2026-09-30 (Layer-2 purge): `combined_unverified` is gone. A
> multi-doc PDF is split in Layer 1 into single-section child files
> (`combined/` → `input/`, sterile parent row); Layer 2 only ever sees
> PO/DN/SI rows and never names `COMBINED` — see the PLAN.

### 3.1.1 One PO document per set

A PO Set is expected to hold exactly one PO. With
`reconciliation.single_po_document: true` (the default, in `config.yaml`), a
set carrying two or more PO documents is **quarantined** with
`multiple_po_documents`, naming the files.

Without the gate, two POs in one set are summed into the baseline, which
silently doubles every quantity the DN and SI are then compared against — the
set could never reconcile, and the reason would not be obvious. This normally
means a re-issued PO or a mistyped PO number landing under the same key.

Turn it off only if your vendors genuinely issue split POs under one number.

### 3.2 Every flag the comparison can produce

Per-line flags, sorted priority 1 before 2. **There are exactly two types and
three reasons** — verified by driving `compare_po_set_lines` over every case.

| Priority | `type` | `reason` | Pool | Means |
|---|---|---|---|---|
| 1 | `identification` | `no_po_line_with_this_number` | DN or SI | vendor row's number is not on the PO |
| 1 | `identification` | `no_line_number_and_no_description_match` | DN or SI | vendor row has no number and no description clears the threshold |
| 2 | `quantity` | `quantity_mismatch` | DN or SI | both sides reported the line, quantities differ |
| 3 | `naming` | *(no `reason` key)* | — | packet had to be named from the PO number because no invoice number was extracted (§6) |

Priority 3 `naming` is the only flag that describes a successful outcome. It
is raised by `reconcile_po_set` rather than by the comparison, and it exists
so the PO-number fallback can never pass silently.

The other set-level identification flags — `po_reference_mismatch`,
`po_line_missing_line_item_no`, `multiple_po_documents` and
`packet_naming_failed` — are raised by `reconcile_po_set` and quarantine the
set directly; they are listed in §3.1 rather than as per-line flags.

Both pools are evaluated independently and reported separately, so a set can
carry a DN flag and an SI flag for the same line.

**Identification outranks quantity** because an unidentifiable line is not a
shortfall — the engine cannot say which item is missing, so guessing is worse
than stopping. Any identification flag quarantines the set outright; a set only
reaches `pending`/`mismatched` on quantity flags alone.

`unmatched_vendor_line` (the set-level reason) is the quarantine raised by
either identification reason above — the set-level code says *what happened*,
the per-line reason says *which row*.

**There is no price flag.** The retired matcher produced identification
variants (`LINE_REINDEXED`, `INDEX_DESCRIPTION_MISMATCH`,
`AMBIGUOUS_LINE_MATCH`) and a `price` flag. All are gone with §7.

---

## 4. Extraction

The VLM's entire job is PDF → structured JSON. Every decision after that is
deterministic code. The model id comes from `config.yaml` and is never
hardcoded.

The schema in `services/extraction.py` is the whole contract with the model.
Extracted, per document: `document_type`, `po_reference`,
`po_reference_ambiguous`, `document_number`, `vendor_name`, and per line
`line_item_no`, `description`, `quantity`, `unit_price`, `dn_no`.

**`quantity` is the number and nothing else.** No unit, no UOM, no currency
symbol, no surrounding text. A cell printed as `12.5 EA` must come back as
`12.5`; `1,200.00 M3` as `1200.00`. The prompt states this explicitly (STEP 4)
and the schema field repeats it, because the strict parser in §5 will reject a
unit-bearing string and quarantine the set for no legible reason.

**UOM is not extracted, not stored, and not reconciled.** There is no UOM
column, by decision — see §8 for what that costs. `unit_price` is extracted
because the merged packet carries it, but it takes no part in matching or
reconciliation (§2, quantities are the only signal).

**Not extracted:** confidence score, part number, UOM, line type. If a value
is not in the schema it is not stored and not used.

Two extraction behaviours are load-bearing and easy to break:

- **Tax, freight, fee, discount and subtotal rows are excluded by the prompt**
  (STEP 3: *"EXCLUDE subtotal, VAT, tax, total, amount-in-words, payment terms,
  signatures"*), not by a stored row-kind column. See §8.
- **`po_reference_ambiguous` must survive into the result dict.** The VLM is
  asked whether more than one PO number is printed. A document that lists
  several POs is left unattached rather than attached to whichever PO was named
  first, because guessing strands the other POs' invoices. Omitting this key
  from `_call_vlm`'s return silently disables the whole feature — it did once,
  and nothing caught it because the extraction path had no test coverage.

---

## 5. Quantity parsing

`parse_quantity_scaled` in `services/sanitizer.py` turns a printed quantity
into an exact integer scaled ×1000, using the `matching.locale` setting
(default `en_IN`).

**Accepted:** `"50"`, `"12.5"`, `"350.00"`, `"1,000"`, `"1,00,000"`, and any
value with **at most 2 significant decimal places**.

**Trailing zeros are formatting, not precision.** `"12.45000000"`, `"12.450"`
and `"12.45"` are the same number and all parse identically. They are
normalised *before* the precision check, because vendors emit them constantly
(Excel and OCR round-trips) and rejecting them would quarantine documents that
are perfectly legible. A precision rule that counted characters instead of
significant decimals would be a bug, not strictness.

**Rejected** (raises → the row stores 0 → the set quarantines on
`non_positive_quantity`): empty, unparseable, inner spaces (`"1 000"`), more
than 2 significant decimals, zero, negative, non-finite, and anything above
1e12.

**Why 2 decimals.** Real quantities are whole units, occasionally `.50`. A
third significant decimal means the printed string is something else — and the
realistic case is a European thousands separator: `"1.234"` printed by a vendor
who means `1234` would otherwise be read as `1.234`, a **1000× error**. Rejecting
it routes the set to quarantine, which is the safe direction. See §8.

**Storage stays ×1000.** Existing databases and the column semantics are
unchanged, so no data migration is needed. With a 2dp input contract the low
two digits are always zero, which is harmless — every comparison still
operates on an exact integer, never a float.

Rejecting is deliberate throughout. A false quarantine costs a human ten
minutes; a silently mis-scaled quantity ships a wrong document.

**The locale is an assumption, not a detection.** `matching.locale` is a fixed
value, never inferred from the document. The 2-decimal rule closes the common
European-thousands case, but the trailing-zero form (`"17.200"`) is
indistinguishable from a legitimate `17.2` and is still read as 17.2. Set
`matching.locale` to match your vendors. A mixed-vendor estate cannot be served
correctly by one locale and would need per-document locale detection, which
this product does not do. This is a known open risk, not a solved problem.

---

## 6. Merge

**Order** — `merge.legal_order` in `config.yaml`, default
`SI → DN → PO → SHIPPING → CUSTOMS`. Types absent from the list
append in first-seen order, so a new manual type never silently vanishes.
`COMBINED` is not in the order and never reaches the merger: split parents
are sterile rows with no `po_set_id`, so no set ever contains one.

**Naming — invoice number, falling back to the PO number.** The packet is
named from the SI document's own number (`si_no`, else `invoice_no`). Nothing
else. A number printed on a DN or a PO is never used, because a packet named
from the wrong document is worse than an unnamed one. (Retired 2026-09-30:
a `COMBINED` document used to count as the SI-bearing document. It no longer
does — children re-extracted from the split carry their own SI section and
its number is the packet's number.)

**When no invoice number was extracted at all**, the packet is named from the
PO number instead: `<po_no>.pdf`. The quantities are what reconciliation
proves, so a missing label must not veto an otherwise correct packet. The
fallback is never silent — it raises a `naming` flag (§3.2) and the dashboard
shows *"No invoice number was extracted; packet named 'X.pdf' from the PO
number"*, so a reviewer who expects an invoice-named file can see why it isn't
one. A PO number alone is safe as a filename: only one open set exists per PO
key at a time, and a genuine duplicate still raises in `_resolve_output_path`.

Force Merge is the operator's override and may look at any document's invoice
number, then the PO number.

**Collisions fail closed.** `_resolve_output_path` raises `MergeNamingError`
rather than overwriting a packet belonging to another PO Set; the reconciler
catches it and quarantines. A clobbered delivered packet is never acceptable.
This is the failure mode when one invoice number legitimately covers two PO
Sets — the second quarantines instead of overwriting the first.

**Refusals.** Auto-merge refuses when the set is `mismatched`, `quarantined` or
`blocked_customs`, when the customs gate is unsatisfied, when the output name
is taken, or when the set has **zero line-item evidence**. Force Merge bypasses
the gates by design but still refuses to write a 0-page PDF.

**Immutability.** Once `merged`, the set is closed. Re-merging returns the
existing path. A later document with the same PO number starts a new PO Set.

**Force Merge writes an audit row on every path**, carrying the customs
document count, the output filename, and the operator's written justification.
The justification must be ≥ 20 characters when supplied.

### 6.1 Merge Now — the gated manual button

`POST /po_sets/{id}/merge` (button: **📦 Merge Now** on the PO Set detail page)
re-runs the comparison and merges **if** the set is now eligible.

It exists because uploading the two customs documents does **not** itself
trigger anything: the upload route only updates `customs_doc_count`, so a
customs-blocked set sits at `blocked_customs` until the midnight sync or a
manual re-check. Without this button an operator who has just finished the
manual step sees nothing happen.

**Merge Now is not Force Merge.** Every gate still applies — quantities must
reconcile, the customs toggle must be satisfied, the packet must be nameable.
It is safe to press at any time: if the set is not eligible the response
carries the status and reason and nothing is written. Force Merge is the
separate, deliberately unconditional override.

---

## 7. What was removed, and why

Each of these was implemented and has been deleted. The code is gone, not
disabled.

| Removed | Reason | Cost — see §8 |
|---|---|---|
| **SKU / part-number rescue** | `part_no` was blank on **every DN sample across all three real vendor sets** reviewed. It cannot be relied on, and the column was permanently NULL because the VLM was never asked for it. | L |
| **Reindex detection** (`LINE_REINDEXED`) | Needed a description-vs-number sanity gate to be useful, and that gate is the next row. | L |
| **ERP step-10 alignment** (PO line 10 ↔ DN line 1) | A real vendor pattern, but it is a second numbering convention to maintain, and it can be defeated by a coincidental collision. | M |
| **Description sanity guard** on exact-number hits | This is the gate that catches "right line number, wrong item". Removing it is the single most consequential decision in this document. | **H** |
| **Conflicting-description quarantine** (old FR-8.4) | Quantities are the only signal. The engine sums; it does not judge. | **H** |
| **Fuzzy margin / ambiguity resolution** | With no second candidate to choose between, the margin has nothing to measure. | — |
| **Price check** (FR-11.1) | Secondary, and a price-only mismatch does not block a merge. Prices still ride along in the packet. | L |
| **`line_items.part_no` column + index** | Never populated. | L |
| **`line_items.line_type` column** | Never populated. Tax rows are excluded by the prompt instead. | M |
| **`line_items.uom`** | Never existed. Deliberately. | M |
| **Extraction `confidence`** | Binary valid/failed is the contract; a score nobody acts on is noise. | — |

Net: ~250 lines of matching code, 1 migration adding a column and an index,
and 4 test files deleted.

---

## 8. Accepted limitations

These are real. They are the price of §7, stated plainly so nobody discovers
them on a live document.

### H — a wrong item can merge (highest consequence)

Two rows on the same printed line number with the same quantity but describing
**different items** are summed, and the set reconciles.

**Real example.** ADES/NOMAC set `D7264-PO186000-013-01`. The PO orders part
`TLMKC`. The DN and SI ship part `Runclimb-VALVE` instead. Same line number,
same quantity, quantities reconcile exactly, packet merges. The guard that used
to catch this was the retired description-conflict check.

> Mitigation today: the CA's human review. This is the case that makes it
> load-bearing rather than a formality.

> If this ever needs closing, the cheapest fix that does not reopen the retired
> matcher is a single pairwise description check inside `group_by_line_no`:
> when two or more vendor rows share a line number, compare their descriptions
> and quarantine if any pair falls below the threshold. ~15 lines. It was
> declined on 2026-09-29; that decision can be revisited.

### H — UOM is invisible

No UOM column, no conversion, not extracted at all (§4). A PO ordering
**1 BOX** and a DN delivering **1 EA** are both the number 1 and reconcile. A
12-piece box counts as one unit.

This is a deliberate narrowing: UOM was blank or inconsistent across the real
vendor samples, and a conversion table cannot be built from data you do not
trust. The cost is that unit-quantity disagreements are invisible to the
engine, and only a human comparing the printed documents can catch one.

### M — tax rows depend on the prompt

There is no `line_type` column. Tax, freight, fee and discount rows are removed
by the extraction prompt. If the VLM fails to exclude one it is summed like any
other line and the set mismatches. Safe direction, false quarantine.

### M — step-10 numbering quarantines instead of merging

A PO numbering 10, 20, 30 against DNs numbering 1, 2, 3 does not map. The DN
lines become orphans and the set quarantines. Safe direction, but a human has to
resolve every such set by hand.

### M — differing line-number conventions quarantine

Real case: Bridon `100060000080880`. PO prints line `1-1`; the DN and SI print
line `1`. The SKU rescue used to bridge this. Now `1` has no PO counterpart and
the set quarantines. Safe direction, recurring cost.

### L — inner-space quantities quarantine

A vendor printing `"1 000"` gets a quarantine, not a merge. Deliberate (§5).

### L — trailing-zero European decimals are still mis-scaled

The 2-decimal rule rejects `"1.234"` (European 1234), so the common form is
caught. But `"17.200"` is indistinguishable from a legitimate `17.2` and is
still read as 17.2. Since real quantities are whole units or occasional `.50`,
this shape is not expected from our vendors — accepted as an open risk rather
than solved (§5).

### L — locale assumption

`matching.locale` is a fixed value (`en_IN`), never inferred from a document.
The client base is **English-language UAE and USA**, which use `.` as the
decimal separator — identical to `en_IN` — so in practice this is correct.

The one divergence is **Western digit grouping at six digits or more**:

| Printed | `en_IN` (default) | `en_US` |
|---|---|---|
| `100,000` | rejected | `100000` |
| `1,234,567` | rejected | `1234567` |
| `1,00,000` | `100000` | rejected |

Indian grouping writes six digits as `1,00,000`; Western grouping writes them
as `100,000`. A row that fails to parse is stored as 0 and the set quarantines
as `non_positive_quantity` — a **false quarantine, never a wrong merge**.

Everything else in the real range is identical under both locales: whole
numbers, `.50` decimals, four-digit grouping (`1,234`), and 5-digit round
numbers. Checked against the client base on 2026-09-29: six-digit quantities
have never been observed, so this limit is **accepted rather than engineered
around**. A dual-locale parse (accept when `en_IN` and `en_US` agree, reject
when they disagree) is the obvious fix if it ever bites, and would be a small,
contained change to `parse_quantity_scaled`.

**If a six-digit quantity appears in production, the fix is one config line, no
code:** set `matching.locale: "en_US"` in `config.yaml`.

Two related risks were checked and are *not* real — babel's strict mode rejects
both under every locale, so they cannot occur:

- comma-as-decimal (`1,5`, `12,50`) — so the "is this 12.5 or 1250?" ambiguity
  never arises
- space grouping (`1 000`)

European `1.234` is caught by the 2-significant-decimal rule in §5.

### L — no price flag

Prices are carried in the packet but never compared, so a price-only
discrepancy is never surfaced to the reviewer.

### M — a broken document is quarantined, and the operator puts it back

**The rule: anything broken, or that cannot be confirmed, is quarantined.**
Nothing uncertain is allowed to look like normal work, and nothing broken is
allowed to keep running.

When a document exhausts its attempt cap it is quarantined at both levels that
apply to it:

- **The document.** Its input-folder copy is removed and the file is copied to
  `quarantine/_documents/<name>/` with a `QUARANTINE.txt` stating the reason.
  This is what stops the loop: `ingest_file` copies into `stored/` and leaves
  the original in the input folder, and input is only cleared when a set
  merges, so without this a dead PDF is re-hashed, de-duplicated to the same
  failed row and re-counted as an error on every run, forever. The `stored/`
  copy is kept, never moved, so the document row stays valid.
- **The PO Set, if the document belongs to one.** A set containing a document
  that cannot be read cannot be verified, so it is moved to `quarantined`
  rather than left in an apparently-normal `pending` state waiting on a
  document that is never going to arrive.

**To put a quarantined file back.** Enter its PO number on the unclassified
row — this attaches it to a PO Set, creating one if needed — then open that set
and press **Redo/Re-extract**, which resets the attempt cap for every attached
file and re-runs extraction. Verified end to end by
`tests/test_failed_document_recovery.py`.

**Why a human has to do that step.** A failed document never produced a PO
number, so it was never grouped and has no PO Set; the set-scoped
`POST /po_sets/{id}/redo_extract` cannot reach it. Reclassifying the type
alone does not help — `POST /unclassified/{id}/reclassify` resets the attempt
count **only** when the new type is `COMBINED`, so a document reclassified as
PO, DN or SI keeps its dead status and is skipped identically on the next run.

**What stays pending, and why that is not the same thing.** A set with a
genuinely absent document is left `pending`, not quarantined — the vendor may
simply not have sent it yet, and POs arrive over days. The engine cannot tell
"not yet sent" from "sent and broken" until the broken one is attached to the
set, which is what the PO-number step is for. The deliberate asymmetry: an
incomplete set never merges either way, so leaving it pending risks nothing.

---

## 9. Non-goals

Not built, by decision: authentication and roles (accepted risk, LAN-only
tool); multi-server deployment; post-merge distribution, email drafting,
company lookup; a UI mode for editing extracted line items; per-document locale
detection; UOM conversion; price reconciliation.

---

## 10. Operating notes

**No authentication.** Anyone with LAN access can force-merge or delete. This is
a documented accepted risk, not an oversight. The audit log is the
accountability record, not access control.

**`cfg_path` is not a parameter.** `POST /sync` and `GET /sync/status` once
accepted a caller-supplied config path, which let any LAN host redirect the
whole pipeline and create directories as the service account. That parameter
has been removed. Do not reintroduce a request-supplied path.

**Windows paths in `config.yaml`.** Use forward slashes, or single quotes. A
double-quoted `C:\AAM\input` is a YAML escape error and the app refuses to
start.

**`backup.*` config is not wired.** NFR-6 backup is a runbook item
(`DEPLOY.md`), not code. `make backup` is Linux-only and unusable on the WS2016
target — do a SQLite `.backup` from a Windows host instead.

**`max_concurrent_extraction_tasks` is not enforced.** Extraction is
sequential, so the cap is satisfied trivially. Do not treat the setting as a
concurrency control.

**A run summary is only as honest as its worst document.** The sync summary
(`processed` / `extracted` / `errors`) must be computed from the *persisted
document row*, never from whether a Prefect task raised. Prefect places a task
in a `COMPLETED` state whenever it returns any Python object, and
`extract_document` deliberately returns normally once a document has exhausted
its attempt cap — a terminal failure. Trusting the task return is how a
permanently lost document came to be reported as `errors: 0, extracted: 4`
against three real successes. Read
`app/flows/sync.py::_persisted_extraction_status`. The same principle as
batch-recovery practice generally: the updated tables are the point of truth,
not what the log claims.

**A failed extraction is quarantined.** Reaching the attempt cap marks the
document `failed`, logs at ERROR, counts in the summary, and moves the file
out of the input folder into `quarantine/_documents/` — see §8 for the rule and
for how an operator puts it back.

**The unclassified view holds losses, not just unknowns.** A document whose
extraction failed keeps `doc_type = UNKNOWN` (nothing advances it), so it
stays listed in the holding area. The row and the page header both label and
count it as failed, because a reviewer must be able to tell a file that will
never be read from one that is merely waiting to be classified.

**A PO Set's detail page lists its files, with their read state.** The set's
own status does not explain why it is stuck: a set on `pending` because its SI
never parsed is indistinguishable from one still waiting for the vendor to
send a third document. Listing the files — filename, type, extraction badge,
line count — is what makes **Redo/Re-extract** an informed action rather than a
guess, and it is where a reviewer confirms a retry actually worked.

---

## 11. Changing this product

1. Read this document first. It is the contract.
2. A change to the rule in §2 needs a decision recorded here, in this file, and
   a test that fails before the change and passes after.
3. If you remove a guard, add the limitation to §8 **and** pin it with a test
   named `test_limitation_*` or `test_known_limitation_*`. A removed guard that
   leaves no trace is how this document came to be needed.
4. `AAM_merger_V3_SPEC.md` is history. If you change §2, update or strike the
   corresponding FR there too, or explicitly mark it superseded — do not leave
   two documents disagreeing.
