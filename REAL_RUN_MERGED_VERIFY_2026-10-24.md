# Real-run merged-set verification (2026-10-24)

Read-only audit of `data/aam_merger.db` (opened `mode=ro`) after the first real end-to-end sync on a fresh DB.
For each of the 12 `po_sets` rows with `status='merged'`, every document page was rendered with pymupdf at 130 DPI and read visually. The checks were the PO number on the page, the doc type, and each line number and quantity (printed vs `line_items.quantity`/1000). The output packet in `data/output` was matched page by page against the source documents with a rendered-page hash.

DB status counts: merged 12, pending 24, quarantined 18, mismatched 3. Documents: 200 (7 with `po_set_id` NULL, 0 archived, 0 split children in merged sets).

## Verdict summary

| Verdict | Count | Sets |
|---|---|---|
| CORRECT | 11 | 16, 19, 24, 25, 26, 27, 29, 30, 31, 36, 54 |
| WRONG (incomplete packet; fails checks 4 and 5) | 1 | 8 (PO 4500051195): PO page 2 (doc 28) is missing because of a misread PO number |
| DOUBT | 0 | none |

**Set 8 is WRONG.** It contains no wrong content: its pages, PO, doc types and quantities are all right. However, a page that belongs to this PO (doc 28, PO page "2 of 8") is not in the packet, and it created a phantom pending set 9.
In the other 11 sets, every document has the correct PO and doc type, the printed quantities equal the extracted ones, and PO = DN = SI on every line. Every output packet holds exactly the 3 source pages of its set, with no foreign pages.

## Per-set evidence

Each set is 3 one-page documents. The quantities below are as printed on the page, and the extracted values (x1000) match them exactly.

### Set 8, PO 4500051195 (Arabian Drilling / Al-Shab), output `SIV-ARS-25-8312.pdf`: WRONG (incomplete packet)

| Doc | Type | PO on page | Lines printed (item: qty) | Extracted |
|---|---|---|---|---|
| 25 `SIV-ARS-25-8312-pages-1.pdf` | SI (Tax Invoice SIV-ARS-25-8312) | P.O. No. 4500051195 | 10: 50.0, 20: 12.0 | 10: 50000, 20: 12000 |
| 26 `...-pages-2.pdf` | DN (GDN-ARS-25-9587) | P.O.# 4500051195 | 10: 50 Each, 20: 12 Each | 10: 50000, 20: 12000 |
| 27 `...-pages-3.pdf` | PO (page "1 of 8") | PO Number 4500051195 | 10: 50 EA, 20: 12 EA | 10: 50000, 20: 12000 |

- Reconciliation is right: 50/50/50 and 12/12/12.
- **Missing document.** Doc 28 `SIV-ARS-25-8312-pages-4.pdf` is page "2 of 8" of the **same** PO. The header prints `PO #4500051195`, but it was extracted as `#4500057195` (a 1 misread as 7). The page has no line items: it holds the Additional Charges, Total P.O. Value 83.66, and the approval/instructions block. The misread made it mint its own set 9 (`4500057195`, status `pending`, "No delivery note in this set yet"). The merged packet for set 8 therefore lacks the PO's totals/approval page, and set 9 is a phantom pending set.
- The quantities and the content that was merged are correct, but the set fails check 4 (a document belonging to the PO is unattached) and check 5 (the packet lacks a page). The dashboard also shows a bogus pending PO. Two notes: PO pages 3-8 were never in the input, and the vendor file was supplied as 4 page-split PDFs.

### Set 16, PO TOGA-PO-25-26-163 (Tubestar / DrillTools), output `SIV-DTS-25-477.pdf`: CORRECT

- Doc 53 SI SIV-DTS-25-477 (Buyers Order TOGA-PO-25-26-163), doc 54 DN GDN-DTS-25-505 (same PO), doc 55 PO (P.O. Number TOGA-PO-25-26-163).
- Lines 1-5, printed on all three documents: 10 / 1 / 1 / 10 / 2. Extracted: 10000 / 1000 / 1000 / 10000 / 2000 on each document.

### Set 19, PO ARM-PO-25-26-029, output `SIV-DTS-25-498.pdf`: CORRECT

- Doc 65 SI SIV-DTS-25-498, doc 66 DN GDN-DTS-25-504, doc 67 PO, all showing ARM-PO-25-26-029.
- Lines 1-3, printed on all three: 7 / 2 / 3. Extracted: 7000 / 2000 / 3000.

### Set 24, PO ARM-PO-25-26-035, output `SIV-DTS-25-565.pdf`: CORRECT

- Doc 90 SI SIV-DTS-25-565, doc 91 DN GDN-DTS-25-603, doc 92 PO, all showing ARM-PO-25-26-035.
- Lines 1-3, printed on all three: 15 / 5 / 8. Extracted: 15000 / 5000 / 8000.

### Set 25, PO 22972 (ADES GCC), output `SIV-DTS-25-572.pdf`: CORRECT

- Doc 96 SI SIV-DTS-25-572 (Buyers Order 22972), doc 97 DN GDN-DTS-25-606 (22972), doc 98 PO (PO 22972, Rev 0).
- Lines 1-2: 2 / 1 on all three. Extracted: 2000 / 1000.
- The PO prints "1 of 7", but only page 1 was in the input. This is an input limitation, not a pipeline fault.

### Set 26, PO TOGA-PO-25-26-228, output `SIV-DTS-25-575.pdf`: CORRECT

