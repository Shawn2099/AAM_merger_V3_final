# Real-run diagnosis, 2026-10-24

This is a read-only diagnosis. No code, test or DB was changed; `data/aam_merger.db` and the reference DB were opened `mode=ro`.

- **This run:** 200 documents (59 DN, 80 PO, 61 SI, all `valid`), 57 open sets, 7 unattached documents.
- **Reference:** `data/reference/aam_merger_reference_2026-10-23.db` (283 docs, 27 merged).
- **Evidence:** DB values, the text layer of each stored PDF, and page images rendered at 100 DPI for every suspicious case. Every claim marked "seen" below was checked on the page image.

Classes:

- **(a)** Correct hold or quarantine: the documents disagree or are incomplete.
- **(b)** A bug or extraction miss that blocks a set which should merge.
- **(c)** Safe, but it costs recall. Either a documented limitation or a deliberate guard.

## 0. Headline

1. **There is no regression against the reference run.** None of the files in the 27 reference merges is in this run's input (0/N files for every one of them). The input is a different corpus (`data/input_corpus_backup`, 201 files), so 27 → 12 is a corpus change and says nothing about the code. All 12 current merges were *pending or quarantined* in the reference. Most of those reference sets held 2 documents where this run has 3. In the reference, their DNs (for example 477-pages-2, 572-2, 580-2 and 596-2) sat unattached with `po_no_raw = NULL`: the old transport did not read the PO number on the DN. The page-image transport now reads it, so the DN attaches and the set merges.
2. **No grouping or reconciliation bug blocks a mergeable set.** Every non-merged set is either a correct hold (a) or a documented or deliberate limitation (c).
3. **The extraction miss on D11 blocks nothing by itself.** D11 (`SIV-ARS-25-7970-pages-2.pdf`) is a degraded scan: the PO number came back null and the line number was misread as `2`. Even with a correct PO number, set 4 would quarantine on the line number.
4. **The 12 merges pass the precision spot-check** (§5). Two notes:
   - Set 8 merged only because a misread created phantom set 9.
   - In sets 20 and 45 the vendor renumbered its lines, so a vendor line number lands on a different PO item. The quantities also disagree, so two independent rules quarantine each set. The §7 hazard (same number, same quantity, different item) did not occur.

## 1. Per-set table

### Merged (12): spot-checked, see §5
Sets 8, 16, 19, 24, 25, 26, 27, 29, 30, 31, 36, 54. In every one, the line keys and quantities agree across PO, DN and SI, and the descriptions name the same item.

### Mismatched (3)
| Set | PO | Files | Finding | Class |
|---|---|---|---|---|
| 1 | LPO26/2360 | PO2360, DN2182, SIV-RHO-26-1910 | PO 4200; DN and SI 400. A real short delivery. | a |
| 11 | 18230578 | SIV-ARS-25-8321 | PO 10; DN and SI 2. | a |
| 44 | J255-300115876 | SIV-RAK-25-2964-Noble | PO lines 1/2/3 = 3/3/1; vendor line 2 = 2. | a |

### Pending: waiting on deliveries (7)
The vendor delivered and invoiced a subset of the PO lines with exact quantities, and the remaining lines are unreported. That is a partial delivery, so all seven are **(a)**.

| Set | PO | Packet | Vendor lines vs PO lines |
|---|---|---|---|
| 2 | 50116501 | ARS-25-7296 | 2 of {1,2} |
| 5 | 18232392 | ARS-25-8144 | 2 of {1,2} |
| 6 | 18231375 | ARS-25-8145 | 4,6 of {4,6,8,9,14} |
| 10 | 18232411 | ARS-25-8320 | 4 of {1,2,4,5,9} |
| 37 | 13060 | RAK-25-2876-Ades | 2 of {1,2,3} |
| 38 | 13696 | RAK-25-2884-Ades | 1 of {1,2} |
| 46 | 300140833 | RAK-25-3057-Noble | 3 of {1..7} |

