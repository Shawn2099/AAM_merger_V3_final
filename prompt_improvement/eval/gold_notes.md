# Gold set notes

37 documents transcribed from rendered page images (144 DPI renders in `scratch_dump/render_all/`,
3-5x PyMuPDF crops for digit-critical fields). No text layer or DB value was used as truth.
Quantity convention: integer part as printed; trailing `.00`/`.000` dropped (e.g. `100.000` -> `100`).
Extra optional fields: `dn_reference` (SI header), `rel` (Noble/ARO release), `sl` (RAK SL column),
`uncertain_fields`, `po_printed_raw`.

## Uncertain (excluded from scoring)

- **19** `SIV-ARS-25-7969-pages-2.pdf` (Al-Shab raster DN): doc number reads `GDN-ARS-25-9155`
  (9th char after `9` is 1 at 4x); item-no glyph illegible. PO `50119501` and qty 50 are legible.
- **23** `SIV-ARS-25-7970-pages-2.pdf`: PO digits blurred (4th from right looks like 3, i.e.
  `50137194`; SI doc 22 prints `50117194`). Doc number `GDN-ARS-25-9?75` and qty glyph blurred.
- **121** `SIV-DTS-25-538-pages-2.pdf`: header and quantities certain; the handwritten L/I column
  has crossed-out 4,5,6,7,9 overwritten with 2,3,4,5,6 (final values recorded).

## Contradictions with PDF_DB_COMPARISON_2026-10-23.md

1. **Doc 23 (X3)**: comparison says the printed PO is image-verified `50117194`. The DN scan itself
   is not legible enough to confirm that; it looks closer to `50137194`. The sibling SI (doc 22)
   does print `50117194`, so the claim is plausible but not reproducible from doc 23's image.
2. **Doc 19 (X4)**: comparison gives header `GDN-ARS-25-9355` and `Item No 1`. I read `9155`
   (uncertain) and could not read the item number (PO/SI line is 6, not 1).
3. Everything else claimed in section 9 matched: doc 2 (4200), 20/21 (lines 1-3 / 4-6, qty
   5,5,5 / 15,15,50), 73 (qty 10), 86, 89 (SI line 1 qty 1; note SI has no line 11), 92 (PO items
   10,20,...), 113 (`22613 , 0`), 121 (header, 5 lines qty 1; line numbers are handwritten
   overwrites), 215/217 (SI 19/21 vs PO 22/24 confirmed; PO has no line 19), 239/241, 257/258/259
   (two PO numbers on 258; A2 shape), 268 (two PO numbers), 272 (no item table).
4. Doc 217 detail not in the comparison: PO lines 3, 11, 13, 16, 19 are absent; lines 2 and 21
   have two releases each (`2/1`,`2/2`; `21/1`,`21/2`). Net Amt sums to the printed 31,949.00.

## Coverage limits

- Boilerplate pages were not all viewed: 217 p8-12, 229 p2, 232 p3, 241 p3-6, 244 p2, 256 p2-3.
  Valaris pages after "Total PO Amount" and Noble/ADES T&C were inferred as terms text.
- Not covered by an independent doc: a Noble DN, Valaris SI/DN (RAK-issued Ensco invoices not
  read), Borr, ADES DN. Vendors covered: Drilling Tools (SI, DN, PO), RAAS (SI, DN), Al-Shab
  (SI, DN), RAK (SI, DN), Valaris/Ensco PO, Noble PO, ARO PO, Northern Offshore (SI, DN, PO), SLB
  (SI, DN, PO), Urbaser (DN, PO), NOMAC (PO), ADES PO, SANAD PO, IMI PO.

## Opus audit (2026-10-23)

Independent re-read from my own PyMuPDF renders (DB opened `mode=ro`; 1.5x full pages, 5-14x crops
for digit fields; Al-Shab scans are 1-bit 200 DPI so crops past ~3x add no detail). I read each page
before looking at its gold entry. No text layer or DB value was used.

**Docs checked (26):** priority 19, 23, 121, 217, 89; plus 1, 2, 18, 20, 22, 82, 83, 92, 113, 195,
196, 215, 216, 232, 241, 258, 259, 268, 272, and the trailing pages of 229, 244, 256.

**Transcription errors on certain fields: none.** Every document_type, document_number,
po_reference, line_item_no and quantity I read matched gold, including all 22 rows of 217
(gaps at 3/11/13/16/19, releases 2/1-2/2 and 21/1-21/2, Net Amt sum 31,949.00) and the 11 SI
lines of 89 (no line 11). Arithmetic cross-checks: 83 (18x127.50=2,295; 15x127.50=1,912.50),
232 (sum 4,880.00), 2 (4200x0.550=2,310), 18 (50x3.00=150). 259's header shows `260988` is
the quotation number, which supports leaving 258's po_reference null.

**Defect fixed: uncertain values were being scored.** `score.py` scores every present key and
never reads `uncertain_fields`, so the "excluded from scoring" section above was not true. Two of
those values came from sibling docs rather than the page. Changes made to gold.json (values kept
under `unscored_reads`, which the scorer ignores):
- 19: removed `document_number` (9155 vs 9355; a vertical scan line crosses the digit) and
  `lines` (item glyph illegible; "6" was taken from SI 18/PO). Still scored: type, PO 50119501.
- 23: removed `po_reference` (page reads 50137194 at 14x; SI 22 prints 50117194),
  `document_number` (9?75), `lines` (item no two specks, qty blurred). Added `lines.line_item_no`
  to uncertain_fields. Only document_type is still scored, so exclude 23 (and arguably 19) from
  doc-pass rates.
- 121: removed `lines` (two competing handwritten L/I sets). Qty 1.00 x5 is legible but cannot be
  scored without a line key. Header still scored.
Effect on the baseline run: only 19, 23 and 121 changed.

**Coverage gaps closed:** 217 p8-13, 232 p3, 241 p3-6, 229 p2, 244 p2 and 256 p2-3 were viewed.
All are terms text, with no line items.

**Conventions to note (not errors):** 113 gold `22613` vs printed `22613 , 0`, where
normalize_po_no gives 22613 for both. 258/268 gold po_reference is null (ambiguous), so any
non-null prediction scores WRONG, per the never-guess rule.
