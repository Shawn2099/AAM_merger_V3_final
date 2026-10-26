# Second-read verification: replay analysis (2026-10-24)

Question: before we pay for a second VLM call, how much would "hold the document when two reads
disagree" actually catch, and how many good documents would it hold? All numbers come from runs
already saved in `results/`. No new API calls were made and nothing in `src/` or `tests/` changed.

Rule this serves (user, 2026-10-23): what gets merged must be correct. A doubtful document goes to
the operator (the Unclassified page). Precision matters more than recall.

---

## 0. Answer in one paragraph

On the adopted config (150 DPI images, effort high, prompt P5), a second identical read held
**4 of 6** wrong reads on the tuning set. All 6 are wrong PO references. The cost was **3.8%** of
correct reads held (8/208), all on two hard documents: doc 19 (illegible digits) and doc 22 (which
had one other, wrong read). On the held-out set the second read held **0 of 29 documents**: no
false holds, and nothing wrong to catch.

The P5 sample is tiny, so the catch rate was also estimated over every 150 DPI label (all
prompts). Counting only reads with a wrong **identity** field (type, number or PO), a second read
caught **28/106 (26%)**. The other **74% escaped** because both reads agreed on the same wrong
value. Most of those are **consistent misreads** from older prompts: `22613 , 0` read as `226130`
in 3/3 reads (docs 113 and 114, fixed by P5), and the two-PO DN 258 read as `260988` in 3/3 reads
under effort low/medium/seed. Counting every wrong value including spurious lines (FULL rule), the
pooled catch is 101/173 (58%) and the escape rate is 72/173 (42%). The difference comes almost
entirely from release-marker lines that P5 no longer produces. False holds pooled: 5.2%
(123/2373).

So a repeated read catches **noisy** errors only. On P5 the noisy errors are the ones left: doc
22's dropped digit, and 2 of the 4 wrong pairs on doc 258.

Across 3,865 scored line reads on images, **no line quantity was ever misread**. Those reads cover
only 156 distinct printed values, so the 95% upper bound is about 1.9% per value. The only wrong
quantities came from the Mistral-OCR transport (x1000 scaling errors), which is not used. So the
second read mainly protects the **PO number and the ambiguity flag**, not quantities.

**Recommendation:** repeat the same full call once (not a header crop) and compare app keys only.
On any disagreement, keep the document out of every PO set with a new hold reason
`read_disagreement`. Cost is about +$0.0006 and +12-15 s per document. Section 5 has the design.

---

## 1. Data and method

| Item | Detail |
|---|---|
| Runs replayed | 23 labels in `results/`, 3 repeats per document. Tuning: `gold.json`, 37 documents. Held-out: `gold_heldout.json`, 29 documents. |
| Adopted config | `P5` (tuning) and `H_P5` (held-out): `img150+eff=high`, `candidates/P5.py`, 0 failed calls. |
| Excluded or flagged | `P6` tuning (37/111 calls failed), `v2` (40 failed), `V2ok` (20 documents only). Pairs that include a failed call are dropped, because a failed call means retry, not hold. |
| Unit | Ordered pair of reads of one document. **A** is the read the app keeps; **B** is the checker. 3 repeats give 6 pairs per document. A 3-read rule (hold unless all 3 agree) is also reported. |
| What is compared ("app" mode) | Only what the app stores or keys on. Document type, exact. DN/SI document number, alphanumerics only, upper-cased; a PO's own number is the PO key, so it is not compared twice. PO key: `normalize_po_no` digits must be equal and `po_letters` must be `letters_compatible`. `po_reference_ambiguous`, exact. Lines: `{normalize_line_no: summed x1000 qty}` over all predicted rows, so a spurious line is a disagreement. Description, price and vendor are never compared. |
| "strict" mode | Document number compared as printed (whitespace collapsed) and PO letters exactly. Reported to show what a naive comparison would cost. |
| Rules | **ID**: type, document number, PO and ambiguity must agree. **FULL**: ID plus lines. **PO**: PO key only (used for section 3). |
| Truth (vs gold, via `score.py`) | **UNSAFE**: the read has at least one WRONG value (a field, a line quantity, an extra line, or a guessed PO on a two-PO document). **PASS**: correct and the app would place it. **PASS-HELD**: correct, but the app already holds it (ambiguity flag or no PO), so a hold costs nothing. **SAFE-FAIL**: null fields; already held. Only PASS reads can be false holds. |
| Catch | A is UNSAFE and B disagrees, so the document is held. |
| False hold | A is PASS and B disagrees. |
| Caveat | PASS-HELD treats "no PO key" as already held. `resolve_unattached_documents` can still place a DN by its `dn_no`, so a few of those holds could have a small cost. No P5 read is affected. |