### Pending: no delivery note (6)
| Set | PO | Finding | Class |
|---|---|---|---|
| 4 | 50117194 | The DN (D11, 7970-pages-2) is a downscaled, blurry scan. The VLM returned `po=null` and `document_number=null`, and read line `2` with an empty description; the printed line is `1` (seen). It sits unattached. Even attached, line `2` would orphan against PO line `1`. Re-extracting or rescanning is the remedy; a code change is not. | b (extraction), and alone it recovers 0 merges |
| 9 | "#4500057195" | **Phantom set.** D28 is page 2 of 8 of the PO for set 8; the page shows `PO #4500051195` (seen). The VLM misread `1` as `7` and the page has no lines. Harmless, but see §5. | b (extraction), harmless |
| 33 | 192123 | Packet 584 has only pages -1 (SI), -4 and -5 (PO page 1 and page 2 of 7). Pages -2 and -3 (the DN) are not in the corpus. It also has 2 PO docs, but the DN check runs first. | a |
| 52 | 8300052050 | The DN (D183) carries `P.O.: 260988` in the box and a handwritten `Po: 8300052050` (seen). The VLM took 260988 without flagging ambiguity, so it is unattached. Separately, the PO has 1 line of 100 and the SI has 2 lines of 50, so it would never merge. | a |
| 53 | 60213576 OPG | The DN (D186) has two POs (`PO - 60213576` and `P.O: 13085`, seen), was correctly flagged ambiguous and is held. Also PO 15/15 vs SI 3/3. | a |
| 55 | 4500362256 | The DN (D193) has two POs (`PO : 4500362256` and `P.O.: 260030`, seen), correctly flagged ambiguous. Also PO step-10 (10–40) vs vendor 1–4. | a |

### Pending: multiple PO documents (11)
Every one of these is a PO split into per-page PDFs. The text layer shows "Page 1 of 3", "Page 2 of 3" and so on. Set 23 is different: it holds two packets (538 and 548) that each contain the same PO.

The "if fragments were summed" column comes from running `compare_po_set_lines` over the combined lines, read-only.

| Set | PO | PO fragments (lines) | If fragments were summed | Class |
|---|---|---|---|---|
| 3 | 50119501 | p3 (1-3), p4 (4-6) | Mismatched. The DN line no. `4` (qty 50) is a misread of an illegible `6` with an empty description (seen), and it lands on PO line 4 (qty 15). Lines 1-5 are also undelivered. | c (no merge lost) |
| 12 | 18230394 | p3 (1-4), p4 (5-9) | Partial delivery (4, 7, 9 only) | c (no merge lost) |
| 13 | 4800466856 | p3 (1-4), p4 (5-6) | Partial delivery (1, 2 only) | c (no merge lost) |
| 14 | 4500026808 | p1 (10), p2 (a qty-0 continuation row) | `non_positive_quantity`, then step-10 vs vendor `1` | c (no merge lost) |
| 17 | 4500496007 | p1 (none), p2 (10, 20), p3 (30-50) | Step-10 vs vendor 1-5 | c (no merge lost) |
| 18 | 4500496514 | p1 (none), p2 (10) | Step-10 | c (no merge lost) |
| 22 | 4500044740 | p1 (10), p2 (a qty-0 continuation row) | `non_positive_quantity`, then step-10 | c (no merge lost) |
| 23 | 4500047182 | 2 packets × 4 PO pages; overlapping lines 20-60 | Real duplicate PO copies, plus step-10. **The hold is correct.** | a |
| **32** | **193781** | p1 of 7 (1-4), p2 of 7 (totals and T&C, no lines; seen) | **Would MERGE.** PO, DN and SI lines 1-4 are 25/15/15/10. | **c (1 merge lost)** |
| 34 | 4500496313 | p1 (none), p2 (10, 20); 2 DNs | Step-10 | c (no merge lost) |
| 35 | 4500497953 | p1 (none), p2 (10, 20), p3 (none) | Step-10 | c (no merge lost) |

