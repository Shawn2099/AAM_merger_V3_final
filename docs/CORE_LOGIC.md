# The Core Contract — AAM Merger

This is the operator's statement of what the product is, in one page. The
long-form reasoning is `AAM_merger_V3_PRODUCT.md`; where the two disagree, this
page is the intent and PRODUCT must be corrected. The rule is implemented in
`src/app/services/matching.py` and `src/app/services/reconciliation.py`; the
contract is pinned by `tests/test_business_scenarios_ui.py`.

---

## 1. The rule, verbatim

> A set is **one PO PDF, plus any number of SI PDFs and DN PDFs**, all carrying
> the **same PO number**. For every line item, the **aggregate quantity of the
> PO equals the aggregate quantity of the DN and equals the aggregate quantity
> of the SI**. Line numbers are the primary key; description is used only when
> a line number is absent. **Only quantities are compared — nothing else.**
> If the customs toggle is enabled, the two customs documents are appended to
> the merged packet; if it is not, the merged packet is final.

## 2. Forming the set

| Rule | Implementation |
|---|---|
| One PO document anchors a set. A second PO document under the same number holds the set **pending** (`multiple_po_documents`) for review; it is never summed and never auto-merged. | `reconciliation.py` (`single_po_document`, default `true`) |
| DN and SI documents never create a set; they attach to the open set with the **same normalised PO number**. | `grouping.py` (`ANCHOR_TYPES`, `get_or_create_po_set(create=False)`) |
| A set is formed **only** by PO, DN and SI documents (customs/shipping are the separate add-ons, §5). An `UNKNOWN` or `COMBINED` document is never attached to a set, even if it carries a PO number, until an operator reclassifies it. A legacy row attached anyway quarantines the set (`unclassified_document_in_set`). | `grouping.py` (`_ATTACHABLE`); `reconciliation.py` |
| Any number of DN and SI documents may attach. All of them are included in the packet. | `reconciliation.py` (`dn_docs`, `si_docs`); `merge.py::_ordered_docs` |
| The PO grouping key is the **digits** of the PO number (labels, a leading `PO`/`LPO` and a trailing revision such as `, 0` / `Rev 2` are removed first). `PO 161538`, `161538` and `PO, Rev # 161538,0` are the same set; so are `60213576 OPG` and `60213576`, `P213899` and `213899`, `LPO26/2360` and `PO 26/2360`. A number with no digits has no key: the document is held (`missing_po_number`) and never groups. | `grouping.normalize_po_no` |
| Letters are a **safety check**, not part of the key. Letters on one side only still attach. Letters on **both** sides that differ after case-folding (`TOGA-PO-25-26-163` vs `ARM-PO-25-26-163`) are different POs that share digits: the document is **held** unattached with `po_letters_conflict` and never grouped; a PO document does not mint a second open set under the same digits. The set's letters are those of its anchoring PO (`po_sets.po_no_letters`). Accepted failure: `J255-300115876` keys as `255300115876` and does not match a plain `300115876`; it waits for an operator. | `grouping.po_letters`, `letters_compatible`, `PoLettersConflict`; `po_sets.po_no_letters` |
| A document whose printed PO number differs from the set's quarantines the set (`po_reference_mismatch`). | `reconciliation.py` |
| Duplicate bytes are one document (SHA-256 unique). The same content cannot be attached twice. | `ingestion.py`, upload route |

## 3. Matching and comparison

**Line key first.** Group **both sides** by `line_item_no`, normalised
(whitespace and leading zeros: `01` = `1`; `1-1` stays `1-1`; a dotted
sub-line keeps its zeros, `1.05` is not `1.5`). Sum all rows in
a group. A PO line delivered or invoiced in several rows reconciles by sum — it
is an aggregate comparison, not a one-to-one pairing.

**Description is the identity when a number is absent — on either side.** A
vendor row with no usable line number is matched to the closest PO line at
≥85 % token-sort similarity (`matching.fuzzy_description_threshold`). A PO
line with no printed number is keyed by its description (`po_item_key`); a
numbered vendor row that the PO does not carry may match such a line by
description too. A printed number that the PO **does** carry is always
authoritative — description never overrides it.

**Line-number fallback (2026-10-24).** Exact matching runs first and is untouched. Only when a vendor pool (DN or SI, each on its own) has numbered rows that exact matching cannot place on the PO, one of four rules may re-key a COPY of that pool's rows: `x10` (PO 10,20,30 vs vendor 1,2,3), `div10` (PO 1,2,3 vs vendor 10,20,30), `add_rel` (PO 1-1,2-1 vs vendor 1,2) and `strip_rel` (PO 1,2 vs vendor 1-1,2-1). Each rule has a switch under `matching.line_number_fallback` (all on by default). Invariants: PO keys are never rewritten; DN and SI resolve independently and may use different rules; one rule per pool, all-or-nothing (every numbered key must land on a PO key); a pool where any single document already fits the PO as printed is never mapped (`line_mapping_rejected`); a numberless row anywhere disables it; the function default is exact-only (`line_fallback=None`). The comparison is unchanged: PO == sum(DN) == sum(SI) per line, exact integers. The rule used is stored in `po_sets.line_mapping_rule`, shown as *lines mapped (rule)*, appended to the reason, and a merged set gets one `line_mapping_applied` audit row. Pinned by `tests/test_line_mapping*.py`.