## 2. Q1: catch rate and recall cost (same call repeated)

### 2.1 Adopted config, two reads, app-key comparison

| Set | Rule | Pairs | Unsafe A reads caught | Wrong values caught | Correct A falsely held | All pairs held |
|---|---|---|---|---|---|---|
| Tuning (P5) | ID | 222 | 4/6 (66.7%) | 4/6 | **8/208 (3.8%)** | 14/222 (6.3%) |
| Tuning (P5) | FULL | 222 | 4/6 (66.7%) | 4/6 | 8/208 (3.8%) | 14/222 (6.3%) |
| Held-out (H_P5) | ID | 174 | n/a (0 wrong) | 0/0 | **0/168 (0%)** | 0/174 (0%) |
| Held-out (H_P5) | FULL | 174 | n/a | 0/0 | 0/168 (0%) | 0/174 (0%) |

The line check (FULL) costs nothing extra on P5: the P5 reads never disagreed on lines.

Where the 6 wrong P5 reads came from:

| Doc | Field | Gold | 3 reads | Kind | Two-read outcome |
|---|---|---|---|---|---|
| 22 (SI) | PO | 50117194 | `0117194`, `50117194`, `50117194` | noisy (digit drop) | the wrong read is caught in both of its pairs |
| 258 (DN, prints two POs) | PO / ambiguity | ambiguous | `260988`, ambiguous-null, `260988` | 2 of 3 consistent | 2 of 4 wrong pairs caught; read 0 + read 2 agree on the guess and **escape** |

The 8 false holds are doc 19 (6 pairs: document number `GDN-ARS-15-9855` / null / `GDN-ARS-25-9855`)
and doc 22 (2 pairs: a correct read checked against the `0117194` read). Gold leaves doc 19's
number unscored because the page is illegible, so holding it is the right outcome. **No clean
document was held.**

3-read rule (hold unless all 3 agree): tuning held 3/37 documents (19, 22, 258) and caught 2/2
unsafe documents. Held-out held 0/29.

Strict comparison (printed text) adds false holds on the held-out set: 4/168 (2.4%), all doc 183
`13060 , 0` vs `13060`. The app keys both identically, so **compare app keys, not text**.

### 2.2 All labels (catch rate needs more errors than P5 has)

Two reads, app keys, FULL rule. Full table: `second_read_output_app.txt`.

| Label | Unsafe A caught | Wrong values caught | Correct A falsely held |
|---|---|---|---|
| P5 (adopted) | 4/6 | 4/6 (66.7%) | 8/208 (3.8%) |
| P0 live prompt, img150+high | 4/12 | 8/16 (50.0%) | 14/202 (6.9%) |
| P2 | 6/6 | 14/14 (100%) | 16/206 (7.8%) |
| P3 | 4/4 | 12/12 (100%) | 12/206 (5.8%) |
| R_high (live, high) | 2/8 | 2/8 (25.0%) | 8/204 (3.9%) |
| R_med / R_low / R_seed | 8/22, 6/20, 6/18 | 53%, 42%, 45% | 4.0%, 4.0%, 6.0% |
| V3 img150 no effort | 6/20 | 10/24 (41.7%) | 14/200 (7.0%) |
| V0 / V1 native PDF | 6/28, 6/24 | 28%, 36% | 8.8%, 5.4% |
| **Pooled img150 tuning** | **53/125 (42.4%)** | **101/173 (58.4%)** | **123/2373 (5.2%)** |
| **Pooled img150 held-out** | 0/6 | 0/6 (0%) | **18/664 (2.7%)** |

ID rule only (no lines), pooled img150 tuning: 28/173 wrong values (16.2%) for 4.0% false holds.
Almost all of the FULL rule's extra catches are spurious "release marker" lines (`1\1`, `4/1`)
under prompts P1 to P3. P5 no longer produces those lines.

**Identity-wrong reads only** (script `second_read_identity_only.py`; the A read has a wrong type,
document number or PO; ID rule):

| Scope | Caught |
|---|---|
| P5 | 4/6 (66.7%) |
| H_P5 | 0/0 |
| Pooled img150 tuning | 28/100 (28.0%) |
| Pooled img150 held-out | 0/6 (0%) |
| **Pooled img150, both sets** | **28/106 (26.4%)** |