### Quarantined: duplicate SI number (2)
| Set | PO | Finding | Class |
|---|---|---|---|
| **7** | 193165 | Two SI files with the same number:<br>• `8261-1` is the invoice's totals page with no lines (seen).<br>• `8261-2` carries all 10 lines (seen).<br>Line keys are disjoint, so these are page fragments, not a rescan. The set also has 2 PO fragments (8261-5: 1-5; 8261-6: 6-10) and 2 genuinely different DNs (9496, 9329). DN 9329's row 5 has its number covered by handwriting (seen); it came back null and matches PO 5 by description.<br>Summing every fragment **would MERGE**. To get there, three guards must all let it through: `duplicate_document_number`, `vendor_document_has_no_line_items` (on the empty totals page) and `multiple_po_documents`. | c (1 merge lost) |
| 15 | 4500039614 | `456-pages-1` and `-pages-2` are each 2-page PDFs of one invoice (L/I 1-12 and 13-18, disjoint; seen). **The invoice itself skips L/I 11 and 19**, which the DN has, and the PO is step-10 (10…180) vs vendor 1…19. It never merges. | a / c (no merge lost) |

### Quarantined: unmatched vendor line (13)
Every PO line number was checked on the page. **None of the 13 is a misread.**

| Set | PO | PO line numbers (as printed) | Vendor line numbers | Class |
|---|---|---|---|---|
| 20 | 22843 (ADES) | 1, 3, 4 (seen) | 1, 2, 3 | a. Vendor renumbered. Vendor 3 (Sat Power, qty 2) lands on PO 3 (Sprinter, qty 6), a different item. The quantity rule also fires. |
| 21 | 22613 (ADES) | 2 (seen) | 1 | a. Vendor renumbered. |
| 39 | 90139625 (Northern) | 10 (seen) | 1 | c. Step-10 (CORE_LOGIC §7). |
| 40 | 90139670 | 10, 20, 30 | 1, 2, 3 | c. Step-10. |
| 41 | 90139566 | 10, 20, 30 | 2, 3 | c. Step-10, and partial anyway. |
| 42 | 90139734 | 10 | 1 | c. Step-10. |
| 43 | 90139766 | 10 (qty 32) | 1 (qty 19) | c. Step-10, and the quantity disagrees anyway. |
| 45 | 300101295 (Noble) | 1, 2(rel 1), 2(rel 2), 4…25 (text layer; Σqty×price = 31,949.00 = the printed total, so the read is complete) | 19, 21 | a. Vendor's own numbering: its 19 is PO 22 (breaker) and its 21 is PO 24 (relay). Vendor 21 (qty 1) lands on PO 21 (oil filter, 2+1 = 3), a different item. The quantity rule also fires. |
| 47 | 10006-0000079633 (Valaris) | `1-1` (seen) | 1 | c. Line-release `1-1` ≠ `1` by contract (§3). Qty 46 vs 50 anyway. |
| 48 | …79657 | `1-1` | 1 | c. Would match if `1-1` ≡ `1`. |
| 49 | …79469 | `1-1`…`4-1` | 3 | c. Partial anyway. |
| 50 | …79573 | `1-1` | 1 | c. Would match if `1-1` ≡ `1`. |
| 51 | …79731 | `1-1`, `2-1` | 2 | c. Partial anyway. |

### Quarantined: PO has no line items (3)
All three stored PO files are **page 1 of N only**. The line-item pages are not in the corpus (checked `input_corpus_backup`), so the extraction is correct and these are **(a)**.

| Set | PO | File | Page (seen) |
|---|---|---|---|
| 28 | 4500498641 (KCA Deutag) | SIV-DTS-25-579-3 | "Page 1 of 3": header and T&C, no item table |
| 56 | C13717 (IMI) | SIV-RHO-26-234B | "Page 1 of 2": header and T&C |
| 57 | 8300046064 (NOMAC) | SIV-RHO-26-687B | "Page 1 of 13": the item table header is at the foot with no rows |

The reason text ("re-extract or contact the VLM operator") points the operator at the VLM. The real cause is missing pages. That is a wording issue only.