**A PO line with neither a number nor a description quarantines**
(`po_line_missing_line_item_no`). Nothing can address it. A PO line with a
description is addressable and is compared like any other line.

**The comparison.** For every PO line, the PO quantity must equal the DN
aggregate **and** the SI aggregate, exactly, as integers scaled ×1000:

```
PO(line) == Σ DN(line) == Σ SI(line)      for every line
```

A vendor row that resolves to no PO line is an orphan → the set quarantines
(`unmatched_vendor_line`). One failing line fails the whole set; there is no
partial merge.

| Only these are compared | Never compared |
|---|---|
| `line_item_no` (the key) | price (not extracted) |
| quantity (scaled integer) | description (except the numberless fallback) |
| | UOM (not stored) |
| | part number / SKU (not stored) |
| | positional order / `slno` |

## 4. What each outcome means

| Outcome | When |
|---|---|
| `merged` | Every line matched exactly and, if the customs requirement is on, both customs documents are present. Packet written. |
| `mismatched` | A line was reported on both sides with different quantities. A human decides which document is right. |
| `pending` | A required side is missing, the vendor reported **nothing** for a PO line, a document is not read yet (`document_not_yet_read`), or the set holds more than one PO document (review hold). **Not a pass.** |
| `pending` (`documents_changed_during_merge`) | A document was attached to, or removed from, the set after reconcile read it and before the packet was written (the check compares the set of document ids; content edits are serialised by the per-PO lock). No packet is written; the next reconcile re-checks the set. **Not a pass.** |
| `quarantined` | A line could not be identified, a quantity is zero/unreadable, a PO reference mismatch, a document failed extraction (`document_extraction_failed`), an unclassified document is attached (`unclassified_document_in_set`), any single DN/SI has no lines (`vendor_document_has_no_line_items`), or the packet cannot be named. Whole set, never a line. |
| held (document, not a set) | A document that cannot be placed waits unattached in the holding area with its reason: `missing_po_number`, `ambiguous_po_number`, or `po_letters_conflict` (the digits match an open set but the letters differ, so they are different POs). The holding area prints the reason; naming a clashing PO by hand is refused with a 409. |

Zero or unreadable quantities quarantine (`non_positive_quantity`); a row is
never silently dropped.

## 5. Customs

Default **off**: the packet is final when the set merges.

Enabled: both a `CUSTOMS` and a `SHIPPING` document are required.

- **Enabled before the merge** → the set waits at `blocked_customs`; after both
  are uploaded, Merge Now (or the next sync) writes the packet with them.
- **Enabled after the merge** → the set stays `merged`; uploading both rebuilds
  the same packet in place (legal order ends SHIPPING → CUSTOMS), atomically,
  with an audit row (`customs_append`). The rebuild is idempotent.
- One document alone does not rebuild the packet; the dashboard shows
  "Customs append pending".
- Before the merge, a wrong CUSTOMS/SHIPPING file can be removed on the set page (detached and archived, audited,
  stored file kept) and replaced; after the merge it cannot. The operator finishes a customs set with Merge Now,
  never Force Merge. Details: `docs/CUSTOMS_OPERATIONS.md`.

## 6. The packet

All attached documents, concatenated whole in the configured legal order:
`SI → DN → PO → SHIPPING → CUSTOMS`. Pages are never edited or re-sorted. The
filename is the first SI's invoice number, falling back to the PO number (with
a visible naming note) when no invoice number was extracted. An existing packet
belonging to another set is never overwritten — that set quarantines as
`packet_naming_failed`.

## 6a. Two standing rules (2026-10-23)

- **A filename is never an input to matching or grouping.** It appears only in
  messages and logs. The packet is named by the first SI's invoice number, else
  the PO number.
- **A combined PDF (several documents in one file) is quarantined whole.**
  `extraction.split_combined` is `false` by default: nothing is cut, no child
  documents exist, and the file waits in the holding area and in
  `quarantine/_documents`. The splitter (`services/splitting.py`) is kept and
  can be re-enabled by setting the flag. Pinned by `tests/test_combined_quarantine.py`.

## 6b. How a document is read (FR-6.1, amended 2026-10-23)