The escapes are the consistent misreads in 2.3.

The held-out 0/6 is H_P0 doc 114: `22613 , 0` read as `226130` in all 3 reads. It is a
consistent misread, and the P5 prompt fixed it.

### 2.3 Consistent vs noisy

A wrong value is **CONSISTENT** when every read of the document gives the same wrong value, and
**noisy** otherwise.

| Group | Noisy | Consistent |
|---|---|---|
| img150 tuning (all prompts) | 51 | 10 |
| img150 held-out | 0 | 1 |
| Other transports (native, OCR) | 27 | 18 |

These are catalogue entries, one per document, field and label. A consistent entry stands for 3
wrong reads, a noisy one for 1 or 2. Weighted by reads, the uncatchable share is much larger: 74%
of identity-wrong pairs and 42% of all wrong pairs (section 0).

The consistent img150 misreads, which repetition can never catch:
- `22613 , 0` → `226130` (docs 113 and 114, live prompt; fixed by P5's copy-exactly rule).
- The two-PO DN 258 → `260988` (effort low/medium/seed, live prompt).

Native and OCR transports are more consistent in their errors. That is one more reason to stay on
images.

## 3. Q2: can a quantity misread survive reconciliation?

### Observed rates

| Scope | Line reads scored | WRONG quantities | 95% upper bound (rule of three) |
|---|---|---|---|
| P5 + H_P5 | 462 | 0 | see below |
| All img150 labels | 3,865 | 0 | see below |
| Mistral-OCR transport | 85 | 10 (x1000: `100` → `100000`) | not used in the app |

The reads are not independent evidence. They are the same **156 distinct printed line values**
(90 tuning, 66 held-out), read by up to 16 labels x 3 repeats. The honest bound uses the distinct
values: 3/156 ≈ **1.9% per value**. Most of the values are small integers.

Extra (spurious) lines: 0 under P5. Under P0 to P3 there were 37 across img150; they come from
release markers.

### Paths to a false merge

The rule is PO = DN aggregate = SI aggregate, exactly.

**(a) Three independent misreads land on the same wrong value.** This needs PO, DN and SI each to
misread the same line to the same number. Taking p ≤ 0.019 per value, p³ × P(same value) ≤ 7e-6 ×
0.1 is under **1e-6 per line**. That is negligible.

**(b) One misread hides a real discrepancy. This is the path that matters.** A set whose
documents genuinely disagree becomes "equal" if one document is misread onto its sibling's value.
The gold corpus has a concrete case: the PO (doc 2) prints **4200** and the DN (doc 1) prints
**400**. A single dropped digit on the PO (`4200` → `400`) would make PO = DN. Dropped digits are
an observed failure mode: `50117194` → `0117194` and `LPO26/2360` → `26360`.

In the gold sets, 17 of the 24 PO-vs-DN/SI comparisons differ (15 distinct POs). That is normal: partial
deliveries and different line numbering. About **1 set in 15** (the 2360 set) is one misread away
from equality. The others need two or more misreads, or have line keys that differ anyway.

Estimate for the adopted config:

  P(false merge per set) ≈ P(set is one misread away) × p × P(misread lands exactly on the sibling value) × P(the second read repeats the same wrong value)
  ≈ 0.07 × 0.019 × 0.1 × 0.4 ≈ **5e-5 per set** (about 1 in 20,000 sets). This is an upper-bound
  style estimate, because p = 0.019 is a 95% bound from zero observed errors.

Assumptions:
- Errors are independent across documents and lines. Same-vendor layouts break this; correlated
  errors make (a) more likely and leave (b) about the same.
- P(lands exactly on the sibling value) ≤ 0.1. A single-digit drop has 1 to 4 outcomes out of
  about 90 single-character edits, and drops are over-represented in the observed misreads.
- The second read repeats a wrong value 40% of the time. That is the pooled FULL-rule escape rate
  (72/173).
- The corpus is small. 0 observed errors gives only an upper bound.

**(c) A wrong PO number puts the document in another set.** On the adopted config a PO-number
error happened in 3/198 reads (tuning 3/111, held-out 0/87). The second read catches 2/3 of those
in pairs. For the error to reach a merge, the wrong number must equal another **open** set's
digits, and that set must be waiting for exactly this document type with equal quantities. With
8-10 digit SAP numbers that is very unlikely. Short vendor POs (`13060`, `13952`, `22613`) are
more exposed.

The real two-PO risk: when the model guesses one of two printed numbers, both are real numbers.
The vendor's own boxed `P.O 260988` usually has no set, so the document waits. If both numbers
were client POs with open sets, the guess could attach to the wrong one. This is the case the
second read only partly covers (doc 258: 2 of 4 wrong pairs escape).

Conclusion for Q2: **reconciliation already makes an undetected quantity misread very unlikely.**
The second read is mainly worth paying for to protect PO identity and the ambiguity flag.

## 4. Q3: other cheap checks, measured on existing data

### 4.1 Model-cleaned value vs `normalize_po_no(verbatim)`

No saved run returns a verbatim and a cleaned PO from the **same** call, so this cannot be
measured directly. The nearest proxy compares a P5 verbatim read with a live-prompt read (P0 or
R_high), which was told to "strip non-alnum, upper". The PO key is the only field compared
(rule PO):

| A vs B | P5 wrong POs caught | Correct A falsely held |
|---|---|---|
| P5 vs P0 (tuning) | 9/9 | 15/312 (4.8%) |
| P5 vs R_high (tuning) | 9/9 | 12/312 (3.8%) |
| H_P5 vs H_P0 (held-out) | 0/0 | 9/252 (3.6%) |

The catches come from a **separate call**, so they are really repeat-noise catches (doc 22,
doc 258). The false holds are docs 113 and 114: there the *cleaned* value is wrong (`226130`),
because cleaning squashes the revision suffix. A same-call cleaned field would share the
verbatim read, so it would not catch doc 22's `0117194`. It would still hold docs 113 and 114.
Expected effect on P5: **about 0 added catches for 3-5% false holds.** Not recommended.

### 4.2 Value must appear in the PDF text layer

Script: `text_layer_check.py`. The text layer is a signal, not truth.

| Item | Result |
|---|---|
| Documents with a usable text layer | **33/66** (tuning 20/37, held-out 13/29). Scans are "not applicable" and are not held. |
| Adopted P5 wrong values on text-layer documents | **0**. Docs 22 and 258 are scans, so this check catches **none** of P5's errors. |
| Pooled img150 tuning, identifiers, `token` match (whitespace removed, punctuation kept) | wrong caught 22/22; correct falsely absent **29/977 (3.0%)** |
| Same, `alnum` match | wrong caught only 4/22. `226130` matches the page's `22613 , 0` once punctuation is removed. |
| Held-out img150, `token` | 0 wrong to catch; correct falsely absent 12/252 (4.8%), from formatting variants (`J255300115876` vs `J255-300115876`). |
| Quantities (number set from the page) | 0 wrong to catch; correct falsely absent **0/2190**. No cost, but nothing to gain. |

Warning from the data: doc 1's text layer itself reads **`LPO2612360`** (the `/` became `1`). A
text-layer check therefore *rejects* the correct image read `LPO26/2360`, and it *confirms* the
wrong native-PDF read `LPO2612360`.

Verdict: a strict text-layer check could be added later as a hold-only signal for identifiers on
digital PDFs. It covers half the documents, catches nothing extra for P5, and costs about 3-5%
false holds. Not part of the minimal design.

### 4.3 OCR

No OCR engine is installed, and the brief forbids new spend or installs. **Unmeasured.**

### 4.4 Differently built second read (proxy for a "header crop")

A header-only crop cannot be measured from saved runs, because none exist. The closest proxies
are a different prompt (P0, R_high, P2) or a different transport (V0/V1 native PDF) as the
checker:

| Checker for P5 | Wrong caught | Correct falsely held (FULL) |
|---|---|---|
| Same P5 call (repeat) | 4/6 | 8/208 (3.8%) |
| P2 prompt | 7/9 | 18/312 (5.8%) |
| P0 / R_high prompt | 9/9 | 26/312 (8.3%) / 20/312 (6.4%) |
| Native PDF (V1 / V0) | 3/9 | 54/312 (17%) / 60/312 (19%) |

A different prompt catches more, because it breaks consistent misreads. It also brings its own
errors as false holds. Native PDF as the checker is worse on both counts.

## 5. Q4: recommended minimal design (for a Sonnet agent)

### Choice

**The same full call, made twice** (same P5 prompt, same 150 DPI images, effort high).

Not a header crop, for four reasons:
1. A header crop cannot be measured from saved runs.
2. Header position varies by vendor. The Drilling Tools header is a narrow left block, and the
   "top 35%" rule was rejected in the findings, section 2.
3. A crop needs layout code.
4. A crop does not re-check line quantities or extra lines.

A different-prompt checker is a possible later step, but it needs a fresh A/B with new runs.

Cost: each document makes 2 calls instead of 1, about **$0.0012-0.0013** instead of $0.0006 (P5
average $0.00066, H_P5 $0.00059). Latency goes from about 12-15 s to about 25-30 s per document
when the calls run one after the other, which is fine for a midnight batch. Expected holds:
about 6% of tuning reads and 0% of held-out reads.

### Hook points

1. **`src/app/services/extraction.py`, `extract_document`, right after
   `result = _call_vlm(doc.stored_path, dtype, cfg)` (around line 967)**, before the COMBINED
   branch:
   ```python
   result = _call_vlm(doc.stored_path, dtype, cfg)
   if _second_read_enabled(cfg):
       check = _call_vlm(doc.stored_path, dtype, cfg)   # same try block: infra error -> existing refund path
       diffs = read_disagreements(result, check)
       result["_second_read"] = {"disagreements": diffs, "read": check}
   # ... raw_extraction_json dump as today ...
   doubtful = bool(result.get("_second_read", {}).get("disagreements"))
   if result.get("document_type") == "COMBINED" and not doubtful:   # was: unconditional
       return _handle_combined(doc, s, result, cfg)
   # doubtful COMBINED (e.g. read 1 COMBINED, read 2 SI): do NOT split. Fall through to
   # populate_from_raw_dict, which stores the row as a held document (item 3).
   populate_from_raw_dict(doc, result, s, cfg)
   ```
   Check what `populate_from_raw_dict` does with `document_type == "COMBINED"` (it sets
   `doc_type = COMBINED`, which is not attachable). The row must stay valid, unattached, flagged
   and visible on `/unclassified`. Pin this with a test: read 1 COMBINED, read 2 SI produces no
   child files.
   - An infrastructure error on the second call takes the existing `_is_infrastructure_error`
     path: attempt refunded, document stays `pending`. A document is never held because of an
     infrastructure error. Both calls count as one attempt.
   - The second read lives *inside* the result dict, so `raw_extraction_json` keeps both reads
     for audit and `repopulate_from_raw_json` still works unchanged.
   - Config: `vlm.second_read: true` (default true), validated in `core/config.py` like
     `reasoning_effort`.
2. **New pure function `read_disagreements(a, b) -> list[str]`** in extraction.py, or a small
   `services/read_check.py`. Port it from `signature()` / `disagree()` in
   `prompt_improvement/eval/second_read_analysis.py` (app mode, FULL rule) and return field names
   such as `["po", "lines"]`. It compares:
   - `document_type`
   - DN/SI `document_number`, alphanumerics only, upper-cased (not compared for a PO)
   - the PO key: the `po_no_raw` that `_call_vlm` already builds. `normalize_po_no` digits must be
     equal and `letters_compatible`
   - `po_reference_ambiguous`
   - `{normalize_line_no: sum(_parse_scaled_int(qty))}`

   For COMBINED, also compare the `components` page ranges. If either read says COMBINED and they
   disagree, hold the parent and do not split.
3. **`populate_from_raw_dict`** (around line 597): read
   `result.get("_second_read", {}).get("disagreements")`. When it is non-empty and the PO is not
   manual, set the new column `doc.read_disagreement = ",".join(fields)` and `doc.po_set_id = None`;
   otherwise set it to `None`. Read 1's values are still stored, so the operator sees what was
   read.
4. **Model + migration**: `Document.read_disagreement: Text | None` (nullable, no default), with an
   Alembic revision on top of `a9b0c1d2e3f4`. Run it through `scripts/migrate_db.py`. Do not reuse
   `po_reference_ambiguous`: the UI says it means "more than one PO number".
5. **`src/app/services/grouping.py`**: refuse a document with `read_disagreement` in the same three
   places that already refuse ambiguity:
   - `group_document` (line 275): `return None, "read_disagreement"`. This sits before
     `get_or_create_po_set`, so a disagreeing PO cannot mint a set.
   - `attach_unattached_to_open_sets` (line 383)
   - `resolve_unattached_documents` (line 496)

   Add `HOLD_REASON_TEXT["read_disagreement"] = "Two reads of this document disagreed (fields: …). Check the PDF and name its PO."`

   Then run `gitnexus impact` / grep for **every assignment to `po_set_id`**. Confirm that each
   automatic path refuses a document with `read_disagreement`; the operator's reclassify is the
   only exception. Known automatic paths: manual upload (`dashboard.py`
   `_upload_manual_doc_impl`), the manual merger route, customs append, the recovery flow, and
   `flows/sync.py` grouping. The three sweeps above are only the ones that already check ambiguity.
6. **`src/app/flows/sync.py`** (around line 676): add an `elif problem == "read_disagreement":`
   log branch. No other change is needed: `ps_id` is `None`, so nothing attaches.
7. **Unclassified page** (`dashboard.py` `unclassified_view`, around line 1097, and
   `templates/unclassified.html`): count `read_disagreement` documents and show the fields on the
   row. The holding-area query already lists them, because `po_set_id` is NULL.
8. **Operator exits**:
   - **Re-Extract with AI** does a fresh double read and clears the hold if the reads now agree.
   - **Reclassify with a typed PO** clears `read_disagreement`, next to the existing
     `po_reference_ambiguous = False` at `dashboard.py:1287`, because the human has named the PO.

**Ask the user first** about item 8 for a *lines* disagreement. Reclassify confirms only the type
and the PO, not the quantities, so read 1's quantities would go into reconciliation unreviewed.
The safe option: when `lines` is among the disagreements, store the disagreeing line keys with
quantity 0. The existing `non_positive_quantity` guard then quarantines the set until a person
checks the PDF. Rows are kept, never dropped, and the values stay in `raw_extraction_json`. On P5
this never fired; lines never disagreed.

### Tests (Sonnet)

- **Unit tests for `read_disagreements`**: equal reads; `22613 , 0` vs `22613` (equal); `60213576`
  vs `60213576 OPG` (equal; letters compatible); `TOGA-…-163` vs `ARM-…-163` (disagree);
  ambiguity flip; an extra line; a quantity change; COMBINED with different components.
- **`extract_document` with a mocked `_call_vlm`**:
  - two different results → document valid, `read_disagreement` set, unattached, on
    `/unclassified`
  - identical results → placed as today
  - infrastructure error on the second call → `pending`, attempt refunded
- **`repopulate_from_raw_json`** keeps the hold.
- **Grouping**: all three sweeps refuse the document.
- **Reclassify** clears the hold.
- **Business scenario** in `tests/test_business_scenarios_ui.py`, then run `make business-logic`.
- **`test_known_limitation_consistent_misread_not_caught`**: two identical wrong reads pass. Also
  record this in PRODUCT section 8.

## 6. Residual limitations (for PRODUCT section 8)

- A consistent misread is not caught by repetition: 74% of identity-wrong pairs pooled over all
  img150 prompts, and 2 of 6 for P5. For P5 that is the two-PO guess `260988` on doc 258 when two
  reads agree.
- Quantity evidence is 0 errors on 156 distinct printed values (3,865 reads) from 66 documents,
  mostly small integers. The 95% bound is about 1.9% per value. Large or decimal quantities are
  under-sampled.
- Doc 23's vendor documents disagree with each other (`50137194` vs `50117194`). No reading check
  can resolve that; reconciliation and quarantine handle it.

## 7. Reproduce

```
E=prompt_improvement/eval
uv run --no-sync python $E/second_read_analysis.py --mode app --detail --json $E/second_read_wrong_catalogue.json > $E/second_read_output_app.txt
uv run --no-sync python $E/second_read_analysis.py --mode strict > $E/second_read_output_strict.txt
uv run --no-sync python $E/text_layer_check.py > $E/text_layer_output.txt
uv run --no-sync python $E/second_read_identity_only.py > $E/second_read_identity_output.txt
uv run --no-sync python $E/usage_report.py P5 H_P5        # cost per doc
```

| File | What it is |
|---|---|
| `second_read_analysis.py` | Pair and 3-read replay, consistent/noisy catalogue, cross-variant checker proxies |
| `text_layer_check.py` | Text-layer verifier, read-only DB plus PyMuPDF |
| `second_read_identity_only.py` | Catch rate on identity-wrong reads only, and the distinct line-value count |
| `second_read_output_app.txt`, `second_read_output_strict.txt`, `text_layer_output.txt` | Outputs these numbers came from |
| `second_read_wrong_catalogue.json` | Every wrong value with its reads |

The scripts import `score.py` and the app's own `normalize_po_no`, `po_letters`,
`letters_compatible`, `normalize_line_no` and `_parse_scaled_int`. They never call the API, and
they open the database read-only.