### Unattached documents (7)
| Doc | File | Finding | Class |
|---|---|---|---|
| D11 | SIV-ARS-25-7970-pages-2 (DN) | Degraded scan: PO number, DN number and description read as null; line misread as `2` (it is `1`) | b (extraction), held safely |
| D93, D94 | SIV-DTS-25-566-1, -2 (SI) | One invoice in 2 fragments, TOGA-PO-25-26-204 | a: no PO for 2526204 in the corpus |
| D95 | SIV-DTS-25-566-3 (DN) | Same PO | a: no PO |
| D183 | SIV-RHO-26-1669A (DN) | Took the box `260988` and ignored the handwritten 8300052050. Not flagged ambiguous, unlike D186 and D193 on the same template. | a (held), but see §5 |
| D186 | SIV-RHO-26-1896A (DN) | Two PO numbers, flagged ambiguous | a |
| D193 | SIV-RHO-26-233A (DN) | Two PO numbers, flagged ambiguous | a |

## 2. Reference comparison
- **27 reference merges.** Every one comes from a packet absent from this run (SIV-ARS-25-7230/7231/7297/7517/8034/8260/8262/8263/8301/8302/8304/8313/8314/8331, RAK-2934/2935/2967/3049/3061/3062/3126/3131/3133/3134, RHO-685/688/854). None of their files is in this run's input, so **no set that merged before fails to merge now.**
- **Reference sets that are in this run and still don't merge.** The packets that do appear in both runs were all non-merged in the reference too. 12 of them now merge: 477, 498, 565, 572, 575, 576, 580, 581, 582, 596, RHO-1914, and ARS-8312, which was quarantined in the reference. The others hold for the reasons in §1.

## 3. Root causes, ranked by sets affected

| # | Cause | Sets | Merges it costs | Kind |
|---|---|---|---|---|
| 1 | ERP step-10 PO numbering (10/20/30 vs 1/2/3) | 39, 40, 41, 42, 43, 14, 15, 17, 18, 22, 23, 34, 35, 55 (14) | Roughly 3 (39, 40, 42), plus any of the DTS sets once fragments are solved | c, documented in CORE_LOGIC §7 |
| 2 | A PO split into per-page PDFs triggers `multiple_po_documents` | 3, 12, 13, 14, 17, 18, 22, 32, 34, 35, 7 (11, plus set 33 behind the DN check) | **1** (32), and 7 only together with #3 | c, deliberate guard (CORE_LOGIC §2) |
| 3 | An invoice split into per-page PDFs triggers `duplicate_document_number` | 7, 15 (+ D93/D94, which are unattached) | 0 on its own; 1 (set 7) together with #2 and the empty-page exemption | **b**: false positive against the guard's own rationale |
| 4 | Line-release `1-1` vs vendor `1` | 47–51 (5) | 2 (48, 50) | c, contract §3 |
| 5 | Vendor renumbers lines | 20, 21, 45 (3) | 0 (correct quarantine) | a |
| 6 | Input truncated to page 1 of N | 28, 56, 57, 33, 566 packet (5) | 0 | a |
| 7 | Partial delivery or short quantity | 2, 5, 6, 10, 37, 38, 46, 1, 11, 44 (10) | 0 | a |
| 8 | Two PO numbers on an RHO DN | 52, 53, 55 (DNs held) | 0 (each set fails on its lines anyway) | a |
| 9 | Degraded scan or VLM misread | D11 (set 4), D28 (phantom set 9), D7 (set 3 DN line `4`) | 0 | b (extraction), held safely |

## 4. Proposed minimal fixes (bug fixes only)