- Doc 99 SI SIV-DTS-25-575, doc 100 DN GDN-DTS-25-614, doc 101 PO, all showing TOGA-PO-25-26-228.
- Line 1: 20 Pair on all three. Extracted: 20000.

### Set 27, PO 22398 (ADES GCC), output `SIV-DTS-25-576.pdf`: CORRECT

- Doc 102 SI SIV-DTS-25-576 ("22398 , 0"), doc 103 DN GDN-DTS-25-615 ("22398 , 0"), doc 104 PO (PO 22398, "1 of 6").
- Line 1: 2 on all three. Extracted: 2000.

### Set 29, PO 192641 (ADES Saudi), output `SIV-DTS-25-580.pdf`: CORRECT

- Doc 108 SI SIV-DTS-25-580, doc 109 DN GDN-DTS-25-619, doc 110 PO (192641, "1 of 6"), all showing 192641.
- Lines 1-3: 1 / 2 / 6 on all three. Extracted: 1000 / 2000 / 6000.
- The UOM on line 3 is REEL on the PO and EACH on the DN/SI. UOM is not a signal by design, so this is noted only.

### Set 30, PO 192701 (ADES Saudi), output `SIV-DTS-25-581.pdf`: CORRECT

- Doc 111 SI SIV-DTS-25-581, doc 112 DN GDN-DTS-25-620, doc 113 PO ("192701 , 0", "1 of 7").
- Lines 1-2: 3 / 7 on all three. Extracted: 3000 / 7000.

### Set 31, PO 192910 (ADES Saudi), output `SIV-DTS-25-582.pdf`: CORRECT

- Doc 114 SI SIV-DTS-25-582, doc 115 DN GDN-DTS-25-604, doc 116 PO ("192910 , 0", "1 of 6").
- Line 1: 2 on all three. Extracted: 2000.
- The descriptions differ: the SI/DN say Gazelle G3012 12FT and the PO says Grainger twin stepladder 12FT. The PO supplier note says "Ladder Twin 12 FT ... PN: G3012 GAZELLE", so they are consistent.

### Set 36, PO 23073 (ADES GCC), output `SIV-DTS-25-596.pdf`: CORRECT

- Doc 134 SI SIV-DTS-25-596, doc 135 DN GDN-DTS-25-632, doc 136 PO ("23073 , 0", "1 of 7").
- Lines 1-3: 32 / 32 / 32 on all three. Extracted: 32000 x3.

### Set 54, PO 4270006645-SNN (Bahrain Airport Services / raas), output `SIV-RHO-26-1914.pdf`: CORRECT

- Doc 190 SI SIV-RHO-26-1914 (P.O. 4270006645 -SNN, DN ref GDN-RHO-26-2183), doc 189 DN GDN-RHO-26-2183 (P.O. 4270006645 -SNN), doc 191 PO (P.O. Number 4270006645 - SNN).
- Line 1: 1 on all three. Extracted: 1000.

## Output packets

Every one of the 12 files in `data/output` holds exactly 3 pages. Each page hash-matches a source page of its own set (SI, DN, PO order), with no foreign pages and no duplicates. The only missing content is set 8's PO page 2 (doc 28), described above.

## Unattached and near-miss documents

- **`po_set_id` NULL** (7 docs: 11, 93, 94, 95, 183, 186, 193). Docs 11, 186 and 193 were read visually.
  - Doc 11: P.O. 50137194, a Saudi Aramco Rowan DN.
  - Doc 186: P.O. 13085, a GARMCO DN.
  - Doc 193: P.O. 260030 / 4500362256, an Armacell DN.
  - Doc 183: PO 260988.
  - Docs 93-95: SIV-DTS-25-566. These were rendered and read because 2526204 is within edit distance 2 of set 26's 2526228. Doc 93 (SI page 1) and doc 95 (DN GDN-DTS-25-571) both **print TOGA-PO-25-26-204**, so they do not belong to set 26. Doc 94 is SI page 2 (lines 8-9), a continuation page labelled SI. The bundle is SI, SI and DN with no PO document, which is correctly unattached.
  - None of them belongs to a merged PO.
- **Near-miss PO numbers.** All documents were checked for PO numbers within edit distance 2 of a merged PO. The only stray member is **doc 28**, which belongs to set 8 as described. All other hits are separate bundles with their own invoice and DN numbers:
  - sets 19/24/26 and 29/30, which are near each other;
  - SIV-DTS-25-517 (22613);
  - SIV-DTS-25-583 (193781);
  - SIV-DTS-25-566 (2526204, SI+SI+DN with no PO; printed PO verified above).

## Recommendations

1. **Set 8 / doc 28.** Have an operator re-key doc 28 to PO 4500051195 or attach it to set 8, regenerate the set 8 packet, and remove phantom set 9. Underlying risk: a single-digit misread of the PO number on a continuation page silently mints a new PO set. Precision held, because nothing was mis-merged, but the packet is incomplete. Consider:
   - flagging a PO-type document that has zero line items as a likely continuation page, not a new anchor;
   - flagging a new PO set whose number is edit distance 1 from an existing set and shares a filename stem with it.
2. **Multi-page POs** ("1 of 6/7/8"). The input contained only page 1 for sets 25, 27, 29, 30, 31 and 36. The packets faithfully reflect the input. Confirm with the business whether the PO T&C pages are expected in the merged packet.