The reading step is one VLM request per document. Every page is rasterised
(PyMuPDF, 150 DPI, PNG) and sent as an image, in real PDF page order, in a
single request with reasoning effort `high`. This replaces the earlier
"native PDF, no rasterisation" transport. The prompt returns identifiers
(PO number, document number, line number) **exactly as printed** and `null`
when unreadable; `normalize_po_no` and the sanitizer make the keys. Two or more
different PO numbers on one document come back as `po_reference_ambiguous=true`
with `po_reference=null`; the document is held unattached for a person.

- Limits: at most `vlm.max_pages` pages (default 30; the longest real file is
  13 pages, about 39k tokens), a per-page pixel ceiling and a total image-size
  ceiling. An oversized, corrupt, truncated, empty or password-protected PDF is
  refused **before** any request is built and takes the ordinary failed
  extraction path (counted attempt, then `document_extraction_failed`). A
  request that exceeds the model context errors; it is never truncated
  (`transforms: []`).
- Settings under `vlm:` in `config.yaml`: `raster_dpi` (72..300, default 150),
  `reasoning_effort` (`minimal|low|medium|high`, default `high`), `max_pages`
  (default 30). A bad value is a configuration fault: the document stays
  pending. Pinned by `tests/test_extraction_raster.py`.

## 7. What this contract does **not** claim

- It does not catch "same line number, same quantity, different item". That is
  the accepted high-consequence limitation; human review of the packet is
  load-bearing. See PRODUCT §8-H.
- It does not compare units. `1 BOX` and `1 EA` are both 1.
- Step-10 and `-1` numbering are mapped by the line-number fallback (section 3). These
  stay quarantined, each pinned by a test: test_limitation_two_rules_never_compose; test_limitation_slash_schedule_is_not_mapped; test_limitation_unicode_dash_is_not_normalised; test_limitation_other_separators_are_not_mapped; test_limitation_labelled_keys_are_not_mapped; test_limitation_steps_other_than_10_are_not_mapped; test_limitation_shifted_numbering_is_not_mapped; test_limitation_real_dts22843_shifted_numbering_is_not_mapped; test_limitation_mixed_pool_is_not_mapped. See PRODUCT section 8 for the three `test_known_limitation_*` entries.
- A missing invoice number does not block the merge; the packet is named from
  the PO number and the gap is flagged.

## 8. Where the contract is tested

| Clause | Test |
|---|---|
| 1 PO + n DN + n SI, per-line aggregates | `test_one_po_two_dn_two_si_sum_and_merge` |
| Only quantities matter (prices/descriptions differ) | `test_only_quantities_decide_the_verdict` |
| Description fallback for a numberless vendor row | `test_a_numberless_vendor_row_falls_back_to_description` |
| A PO line with no number matches by description | `test_a_numberless_po_line_reconciles_by_description` |
| A PO line with no number *and* no description quarantines | `test_po_line_without_a_number_or_description_quarantines_and_both_pages_say_why` |
| Second PO document holds the set pending | `test_two_po_documents_hold_the_set_pending_for_review` |
| Identical bytes are one document | `test_identical_po_bytes_are_one_document_not_a_second_po` |
| Same PO number is required | `test_po_reference_mismatch_quarantines_with_the_offending_file` |
| PO key is the digits; letters on one side only attach | `test_letters_on_one_side_only_attach_in_both_directions` |
| Different letters on both sides are held, with the reason on the page | `test_different_letters_same_digits_are_held_not_grouped`, `test_the_holding_area_says_why_a_lettered_document_was_held` |
| `J255-300115876` does not join `300115876`; a digit-less PO never groups | `test_j_prefixed_number_does_not_join_the_plain_one`, `test_a_po_with_no_digits_is_held_and_never_groups` |
| Step-10 / `-1` numbering maps by fallback; the pages say so | `test_step10_po_merges_by_line_mapping_and_pages_say_so`, `test_dn_exact_si_step10_merges_with_label`, `test_release_suffix_po_merges_by_line_mapping` |
| Partial or disagreeing mapped delivery | `test_partial_step10_delivery_is_pending_with_mapping`, `test_step10_quantity_mismatch_is_mismatched_on_the_po_line` |
| A mixed-numbering pool is not mapped | `test_mixed_numbering_in_one_pool_quarantines_and_both_pages_say_why` |
| Fallback switched off = exact only | `test_line_number_fallback_switched_off_quarantines_as_before` |
| Partial delivery is a wait, not a pass | `test_partial_delivery_is_pending_not_quarantined` |
| Customs off (default) → the merge is the end | `test_one_po_two_dn_two_si_sum_and_merge` |
| Customs on before merge → blocked then merged | `test_customs_on_before_merge_blocks_then_merges_after_both_docs` |
| Customs on after merge → appended, status stays merged | `test_customs_documents_can_be_appended_after_merge` |

Run them with `make business-logic`.