**F1. Narrow `duplicate_document_number` to real duplicates. Affects 2 sets (7, 15) plus D93/D94; recovers 0 merges alone.** In `reconciliation.py` around lines 520–555, two DN or SI documents with the same number should count as duplicates only when their normalised line keys **overlap**.
- **The overlap test runs before matching, so it must be strict.** Any row with no line number on either document counts as overlapping, and the set keeps the quarantine. Without this rule, two scans of the same invoice whose rows came back numberless would look disjoint, be summed, and double the quantities.
- **Why.** The guard exists so that a rescan does not double a quantity. Fragments with disjoint keys cannot double anything, because each key's sum gets a contribution from only one document.
- **Precision.** Safe. An overlap still quarantines, and a vendor-side fragment that is missing or misread only makes the vendor aggregate *smaller*, which can never produce a false merge.
- **Effect.** Sets 7 and 15 reach the comparison. 0 merges on its own: set 15 then quarantines on step-10, and set 7 stops at the next guard.
- **Companion.** An empty DN or SI document that shares its number with a non-empty sibling is the totals page of that document. It should not trip `vendor_document_has_no_line_items`. Same precision argument: a vendor-side omission cannot create a merge.
- **Tests.** Add a `test_*` for both, and run `make business-logic`.

**F2. Fix the reason text for `po_document_has_no_line_items`. Affects 3 sets (28, 56, 57); wording only.** Say that the PO file may be only the cover page of a longer PO and that the line-item pages may be missing, rather than "contact the VLM operator". Wording only; it changes no verdict.

**Not proposed as a bug fix: an operator decision (contract change).** Summing PO documents whose line keys are disjoint (#2) would recover set 32, and set 7 together with F1.
- **Contract.** CORE_LOGIC §2 states that a second PO document is "never summed and never auto-merged".
- **Residual risk.** It brings a precision risk the vendor side does not have. Suppose a PO page that does carry lines is misread as empty, and the vendor delivered only the lines on the other pages. The set would merge as complete when it is a partial delivery.
- **Payoff.** This run shows 1–2 merges.
- **Recommendation.** Leave the hold. If the operator wants it anyway, accept only PO fragments where every fragment has ≥1 line and the keys are pairwise disjoint. That still recovers set 7 (fragments 1-5 and 6-10), but not set 32, whose page 2 is empty.

**Not proposed: step-10 mapping or `1-1` ≡ `1`.** Both are documented contract limitations (CORE_LOGIC §3 and §7). They are new behaviour, not fixes.

## 5. Precision notes, from the 12 merges and the near-misses
- **Set 8 merged by accident of a misread.**
  - What happened: the packet's PO has 8 pages, and D28 (page 2) prints the same PO `#4500051195`. The VLM read it as `4500057195`, which created phantom set 9. With a correct read, set 8 would hold 2 PO docs and be held as `multiple_po_documents`.
  - Is the packet right? The verdict is right: PO page 1 carries lines 10/20 at 50/12, the same as DN and SI. But the packet is missing PO page 2. The other merged ADES/DTS packets also carry only PO page 1 of N, because the corpus held only that page.
- **In sets 20 and 45 the vendor renumbered its lines.** Vendor 3 lands on PO 3, and vendor 21 on PO 21, each a different item. Both the orphan rule and the quantity rule fire, so these are not instances of the §7 hazard (which needs same number *and* same quantity). They show the hazard is plausible with this vendor's numbering, so human review of the packet stays load-bearing.
- **The VLM guesses line numbers on degraded scans.** It returned `4` (D7) and `2` (D11) for illegible numbers, with an empty description, instead of `null`. Today this only blocks merges. But a guessed number that happens to hit a PO line with the same quantity, on both DN and SI, would pass. Watch for this in the prompt evaluation; a row with no readable description and a confident line number is a signal.
- **D183 is inconsistent with D186 and D193.** All three are the same RHO template carrying two PO numbers, but D183 was not flagged ambiguous and took the vendor-internal box number. Today it attaches nowhere. If a set 260988 ever existed, it would attach to the wrong PO. That is a latent wrong-attach risk. It is an extraction-prompt issue; no code fix is proposed.
- **Qty-0 continuation rows.** On D46, D77, D83 and D89, the VLM emits the tail of the previous page's item as a numberless row with qty 0. Today the multi-PO hold masks them. If fragments were ever summed, they would raise `non_positive_quantity`. The contract forbids silently dropping a row, so they cannot simply be filtered out.
