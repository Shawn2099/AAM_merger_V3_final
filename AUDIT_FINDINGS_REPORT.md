# PDF ↔ Database Comprehensive Extraction & Reconciliation Audit

**Audit Date:** 2026-10-23  
**Corpus Scope:** 200 PDFs in `data/input`  
**Methodology:** Direct visual inspection of rendered document images and PDF vectors (never trusting embedded text layers / OCR text streams), compared directly against SQLite database records (`documents`, `line_items`, `po_sets`). Audited in sequential batches of 5 PDFs, prioritized starting from pending, unattached/failed, quarantined, and mismatched sets.

---

## Batch 01 (PDFs 1 to 5) — Pending PO Sets 4 & 10

### Documents Audited
1. **Doc 9 (`SIV-ARS-25-7296-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 4
2. **Doc 10 (`SIV-ARS-25-7296-pages-2.pdf`)** — Delivery Note (DN) | PO Set 4
3. **Doc 11 (`SIV-ARS-25-7296-pages-3.pdf`)** — Purchase Order (PO) | PO Set 4
4. **Doc 28 (`SIV-ARS-25-8144-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 10
5. **Doc 29 (`SIV-ARS-25-8144-pages-2.pdf`)** — Delivery Note (DN) | PO Set 10

### Detailed Image Comparison & Verification
- **Doc 9 (SI `SIV-ARS-25-7296`)**: Visually verifies Tax Invoice from Ibrahim Ali Al-Shab Trading Est. for ARO Drilling. Invoice No `SIV-ARS-25-7296`, PO `50116501`. Line 2: `LIFTING BRIDLE,4 POINT,STX 519`, Qty `2.0 Each`, Unit Rate `3,250.00 SAR`. Stored in DB as line `2`, qty `2000` (2.0), price `3250000` (3,250.00). **Exact match**.
- **Doc 10 (DN `GDN-ARS-25-8417`)**: Visually verifies Delivery Note. Delivery Note No `GDN-ARS-25-8417`, PO `50116501`. Item No `2`: `LIFTING BRIDLE,4 POINT,STX 519`, Part No `ST04519C`, Qty `2 Each`. Stored in DB as line `2`, qty `2000`, DN number `GDN-ARS-25-8417`. **Exact match**.
- **Doc 11 (PO `50116501`)**: Visually verifies ARO DPO `50116501` Rev `0`. Contains 2 line items:
  - Item 1: Material `AD048963`, `PRESERVATIVE,EYE WASH;8OZ,6-20GAL/BOTTLE`, Qty `4 EA`, Price `110.00`.
  - Item 2: Material `AD090028`, `LIFTING BRIDLE,4 POINT,STX 519`, Qty `2 EA`, Price `3,250.00`.
  Stored in DB as line `1` (qty `4000`, price `110000`) and line `2` (qty `2000`, price `3250000`). **Exact match**.
- **Doc 28 (SI `SIV-ARS-25-8144`)**: Visually verifies Al-Shab Tax Invoice for SANAD (Saudi Aramco Nabors Drilling). Invoice No `SIV-ARS-25-8144`, PO `18232392`. Line 2: `PLUG, HEX HEAD, 3/8 NPT`, Qty `6.0 Each`, Rate `46.00`. Stored in DB as line `2`, qty `6000`, price `46000`. **Exact match**.
- **Doc 29 (DN `GDN-ARS-25-9377`)**: Visually verifies Delivery Note. Delivery Note No `GDN-ARS-25-9377`, PO `18232392`. Line 2: `PLUG, HEX HEAD, 3/8 NPT`, Qty `6 Each`, Part No `6-PH-SS`. Stored in DB as line `2`, qty `6000`. **Exact match**.

### Findings Summary
- **What was Correct:**
  - Classification of SI, DN, and PO is 100% accurate.
  - Document numbers (`SIV-ARS-25-7296`, `GDN-ARS-25-8417`, `50116501`, `SIV-ARS-25-8144`, `GDN-ARS-25-9377`, `18232392`) extracted without error.
  - Line numbers, quantities, and unit prices match printed page values down to the integer milli-unit.
  - Reconcile reason for Set 4 (`Waiting on more deliveries or invoices: delivered 0 of 4; invoiced 0 of 4`) is commercially accurate: PO item 1 was not fulfilled by the vendor yet.
- **What was Wrong / Misrepresented:**
  - No numerical or structural extraction errors.
  - Minor text contamination in Doc 11: column headers and line metadata (`LineRel No: 11 Part Main Group: CONS`) leaked into the PO item description field.
- **What was Missed:**
  - Nothing missed on these pages.
- **What We Need to Do to Improve:**
  - Implement regex post-cleaning on PO item descriptions to strip trailing column metadata like `LineRel No: ...` and `Part Main Group: ...`.
  - Provide a UI badge differentiating "Genuine Partial Delivery" from sets blocked due to missing document uploads.

---

## Batch 02 (PDFs 6 to 10) — Pending PO Sets 10, 11 & 22

### Documents Audited
6. **Doc 30 (`SIV-ARS-25-8144-pages-3.pdf`)** — Purchase Order (PO) | PO Set 10
7. **Doc 31 (`SIV-ARS-25-8145-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 11
8. **Doc 32 (`SIV-ARS-25-8145-pages-2.pdf`)** — Delivery Note (DN) | PO Set 11
9. **Doc 33 (`SIV-ARS-25-8145-pages-3.pdf`)** — Purchase Order (PO) | PO Set 11
10. **Doc 68 (`SIV-ARS-25-8320-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 22

### Detailed Image Comparison & Verification
- **Doc 30 (PO `18232392`)**: Visually verifies SANAD PO `18232392` Rev `0`. Contains 2 lines:
  - Line 1: `WRENCH 3/4" DRIVE AIR IMPACT`, Qty `1 EACH`, Unit Price `345 SAR`.
  - Line 2: `PLUG, HEX HEAD, 3/8 NPT`, Qty `6 EACH`, Unit Price `46 SAR`.
  Stored in DB as line `1` (qty `1000`, price `345000`) and line `2` (qty `6000`, price `46000`). **Exact match**. Set 10 holds in `pending` because Line 1 was not delivered/invoiced by the vendor.
- **Doc 31 (SI `SIV-ARS-25-8145`)**: Visually verifies Al-Shab Tax Invoice for SANAD PO `18231375`. Contains 2 lines:
  - Line 4: `FILTER, OIL, SPIN ON, 4-11/16" OD X 9-15/16", BY-PASS`, Qty `4.0 Each`, Rate `88.00 SAR`.
  - Line 6: `FILTER, OIL, SPIN ON, 3-11/16" OD X 7-1/8", FULL FLOW - BT237`, Qty `6.0 Each`, Rate `44.00 SAR`.
  Stored in DB as lines `4` (qty `4000`, price `88000`) and `6` (qty `6000`, price `44000`). **Exact match**.
- **Doc 32 (DN `GDN-ARS-25-9374`)**: Visually verifies Al-Shab Delivery Note for SANAD PO `18231375`. Contains lines `4` (qty `4 Each`, part `B7577`) and `6` (qty `6 Each`, part `LF699`), stamped received. Stored in DB as lines `4` and `6` with exact quantities. **Exact match**.
- **Doc 33 (PO `18231375`)**: Visually verifies SANAD PO `18231375` Rev `0` (Page 1 of 2). Contains 5 lines:
  - Line 4: Qty `4`, Unit Price `88`
  - Line 6: Qty `6`, Unit Price `44`
  - Line 8: `FILTER, FUEL...`, Qty `16`, Unit Price `55`
  - Line 9: `FILTER, AIR, PRIMARY`, Qty `3`, Unit Price `91`
  - Line 14: `FILTER, AIR, PRIMARY`, Qty `2`, Unit Price `91`
  Stored in DB with all 5 lines matching line numbers, quantities, and prices exactly. **Exact match**. Set 11 correctly pending on partial delivery of lines 8, 9, 14.
- **Doc 68 (SI `SIV-ARS-25-8320`)**: Visually verifies Al-Shab Tax Invoice for SANAD PO `18232411`. Line 4: `IMPEX 65" TV WEBOS 4K UHD`, Qty `1.0 Each`, Rate `1,553.00 SAR`. Stored in DB as line `4`, qty `1000`, price `1553000`. **Exact match**.

### Findings Summary
- **What was Correct:**
  - Perfect extraction across all 5 files for document headers, line item numbers, quantities, and prices.
  - Reconcile engine status `pending` for Set 10 and Set 11 correctly captures commercial partial fulfillments.
- **What was Wrong / Misrepresented:**
  - None.
- **What was Missed:**
  - None.
- **What We Need to Do to Improve:**
  - Business workflow: Provide an operator toggle or setting to allow partial PO sets to be merged and paid on verified delivery tranches without waiting indefinitely for remaining lines.

---

## Batch 03 (PDFs 11 to 15) — Pending PO Sets 22 & 27

### Documents Audited
11. **Doc 69 (`SIV-ARS-25-8320-pages-2.pdf`)** — Delivery Note (DN) | PO Set 22
12. **Doc 70 (`SIV-ARS-25-8320-pages-3.pdf`)** — Purchase Order (PO) | PO Set 22
13. **Doc 85 (`SIV-DTS-25-452-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 27
14. **Doc 87 (`SIV-DTS-25-452-pages-3.pdf`)** — Purchase Order (PO) | PO Set 27
15. **Doc 88 (`SIV-DTS-25-452-pages-4.pdf`)** — Purchase Order (PO) | PO Set 27

### Detailed Image Comparison & Verification
- **Doc 69 (DN `GDN-ARS-25-9520`)**: Visually verifies Al-Shab Delivery Note for SANAD PO `18232411`.
  - Printed Item No: `4`.
  - Printed Description: `IMPEX 65" TV WEBOS 4K UHD`, Qty `1 Each`.
  - **Misread in DB**: The DB stored description as `IMPEX 85" TV WEBOS 4K UHD` (misread `6` as `8`).
  - Stored quantity `1000` (1.0) and line item number `4` are correct.
- **Doc 70 (PO `18232411`)**: Visually verifies SANAD PO `18232411` Rev `3`. Page 1 of 2 contains 5 lines:
  - Line 1: `JACKET, JEANS, XL...`, Qty `1`, Unit Price `397`
  - Line 2: `[PROTO - J2146]...`, Qty `1`, Unit Price `131`
  - Line 4: `IMPEX 65" TV WEBOS 4K UHD`, Qty `1`, Unit Price `1553` (DB has minor typo `WEBOBS`)
  - Line 5: `[FACOM - NS.3100GPB]...`, Qty `1`, Unit Price `2286`
  - Line 9: `B 53 Optibelt`, Qty `2`, Unit Price `27`
  Extracted accurately. Set 22 holds in `pending` because only Line 4 was delivered/invoiced.
- **Doc 85 (SI `SIV-DTS-25-452`)**: Visually verifies Tax Invoice from Drilling Tools Trading Est. (DrillTools) for Arabian Drilling Company.
  - Buyers Order No: `4500026808`, Invoice No `SIV-DTS-25-452`, Delivery Note reference `GDN-DTS-25-478`.
  - Line `1`: `HAMMER, ANTI-SPARK HEAD, 7LB (4KG), 16" (305MM) HANDLE...`, Qty `1.00 EA`, Rate `2,700.00 SAR`.
  - Stored in DB as line `1`, qty `1000`, price `2700000`. **Exact match**.
- **Doc 87 (PO `4500026808`)**: Visually verifies Arabian Drilling Company PO `4500026808`, Page 1 of 8.
  - Printed Item: `10`.
  - Description: `125152 Hammer: Sledge 7 Lbs, 16" Fiberglass Handle, Anti-Spark...`, Qty `1 EA`, Unit Price `2700.00 SAR`.
  - Stored in DB as line `10`, qty `1000`, price `2700000`. **Exact match**.
- **Doc 88 (PO `4500026808`)**: Visually verifies Arabian Drilling Company PO `4500026808`, Page 2 of 8.
  - Contains only total summary (`Total P.O. Value: 2,700.00`) and instructions to vendor. No line item table.
  - Stored in DB as a PO document with 0 line items.
  - **Structural Input Issue**: Ingestion of page-split input creates a second PO document for the same PO number with 0 lines, tripping the `single_po_document` guard.

### Findings Summary
- **What was Correct:**
  - Header numbers (`18232411`, `4500026808`, `SIV-DTS-25-452`, `GDN-ARS-25-9520`) extracted accurately.
  - Quantities and unit prices across all documents match the printed pages.
- **What was Wrong / Misrepresented:**
  - **Doc 69 Description Misread**: Digit `6` misread as `8` (`65"` → `85"`).
  - **Set 27 Line Number Convention (L1)**: PO numbers the item as `10`, while vendor invoice numbers the item as `1`.
  - **Missing Delivery Note for Set 27**: Set 27 is pending with `"No delivery note in this set yet"` because the corresponding DN (`SIV-DTS-25-452-pages-2.pdf`, Doc 86) lost its header and became unattached.
- **What was Missed:**
  - The continuation page Doc 88 has zero line items and is merely totals/terms. Treating it as a distinct PO document causes structural set pollution.
- **What We Need to Do to Improve:**
  - Ignore or classify 0-line continuation pages as `PO_CONTINUATION` rather than registering them as a full secondary PO.
  - Add OCR/VLM post-check on critical digit/token specifications in descriptions.
  - Support Step-10 mapping (`10 ↔ 1`) for SAP/Arabian Drilling POs.

---

## Batch 04 (PDFs 16 to 20) — Pending PO Set 28

### Documents Audited
16. **Doc 89 (`SIV-DTS-25-456-pages-1.pdf`)** — Sales Invoice (SI) Page 1 | PO Set 28
17. **Doc 90 (`SIV-DTS-25-456-pages-2.pdf`)** — Sales Invoice (SI) Page 2 | PO Set 28
18. **Doc 92 (`SIV-DTS-25-456-pages-4.pdf`)** — Purchase Order (PO) Page 1 | PO Set 28
19. **Doc 93 (`SIV-DTS-25-456-pages-5.pdf`)** — Purchase Order (PO) Page 2 | PO Set 28
20. **Doc 94 (`SIV-DTS-25-456-pages-6.pdf`)** — Purchase Order (PO) Page 3 | PO Set 28

### Detailed Image Comparison & Verification
- **Doc 89 & Doc 90 (SI `SIV-DTS-25-456`)**: Visually verifies 2-page Tax Invoice from Drilling Tools Trading Est. for Arabian Drilling Company PO `4500039614`.
  - Delivery Note reference: `GDN-DTS-25-493`.
  - Contains 17 item lines: `1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18` (note: line 11 was intentionally skipped on both customer PO and vendor invoice).
  - All quantities and rates match exactly down to the cent:
    - Line 1: Qty 1.00, Rate 104.50
    - Line 2: Qty 2.00, Rate 34.43
    - Line 3: Qty 2.00, Rate 244.20
    - Line 7: Qty 2.00, Rate 112.22
    - Line 9: Qty 1.00, Rate 1526.80
    - Line 18: Qty 1.00, Rate 199.10
  - Stored in DB across Doc 89 (11 items) and Doc 90 (6 items) with 100% numerical accuracy.
- **Doc 92, Doc 93 & Doc 94 (PO `4500039614`)**: Visually verifies 3 pages of Arabian Drilling Company PO `4500039614`.
  - Item lines printed: `10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 120, 130, 140, 150, 160, 170, 180`.
  - Every line corresponds 1-to-1 with the vendor invoice:
    - PO Line 10 (Proto Adj Wrench 10") = SI Line 1 (Qty 1.0, 104.50)
    - PO Line 20 (Adj Wrench 12") = SI Line 2 (Qty 2.0, 34.43)
    - PO Line 90 (Combo Wrench Set) = SI Line 9 (Qty 1.0, 1526.80)
    - PO Line 180 (28" Pry Bar) = SI Line 18 (Qty 1.0, 199.10)
  - All 17 lines stored in DB with complete fidelity to the printed page.

### Findings Summary
- **What was Correct:**
  - Complete, flawless extraction of all 17 items, quantities, unit prices, and descriptions across multiple split pages.
  - Zero OCR errors or column misalignments.
- **What was Wrong / Misrepresented:**
  - **L1 Numbering Convention Mismatch**: Arabian Drilling PO uses SAP standard step-10 numbering (`10, 20, ... 180`) while Drilling Tools invoice uses sequential numbering (`1, 2, ... 18`). The reconciliation engine cannot pair them without a step-10 normalizer or fuzzy fallback.
  - **Missing DN**: The set lacks a delivery note because the corresponding DTS DN (`SIV-DTS-25-456-pages-3.pdf`, Doc 91) lost its header during extraction and is unattached (`NO_PO_SET`).
- **What was Missed:**
  - Because page-split inputs were uploaded as separate PDFs, PO `4500039614` was ingested as 3 separate PO documents (Docs 92, 93, 94) instead of a single 3-page PO entity.
- **What We Need to Do to Improve:**
  - Add an automated step-10 normalization rule (`if all PO lines are multiples of 10 and vendor lines are 1..N, match line_no // 10`).
  - Read rotated/side-margin DTS DN headers so Doc 91 attaches to this set.
  - Group page-split documents (`-pages-1.pdf`, `-pages-2.pdf`, etc.) at ingestion time into a single multi-page PDF before database registration.

---

## Batch 05 (PDFs 21 to 25) — Pending PO Sets 29 & 30

### Documents Audited
21. **Doc 95 (`SIV-DTS-25-477-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 29
22. **Doc 97 (`SIV-DTS-25-477-pages-3.pdf`)** — Purchase Order (PO) | PO Set 29
23. **Doc 98 (`SIV-DTS-25-480-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 30
24. **Doc 100 (`SIV-DTS-25-480-pages-3.pdf`)** — Purchase Order (PO) Page 1 | PO Set 30
25. **Doc 101 (`SIV-DTS-25-480-pages-4.pdf`)** — Purchase Order (PO) Page 2 | PO Set 30

### Detailed Image Comparison & Verification
- **Doc 95 (SI `SIV-DTS-25-477`) & Doc 97 (PO `TOGA-PO-25-26-163`)**: Visually verifies Toga Drilling / Tubestar PO `TOGAPO2526163` vs Drilling Tools invoice `SIV-DTS-25-477`.
  - Both documents share identical line items (1 to 5):
    - Line 1: `A4 Pocket File ROCO 11 Punched Hole`, Qty `10.0`, Unit Price `11.50`
    - Line 2: `Ball Point Pen Leeno 0.5mm RED`, Qty `1.0`, Unit Price `10.00`
    - Line 3: `Gel Pen Pilot G-2 Blue`, Qty `1.0`, Unit Price `39.00`
    - Line 4: `Ball Point Pen Leeno 0.5mm Blue`, Qty `10.0`, Unit Price `10.00`
    - Line 5: `Correction Pen (whitener)`, Qty `2.0`, Unit Price `37.00`
  - Total value matches exactly: `338.00 SAR`.
  - **Reconciliation Impact**: Set 29 has 100% agreement between PO and SI on all lines, quantities, and prices. It remains pending solely because the DN (`SIV-DTS-25-477-pages-2.pdf`, Doc 96) lost its header during extraction and is unattached.
- **Doc 98 (SI `SIV-DTS-25-480`)**: Visually verifies Drilling Tools Tax Invoice for KCA Deutag PO `4500496007`.
  - Contains 5 lines of `SPLIT COTTER PIN 3mm x 40mm`:
    - Line 1: Qty 200, Rate 0.33
    - Line 2: Qty 100, Rate 0.33
    - Line 3: Qty 100, Rate 0.33
    - Line 4: Qty 100, Rate 0.33
    - Line 5: Qty 200, Rate 0.33
  - Total quantity: `700`. Stored in DB accurately across all 5 lines.
- **Doc 100 (PO `4500496007` Page 1)**: Visually verifies KCA Deutag PO Page 1 of 3. Contains instructions and vendor terms only; zero line items. Stored in DB with 0 line items.
- **Doc 101 (PO `4500496007` Page 2)**: Visually verifies KCA Deutag PO Page 2 of 3.
  - Item 10: `COTTER PIN: 3X40MM`, Qty `200 PC`, Unit Price printed as **`0,33`** (comma decimal separator).
  - Item 20: `COTTER PIN: 3X40MM`, Qty `100 PC`, Unit Price printed as `0.33` (dot decimal separator).
  - **CRITICAL Extraction Defect Found**: DB stored Item 10 with `unit_price = 0` (`0.0`), while Item 20 was stored with `unit_price = 330` (`0.33`). The VLM/parser failed to parse the comma decimal separator `0,33` and defaulted to zero!

### Findings Summary
- **What was Correct:**
  - Set 29 extraction is 100% flawless; PO and SI match down to the exact item, quantity, and cent.
  - Doc 98 invoice line extraction correctly captured all 5 cotter pin delivery tranches.
- **What was Wrong / Misrepresented:**
  - **Comma Decimal Parse Failure**: In Doc 101 Line 10, `0,33` was converted to `0.0` in the database.
  - **Set 30 Line Numbering**: KCA Deutag uses SAP step-10 numbering (`10, 20...`), while DTS invoice uses `1, 2, 3, 4, 5`.
- **What was Missed:**
  - Doc 100 is an empty terms page uploaded as a separate PO document.
  - Doc 96 delivery note is detached from Set 29 due to lost DTS header.
- **What We Need to Do to Improve:**
  - Support locale-tolerant decimal parsing: normalize `,` between digits (e.g. `0,33` → `0.33`) during price and quantity extraction.
  - Attach Doc 96 to Set 29 so Set 29 can merge immediately.

---

## Batch 06 (PDFs 26 to 30) — Pending PO Sets 30, 31 & 32

### Documents Audited
26. **Doc 102 (`SIV-DTS-25-480-pages-5.pdf`)** — Purchase Order (PO) Page 3 | PO Set 30
27. **Doc 103 (`SIV-DTS-25-486-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 31
28. **Doc 105 (`SIV-DTS-25-486-pages-3.pdf`)** — Purchase Order (PO) Page 1 | PO Set 31
29. **Doc 106 (`SIV-DTS-25-486-pages-4.pdf`)** — Purchase Order (PO) Page 2 | PO Set 31
30. **Doc 107 (`SIV-DTS-25-498-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 32

### Detailed Image Comparison & Verification
- **Doc 102 (PO `4500496007` Page 3)**: Visually verifies KCA Deutag PO Page 3 of 3.
  - Lines 30, 40, 50: `COTTER PIN: 3X40MM` with quantities `100`, `100`, `200` and unit prices `0.33 SAR`.
  - Together with Doc 101 (lines 10 & 20, quantities 200 & 100), total PO cotter pin quantity is **700**, matching invoice Doc 98 (**700**) exactly.
- **Doc 103 (SI `SIV-DTS-25-486`)**: Visually verifies Drilling Tools Tax Invoice for KCA Deutag PO `4500496514`.
  - Item 1: `Cotter Pin Galvanized M5X152MM`, Qty `250.0`, Rate `1.70 SAR`, Value `425.00 SAR`.
  - Stored in DB as line `1`, qty `250000`, price `1700`. **Exact match**.
- **Doc 105 (PO `4500496514` Page 1)**: Visually verifies KCA Deutag PO terms/conditions page. Zero lines. Stored with 0 lines.
- **Doc 106 (PO `4500496514` Page 2)**: Visually verifies KCA Deutag PO Item page.
  - Item 10: `COTTER PIN: 5MM X 152MM`, Qty `250 EA`, Unit Price printed as **`1,70`**, Total `425,00 SAR`.
  - **SYSTEMIC DEFECT RE-CONFIRMED**: DB stored line 10 `unit_price = 0` (`0.0`). The extractor completely dropped the unit price because KCA Deutag uses a European comma decimal separator (`1,70`).
- **Doc 107 (SI `SIV-DTS-25-498`)**: Visually verifies Drilling Tools Tax Invoice for Tubestar PO `ARMPO2526029`.
  - Lines 1, 2, 3: Steel Files (qty 7, rate 12), Rope (qty 2, rate 53), LED Torch (qty 3, rate 35).
  - Stored in DB with 100% accuracy.

### Findings Summary
- **What was Correct:**
  - Header numbers (`4500496007`, `4500496514`, `ARMPO2526029`, `SIV-DTS-25-486`, `SIV-DTS-25-498`) extracted accurately.
  - Line quantities match printed pages across all items.
- **What was Wrong / Misrepresented:**
  - **Systemic Decimal Comma Extraction Bug**: In both KCA Deutag POs (`4500496007` Doc 101 and `4500496514` Doc 106), prices printed with a comma separator (`0,33` and `1,70`) are stored in the database as `0.0`.
  - **L1 Numbering**: Step-10 convention on KCA Deutag PO (`10 ↔ 1`).
- **What was Missed:**
  - Missing Delivery Notes: Sets 30, 31, 32 are all blocked pending because their respective DTS delivery notes (`SIV-DTS-25-480-pages-2.pdf`, `SIV-DTS-25-486-pages-2.pdf`, `SIV-DTS-25-498-pages-2.pdf`) are unattached due to lost headers.
- **What We Need to Do to Improve:**
  - **Fix Decimal Parser**: Update LLM extraction prompt and validator to accept European comma decimals (`\d+,\d{2}`) and normalize them to standard decimal points before integer milli-unit conversion.
  - Fix DTS delivery note header detection so delivery notes attach to Sets 30, 31, and 32.

---

## Batch 07 (PDFs 31 to 35) — Pending PO Sets 32, 33 & 34

### Documents Audited
31. **Doc 109 (`SIV-DTS-25-498-pages-3.pdf`)** — Purchase Order (PO) | PO Set 32
32. **Doc 110 (`SIV-DTS-25-509-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 33
33. **Doc 112 (`SIV-DTS-25-509-pages-3.pdf`)** — Purchase Order (PO) | PO Set 33
34. **Doc 113 (`SIV-DTS-25-517-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 34
35. **Doc 115 (`SIV-DTS-25-517-pages-3.pdf`)** — Purchase Order (PO) | PO Set 34

### Detailed Image Comparison & Verification
- **Doc 109 (PO `ARMPO2526029`)**: Visually verifies Tubestar PO `ARM-PO-25-26-029`.
  - Lines 1 (Steel Files, qty 7, price 12), 2 (Rope, qty 2, price 53), 3 (LED Torch, qty 3, price 35).
  - Matches invoice Doc 107 in Set 32 across all line items, quantities, and prices **100% exactly**. Set 32 is pending solely because DTS delivery note Doc 108 is unattached.
- **Doc 110 (SI `SIV-DTS-25-509`)**: Visually verifies Drilling Tools Tax Invoice for ADES PO `22843`.
  - Item lines printed: `1, 2, 3`:
    - Line 1: `SAT Power Maintenance Battery CMF180`, Qty `2.0`, Rate `660.00 SAR`
    - Line 2: `Sprinter Rechargeable battery XP12V4800`, Qty `6.0`, Rate `1705.00 SAR`
    - Line 3: `SAT Power Battery 60032`, Qty `2.0`, Rate `429.00 SAR`
  - Stored in DB with 100% numerical accuracy.
- **Doc 112 (PO `22843`)**: Visually verifies ADES Standard Purchase Order `PO 22843` Rev `0`.
  - **Printed Line Numbers**: The table printed on the ADES PO explicitly skips line 2 and labels the rows:
    - Line `1`: SAT Power Battery, Qty `2`, Unit Price `660`
    - Line `3`: Sprinter Battery, Qty `6`, Unit Price `1705`
    - Line `4`: SAT Power Battery, Qty `2`, Unit Price `429`
  - All quantities and rates match invoice Doc 110 exactly, but because ADES numbered the lines `1, 3, 4` and vendor invoice numbered them `1, 2, 3`, lines 2/3 cannot reconcile under strict line-number matching.
- **Doc 113 (SI `SIV-DTS-25-517`)**: Visually verifies Drilling Tools Tax Invoice for ADES PO.
  - Buyers Order No printed field: `22613 , 0` (PO 22613, Revision 0).
  - Item 1: `Drill Pipe Connection Guide 4-1/2"`, Qty `1.00`, Rate `2,173.00 SAR`.
  - **CRITICAL Extraction Defect Confirmed (X2)**: DB stored `po_no_raw = 226130` and `po_no_normalized = 226130`! The extractor concatenated the trailing revision digit `, 0` directly onto the PO number.
- **Doc 115 (PO `22613`)**: Visually verifies ADES PO `22613` Rev `0`.
  - Header: `PO , Rev #`: `22613 , 0`. Stored in DB correctly as `po_no_normalized = 22613`.
  - Line Item printed: `Line 2`: `DRILL PIPE CONNECTION GUIDE 4.1/2"`, Qty `1`, Unit Price `2173.00`.
  - Stored in DB as line `2`, qty `1000`, price `2173000`.
  - **Two-fold Reconciliation Failure**:
    1. PO number mismatch (`226130` on invoice vs `22613` on PO) due to revision digit concatenation.
    2. Line number mismatch (`1` on invoice vs `2` on PO).

### Findings Summary
- **What was Correct:**
  - Quantities and unit prices across all 5 files were extracted with 100% precision.
  - Set 32 PO items match invoice items completely.
- **What was Wrong / Misrepresented:**
  - **PO Revision Concatenation (X2)**: `22613 , 0` on Doc 113 misread as `226130`.
  - **Vendor vs Buyer Line Number Discrepancies**:
    - Set 33: PO has lines `1, 3, 4`; invoice has `1, 2, 3`.
    - Set 34: PO has line `2`; invoice has line `1`.
- **What was Missed:**
  - Attached delivery notes for Sets 32, 33, and 34 were not associated because DTS delivery notes lost their headers.
- **What We Need to Do to Improve:**
  - **PO Revision Stripping Rule**: Regex strip trailing `, \d+` or `\s*,\s*[0-9]+` from ADES/DTS buyer order fields.
  - Enable description similarity or sequential index fallback when vendor re-indexes line numbers sequentially.

---

## Batch 08 (PDFs 36 to 40) — Pending PO Sets 35 & 36

### Documents Audited
36. **Doc 116 (`SIV-DTS-25-519-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 35
37. **Doc 118 (`SIV-DTS-25-519-pages-3.pdf`)** — Purchase Order (PO) Page 1 | PO Set 35
38. **Doc 119 (`SIV-DTS-25-519-pages-4.pdf`)** — Purchase Order (PO) Page 2 | PO Set 35
39. **Doc 120 (`SIV-DTS-25-538-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 36
40. **Doc 122 (`SIV-DTS-25-538-pages-3.pdf`)** — Purchase Order (PO) Page 1 | PO Set 36

### Detailed Image Comparison & Verification
- **Doc 116 (SI `SIV-DTS-25-519`)**: Visually verifies Drilling Tools Tax Invoice for Arabian Drilling Company PO `4500044740`.
  - Delivery Note reference: `GDN-DTS-25-555`.
  - Item 1: `Body Solid Pro Clubline Commercial Squat Stand`, Qty `1.00 PCS`, Rate `1,856.00 SAR`.
  - Stored in DB as line `1`, qty `1000`, price `1856000`. **Exact match**.
- **Doc 118 (PO `4500044740` Page 1)**: Visually verifies Arabian Drilling Company PO `4500044740`.
  - Item printed: `10`.
  - Description: `Rakc: Squat F/ Gym Room, Mfg: SportsArt; Body Solid Pro Clubine Commercial Squat Stand...`, Qty `1 EA`, Unit Price `1856.00 SAR`.
  - Stored in DB as line `10`, qty `1000`, price `1856000`. **Exact match**.
- **Doc 119 (PO `4500044740` Page 2)**: Visually verifies continuation sheet with 0 lines.
- **Doc 120 (SI `SIV-DTS-25-538`)**: Visually verifies Drilling Tools Tax Invoice for Arabian Drilling Company PO `4500047182`.
  - Delivery Note reference: `GDN-DTS-25-574`.
  - Contains 5 gym equipment items:
    - Line 1: `Insight Fitness DR010B Back Extension`, Qty `1.00`, Rate `1,530.00`
    - Line 2: `Axox Fitness Aerobic Stepper`, Qty `1.00`, Rate `65.00`
    - Line 3: `Body Solid GFID100B FID Bench`, Qty `1.00`, Rate `1,050.00`
    - Line 4: `Axox Fitness Solid Tricep Press Down Bar`, Qty `1.00`, Rate `95.00`
    - Line 5: `Insight Fitness Leg Curl/Extension`, Qty `1.00`, Rate `5,570.00`
  - Total: `8,310.00 SAR`. Stored in DB across all 5 lines with 100% accuracy.
- **Doc 122 (PO `4500047182` Page 1)**: Visually verifies Arabian Drilling Company PO `4500047182`.
  - Item printed: `20`: `183371 Roman Machine: 45 Deg Positioning... Insight Fitness DR010B Back Extension...`, Qty `1`, Unit Price `1530.00 SAR`.
  - Corresponds exactly to SI Line 1 (`1,530.00 SAR`).

### Findings Summary
- **What was Correct:**
  - Complete, accurate extraction of all line items, quantities, and prices for gym equipment orders.
- **What was Wrong / Misrepresented:**
  - **L1 Numbering Convention**: Set 35 PO item `10` vs invoice item `1`. Set 36 PO item `20` vs invoice item `1`.
  - **Page-Split Ingestion**: Doc 119 (terms only) ingested as a distinct PO document.
- **What was Missed:**
  - Both delivery notes (`SIV-DTS-25-519-pages-2.pdf` Doc 117 and `SIV-DTS-25-538-pages-2.pdf` Doc 121) were unattached due to lost DTS headers.
- **What We Need to Do to Improve:**
  - Link DTS delivery notes to unblock Sets 35 and 36.
  - Apply step-10 line normalizer on Arabian Drilling POs.

---

## Batch 09 (PDFs 41 to 45) — Pending PO Set 36

### Documents Audited
41. **Doc 123 (`SIV-DTS-25-538-pages-4.pdf`)** — Purchase Order (PO) Page 2 | PO Set 36
42. **Doc 124 (`SIV-DTS-25-538-pages-5.pdf`)** — Purchase Order (PO) Page 3 | PO Set 36
43. **Doc 125 (`SIV-DTS-25-538-pages-6.pdf`)** — Purchase Order (PO) Page 4 | PO Set 36
44. **Doc 126 (`SIV-DTS-25-548-pages-1.pdf`)** — Sales Invoice (SI) 2 | PO Set 36
45. **Doc 128 (`SIV-DTS-25-548-pages-3.pdf`)** — Purchase Order (PO) Page 1 (Duplicate Upload) | PO Set 36

### Detailed Image Comparison & Verification
- **Doc 123 (PO `4500047182` Page 2)**: Visually verifies Arabian Drilling Company PO lines:
  - Line 30: `Aerobic Step F/ Gym Room`, Qty `1.0`, Price `65.00 SAR`.
  - Line 40: `Bench: Adjustable Incline... Body Solid GFID100B`, Qty `1.0`, Price `1050.00 SAR`.
  - Corresponds exactly to SI Doc 120 Line 2 (`65.00`) and Line 3 (`1050.00`).
- **Doc 124 (PO `4500047182` Page 3)**: Visually verifies Arabian Drilling Company PO lines:
  - Line 50: `Press Down Bar: Tricep...`, Qty `1.0`, Price `95.00 SAR` (matches SI Doc 120 Line 4).
  - Line 60: `Machine: Seated Leg Curl/Extension...`, Qty `1.0`, Price `5570.00 SAR` (matches SI Doc 120 Line 5).
  - Quantities and prices stored in DB with 100% accuracy.
- **Doc 125 (PO `4500047182` Page 4)**: Visually verifies continuation sheet of Line 60 specs.
  - The printed table on Page 4 has **no Item number, no quantity, and no price**; it merely prints the continuation text `"Non-absorbing PVC compound retained with aluminum collars..."` followed by the PO summary total (`8,310.00 SAR`).
  - **CRITICAL Extraction Defect Confirmed (X7)**: The extractor created a phantom line item in DB: `line_item_no = NULL`, `quantity = 0.0`, `price = 0.0` for this continuation text.
- **Doc 126 (SI `SIV-DTS-25-548`)**: Visually verifies second Tax Invoice for PO `4500047182`.
  - Item 6: `Axox Fitness 1sqm Rubber Interlocking Mat`, Qty `1.00 Each`, Rate `110.00 SAR`.
  - Stored in DB with exact line `6`, qty `1000`, price `110000`.
- **Doc 128 (PO `4500047182`)**: Visually verifies duplicate copy of PO Page 1 re-uploaded with the second invoice tranche.

### Findings Summary
- **What was Correct:**
  - Real items (lines 30, 40, 50, 60 on PO and lines 1, 2, 3, 4, 5, 6 across invoices) extracted with exact quantities and unit prices.
- **What was Wrong / Misrepresented:**
  - **Phantom Zero-Quantity Row (X7)**: Doc 125 created a line item with `line_item_no = NULL` and `quantity = 0`. Description continuation rows must never be stored as independent line items.
  - **L1 Numbering**: Customer PO numbers `20, 30, 40, 50, 60` while vendor invoices number `1, 2, 3, 4, 5, 6`.
- **What was Missed:**
  - Both delivery notes (`GDN-DTS-25-574` on Doc 121 and `GDN-DTS-25-584` on Doc 127) were unattached due to DTS header loss.
- **What We Need to Do to Improve:**
  - **Filter Continuation Rows**: Drop any extracted line item where both `line_item_no` is null/empty and `quantity <= 0`, or append its description to the preceding line item.
  - Support multi-invoice sets: Set 36 has two legitimate partial invoices (`SIV-DTS-25-538` and `SIV-DTS-25-548`) covering distinct items on PO `4500047182`.

---

## Batch 10 (PDFs 46 to 50) — Pending PO Sets 36 & 37

### Documents Audited
46. **Doc 129 (`SIV-DTS-25-548-pages-4.pdf`)** — Purchase Order (PO) Page 2 (Tranche 2) | PO Set 36
47. **Doc 130 (`SIV-DTS-25-548-pages-5.pdf`)** — Purchase Order (PO) Page 3 (Tranche 2) | PO Set 36
48. **Doc 131 (`SIV-DTS-25-548-pages-6.pdf`)** — Purchase Order (PO) Page 4 (Tranche 2) | PO Set 36
49. **Doc 132 (`SIV-DTS-25-565-1.pdf`)** — Sales Invoice (SI) | PO Set 37
50. **Doc 134 (`SIV-DTS-25-565-3.pdf`)** — Purchase Order (PO) | PO Set 37

### Detailed Image Comparison & Verification
- **Docs 129, 130, 131 (PO `4500047182` Re-upload)**: Visually verifies pages 4, 5, 6 of Arabian Drilling PO re-uploaded under the second invoice delivery package.
  - Doc 131 prints Line `70`: `129162 Mat: Tiles 1 Mtr x 1 Mtr x 1" Thk Soft Foam Rubber`, Qty `1 EA`, Unit Price `110.00 SAR`.
  - Corresponds exactly to SI Doc 126 Line 6 (`110.00 SAR`).
- **Doc 132 (SI `SIV-DTS-25-565`) & Doc 134 (PO `ARMPO2526035`)**: Visually verifies Tubestar PO `ARM-PO-25-26-035` vs Drilling Tools Tax Invoice `SIV-DTS-25-565`.
  - Line 1: `Cup Wire Brush 75MM M14X2`, Qty `15.0`, Unit Price `15.00 SAR` (Value: 225.00 SAR).
  - Line 2: `Wire Brush 5 Line *16`, Qty `5.0`, Unit Price `54.00 SAR` (Value: 270.00 SAR).
  - Line 3: `Cotton Rags - White Baniyan Cloth`, Qty `8.0`, Unit Price `104.00 SAR` (Value: 832.00 SAR).
  - Total: **1,327.00 SAR** (+ 15% VAT = 1,526.05 SAR).
  - **100% Exact Agreement**: Both buyer PO and vendor SI use identical line numbering (`1, 2, 3`), identical quantities, and identical unit prices.
  - Set 37 is pending solely because the delivery note (`Doc 133`, `SIV-DTS-25-565-2.pdf`) lost its DTS header during extraction and is sitting unattached in `NO_PO_SET`.

### Findings Summary
- **What was Correct:**
  - Complete, error-free extraction of line numbers, descriptions, quantities, and unit prices for Set 37.
  - Clean extraction of Line 70 (Mat) on Doc 131.
- **What was Wrong / Misrepresented:**
  - Duplicate PO document uploads (Docs 128–131) create redundant PO records in Set 36.
- **What was Missed:**
  - Delivery note Doc 133 unattached, blocking immediate automatic merge of Set 37.
- **What We Need to Do to Improve:**
  - Immediate high-ROI fix: Reading the DTS delivery note header on Doc 133 will allow Set 37 to merge automatically with zero errors.

---

## Batch 11 (PDFs 51 to 55) — Pending PO Sets 38, 39 & 40

### Documents Audited
51. **Doc 138 (`SIV-DTS-25-572-1.pdf`)** — Sales Invoice (SI) | PO Set 38
52. **Doc 140 (`SIV-DTS-25-572-3.pdf`)** — Purchase Order (PO) | PO Set 38
53. **Doc 141 (`SIV-DTS-25-575-1.pdf`)** — Sales Invoice (SI) | PO Set 39
54. **Doc 143 (`SIV-DTS-25-575-3.pdf`)** — Purchase Order (PO) | PO Set 39
55. **Doc 144 (`SIV-DTS-25-576-1.pdf`)** — Sales Invoice (SI) | PO Set 40

### Detailed Image Comparison & Verification
- **Doc 138 (SI `SIV-DTS-25-572`) & Doc 140 (PO `22972`)**: Visually verifies ADES PO `22972` vs Drilling Tools Tax Invoice `SIV-DTS-25-572`.
  - Line 1: `WIRELESS CONTROLLER FOR PLAYSTATION`, Qty `2.0`, Unit Price `376.29 SAR` (Value: 752.58 SAR).
  - Line 2: `DUAL SENSE CHARGING STATION`, Qty `1.0`, Unit Price `144.30 SAR` (Value: 144.30 SAR).
  - Total: **896.88 SAR** (+ 15% VAT = 1,031.42 SAR).
  - **100% Exact Agreement**: Both buyer PO and vendor SI share identical line numbers (`1, 2`), identical quantities, and identical unit rates. Set 38 is ready to merge immediately once the delivery note is attached.
- **Doc 141 (SI `SIV-DTS-25-575`) & Doc 143 (PO `TOGAPO2526228`)**: Visually verifies Tubestar PO `TOGA-PO-25-26-228` vs Drilling Tools Tax Invoice `SIV-DTS-25-575`.
  - Line 1: `Ansell Edge Heavy-Duty Hi-Viz Impact Resistant Gloves-12 (XXL-Large)`, Qty `20.0 Pair`, Unit Price `56.50 SAR`.
  - Total: **1,130.00 SAR** (+ 15% VAT = 1,299.50 SAR).
  - **100% Exact Agreement**: Exact line `1`, exact qty `20`, exact price `56.50`.
- **Doc 144 (SI `SIV-DTS-25-576`)**: Visually verifies Drilling Tools Tax Invoice for ADES PO `22398`.
  - Buyers Order No: `22398 , 0`.
  - Here the extractor correctly normalized PO as `22398` (did not concatenate the `0`).
  - Line 1: `SpareLamp, Nova-Strobe Xenon Stroboscopes MONARCH`, Qty `2.00 EA`, Rate `410.00 SAR`. Stored accurately.

### Findings Summary
- **What was Correct:**
  - Flawless extraction for Set 38 and Set 39 across all headers, line items, quantities, and prices.
  - PO number normalization correctly handled `22398 , 0` on Doc 144.
- **What was Wrong / Misrepresented:**
  - None on extraction for these documents.
- **What was Missed:**
  - Both delivery notes (`Doc 139`, `SIV-DTS-25-572-2.pdf` and `Doc 142`, `SIV-DTS-25-575-2.pdf`) are unattached because DTS delivery note headers are not read.
- **What We Need to Do to Improve:**
  - Attach DTS delivery notes Doc 139 and Doc 142. Sets 38 and 39 have zero commercial discrepancies and will merge automatically.

---

## Batch 12 (PDFs 56 to 60) — Pending PO Sets 40, 41 & 42

### Documents Audited
56. **Doc 146 (`SIV-DTS-25-576-3.pdf`)** — Purchase Order (PO) | PO Set 40
57. **Doc 147 (`SIV-DTS-25-579-1.pdf`)** — Sales Invoice (SI) | PO Set 41
58. **Doc 149 (`SIV-DTS-25-579-3.pdf`)** — Purchase Order (PO) | PO Set 41
59. **Doc 150 (`SIV-DTS-25-580-1.pdf`)** — Sales Invoice (SI) | PO Set 42
60. **Doc 152 (`SIV-DTS-25-580-3.pdf`)** — Purchase Order (PO) | PO Set 42

### Detailed Image Comparison & Verification
- **Doc 146 (PO `22398`)**: Visually verifies ADES PO `22398` Rev `0`.
  - Line 1: `LAMP, STOBE, NOVA FOR AVIATION WARNING LIGHT...`, Qty `2 EACH`, Unit Price `410.00 SAR` (Value: 820.00 SAR).
  - Corresponds exactly to invoice Doc 144 (`410.00 SAR`, qty 2).
  - **100% Exact Match**: Set 40 has identical quantities and prices on Line 1. It is pending solely because delivery note `SIV-DTS-25-576-2.pdf` (Doc 145) lost its header during extraction.
- **Doc 147 (SI `SIV-DTS-25-579`)**: Visually verifies Drilling Tools Tax Invoice for KCA Deutag PO `4500498641`.
  - Line 1: `DWE492-220V`, Qty `1.0`, Rate `521.02 SAR`.
  - Line 2: `DWE4120-220V`, Qty `1.0`, Rate `303.02 SAR`.
  - Extracted accurately into DB.
- **Doc 149 (PO `4500498641`)**: Visually verifies KCA Deutag PO file.
  - **Image Verification of Incomplete Document (D1)**: The file uploaded is strictly **Page 1 of 3**. It contains solely header info, terms, and delivery instructions. Pages 2 and 3 containing the line item table were never uploaded.
  - The DB stored 0 line items, which correctly reflects the incomplete PDF.
- **Doc 150 (SI `SIV-DTS-25-580`) & Doc 152 (PO `192641`)**: Visually verifies ADES PO `192641` vs Drilling Tools Tax Invoice `SIV-DTS-25-580`.
  - Line 1: `BUCKLE FOR POLYCORD STRAPPING...`, Qty `1`, Unit Price `239.76 SAR`.
  - Line 2: `TENSIONER, STRAPPING PLASTIC...`, Qty `2`, Unit Price `1,165.50 SAR`.
  - Line 3: `STRAPPING, PLASTIC, 5/8" x 1200MTR...`, Qty `6`, Unit Price `158.73 SAR`.
  - Total: **3,523.14 SAR** (+ 15% VAT = 4,051.61 SAR).
  - **100% Exact Agreement**: Both documents match on line numbers (`1, 2, 3`), quantities (`1, 2, 6`), and unit rates.
  - Set 42 is pending solely because delivery note Doc 151 lost its DTS header.

### Findings Summary
- **What was Correct:**
  - Perfect extraction of lines, quantities, and rates for Set 40 and Set 42.
- **What was Wrong / Misrepresented:**
  - None on extraction for valid documents.
- **What was Missed:**
  - **Incomplete Upload (D1)**: Doc 149 is missing pages 2 & 3 of the PO.
  - **Detached Delivery Notes**: Sets 40 and 42 are blocked pending because their DTS delivery notes (Docs 145 and 151) lost their headers during extraction.
- **What We Need to Do to Improve:**
  - Attach DTS delivery notes Doc 145 and Doc 151 to unblock Sets 40 and 42 for immediate auto-merging.
  - Flag incomplete multi-page POs (e.g. "Page 1 of 3 uploaded; pages 2–3 missing") in the UI ingestion queue.

---

## Batch 13 (PDFs 61 to 65) — Pending PO Sets 43, 44 & 45

### Documents Audited
61. **Doc 153 (`SIV-DTS-25-581-1.pdf`)** — Sales Invoice (SI) | PO Set 43
62. **Doc 155 (`SIV-DTS-25-581-3.pdf`)** — Purchase Order (PO) | PO Set 43
63. **Doc 156 (`SIV-DTS-25-582-1.pdf`)** — Sales Invoice (SI) | PO Set 44
64. **Doc 158 (`SIV-DTS-25-582-3.pdf`)** — Purchase Order (PO) | PO Set 44
65. **Doc 159 (`SIV-DTS-25-583-1.pdf`)** — Sales Invoice (SI) | PO Set 45

### Detailed Image Comparison & Verification
- **Doc 153 (SI `SIV-DTS-25-581`) & Doc 155 (PO `192701`)**: Visually verifies ADES PO `192701` vs Drilling Tools Tax Invoice `SIV-DTS-25-581`.
  - Line 1: `Ductile Iron Butterfly Valve Wafer Type... Size: 12"`, Qty `3.0 EA`, Unit Price `2,225.00 SAR` (Value: 6,675.00 SAR).
  - Line 2: `Ductile Iron Butterfly Valve Wafer Type... Size: 8"`, Qty `7.0 EA`, Unit Price `1,185.00 SAR` (Value: 8,295.00 SAR).
  - Total: **14,970.00 SAR** (+ 15% VAT = 17,215.50 SAR).
  - **100% Exact Agreement**: Both buyer PO and vendor SI share identical line numbers (`1, 2`), identical quantities, and identical unit rates. Set 43 will merge immediately once the delivery note is linked.
- **Doc 156 (SI `SIV-DTS-25-582`) & Doc 158 (PO `192910`)**: Visually verifies ADES PO `192910` vs Drilling Tools Tax Invoice `SIV-DTS-25-582`.
  - Line 1: `Gazelle - 12FT FIBERGLASS STEP LADDER (3.6M) / Twin Stepladder`, Qty `2.0 EACH`, Unit Price `1,500.00 SAR`.
  - Total: **3,000.00 SAR** (+ 15% VAT = 3,450.00 SAR).
  - **100% Exact Agreement**: Line `1`, quantity `2.0`, price `1500.00`. Set 44 is ready for immediate merge.
- **Doc 159 (SI `SIV-DTS-25-583`)**: Visually verifies Drilling Tools Tax Invoice for ADES PO `193781`.
  - Contains 4 welding lines:
    - Line 1: `ELECTRODE, WELDING ROD 2.4MM 7018`, Qty `25.0`, Rate `96.80 SAR`
    - Line 2: `ELECTRODE, WELDING RODE 3.2 MM`, Qty `15.0`, Rate `85.80 SAR`
    - Line 3: `ELECTRODE, WELDING ROD 4 MM 7018`, Qty `15.0`, Rate `85.80 SAR`
    - Line 4: `DISC, ABRASIVE FLAP 4-1/2 80 GRIT`, Qty `10.0`, Rate `3.30 SAR`
  - Total: 4,994.00 SAR (+ 15% VAT = 5,743.10 SAR). Stored in DB with 100% fidelity.

### Findings Summary
- **What was Correct:**
  - High-precision extraction across all 5 documents for valves, ladders, and welding electrodes.
  - Zero numerical discrepancies between PO and SI for Sets 43 and 44.
- **What was Wrong / Misrepresented:**
  - None on extraction.
- **What was Missed:**
  - Delivery notes Doc 154 (`SIV-DTS-25-581-2.pdf`, note: corrupted/truncated `dn_no = 688`) and Doc 157 (`SIV-DTS-25-582-2.pdf`) are unattached due to lost DTS headers.
- **What We Need to Do to Improve:**
  - Re-ingest / extract DTS delivery notes Doc 154 and Doc 157 to auto-merge Sets 43 and 44 immediately.

---

## Audit Findings: Batch 14 (Docs 161, 162, 163, 166, 167)

### Document Details
- **Doc 161**: `SIV-DTS-25-583-3.pdf` (PO 193781 p1, PO Set 45, Status: `pending`)
- **Doc 162**: `SIV-DTS-25-583-4.pdf` (PO 193781 p2, PO Set 45, Status: `pending`)
- **Doc 163**: `SIV-DTS-25-584-1.pdf` (SI SIV-DTS-25-584, PO Set 46, Status: `pending`)
- **Doc 166**: `SIV-DTS-25-584-4.pdf` (PO 192123 p1, PO Set 46, Status: `pending`)
- **Doc 167**: `SIV-DTS-25-584-5.pdf` (PO 192123 p2, PO Set 46, Status: `pending`)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 45: Doc 161 (PO p1) & Doc 162 (PO p2)
- **Doc 161**: ADES PO 193781, 4 lines (welding electrodes and abrasive flap disc):
  - Line 1: Qty 25.0, Unit Price 96.8 SAR
  - Line 2: Qty 15.0, Unit Price 85.8 SAR
  - Line 3: Qty 15.0, Unit Price 85.8 SAR
  - Line 4: Qty 10.0, Unit Price 3.3 SAR
  - Extraction matches printed values with 100% precision.
- **Doc 162**: ADES PO 193781 page 2 of 7 (totals and standard T&Cs). DB correctly records 0 line items.
- **Reconciliation Status**: Set 45 has exact 1:1 match between PO lines and SI lines (from Doc 159 audited in Batch 13), but is pending solely due to the missing DN link (`GDN-DTS-25-627` on unattached Doc 160).

#### 2. PO Set 46: Doc 163 (SI), Doc 166 (PO p1), Doc 167 (PO p2)
- **Doc 163**: Drilling Tools Trading Est Tax Invoice `SIV-DTS-25-584`.
  - Header explicitly specifies `Buyers Order No : 192123` and `Delivery Note : GDN-DTS-25-628`.
  - In DB: `po_no_normalized = 192123` was extracted, but `dn_no` was left `NULL`!
  - Lines 1–7 (Adapters and connectors, Qty 4.0 each, Rates 37.0, 58.0, 62.0, 37.0, 33.0, 25.0, 49.0) are extracted perfectly.
- **Doc 166**: ADES PO 192123 page 1 of 7. Extracted lines 1–5 perfectly (Qty 4.0 each).
- **Doc 167**: ADES PO 192123 page 2 of 7. Extracted lines 6–12 perfectly (Qty 4.0 each, rates 25, 49, 37, 58, 37, 49, 33).
- **Reconciliation Reality**:
  - PO 192123 has 12 items. Invoice SIV-DTS-25-584 invoices items 1 to 7 only (partial delivery / partial fulfillment).
  - Lines 8–12 remain unbilled, which is commercially legitimate partial delivery.
  - The missing `dn_no` (`GDN-DTS-25-628`) on the invoice prevents automatic cross-referencing with the vendor's delivery note.

---

### Core Findings Summary
1. **What was Correct**:
   - High fidelity extraction of all 12 items on ADES PO 192123 split across pages 1 and 2.
   - Exact extraction of all 4 items on PO 193781.
   - Exact numerical extraction of all 7 items on Invoice SIV-DTS-25-584.
2. **What was Wrong**:
   - Ingestion split multi-page PO documents into individual PDF files (`-pages-1.pdf`, `-pages-2.pdf`), causing multiple PO rows per set and tripping `single_po_document` guards.
3. **What was Misrepresented**:
   - PO Set 46 `pending` status reflects an incomplete set, but without partial delivery tracking, the system cannot distinguish between a data error and an in-progress partial delivery.
4. **What was Missed**:
   - Invoice `dn_no = GDN-DTS-25-628` printed clearly on Doc 163 was missed and stored as `NULL`.
5. **What We Need to Do to Improve**:
   - Extract the `Delivery Note : GDN-DTS-25-628` reference from the top-right metadata table of Drilling Tools invoices.

## Audit Findings: Batch 15 (Docs 168, 171, 172, 173, 175)

### Document Details
- **Doc 168**: `SIV-DTS-25-588-1.pdf` (SI SIV-DTS-25-588, PO Set 47, Status: `pending`)
- **Doc 171**: `SIV-DTS-25-588-4.pdf` (PO 4500496313 p1, PO Set 47, Status: `pending`)
- **Doc 172**: `SIV-DTS-25-588-5.pdf` (PO 4500496313 p2, PO Set 47, Status: `pending`)
- **Doc 173**: `SIV-DTS-25-589-1.pdf` (SI SIV-DTS-25-589, PO Set 48, Status: `pending`)
- **Doc 175**: `SIV-DTS-25-589-3.pdf` (PO 4500497953 p1, PO Set 48, Status: `pending`)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 47: Doc 168 (SI), Doc 171 (PO p1), Doc 172 (PO p2)
- **Doc 168**: Drilling Tools Invoice `SIV-DTS-25-588` to Saudi International Energy Services Co (KCA Deutag).
  - Explicit header: `Buyers Order No : 4500496313`, `Delivery Note : GDN-DTS-25-520, GDN-DTS-25-626`.
  - In DB: `dn_no = NULL` (missed!).
  - Line 1: `BROTHER TAPE TZE-241 18MM BLACK ON WHITE`, Qty 10.0, Rate 57.72 SAR.
  - Line 2: `SLEDGE HAMMER, 6LB, 24" RUBBER REINFORCED HANDLE`, Qty 2.0, Rate 867.30 SAR.
- **Doc 171**: KCA Deutag PO 4500496313 page 1 of 3 (header/terms). DB correctly records 0 lines.
- **Doc 172**: KCA Deutag PO 4500496313 page 2 of 3 (items table).
  - Item 10: Brother Labelling Tape, Qty 10 PC, Unit Price printed as `57,72` SAR (comma decimal!).
    - **CRITICAL PARSER BUG**: In DB, `unit_price = 0.0`! The comma decimal parsing failure struck again, dropping the price to zero.
  - Item 20: Sledge Hammer 6 lbs, Qty 2 EA, Unit Price printed as `867.30` SAR (dot decimal!).
    - In DB: `unit_price = 867.3`!
  - **Reconciliation Reality**:
    - Both lines match 100% in quantities (10.0 and 2.0) and real unit prices (57.72 and 867.30).
    - The PO set is pending due to unlinked Delivery Notes and artificial price mismatch caused by the comma decimal drop bug.

#### 2. PO Set 48: Doc 173 (SI) & Doc 175 (PO p1)
- **Doc 173**: Drilling Tools Invoice `SIV-DTS-25-589` to KCA Deutag.
  - Header: `Buyers Order No : 4500497953`, `Delivery Note : GDN-DTS-25-627`.
  - In DB: `dn_no = NULL` (missed).
  - Line 1: `SERVICE KIT -1000/3000/5000/7000HRS`, Qty 6.0, Rate 1,650.0 SAR.
  - Line 2: `SERVICE KIT - 2000HRS 6000HRS`, Qty 4.0, Rate 5,300.0 SAR.
  - Extracted lines match the printed document 100%.
- **Doc 175**: KCA Deutag PO 4500497953 page 1 of 3 (terms/instructions). DB correctly records 0 lines.

---

### Core Findings Summary
1. **What was Correct**:
   - Clean extraction of item descriptions and quantities for all lines across both sets.
   - Successful handling of dot-decimal price `867.30` on Doc 172.
2. **What was Wrong**:
   - **Comma Decimal Bug Reproduced**: Doc 172 Item 10 printed `57,72` SAR was parsed as `0.0` in the database.
3. **What was Misrepresented**:
   - Item 10 price in DB is `0.0`, creating a false discrepancy with the supplier's invoice rate of `57.72`.
4. **What was Missed**:
   - Delivery note numbers `GDN-DTS-25-520, GDN-DTS-25-626` on Doc 168 and `GDN-DTS-25-627` on Doc 173 were missed.
5. **What We Need to Do to Improve**:
   - Implement universal comma/period decimal normalisation (`str.replace(',', '.')`) across all price parsing routines.

## Audit Findings: Batch 16 (Docs 176, 177, 178, 180, 181)

### Document Details
- **Doc 176**: `SIV-DTS-25-589-4.pdf` (PO 4500497953 p2, PO Set 48, Status: `pending`)
- **Doc 177**: `SIV-DTS-25-589-5.pdf` (PO 4500497953 p3, PO Set 48, Status: `pending`)
- **Doc 178**: `SIV-DTS-25-596-1.pdf` (SI SIV-DTS-25-596, PO Set 49, Status: `pending`)
- **Doc 180**: `SIV-DTS-25-596-3.pdf` (PO 23073 p1, PO Set 49, Status: `pending`)
- **Doc 181**: `SIV-RAK-25-2876-Ades-1.pdf` (SI SIV-RAK-25-2876, PO Set 50, Status: `pending`)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 48: Doc 176 (PO p2) & Doc 177 (PO p3)
- **Doc 176**: KCA Deutag PO 4500497953 page 2 of 3 (item table):
  - Item 10: `SERVICE KIT: AIR COMPRESSORS... 1000 AND 5000 HOURS`, Qty 6 SET.
    - Printed Unit Price: `1.650,00` SAR (European format: thousands dot, comma decimal).
    - **CRITICAL PARSER DEFECT**: In DB, `unit_price = 0.0`! The parser failed on `1.650,00` and stored zero.
  - Item 20: `SERVICE KIT: AIR COMPRESSORS... 2000 AND 6000 HOURS`, Qty 4 SET.
    - Printed Unit Price: `5,300.00` SAR (Anglo format: comma thousands, dot decimal).
    - In DB: `unit_price = 5300.0` (correct).
  - **Reconciliation Impact**: Invoice SIV-DTS-25-589 (Doc 173) charges Line 1 @ 1,650.00 SAR and Line 2 @ 5,300.00 SAR. The PO and SI match 100% numerically, but DB records a price discrepancy due to the comma decimal drop.
- **Doc 177**: Page 3 of 3 (certifications and total 31,100.00 SAR). Extracted correctly with 0 line items.

#### 2. PO Set 49: Doc 178 (SI) & Doc 180 (PO p1)
- **Doc 178**: Drilling Tools Invoice `SIV-DTS-25-596`.
  - Header: `Buyers Order No : 23073`, `Delivery Note : GDN-DTS-25-632`.
  - In DB: `dn_no = NULL` (missed).
  - Lines 1–3: Hex Bolt (Qty 32, Rate 15.68), Spring Washer (Qty 32, Rate 0.80), Nylock Nut (Qty 32, Rate 11.98).
- **Doc 180**: ADES Standard PO 23073 page 1 of 7.
  - Lines 1–3: Exactly identical quantities (32 each) and unit prices (15.68, .8, 11.98). Total: 910.72 SAR.
  - **Reconciliation Reality**:
    - **100% exact numerical agreement** across all lines, quantities, and prices!
    - Set remains pending purely because of the unlinked delivery note.

#### 3. PO Set 50: Doc 181 (SI)
- **Doc 181**: RAK Energy Services & Supplies Sales Invoice `SIV-RAK-25-2876`.
  - Header: `Buyer's Order No : 13060`, `Delivery Note : GDN-RAK-25-3081`.
  - In DB: `dn_no = NULL` (missed).
  - Item: `Ty-Down straps for helideck 9m 5000kg`, Qty 4.0 EA, Rate 270.00 QAR.
  - Extracted accurately as Line 2 (based on text note `Line Item - 2`).

---

### Core Findings Summary
1. **What was Correct**:
   - Flawless line extraction for PO Set 49 (Doc 178 and Doc 180) with 100% mathematical match (910.72 SAR).
   - Correct extraction of RAK Energy invoice item, qty, and price.
2. **What was Wrong**:
   - European thousand/decimal representation `1.650,00` on Doc 176 caused unit price to drop to `0.0`.
3. **What was Misrepresented**:
   - PO Set 49 is marked `pending` despite perfect 3-way line item and price alignment.
4. **What was Missed**:
   - `Delivery Note : GDN-DTS-25-632` on Doc 178 and `GDN-RAK-25-3081` on Doc 181 were completely omitted.
5. **What We Need to Do to Improve**:
   - Implement locale-aware float parser that detects European number formatting (`1.650,00` -> `1650.00`).
   - Add RAK Energy invoice template parser to capture `Delivery Note : GDN-RAK-*` and `Buyer's Order No`.

## Audit Findings: Batch 17 (Docs 182, 183, 184, 185, 186)

### Document Details
- **Doc 182**: `SIV-RAK-25-2876-Ades-2.pdf` (DN GDN-RAK-25-3081, PO Set 50, Status: `pending`)
- **Doc 183**: `SIV-RAK-25-2876-Ades-3.pdf` (PO 13060, PO Set 50, Status: `pending`)
- **Doc 184**: `SIV-RAK-25-2884-Ades-1.pdf` (SI SIV-RAK-25-2884, PO Set 51, Status: `pending`)
- **Doc 185**: `SIV-RAK-25-2884-Ades-2.pdf` (DN GDN-RAK-25-3065, PO Set 51, Status: `pending`)
- **Doc 186**: `SIV-RAK-25-2884-Ades-3.pdf` (PO 13696, PO Set 51, Status: `pending`)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 50: Doc 182 (DN) & Doc 183 (PO)
- **Doc 182**: RAK Energy Services Delivery Note `GDN-RAK-25-3081`.
  - Header: Buyers Order No `13060`. Extracted correctly.
  - Line: `Ty-Down straps for helideck 9m 5000kg` (annotated `Line Item - 2`), Qty 4.0 EA. Extracted accurately as Line 2.
- **Doc 183**: Emerald Driller Co (ADES) PO 13060 (7 pages, items on p1):
  - Line 1: Wheel Chock, Qty 2, Price 380 QAR.
  - Line 2: Helideck Ty Down Straps, Qty 4, Price 270 QAR.
  - Line 3: IATA Dangerous Goods book, Qty 1, Price 2699 QAR.
  - Extraction matches printed PO lines 1–3 with 100% precision.
- **Reconciliation Status for Set 50**:
  - Combined with Doc 181 (SI audited in Batch 16), Line 2 has an **exact 3-way match**: PO ordered 4 @ 270 QAR, DN delivered 4, SI billed 4 @ 270 QAR.
  - Set status `pending` is commercially normal because lines 1 and 3 are pending delivery from the vendor.

#### 2. PO Set 51: Doc 184 (SI), Doc 185 (DN), Doc 186 (PO)
- **Doc 184**: RAK Energy Sales Invoice `SIV-RAK-25-2884`.
  - Header: `Buyer's Order No : 13696`, `Delivery Note : GDN-RAK-25-3065`.
  - In DB: `dn_no = NULL` (missed).
  - Line 1: `DANFOSS PRESSURE SWITCH`, Qty 1.0, Rate 1,030.00 QAR.
- **Doc 185**: RAK Energy Delivery Note `GDN-RAK-25-3065`.
  - Header: `Buyers Order No : 13696`, `Del. Note No : GDN-RAK-25-3065`.
  - Line 1: `DANFOSS PRESSURE SWITCH`, Qty 1.0. Extracted accurately.
- **Doc 186**: Emerald Driller Co PO 13696 (6 pages, items on p1):
  - Line 1: Danfoss Pressure Switch, Qty 1, Price 1030 QAR.
  - Line 2: Motorola Radio Microphone, Qty 2, Price 238 QAR.
  - Extracted lines 1 and 2 perfectly.
- **Reconciliation Status for Set 51**:
  - Line 1 has an **exact 3-way match** (PO 1 @ 1030 QAR, DN 1, SI 1 @ 1030 QAR).
  - Line 2 is awaiting fulfillment.

---

### Core Findings Summary
1. **What was Correct**:
   - Flawless extraction of multi-currency QAR amounts and line item details across RAK Energy POs, DNs, and SIs.
   - Exact line-item number alignment using descriptive line annotations (`Line Item - 2`).
2. **What was Wrong**:
   - Lack of partial-fulfillment handling causes sets with 100% matched delivered lines to remain indefinitely `pending`.
3. **What was Misrepresented**:
   - Nothing misrepresented in the line data; both sets reflect valid commercial partial deliveries.
4. **What was Missed**:
   - Delivery note reference `GDN-RAK-25-3065` printed on Invoice Doc 184 was missed and saved as `NULL`.
5. **What We Need to Do to Improve**:
   - Capture delivery note cross-references on RAK Energy sales invoices.
   - Implement partial delivery state tracking so sets with completely reconciled delivered lines can be flagged as "Partially Delivered & Reconciled".

## Audit Findings: Batch 18 (Docs 221, 222, 223, 257, 259)

### Document Details
- **Doc 221**: `SIV-RAK-25-3057-Noble-1.pdf` (SI SIV-RAK-25-3057, PO Set 63, Status: `pending`)
- **Doc 222**: `SIV-RAK-25-3057-Noble-2.pdf` (DN GDN-RAK-25-3215, PO Set 63, Status: `pending`)
- **Doc 223**: `SIV-RAK-25-3057-Noble-3.pdf` (PO 300140833, PO Set 63, Status: `pending`)
- **Doc 257**: `SIV-RHO-26-1669.pdf` (SI SIV-RHO-26-1669, PO Set 75, Status: `pending`)
- **Doc 259**: `SIV-RHO-26-1669B.pdf` (PO 8300052050, PO Set 75, Status: `pending`)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 63: Doc 221 (SI), Doc 222 (DN), Doc 223 (PO)
- **Doc 221**: RAK Energy Sales Invoice `SIV-RAK-25-3057` to Noble Drilling Doha LLC.
  - Header: Buyers Order No `300140833`, Delivery Note `GDN-RAK-25-3215`. In DB, `dn_no = NULL` (missed).
  - Line 3: `Canon LS555H Handheld Foldable Pocket Calculator`, Qty 5.0 EA, Rate 192.50 QAR.
- **Doc 222**: RAK Energy Delivery Note `GDN-RAK-25-3215`. Line 3: Qty 5.0 EA. Extracted accurately.
- **Doc 223**: Noble Drilling PO 300140833 (4 pages, items on p2):
  - Extracted 7 lines (Lubricant, flap discs, calculator, lithium battery, pilot markers).
  - Line 3: Canon Foldable Pocket Calculator LS555H, Qty 5 EA, Price 192.50 QAR.
  - **Reconciliation Reality**:
    - Line 3 has an **exact 3-way match** (PO 5 @ 192.50, DN 5, SI 5 @ 192.50).
    - Status is `pending` because this is a legitimate partial fulfillment (other 6 PO lines awaiting delivery).

#### 2. PO Set 75: Doc 257 (SI) & Doc 259 (PO) — The Blended Average / Line-Split Discrepancy
- **Doc 257**: RAAS Oilfield Services & Supplies WLL Tax Invoice `SIV-RHO-26-1669` to NOMAC Al Dur Power & Water Services (Kingdom of Bahrain).
  - Delivery Note No column on lines: `GDN-RHO-26-1799`. In DB, `dn_no = NULL`.
  - Line 1: `STORAGE BIN 350L X 200W X 200H mm (LF322)`, Qty 50.00, Unit Price 4.600 BHD, Amount: 230.000 BHD.
  - Line 2: `STORAGE BIN 500L X 300W X 200H mm (LF532)`, Qty 50.00, Unit Price 5.900 BHD, Amount: 295.000 BHD.
  - Total: 525.000 BHD.
- **Doc 259**: NOMAC PO 8300052050.
  - Line 1: `1400026995 STORAGE BINS`, Qty 100.00 EA, Price: 5.250 BHD, Total: 525.000 BHD.
- **Reconciliation Analysis**:
  - The buyer created a single lump-sum PO line for 100 storage bins at the weighted average price of 5.250 BHD ($50 \times 4.60 + 50 \times 5.90 = 525.00$).
  - The supplier invoiced as two distinct lines of 50 each with size-specific pricing (4.600 and 5.900).
  - Total quantity (100) and total value (525.000 BHD) match 100%, but under line-item matching rules, line 1 fails on quantity (100 vs 50) and price (5.25 vs 4.60), and line 2 is an orphan vendor line!

---

### Core Findings Summary
1. **What was Correct**:
   - Flawless visual extraction of line items, currencies (BHD, QAR), quantities, and prices for all 5 documents.
2. **What was Wrong**:
   - PO Set 75 cannot be reconciled by standard 1:1 matcher despite total order quantity and total financial value matching exactly.
3. **What was Misrepresented**:
   - Set 75 appears as an unreconciled failure in standard reports, masking that it is a 1-to-many line split with 0% total financial variance.
4. **What was Missed**:
   - Delivery note numbers `GDN-RAK-25-3215` (Doc 221) and per-line delivery note `GDN-RHO-26-1799` (Doc 257) were omitted from DB records.
5. **What We Need to Do to Improve**:
   - Add a "Set-Level Total Quantity & Total Amount Sanity Check" in reconciliation reporting to flag when an apparent line mismatch actually sums to identical order totals.
   - Extract the `Delivery Note No` table column on RAAS Oilfield invoices.

## Audit Findings: Batch 19 (Docs 262, 264, 266, 267, 269)

### Document Details
- **Doc 262**: `SIV-RHO-26-1896B.pdf` (PO 60213576OPG, PO Set 76, Status: `pending`)
- **Doc 264**: `SIV-RHO-26-1914 A.pdf` (DN GDN-RHO-26-2183, PO Set 77, Status: `pending`)
- **Doc 266**: `SIV-RHO-26-1914B.pdf` (PO 4270006645SNN, PO Set 77, Status: `pending`)
- **Doc 267**: `SIV-RHO-26-233.pdf` (SI SIV-RHO-26-233, PO Set 78, Status: `pending`)
- **Doc 269**: `SIV-RHO-26-233B.pdf` (PO 4500362256, PO Set 78, Status: `pending`)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 76: Doc 262 (PO)
- **Doc 262**: Gulf Aluminium Rolling Mill (GARMCO) PO `60213576 OPG` to RAAS Oilfield Services.
  - Line 1: `JOTUN THINNER NO.2`, Qty 15 LITRE, Unit Price 2.2500 BD.
  - Line 2: `JOTUN THINNER NO.7`, Qty 15 LITRE, Unit Price 2.3000 BD.
  - Total: 68.250 BD. Extracted into DB with 100% precision.

#### 2. PO Set 77: Doc 264 (DN) & Doc 266 (PO)
- **Doc 264**: RAAS Delivery Note `GDN-RHO-26-2183` to Bahrain Airport Services (BAS).
  - P.O: `4270006645 -SNN`. Line 1: `Mixer/Grinder Machine With Stainless Ste`, Qty 1.0 NOS.
- **Doc 266**: BAS Purchase Order `4270006645 - SNN`.
  - Line 1: `Mixer/Grinder Machine With Stainless Ste`, Qty 1 NOS, Unit Price 42.000 BHD. Total: 42.000 BHD.
  - Perfect 1:1 match between PO and DN. Set is pending awaiting invoice submission.

#### 3. PO Set 78: Doc 267 (SI) & Doc 269 (PO) — Critical 3-Decimal (Fils) Price Bug Uncovered
- **Doc 267**: RAAS Tax Invoice `SIV-RHO-26-233` to Armacell Middle East Company S.P.C.
  - P.O. #: `4500362256`. Total Amount: 92.650 BHD.
  - Line 1: `SPRAY PAINT - RAL6011 RESEDA GREEN`, Qty 12.00, Unit Price printed clearly as `6.325` BHD!
  - Lines 2–4: Self Drill Screws (Qty 1 @ 6.500, Qty 1 @ 6.000) and Silicone Sealant (Qty 5 @ 0.850).
  - **DEFECT**: In DB, Line 1 `unit_price = 0.0`!
- **Doc 269**: Armacell Purchase Order `4500362256`.
  - Line 10: `Paint Spray - RAL 6011`, Qty 12 PC, Unit Price printed clearly as `6.325` BHD, Total: 75.900 BHD.
  - Lines 20–40: Screws and sealant matching 6.500, 6.000, 0.850 BHD.
  - Total: 92.650 BHD.
  - **DEFECT**: In DB, Line 10 `unit_price = 0.0`!
- **ROOT CAUSE DIAGNOSIS**:
  - Bahraini Dinar (BHD) prices use 3 decimal places (1 Dinar = 1,000 fils).
  - Lines with ending zeroes (`6.500`, `6.000`, `0.850`) were parsed because they collapsed to standard 1 or 2 decimals (`6.5`, `6.0`, `0.85`).
  - Line 1 (`6.325`) has 3 non-zero decimal digits, violating a hardcoded 2-decimal regex or float assumption, causing price parsing to abort and default to `0.0` on both PO and Invoice!
- **Reconciliation Reality**:
  - The documents are in **100% mathematical and commercial agreement**: 4 lines, total 92.650 BHD net, 101.915 BHD gross.
  - The system records both line prices as `0.0`, masking this perfect match.

---

### Core Findings Summary
1. **What was Correct**:
   - Clean extraction of BAS and GARMCO purchase orders.
   - Exact line extraction of quantities and descriptions across Armacell PO and RAAS invoice.
2. **What was Wrong**:
   - **3-Decimal Price Parser Failure**: Unit price `6.325` BHD was dropped to `0.0` on both Doc 267 and Doc 269.
3. **What was Misrepresented**:
   - Armacell PO Set 78 shows zero-price line items in the database, despite having full commercial pricing printed.
4. **What was Missed**:
   - Delivery note numbers in table columns (`GDN-RHO-26-92`) on Doc 267 were missed.
5. **What We Need to Do to Improve**:
   - Support arbitrary decimal precision (specifically 3 decimals for BHD/OMR/KWD/JOD currencies) in numerical price parsers.

## Audit Findings: Batch 20 (Docs 270, 272, 86, 91, 96)

### Document Details
- **Doc 270**: `SIV-RHO-26-234.pdf` (SI SIV-RHO-26-234, PO Set 79, Status: `pending`)
- **Doc 272**: `SIV-RHO-26-234B.pdf` (PO C13717 p1, PO Set 79, Status: `pending`)
- **Doc 86**: `SIV-DTS-25-452-pages-2.pdf` (DN GDN-DTS-25-478, PO Set `None` / Unattached)
- **Doc 91**: `SIV-DTS-25-456-pages-3.pdf` (DN GDN-DTS-25-493, PO Set `None` / Unattached)
- **Doc 96**: `SIV-DTS-25-477-pages-2.pdf` (DN GDN-DTS-25-505, PO Set `None` / Unattached)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 79: Doc 270 (SI) & Doc 272 (PO p1)
- **Doc 270**: RAAS Tax Invoice `SIV-RHO-26-234` to International Maritime Industries (IMI).
  - P.O. #: `C13717`, Delivery Note: `GDN-RHO-26-270` (in table column, missed in DB).
  - Line 1: `Air vent ducting Flexible... 50 meter`, Qty 120.00, Unit Price 128.000 USD, Total: 15,360.00 USD. Extracted cleanly into DB.
- **Doc 272**: IMI Purchase Order `C13717` (Page 1 of 2).
  - PO Value: 15,360.00 USD.
  - Page 1 contains only metadata and terms; items table is on Page 2 (which was not ingested or split). Extracted 0 lines in DB.

#### 2. The Unattached Delivery Notes Revelation: Docs 86, 91, 96
- **Doc 86**: Drilling Tools Delivery Note:
  - Header: `Del. Note No: GDN-DTS-25-478`, `Buyers Order No: 4500026808`, Client: `ARABIAN DRILLING COMPANY`.
  - Line 1: Anti-spark hammer 7lb, Qty 1.00 EA.
  - **SYSTEM FAILURE**: DB has `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`!
  - **Root Cause & Impact**: The extractor failed to parse the pink-tinted header table. This is the **missing Delivery Note for PO Set 29** (`4500026808`)! PO Set 29 has been sitting in `pending` because this document was orphaned.
- **Doc 91**: Drilling Tools Delivery Note:
  - Header: `Del. Note No: GDN-DTS-25-493`, `Buyers Order No: 4500039614`, Client: `ARABIAN DRILLING COMPANY`.
  - 17 tool items (wrenches, pliers, hammer, pry bar).
  - **SYSTEM FAILURE**: DB has `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`!
  - **Root Cause & Impact**: This is the **missing Delivery Note for PO Set 30** (`4500039614`)!
- **Doc 96**: Drilling Tools Delivery Note:
  - Header: `Del. Note No: GDN-DTS-25-505`, `Buyers Order No: TOGA-PO-25-26-163`, Client: `TUBESTAR OIL & GAS ARABIA`.
  - Lines 1–5: Stationery supplies, Qty 24 PKT total.
  - **SYSTEM FAILURE**: DB has `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`!
  - **Root Cause & Impact**: This is the **missing Delivery Note for PO Set 31** (`TOGA-PO-25-26-163`)!

---

### Core Findings Summary
1. **What was Correct**:
   - High OCR line item extraction fidelity on the item tables of Docs 86, 91, and 96 (even reading hand-annotated strike-throughs on Doc 91).
2. **What was Wrong**:
   - Complete loss of header data (`Buyers Order No` and `Del. Note No`) across all Drilling Tools scanned delivery notes, leaving dozens of valid delivery notes completely unattached.
3. **What was Misrepresented**:
   - Dozens of delivery notes appear as "unattached orphan documents" with no PO reference, when in fact their PO numbers are clearly printed in the upper right quadrant.
4. **What was Missed**:
   - `4500026808` / `GDN-DTS-25-478` on Doc 86
   - `4500039614` / `GDN-DTS-25-493` on Doc 91
   - `TOGA-PO-25-26-163` / `GDN-DTS-25-505` on Doc 96
5. **What We Need to Do to Improve**:
   - Retrain or anchor the Vision/LLM prompt to explicitly target the top-right metadata box (`Del. Note No:` and `Buyers Order No:`) on Drilling Tools delivery note scans.
   - Run an automated linking pass that re-attaches these orphaned delivery notes to their corresponding PO sets.

## Audit Findings: Batch 21 (Docs 99, 104, 108, 111, 114)

### Document Details
All 5 documents in this batch are scanned Delivery Notes from Drilling Tools Trading Est that were completely orphaned (`PO Set: None`, `Status: None`) due to header parsing failures:
- **Doc 99**: `SIV-DTS-25-480-pages-2.pdf`
- **Doc 104**: `SIV-DTS-25-486-pages-2.pdf`
- **Doc 108**: `SIV-DTS-25-498-pages-2.pdf`
- **Doc 111**: `SIV-DTS-25-509-pages-2.pdf`
- **Doc 114**: `SIV-DTS-25-517-pages-2.pdf`

---

### Visual Inspection vs DB Comparison & Cross-Set Resolution

#### 1. Doc 99 (`SIV-DTS-25-480-pages-2.pdf`) — Resolves PO Set 32
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-512`, `Buyers Order No: 4500496007`, Date: 15/09/2025.
  - Client: `SAUDI INTERNATIONAL ENERGY SERVICES COMPANY LTD` (KCA Deutag).
  - Lines 1–5: Split Cotter Pins 3mm x 40mm, Qtys: 200, 100, 100, 100, 200 EA. Total: 700 EA.
  - Stamped and signed by KCA Deutag Goods Receiving on 17/09/25.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 32** (`4500496007`), matching PO Doc 101 and Invoice Doc 98 with 100% exact quantities!

#### 2. Doc 104 (`SIV-DTS-25-486-pages-2.pdf`) — Resolves PO Set 33
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-521`, `Buyers Order No: 4500496514`, Date: 20-09-2025.
  - Client: `SAUDI INTERNATIONAL ENERGY SERVICES COMPANY LTD`.
  - Line 1: `Cotter Pin Galvanized M5X152MM`, Qty: 250.00 EACH.
  - Stamped by Goods Receiving on 21/09/25.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 33** (`4500496514`), matching PO Doc 106 and Invoice Doc 103 with 100% exact quantity!

#### 3. Doc 108 (`SIV-DTS-25-498-pages-2.pdf`) — Resolves PO Set 34
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-504`, `Buyers Order No: ARM-PO-25-26-029`, Date: 13-09-2025.
  - Client: `TUBESTAR OIL & GAS ARABIA COMPANY FOR GAS`.
  - Lines 1–3: Steel files flat (7 EA), Single color rope (2 ROLL), LED flashlight (3 EACH). Total: 12.000.
  - Stamped and signed by Tubestar on 16/09/2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 34** (`ARM-PO-25-26-029`), matching PO Doc 109 and Invoice Doc 107 with 100% exact quantities!

#### 4. Doc 111 (`SIV-DTS-25-509-pages-2.pdf`) — Resolves PO Set 35
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-551`, `Buyers Order No: 22843`, Date: 12-10-2025.
  - Client: `ADES GCC FOR DRILLING(KSA)`.
  - Lines 1–3: SAT Power and Sprinter Batteries, Qtys: 2 EACH, 6 EACH, 2 EACH. Total: 10.000.
  - Stamped by ADES Central W/H on 13 OCT 2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 35** (`22843`), matching PO Doc 112 and Invoice Doc 110 with 100% exact quantities!

#### 5. Doc 114 (`SIV-DTS-25-517-pages-2.pdf`) — Resolves PO Set 36
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-548`, `Buyers Order No: 22613 , 0`, Date: 12-10-2025.
  - Client: `ADES GCC FOR DRILLING(KSA)`.
  - Line 1: `Drill Pipe Connection Guide 4-1/2"`, Qty: 1.00 EACH.
  - Stamped by ADES Central W/H on 14 OCT 2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 36** (`22613`). Notice the printed PO is `22613 , 0` (PO 22613, Rev 0), which explains why the Invoice extractor erroneously generated `226130`.

---

### Core Findings Summary
1. **What was Correct**:
   - The line item tables and descriptions on all 5 delivery notes were extracted cleanly into the database.
2. **What was Wrong**:
   - **Systemic Header Blindness**: The extraction model failed completely to read the top-right metadata table (`Buyers Order No` and `Del. Note No`) on all 5 pink-tinted Drilling Tools delivery notes.
3. **What was Misrepresented**:
   - 5 valid, fully executed, physically stamped delivery notes were flagged as orphaned, unattached documents.
   - 5 corresponding PO sets (Sets 32, 33, 34, 35, 36) were left stuck in `pending` under the false premise that no delivery documentation had been provided.
4. **What was Missed**:
   - All 5 PO numbers (`4500496007`, `4500496514`, `ARM-PO-25-26-029`, `22843`, `22613`) and DN numbers (`GDN-DTS-25-512`, `GDN-DTS-25-521`, `GDN-DTS-25-504`, `GDN-DTS-25-551`, `GDN-DTS-25-548`).
5. **What We Need to Do to Improve**:
   - Fix the header region ROI detection for pink Drilling Tools delivery notes.
   - Run a reconciliation link pass to attach Docs 99, 104, 108, 111, 114 to Sets 32, 33, 34, 35, 36, unlocking all 5 sets for immediate automatic merge.

## Audit Findings: Batch 22 (Docs 117, 121, 127, 133, 135)

### Document Details
- **Doc 117**: `SIV-DTS-25-519-pages-2.pdf` (DN GDN-DTS-25-555, PO Set `None` / Unattached)
- **Doc 121**: `SIV-DTS-25-538-pages-2.pdf` (DN GDN-DTS-25-574, PO Set `None` / Unattached)
- **Doc 127**: `SIV-DTS-25-548-pages-2.pdf` (DN GDN-DTS-25-584, PO Set `None` / Unattached)
- **Doc 133**: `SIV-DTS-25-565-2.pdf` (DN GDN-DTS-25-603, PO Set `None` / Unattached)
- **Doc 135**: `SIV-DTS-25-566-1.pdf` (SI SIV-DTS-25-566, PO Set `None` / Unattached)

---

### Visual Inspection vs DB Comparison & Cross-Set Resolution

#### 1. Doc 117 (`SIV-DTS-25-519-pages-2.pdf`) — Resolves PO Set 37
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-555`, `Buyers Order No: 4500044740`, Date: 14-10-2025.
  - Client: `ARABIAN DRILLING COMPANY`.
  - Line 1: `Body Solid Pro Clubline Commercial Squat Stand`, Qty: 1.00 PCS.
  - Stamped and signed by Arabian Drilling on 15 OCT 2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 37** (`4500044740`), matching PO Doc 119 and Invoice Doc 116 with 100% exact quantity!

#### 2. Doc 121 (`SIV-DTS-25-538-pages-2.pdf`) & Doc 127 (`SIV-DTS-25-548-pages-2.pdf`) — Resolves PO Set 38
- **Doc 121 Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-574`, `Buyers Order No: 4500047182`, Date: 27-10-2025.
  - Client: `ARABIAN DRILLING COMPANY`.
  - Lines 2–6: Insight Fitness Back Extension (1), Axox Stepper (1), Body Solid FID Bench (1), Axox Tricep Press Down Bar (1), Insight Leg Curl/Extension (1). Total: 5.000.
  - Stamped and signed on 29 OCT 2025.
- **Doc 127 Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-584`, `Buyers Order No: 4500047182`, Date: 01-11-2025.
  - Line 7: `Axox Fitness 1sqm Rubber Interlocking Mat | Single Piece`, Qty: 1.00 Each.
  - Stamped and signed on 02 NOV 2025.
- **Database Status**: Both documents have `PO Raw = None`, `DN = None`, `PO Set = None`.
- **System Impact**: Together, Docs 121 and 127 deliver lines 2 through 7 of **PO Set 38** (`4500047182`), matching Invoice Doc 124 (audited in Batch 11) lines 2 through 7 with 100% precision! Both delivery notes were orphaned due to the unread header table.

#### 3. Doc 133 (`SIV-DTS-25-565-2.pdf`) — Resolves PO Set 40
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-603`, `Buyers Order No: ARM-PO-25-26-035`, Date: 10-11-2025.
  - Client: `TUBESTAR OIL & GAS ARABIA COMPANY FOR GAS`.
  - Lines 1–3: Cup wire brush (15 EACH), Wire brush (5 DZN), Cotton rags (8 BND). Total: 28.000.
  - Stamped and signed on 11/11/2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 40** (`ARM-PO-25-26-035`), matching PO Doc 134 and Invoice Doc 132 with 100% exact quantities!

#### 4. Doc 135 (`SIV-DTS-25-566-1.pdf`) — Unattached Supplier Invoice
- **Visual Evidence**:
  - Drilling Tools Tax Invoice `SIV-DTS-25-566` to Tubestar Oil & Gas Arabia.
  - `Buyers Order No : TOGA-PO-25-26-204`, `Delivery Note : GDN-DTS-25-571`.
  - 7 lines (cup brush, apron, paints, rags, pad locks).
- **Database Status**: Extracted `po_no_normalized = TOGAPO2526204`, but `po_set_id = NULL`.
- **System Impact**: No PO document was uploaded for `TOGA-PO-25-26-204` (only invoice pages and delivery note Doc 137). In the absence of an ingested PO document, the system left the invoice unattached.

---

### Core Findings Summary
1. **What was Correct**:
   - Exact line-item, quantity, and unit-of-measure extraction on all 4 scanned delivery notes.
   - Clean 7-line extraction of Invoice Doc 135.
2. **What was Wrong**:
   - Extraction model failed to capture `Buyers Order No` on Docs 117, 121, 127, and 133, stranding them as unattached documents.
3. **What was Misrepresented**:
   - Multi-part delivery for PO Set 38 (split across delivery notes Doc 121 and Doc 127) was lost entirely from the reconciliation pipeline.
4. **What was Missed**:
   - `4500044740` on Doc 117
   - `4500047182` on Docs 121 and 127
   - `ARM-PO-25-26-035` on Doc 133
5. **What We Need to Do to Improve**:
   - Re-link Docs 117, 121, 127, 133 to their respective PO sets (37, 38, 40), allowing Sets 37 and 40 to merge automatically, and Set 38 to achieve full reconciliation of lines 2–7.

## Audit Findings: Batch 23 (Docs 136, 137, 139, 142, 145)

### Document Details
- **Doc 136**: `SIV-DTS-25-566-2.pdf` (SI p2 SIV-DTS-25-566, PO Set `None` / Unattached)
- **Doc 137**: `SIV-DTS-25-566-3.pdf` (DN GDN-DTS-25-571, PO Set `None` / Unattached)
- **Doc 139**: `SIV-DTS-25-572-2.pdf` (DN GDN-DTS-25-606, PO Set `None` / Unattached)
- **Doc 142**: `SIV-DTS-25-575-2.pdf` (DN GDN-DTS-25-614, PO Set `None` / Unattached)
- **Doc 145**: `SIV-DTS-25-576-2.pdf` (DN GDN-DTS-25-615, PO Set `None` / Unattached)

---

### Visual Inspection vs DB Comparison & Cross-Set Resolution

#### 1. SIV-DTS-25-566 Match: Doc 136 (SI p2) & Doc 137 (DN)
- **Doc 136 Visual Evidence**:
  - Page 2 of Invoice `SIV-DTS-25-566` to Tubestar Oil & Gas Arabia.
  - Header: `Buyers Order No: TOGA-PO-25-26-204`, `Delivery Note: GDN-DTS-25-571`.
  - Lines 8 & 9: Paint brushes (Qty 10 DZN @ 21.00 SAR, Qty 10 DZN @ 35.00 SAR). Total invoice amount: 5,288.00 SAR net.
- **Doc 137 Visual Evidence**:
  - Drilling Tools Delivery Note `GDN-DTS-25-571`, Date: 22-10-2025.
  - Header: `Buyers Order No: TOGA-PO-25-26-204`. Client: `TUBESTAR OIL & GAS ARABIA`.
  - All 9 lines matching Invoice Docs 135 & 136 exactly (Total Qty: 188.000).
  - Stamped and signed by Tubestar Goods Receiving on 23/10/25.
- **Database Status**: Doc 137 header was missed (`PO Raw = None`, `PO Set = None`).
- **Reconciliation Impact**: Invoice SIV-DTS-25-566 (Docs 135, 136) and Delivery Note Doc 137 match with **100% perfect alignment** across all 9 lines and 188 units.

#### 2. Doc 139 (`SIV-DTS-25-572-2.pdf`) — Resolves ADES PO 22972
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-606`, `Buyers Order No: 22972`, Date: 10-11-2025.
  - Client: `ADES GCC FOR DRILLING(KSA)`.
  - Line 1: PS5 DualSense Controller (Qty 2.00 EA), Line 2: PS5 Charging Station (Qty 1.00 EA).
  - Stamped and signed by ADES Central W/H on 12 NOV 2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for ADES PO 22972** (Invoice Doc 138 audited in Batch 12), matching 100% in line items and quantities!

#### 3. Doc 142 (`SIV-DTS-25-575-2.pdf`) — Resolves Tubestar PO TOGA-PO-25-26-228
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-614`, `Buyers Order No: TOGA-PO-25-26-228`, Date: 11-11-2025.
  - Client: `TUBESTAR OIL & GAS ARABIA COMPANY FOR GAS`.
  - Line 1: `Ansell Edge Heavy-Duty Hi-Viz Impact Resistant Gloves-12 (XXL-LARGE)`, Qty: 20.00 Pair.
  - Stamped and signed by Tubestar on 12/11/2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for Tubestar PO TOGA-PO-25-26-228** (Invoice Doc 141 audited in Batch 12), matching 100% in line item and quantity!

#### 4. Doc 145 (`SIV-DTS-25-576-2.pdf`) — Resolves ADES PO 22398
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-615`, `Buyers Order No: 22398 , 0` (PO 22398 Rev 0), Date: 12-11-2025.
  - Client: `ADES GCC FOR DRILLING(KSA)`.
  - Line 1: `SpareLamp, Nova-Strobe Xenon Stroboscopes MONARCH 6280-030`, Qty: 2.00 EA.
  - Stamped and signed by ADES Central W/H on 13 NOV 2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for ADES PO 22398** (Invoice Doc 144 audited in Batch 12), matching 100% in line item and quantity!

---

### Core Findings Summary
1. **What was Correct**:
   - Flawless line extraction of items, part numbers, and quantities across all 5 delivery notes and invoices.
2. **What was Wrong**:
   - The extractor consistently failed to capture the top-right header block on all pink Drilling Tools delivery notes, leaving them orphaned without a PO Set ID.
3. **What was Misrepresented**:
   - These 4 delivery notes represent legitimate, fully accepted material receipts, but the database treated them as unreferenced garbage.
4. **What was Missed**:
   - `TOGA-PO-25-26-204` / `GDN-DTS-25-571` on Doc 137
   - `22972` / `GDN-DTS-25-606` on Doc 139
   - `TOGA-PO-25-26-228` / `GDN-DTS-25-614` on Doc 142
   - `22398` / `GDN-DTS-25-615` on Doc 145
5. **What We Need to Do to Improve**:
   - Automatically associate delivery notes using the printed `Buyers Order No` and `Del. Note No` to complete the 3-way matching sets.

## Audit Findings: Batch 24 (Docs 148, 151, 154, 157, 160)

### Document & Brand Overview
This entire batch consists of **Delivery Notes (DN)** issued by **Drilling Tools Trading Est** (`DrillTools`) for clients **ADES Saudi Company Limited** and **Saudi International Energy Services (SIES)**.

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB DN No | Actual PO (Visual) | Actual DN No (Visual) |
|---|---|---|---|---|---|---|---|---|
| **148** | `SIV-DTS-25-579-2.pdf` | DN | Drilling Tools Trading Est | SIES | `None` | `None` | **4500498641** | **GDN-DTS-25-617** |
| **151** | `SIV-DTS-25-580-2.pdf` | DN | Drilling Tools Trading Est | ADES | `None` | `None` | **192641** | **GDN-DTS-25-619** |
| **154** | `SIV-DTS-25-581-2.pdf` | DN | Drilling Tools Trading Est | ADES | `None` | `688` *(rig hallucination)* | **192701** | **GDN-DTS-25-620** |
| **157** | `SIV-DTS-25-582-2.pdf` | DN | Drilling Tools Trading Est | ADES | `None` | `None` | **192910** | **GDN-DTS-25-604** |
| **160** | `SIV-DTS-25-583-2.pdf` | DN | Drilling Tools Trading Est | ADES | `None` | `None` | **193781** | **GDN-DTS-25-622** |

---

### Brand Template Analysis: Drilling Tools Trading Est (Delivery Note)
- **Visual Layout Geometry**:
  - **Header Region**: Prominent top banner with `DrillTools` logo (left) and Arabic company name (right), spanning the top 0%–12%.
  - **Metadata Grid**: A boxed two-column table located between **15% and 33% page height**:
    - *Left Column*: Supplier details, VAT number, Client name (`ADES`, `SIES`, `Tubestar`), and Client address.
    - *Right Column*: 4 to 5 bordered rows:
      1. `Date : DD-MM-YYYY`
      2. `Del. Note No : GDN-DTS-YY-NNN`
      3. `Buyers Order No : <PO Number>` (often formatted with revision, e.g. `192701` or `22398 , 0`)
      4. `Rig / Location : ADES <Rig#>` (frequently contains handwritten rig numbers like `688`, `689`, `690`)
      5. `Remarks :` (frequently stamped or annotated by receiving warehouse)
  - **Table Region**: Spans 35% to 75% page height:
    - Columns: `L/I.#` | `Description` | `P/N` | `Qty` | `Units`
  - **Footer Region**: 75% to 100%: Customer goods receipt stamps (e.g. `ADES K.S.A CENTRAL W/H`, `GOODS RECEIVED CONTENT UNCHECKED`), signatures, and company registration footer.

---

### Detailed Document Audit & Extraction Prompt Diagnostics

#### Doc 148 (`SIV-DTS-25-579-2.pdf`)
- **Visual Truth**:
  - Header right box: `Del. Note No : GDN-DTS-25-617`, `Buyers Order No : 4500498641`, `Date : 12-11-2025`.
  - Client: `SAUDI INTERNATIONAL ENERGY SERVICES COMPANY LTD`.
  - Line 1: `DWE492-220V` | Qty: `1.00 EA`
  - Line 2: `DWE4120-220V` | Qty: `1.00 EA`
- **DB Record**:
  - `doc_type`: DN | `po_no_raw`: None | `dn_no`: None | `po_set_id`: None
  - Line items: Extracted correctly (2 lines, Qty 1.0 each).
- **Prompt Diagnostic**:
  - **Success**: Correctly classified as DN; lines extracted with pristine accuracy.
  - **Failure**: Missed both `Buyers Order No` (`4500498641`) and `Del. Note No` (`GDN-DTS-25-617`).
  - **Prompt Root Cause**: The instruction `first page top 20% for po_reference/document_number` cuts off the `Buyers Order No` line, which sits at ~24% page height on this template.

#### Doc 151 (`SIV-DTS-25-580-2.pdf`)
- **Visual Truth**:
  - Header right box: `Del. Note No : GDN-DTS-25-619`, `Buyers Order No : 192641`, `Rig / Location : ADES 689`.
  - Client: `ADES SAUDI COMPANY LIMITED`.
  - Line 1: `COB Buckles BB-5` (Qty 1.00 EACH), Line 2: `Tensioner CT-20 - 7500000` (Qty 2.00 EACH), Line 3: `BC 50/2 - Composite Strap - SBCS1002` (Qty 6.00 EACH).
- **DB Record**:
  - `doc_type`: DN | `po_no_raw`: None | `dn_no`: None | `po_set_id`: None
  - Line items: Extracted correctly (3 lines, exact quantities).
- **Prompt Diagnostic**:
  - **Failure**: `po_reference` and `document_number` both returned `None`.
  - **Prompt Root Cause**: Label `Del. Note No` is not in the explicit alias list of `_PAGE_PROMPT` (`'DN No'/'Delivery Note No'/'GDN No'`); 20% header ceiling truncated the search area.

#### Doc 154 (`SIV-DTS-25-581-2.pdf`)
- **Visual Truth**:
  - Header right box: `Del. Note No : GDN-DTS-25-620`, `Buyers Order No : 192701`, `Rig / Location : ADES 688` (with prominent handwritten `688` in Remarks and Rig field).
  - Client: `ADES SAUDI COMPANY LIMITED`.
  - Line 1: Ductile Iron Butterfly Valve 12" (Qty 3.00 EA), Line 2: Ductile Iron Butterfly Valve 8" (Qty 7.00 EA).
- **DB Record**:
  - `doc_type`: DN | `po_no_raw`: None | `dn_no`: `688` *(CRITICAL ERROR)* | `po_set_id`: None
  - Line items: Extracted correctly (2 lines, Qty 3.0 and 7.0).
- **Prompt Diagnostic**:
  - **Critical Failure**: The VLM extracted `688` as `document_number` (stored in `dn_no`), completely hallucinating the rig location as a Delivery Note number!
  - **Prompt Root Cause**: The prompt lacks negative constraints for location/rig numbers: `Rig numbers, well names, or handwritten warehouse tags (e.g. ADES 688) are NEVER document_number or po_reference`.
  - In addition, `Buyers Order No : 192701` was omitted.

#### Doc 157 (`SIV-DTS-25-582-2.pdf`)
- **Visual Truth**:
  - Header right box: `Del. Note No : GDN-DTS-25-604`, `Buyers Order No : 192910`, `Rig / Location : ADES 690`.
  - Client: `ADES SAUDI COMPANY LIMITED`.
  - Line 1: `[Gazelle - G3012] Gazelle - 12FT FIBERGLASS STEP LADDER (3.6M)` | Qty: `2.00 EACH`.
- **DB Record**:
  - `doc_type`: DN | `po_no_raw`: None | `dn_no`: None | `po_set_id`: None
  - Line items: Extracted correctly (1 line, Qty 2.0).
- **Prompt Diagnostic**:
  - **Failure**: Missing `po_reference` and `dn_no`.

#### Doc 160 (`SIV-DTS-25-583-2.pdf`)
- **Visual Truth**:
  - Header right box: `Del. Note No : GDN-DTS-25-622`, `Buyers Order No : 193781`, `Rig / Location : ADES 657`.
  - Client: `ADES SAUDI COMPANY LIMITED`.
  - 4 Lines: ESAB Welding electrodes 2.4mm (25.0 PKG), 3.2mm (15.0 BOX), 4mm (15.0 PKG), Flap disc 80 grit (10.0 EA).
- **DB Record**:
  - `doc_type`: DN | `po_no_raw`: None | `dn_no`: None | `po_set_id`: None
  - Line items: Extracted correctly (4 lines).
- **Prompt Diagnostic**:
  - **Failure**: Missing `po_reference` and `dn_no`.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • Document type classified as `DN` across all 5 files.<br>• 100% of line item descriptions, part numbers, and quantities parsed with zero errors.<br>• Clean unit stripping (`EA`, `EACH`, `PKG`, `BOX` removed, leaving raw float). | `_PAGE_PROMPT` Step 3 & Step 4 rules for tabular items and numeric-only quantities performed exceptionally well. |
| **What was Wrong** | • Doc 154 extracted `dn_no = 688` instead of `GDN-DTS-25-620`. | Prompt has no guardrail preventing Rig/Location numbers (`ADES 688`) from being selected as document numbers. |
| **What was Misrepresented** | • All 5 valid, signed Delivery Notes were recorded as orphan documents (`po_set_id = None`), causing their 3-way matching sets to remain permanently stuck in `pending`. | DB stores `po_no_raw = NULL`, isolating them from their corresponding Invoices (Docs 147, 150, 153, 156, 159). |
| **What was Missed** | • PO references missed: `4500498641`, `192641`, `192701`, `192910`, `193781`.<br>• DN numbers missed: `GDN-DTS-25-617`, `GDN-DTS-25-619`, `GDN-DTS-25-620`, `GDN-DTS-25-604`, `GDN-DTS-25-622`. | The `top 20%` header scanning restriction in `_PAGE_PROMPT` cuts off the metadata box located between 15% and 33% page height. |

---

### Actionable Prompt Improvement Insights (Brand: Drilling Tools | Type: DN)

To resolve the 100% failure rate of header metadata extraction on Drilling Tools Delivery Notes:

1. **Expand Header ROI Boundary in `_PAGE_PROMPT`**:
   - **Current Prompt**: `STEP 1 — HEADER SCAN (first page top 20% for po_reference/document_number...)`
   - **Fix**: Change `top 20%` to `top 35%`. On boxed supplier templates (Drilling Tools, RAK Energy, RAAS), logos and company credentials consume the top 15%, placing the metadata table between 15% and 35%.

2. **Add Explicit Label Aliases**:
   - Add `'Del. Note No'` and `'Del Note No'` to the document number trigger list:
     `Locate 'DN No'/'Delivery Note No'/'Del. Note No'/'GDN No'/'GDN-...' → document_number`
   - Emphasize that Drilling Tools delivery notes follow the pattern `GDN-DTS-*` and invoices follow `SIV-DTS-*`.

3. **Negative Instruction for Rig / Location Numbers**:
   - Explicitly instruct the model:
     `"Rig numbers, well numbers, location names, and warehouse codes (e.g. 'ADES 688', 'Rig 14', 'Loc: 690') are operational tags, NEVER the document_number or po_reference."`

4. **Add Few-Shot for Drilling Tools Pink Delivery Note Template**:
   ```json
   {
     "document_type": "DN",
     "document_number": "GDN-DTS-25-620",
     "po_reference": "192701",
     "vendor_name": "Drilling Tools Trading Est",
     "line_items": [
       {"line_item_no": "1", "description": "Ductile Iron Butterfly Valve Wafer Type 12\"", "quantity": "3"}
     ]
   }
   ```

## Audit Findings: Batch 25 (Docs 169, 170, 174, 179, 258)

### Document & Brand Overview
Batch 25 consists of **5 Delivery Notes (DN)** spanning two major vendor brands:
1. **Drilling Tools Trading Est** (Docs 169, 170, 174, 179) — pink-tinted scanned delivery notes for clients **Saudi International Energy Services (SIES)** and **ADES GCC for Drilling**.
2. **RAAS Oilfield Services and Supplies WLL** (Doc 258) — clean tabular delivery note from Bahrain for client **NOMAC Al Dur Power & Water Services**.

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB DN No | Actual PO (Visual) | Actual DN No (Visual) | Associated Parent PO Set |
|---|---|---|---|---|---|---|---|---|---|
| **169** | `SIV-DTS-25-588-2.pdf` | DN | Drilling Tools Trading Est | SIES | `None` | `None` | **4500496313** | **GDN-DTS-25-520** | PO Set 47 (Stuck in `pending`) |
| **170** | `SIV-DTS-25-588-3.pdf` | DN | Drilling Tools Trading Est | SIES | `None` | `None` | **4500496313** | **GDN-DTS-25-626** | PO Set 47 (Stuck in `pending`) |
| **174** | `SIV-DTS-25-589-2.pdf` | DN | Drilling Tools Trading Est | SIES | `None` | `None` | **4500497953** | **GDN-DTS-25-627** | PO Set 48 (Stuck in `pending`) |
| **179** | `SIV-DTS-25-596-2.pdf` | DN | Drilling Tools Trading Est | ADES | `None` | `None` | **23073** | **GDN-DTS-25-632** | PO Set 49 (Stuck in `pending`) |
| **258** | `SIV-RHO-26-1669A.pdf` | DN | RAAS Oilfield Services | NOMAC | **260988** | **GDN-RHO-26-1799** | **8300052050** *(handwritten)* / `260988` *(printed)* | **GDN-RHO-26-1799** | PO Set 75 (Stuck in `pending`) |

---

### Comparative Brand Template Analysis

#### 1. Brand: Drilling Tools Trading Est (Pink Scanned Form)
- **Visual Geometry**:
  - Header right box sits between **16% and 32% of page height**, containing:
    - `Date : DD-MM-YYYY`
    - `Del. Note No : GDN-DTS-YY-NNN`
    - `Buyers Order No : <PO Number>`
    - `Rig / Location : <Location>`
  - Visual inspection confirms all documents (Docs 169, 170, 174, 179) have high contrast, machine-printed text in this box.
- **Systemic Extraction Failure**:
  - All 4 delivery notes extracted `po_no_raw = None` and `dn_no = None`.
  - **Severe Downstream Impact**:
    - **PO Set 47** contains Invoice Doc 168 and PO Docs 171–172. Because Docs 169 & 170 failed extraction, PO Set 47 is missing its delivery notes and cannot reconcile. In reality, Docs 169 & 170 represent a **multi-DN split delivery** (DN 520 delivered Line 1: 10 Brother tapes on 24/09/25; DN 626 delivered Line 2: 2 Sledge hammers on 17/11/25). Together, they deliver 100% of PO Set 47!
    - **PO Set 48** contains Invoice Doc 173 and PO Docs 175–177. Missing DN Doc 174 leaves it permanently pending.
    - **PO Set 49** contains Invoice Doc 178 and PO Doc 180. Missing DN Doc 179 leaves it permanently pending.

#### 2. Brand: RAAS Oilfield Services (Bahrain Template)
- **Visual Geometry**:
  - Top header: Black-and-white vector layout.
  - Left Box: `Doc No : GDN-RHO-26-1799`, `Customer Name : NOMAC`.
  - Right Box: `P.O. : 260988`.
  - **Handwritten Annotation**: At the very top right, above the logo banner, the receiving/finance team wrote: `Po: 83000 52050`.
- **Extraction Behavior**:
  - The model extracted `document_type = DN`, `document_number = GDN-RHO-26-1799`, and `po_reference = 260988`.
  - **The Discrepancy**: The printed PO box (`260988`) is NOMAC's internal local order number, whereas the corporate SAP PO used by the vendor for Invoice (Doc 257) and PO contract (Doc 259) is `8300052050` (belonging to **PO Set 75**).
  - Because the model extracted `260988` instead of `8300052050`, Doc 258 was not attached to PO Set 75. Consequently, PO Set 75 is stuck in `pending` missing its DN, while Doc 258 sits unattached!

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% classification accuracy on `document_type: DN`.<br>• Accurate table extraction across all 5 files, including line numbering, multi-line descriptions, and quantity parsing.<br>• Doc 258 successfully followed the embedded line marker rule: `Line Item - 1` and `Line Item - 2` were correctly parsed as line numbers 1 and 2. | `_PAGE_PROMPT` Step 2 (doc type), Step 3 (line item markers), and Step 4 (quantity numbers) operate reliably on clean table layouts. |
| **What was Wrong** | • Doc 258 extracted `po_reference = 260988` instead of flagging `po_reference_ambiguous = true` or prioritizing the 10-digit SAP PO number `8300052050` written on the document. | Prompt instructs: `If MORE THAN ONE distinct PO number is visible on a document, set po_reference to the first and set po_reference_ambiguous true`. The VLM failed to identify the handwritten `Po: 83000 52050` at the top as an active PO reference. |
| **What was Misrepresented** | • Docs 169 and 170 are legitimate partial deliveries completing PO Set 47, but were recorded as useless orphans without a PO reference. | The database records `po_no_raw = NULL`, fracturing a valid 2-DN split delivery into disconnected files. |
| **What was Missed** | • PO references missed on Drilling Tools DNs: `4500496313` (Docs 169 & 170), `4500497953` (Doc 174), and `23073` (Doc 179).<br>• DN numbers missed: `GDN-DTS-25-520`, `GDN-DTS-25-626`, `GDN-DTS-25-627`, `GDN-DTS-25-632`. | Prompt instruction `first page top 20% for po_reference/document_number` limits the vision crop boundary, consistently ignoring the metadata table spanning 15%–33% on Drilling Tools forms. |

---

### Actionable Prompt Improvement Insights

1. **Header Boundary Revision (`_PAGE_PROMPT` Step 1)**:
   - **Current Rule**: `first page top 20% for po_reference/document_number`
   - **Updated Rule**: `first page top 35% for po_reference/document_number`
   - **Rationale**: On both Drilling Tools and RAAS templates, header blocks extend down to 33% of page height. A 20% cutoff blindfolds the model to `Buyers Order No` and `Del. Note No`.

2. **Handle Multi-PO Discrepancies & Handwritten Overrides on Delivery Notes**:
   - Add explicit guidance in `_PAGE_PROMPT` Step 1:
     `"On delivery notes, look for handwritten PO numbers (e.g. 'Po: 83000 52050') often annotated by receiving clerks at the top of the page. If a document displays both a printed internal order number (e.g. 6-digit 260988) and a handwritten standard SAP PO number (e.g. 10-digit starting with 83... or 45...), record the standard SAP PO number as po_reference, or set po_reference_ambiguous: true if both are valid candidate numbers."`

3. **Multi-Delivery Note Set Association Rule**:
   - Add guidance that multiple Delivery Notes referencing the same `Buyers Order No` (e.g. GDN 520 and GDN 626 both referencing `4500496313`) are expected and must both be extracted with the exact same `po_reference` to allow 3-way reconciliation across split shipments.

## Audit Findings: Batch 26 (Docs 260, 261, 265, 268, 271)

### Document & Brand Overview
Batch 26 consists of **5 documents** (2 Sales Invoices and 3 Delivery Notes) all issued by **RAAS Oilfield Services and Supplies WLL** (`raas`, Kingdom of Bahrain) across diverse industrial clients:
- **Gulf Aluminium Rolling Mill (GARMCO)** (Docs 260, 261)
- **Bahrain Airport Services (BAS)** (Doc 265)
- **Armacell Middle East Company** (Doc 268)
- **International Maritime Industries (IMI)** (Doc 271)

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB DN No | Actual PO (Visual) | Actual DN No (Visual) | Target PO Set |
|---|---|---|---|---|---|---|---|---|---|
| **260** | `SIV-RHO-26-1896.pdf` | SI | RAAS Oilfield Services | GARMCO | `60213576` | `None` *(on header)* | **60213576** | **GDN-RHO-26-2105** *(in table)* | PO Set 76 (`60213576OPG`) |
| **261** | `SIV-RHO-26-1896A.pdf` | DN | RAAS Oilfield Services | GARMCO | `60213576` | `GDN-RHO-26-2105` | **60213576** / `13085` | **GDN-RHO-26-2105** | PO Set 76 (`60213576OPG`) |
| **265** | `SIV-RHO-26-1914.pdf` | SI | RAAS Oilfield Services | BAS | `4270006645` | `None` *(on header)* | **4270006645 -SNN** | **GDN-RHO-26-2183** *(in table)* | PO Set 77 (`4270006645SNN`) |
| **268** | `SIV-RHO-26-233A.pdf` | DN | RAAS Oilfield Services | Armacell | `260030` | `GDN-RHO-26-92` | **4500362256** *(header)* / `260030` *(box)* | **GDN-RHO-26-92** | PO Set 78 (`4500362256`) |
| **271** | `SIV-RHO-26-234A.pdf` | DN | RAAS Oilfield Services | IMI | `None` | `None` | **C13717** | **GDN-RHO-26-270** | PO Set 79 (`C13717`) |

---

### Brand Template Analysis: RAAS Oilfield Services (Kingdom of Bahrain)

#### 1. Invoices (`TAX INVOICE` / `SIV-RHO-*`)
- **Header Structure**: Top-left contains company logo and address; top-right contains a 5-row bordered metadata grid (`INVOICE #`, `Date`, `P.O. #`, `Reference`, `Payment Terms`).
- **Table Structure**: Includes an explicit **`Delivery Note No` column** alongside `Description`, `Qty`, `Unit Price`, and `Amount`.
- **Currency**: Bahraini Dinar (`BHD`), using **3 decimal places** (e.g. `11.250` BHD = 11 Dinar 250 fils).

#### 2. Delivery Notes (`DELIVERY NOTE` / `GDN-RHO-*`)
- **Header Structure**: Sits at **22% to 32% page height**, beneath company contact lines:
  - Left Box: `Doc No : GDN-RHO-YY-NNN`, `Doc Date`, `Customer Name`.
  - Right Box: `P.O. : <Order No>`, `Rig.No : <Rig>`.
  - Top Region (above or outside box): Client SAP PO number often printed in bold (e.g. `PO : 4500362256` or `PO - 60213576`), or handwritten (`Po: 83000 52050`).
- **Line Item Numbering**: Descriptions systematically include an embedded marker on line 2: `"Line Item - 1"`, `"Line Item - 2"`.

---

### Detailed Document Audit & Extraction Prompt Diagnostics

#### Docs 260 & 261 (GARMCO Order 60213576 / PO Set 76)
- **Visual Truth**:
  - Invoice Doc 260: `INVOICE # : SIV-RHO-26-1896`, `P.O. # : 60213576`. Table lines specify `Delivery Note No : GDN-RHO-26-2105`.
  - Delivery Note Doc 261: `Doc No : GDN-RHO-26-2105`. Top right prints `PO - 60213576`; box prints `P.O. : 13085` (local internal requisition).
  - Line Items: Line 1 (3 cans Thinner No. 02 @ 11.250 BHD), Line 2 (3 cans Thinner No. 07 @ 11.500 BHD).
- **Prompt Diagnostic**:
  - **Success**: The prompt successfully prioritized `PO - 60213576` over the internal requisition `13085` on Doc 261. Table-level DN extraction (`GDN-RHO-26-2105`) on Doc 260 succeeded 100%. Line marker resolution (`Line Item - 1` → line_item_no 1) succeeded.
  - **System Grouping Failure**: Why are Docs 260 and 261 unattached (`po_set_id = None`)?
    - The actual PO file for this order (Doc 262) printed `Order: 60213576 OPG` (where `OPG` is GARMCO's Operations department code).
    - The PO extractor normalized this as `60213576OPG` and spawned **PO Set 76** with key `60213576OPG`.
    - Docs 260 and 261 extracted `60213576` (digits only).
    - Because grouping requires an exact normalized string match, `60213576` failed to join `60213576OPG`, leaving the entire set fractured!

#### Doc 265 (Bahrain Airport Services Order 4270006645 / PO Set 77)
- **Visual Truth**:
  - Invoice `SIV-RHO-26-1914`: Header clearly prints `P.O. # : 4270006645 -SNN`.
  - Table: Mixer/Grinder Machine with SS (Qty 1.0, Unit Price 42.000 BHD), `Delivery Note No : GDN-RHO-26-2183`.
- **Prompt Diagnostic**:
  - **Discrepancy**: The VLM extracted `po_no_raw: 4270006645`, dropping the `-SNN` suffix.
  - However, the corresponding DN (Doc 264) and PO (Doc 266) extracted `4270006645SNN` and joined **PO Set 77**.
  - **Result**: SI Doc 265 was isolated from PO Set 77 due to the dropped suffix.

#### Doc 268 (Armacell Order 4500362256 / PO Set 78)
- **Visual Truth**:
  - Delivery Note `GDN-RHO-26-92`.
  - In bold text at top right: `PO : 4500362256`.
  - In the bordered metadata box below: `P.O : 260030` (Armacell internal job/order number).
  - 4 lines: Spray paint (12 ea), Self drill screws (1 box, 1 box), Silicone sealant (5 ea). Stamped by Armacell Middle East on 08/01/26.
- **Prompt Diagnostic**:
  - **Prompt Failure**: The extractor picked `260030` from the box instead of `4500362256`!
  - **Root Cause**: The prompt lacks guidance on multiple PO labels. It found `P.O :` in the box first and ignored the standard 10-digit SAP PO number `4500362256` printed in bold directly above it.
  - **Result**: Doc 268 was orphaned under PO `260030`, stranding **PO Set 78** (`4500362256`) without its delivery note.

#### Doc 271 (International Maritime Industries Order C13717 / PO Set 79)
- **Visual Truth**:
  - Delivery Note `GDN-RHO-26-270`, Date `26/01/2026`.
  - In the metadata grid at 24%–28% page height:
    - `Doc No : GDN-RHO-26-270`
    - `P.O : C13717`
  - Line 1: Air vent ducting Flexible (Qty 120.00 EACH). Stamped by IMI Warehouse on 27 JAN 2026.
- **Prompt Diagnostic**:
  - **Critical Failure**: DB recorded `po_no_raw: None`, `dn_no: None`!
  - **Smoking Gun Root Cause**:
    - `_PAGE_PROMPT` Step 1 explicitly specifies: `STEP 1 — HEADER SCAN (first page top 20% for po_reference/document_number...)`.
    - On Doc 271, the company contact information occupies the top 0%–22% of the page. The `Doc No` and `P.O` boxes sit at **24% to 28% of page height**.
    - The model followed the `top 20%` limit, scanned only the address lines, found no PO or DN, and returned `null` for both!
    - **Result**: Doc 271 was orphaned, leaving **PO Set 79** (`C13717`) pending without its DN.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% accurate classification of Invoices (`SI`) and Delivery Notes (`DN`).<br>• Table-level `Delivery Note No` extraction on invoices (Doc 260 extracted `GDN-RHO-26-2105` into `line_items.dn_no`).<br>• Accurate parsing of embedded `"Line Item - N"` markers into `line_item_no`. | `_PAGE_PROMPT` Step 2, Step 3, and table column instructions for `dn_no` performed as designed on clean invoice tables. |
| **What was Wrong** | • Doc 268 picked internal job number `260030` instead of the 10-digit SAP PO `4500362256` printed in bold above it.<br>• Doc 265 stripped suffix `-SNN` from `4270006645 -SNN`, while PO and DN kept it. | • Prompt has no heuristics to prioritize 10-digit SAP PO numbers (`45...`, `60...`, `42...`) over short internal requisition numbers.<br>• Lack of unified suffix handling rule (e.g. whether trailing departmental codes like `-SNN` or `OPG` should be stripped or retained). |
| **What was Misrepresented** | • Docs 260 & 261 were extracted as PO `60213576` while PO Doc 262 was extracted as `60213576OPG`, causing grouping to treat them as two completely separate orders. | System lacks fuzzy/prefix grouping for department letter suffixes appended to standard numeric PO numbers. |
| **What was Missed** | • Doc 271 completely missed `Doc No : GDN-RHO-26-270` and `P.O : C13717`. | The strict `top 20%` header scan limit in `_PAGE_PROMPT` actively blinded the model to the metadata grid located at 24%–28% page height. |

---

### Actionable Prompt Improvement Insights (Brand: RAAS | Types: SI, DN)

1. **Eliminate the 20% Header Restriction**:
   - Update `_PAGE_PROMPT` Step 1:
     `"STEP 1 — HEADER SCAN (first page top 35% for po_reference/document_number): On documents with logos and contact headers (e.g. RAAS, Drilling Tools), the metadata box is located between 15% and 35% page height. Scan the full upper 35% of the page."`

2. **PO Number Resolution Heuristic (Corporate PO vs Internal Requisition)**:
   - Add explicit disambiguation in `_PAGE_PROMPT` Step 1:
     `"If a delivery note displays two order numbers (e.g. a bold header 'PO : 4500362256' AND a box 'P.O : 260030'): ALWAYS prioritize the corporate customer PO number (typically 8–10 digits, such as 45..., 60..., 83..., 42...) over internal 5-to-6 digit job/ticket/requisition numbers."`

3. **Standardize Suffix Handling for Department / Plant Codes**:
   - Add clear instruction on trailing alphabetic suffixes:
     `"If a PO number is printed with a departmental or plant suffix separated by a space or hyphen (e.g. '4270006645 -SNN' or '60213576 OPG'): include the suffix verbatim in po_reference (strip hyphen, keep alnum: '4270006645SNN', '60213576OPG'). Do NOT drop trailing letters on invoices while keeping them on POs."`

## Audit Findings: Batch 27 (Docs 18, 19, 20, 21, 22)

### Document & Brand Overview
Batch 27 covers **5 documents** belonging to two major corporate entities:
1. **Vendor**: **Ibrahim Ali Al-Shab Trading Est** (`AL-SHAB` / `ARS`, Al Khobar, KSA)
2. **Buyer / Client**: **Saudi Aramco Rowan Offshore Drilling Co.** (`ARO Drilling`, KSA)

These documents form the backbone of:
- **PO Set 7** (`50119501`, Status: `quarantined`) — Docs 18, 19, 20, 21
- **PO Set 8** (`50117194`, Status: `quarantined`) — Doc 22

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB DN / SI No | Actual PO (Visual) | Actual Document No (Visual) | Status in DB |
|---|---|---|---|---|---|---|---|---|---|
| **18** | `SIV-ARS-25-7969-pages-1.pdf` | SI | Ibrahim Ali Al-Shab | ARO Drilling | `50119501` | SI: `SIV-ARS-25-7969` | **50119501** | **SIV-ARS-25-7969** | PO Set 7 (`quarantined`) |
| **19** | `SIV-ARS-25-7969-pages-2.pdf` | DN | Ibrahim Ali Al-Shab | ARO Drilling | `50119501` | DN: `GDN-ARS-25-9355` | **50119501** | **GDN-ARS-25-9355** | PO Set 7 (`quarantined`) |
| **20** | `SIV-ARS-25-7969-pages-3.pdf` | PO | ARO Drilling (Buyer) | Al-Shab | `50119501` | `None` | **50119501** | DPO: **50119501** (p1) | PO Set 7 (`quarantined`) |
| **21** | `SIV-ARS-25-7969-pages-4.pdf` | PO | ARO Drilling (Buyer) | Al-Shab | `50119501` | `None` | **50119501** | DPO: **50119501** (p2) | PO Set 7 (`quarantined`) |
| **22** | `SIV-ARS-25-7970-pages-1.pdf` | SI | Ibrahim Ali Al-Shab | ARO Drilling | `50117194` | SI: `SIV-ARS-25-7970` | **50117194** | **SIV-ARS-25-7970** | PO Set 8 (`quarantined`) |

---

### Brand Template Analysis: Al-Shab & ARO Drilling

#### 1. Ibrahim Ali Al-Shab Invoices (`TAX INVOICE` / `SIV-ARS-*`)
- **Layout**: Crisp bilingual (English/Arabic) boxed header.
- **Key Fields**: `Invoice No. / رقم الفاتورة`, `P.O. No. / رقم امر الشراء`, `Rig No. / رقم الحفار` (e.g. `C016`), `Date of Supply`.
- **Table**: SNO, bilingual Description of Goods, Qty, Unit, Unit Rate, Total Amount, VAT%.
- **Extraction Performance**: **100% flawless**. The prompt extracted invoice numbers, PO references, line numbers, quantities, and unit rates without a single error on both Doc 18 and Doc 22.

#### 2. ARO Drilling Purchase Orders (`DPO Number`)
- **Layout**: Standard SAP ARO Drilling purchase contract across multiple pages.
- **Key Fields**: Top-right header box lists `DPO Number : 50119501`, `Revision : 0`, `Date : 28/10/2025`.
- **Table**: `Item` | `Material` | `Description` | `PO Type` | `Quantity` | `Unit` | `Unit Price` | `Delivery Date`. Descriptions contain embedded metadata lines: `Line\Rel No: 1\1`, `Line\Rel No: 6\1`.
- **Extraction Performance**: Extracted all lines cleanly, matching prices and quantities accurately across pages (Docs 20 & 21).

#### 3. Ibrahim Ali Al-Shab Delivery Notes (`DELIVERY NOTE` / `GDN-ARS-*`)
- **Layout**: Photocopied raster scan with heavy black borders and dotted background noise.
- **Key Fields**: `DELIVERY NOTE NO. : GDN-ARS-25-9355`, `P.O. # : 50119501`, `Rig No. : C016`.
- **Table**: Columns for `Item No.`, `DESCRIPTION`, `Part No.`, `QTY.`

---

### Deep Prompt Diagnostic: The "Flex Joint" Hallucination in Doc 19

Visual comparison between the physical page image (`doc019_p1.png`) and the database record uncovered a **critical VLM hallucination failure**:

1. **Visual Reality on Doc 19**:
   - Header: `GDN-ARS-25-9355`, PO `50119501`, Rig `C016`.
   - Table:
     - `Item No.`: 6 (or faint 1)
     - `DESCRIPTION`: `CLIP, GLOVE GRABBER; W/BELTCLIP, LIME`
     - `Part No.`: `3405`
     - `QTY.`: `50 Each`
2. **What the Extractor Stored in DB**:
   - `line_item_no`: `1`
   - `description`: `FLEX JOINT 3/4" NPT FEMALE X 1/2" NPT MALE` *(COMPLETE HALLUCINATION!)*
   - `quantity`: `50.0`
3. **Catastrophic Reconciliation Impact**:
   - PO Set 7 consists of:
     - PO (Docs 20 & 21): 6 items (Lines 1–5: Gloves; Line 6: 50 Glove Grabber clips).
     - SI (Doc 18): Line 6 (50 Glove Grabber clips @ 3.00 SAR).
     - DN (Doc 19): Extracted as Line 1: `FLEX JOINT` (Qty 50).
   - Because the DN extracted a phantom "FLEX JOINT" on Line 1, the matcher detected an orphan vendor item on Line 1 and a missing delivery on Line 6, immediately plunging PO Set 7 into **`quarantined`**!
   - In physical reality, Doc 18 (SI) and Doc 19 (DN) deliver and invoice Line 6 **with 100% perfect 3-way alignment** (50 clips ordered, delivered, and invoiced)!
4. **Prompt Root Cause Analysis**:
   - On photocopied scans with dithered dot patterns, VLMs without strict visual grounding guidelines can hallucinate plausible industrial terms ("FLEX JOINT") when trying to resolve fuzzy letterforms.
   - The current `_PAGE_PROMPT` contains zero anti-hallucination instructions for low-contrast/degraded scans.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% accurate classification of `SI`, `DN`, and multi-page `PO`.<br>• Accurate extraction of bilingual Al-Shab invoices (Docs 18 & 22) and ARO Drilling PO tables (Docs 20 & 21).<br>• Correct header capture of `DPO Number : 50119501` on ARO PO and `P.O. No : 50119501` on Al-Shab SI/DN. | `_PAGE_PROMPT` Step 1 (`DPO Number` / `P.O. No`) and Step 2 handled standard high-contrast layouts effectively. |
| **What was Wrong** | • Doc 19 table extracted phantom item `FLEX JOINT 3/4" NPT FEMALE X 1/2" NPT MALE` instead of `CLIP, GLOVE GRABBER; W/BELTCLIP, LIME`.<br>• Extracted line item number as `1` instead of line item `6`. | Severe VLM OCR hallucination on low-contrast photocopied scan, combined with lack of cross-document grounding in prompt. |
| **What was Misrepresented** | • PO Set 7 was quarantined as a reconciliation failure, when in reality it is a 100% valid partial delivery/invoice of Line 6. | Caused directly by the hallucinated item description and erroneous line number on Doc 19. |
| **What was Missed** | • Genuine item text `CLIP, GLOVE GRABBER; W/BELTCLIP, LIME` and Part No `3405` were completely lost on Doc 19. | OCR degradation caused model to discard actual visual tokens. |

---

### Actionable Prompt Improvement Insights (Brand: Al-Shab / ARO | Types: SI, DN, PO)

1. **Anti-Hallucination & Visual Grounding Rule (`_PAGE_PROMPT` Step 3)**:
   - Add explicit instruction:
     `"GROUNDING RULE: Never invent, extrapolate, or hallucinate industrial descriptions (e.g. inventing 'FLEX JOINT' or 'FLANGE' on noisy scans). Transcribe ONLY what is visually discernible. If characters are grainy, transcribe the best visual match (e.g. 'CLIP, GLOVE GRABBER') or check the customer PO description for reference."`

2. **DPO Number Recognition for ARO Drilling Contracts**:
   - Confirm alias in `_PAGE_PROMPT` Step 1:
     `"ARO Drilling purchase orders print 'DPO Number' in the top right box; extract this as document_number for POs."`

3. **Line Item Numbering on Degraded Dockets**:
   - Instruct the model:
     `"When reading the side column (Item No / SNO) on a Delivery Note that corresponds to a multi-line PO, inspect the digit carefully (do not mistake '6' for '1' or bullet point). If the description matches a specific PO line item, ensure the line_item_no aligns with the PO line."`

## Audit Findings: Batch 28 (Docs 23, 24, 37, 38, 39)

### Document & Brand Overview
Batch 28 covers **5 documents** from **Ibrahim Ali Al-Shab Trading Est** (`AL-SHAB` / `ARS`) and buyer contracts from **ARO Drilling** and **ADES Saudi Company Limited**:
- **PO Set 8** (`50117194`, Status: `quarantined`) — Docs 23, 24 (and Doc 22 from Batch 27)
- **PO Set 13** (`193165`, Status: `quarantined`) — Docs 37, 38, 39 (along with Docs 40–42)

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB Document No | Actual PO (Visual) | Actual Document No (Visual) | Reconciliation Status |
|---|---|---|---|---|---|---|---|---|---|
| **23** | `SIV-ARS-25-7970-pages-2.pdf` | DN | Ibrahim Ali Al-Shab | ARO Drilling | `50137194` *(OCR digit error)* | `GDN-ARS-25-9175` | **50117194** | **GDN-ARS-25-9175** | PO Set 8 (`quarantined`) |
| **24** | `SIV-ARS-25-7970-pages-3.pdf` | PO | ARO Drilling (Buyer) | Al-Shab | `50117194` | `None` | **50117194** | DPO: **50117194** | PO Set 8 (`quarantined`) |
| **37** | `SIV-ARS-25-8261-1.pdf` | SI | Ibrahim Ali Al-Shab | ADES | `193165` | `SIV-ARS-25-8261` | **193165** | **SIV-ARS-25-8261** (p1) | PO Set 13 (`quarantined`) |
| **38** | `SIV-ARS-25-8261-2.pdf` | SI | Ibrahim Ali Al-Shab | ADES | `193165` | `SIV-ARS-25-8261` | **193165** | **SIV-ARS-25-8261** (p2) | PO Set 13 (`quarantined`) |
| **39** | `SIV-ARS-25-8261-3.pdf` | DN | Ibrahim Ali Al-Shab | ADES | `193165` | `GDN-ARS-25-9496` | **193165** | **GDN-ARS-25-9496** | PO Set 13 (`quarantined`) |

---

### Brand Template Analysis: Al-Shab & ADES / ARO Orders

#### 1. Degraded Photocopied Delivery Notes (Doc 23)
- **Visual Appearance**: High-contrast, noisy photocopy scan. Header contains `P.O. # : 50117194` and `DELIVERY NOTE NO. : GDN-ARS-25-9175`.
- **Systemic Vulnerability**: On these low-fidelity photocopies, the VLM displays two acute vulnerabilities:
  1. **Digit Confusion**: Misreads `'1'` as `'3'` (`50117194` → `50137194`).
  2. **Chemical/Industrial Hallucination**: Emits completely fabricated item descriptions (e.g. hallucinating `REFRIGERANT PENETRATE 21 cm 1 carton MEGAPL...` when the visible text is `REFRESH & REHYDRATE 21 grm 1 sachets 10EACH/PAC`).

#### 2. Clean Multi-Page Invoice Split (Docs 37 & 38)
- **Visual Appearance**: Invoice `SIV-ARS-25-8261` to ADES:
  - Page 1 (Doc 37): Top company header, customer details, empty item table grid (0 items), subtotal and VAT totals (`6,332.40 SAR` net / `7,282.26 SAR` gross).
  - Page 2 (Doc 38): Identical header block followed by 10 detailed line items (Cotton rags, WD-40, Permatex anti-seize, Silicone, Duct tape, Floor squeegees, Mops, Brooms, Kolor Kut paste).
- **Prompt Performance**:
  - The extractor behaved flawlessly on both pages: Doc 37 returned `line_items: []` without making up rows for the summary box, while Doc 38 extracted all 10 lines with 100% accurate quantities, units, and unit rates.

#### 3. Single-Item Partial Delivery Note (Doc 39)
- **Visual Appearance**: Clean scan of Delivery Note `GDN-ARS-25-9496` to ADES. Contains 1 line item: `Item No: 4`, `SILICONE, CLEAR (TUBE) - MFG: GULF - P/N# 1200`, `Qty: 24 Each`.
- **Prompt Performance**: The model accurately retained the vendor's line number `4` (`line_item_no: "4"`), perfectly matching Line 4 of Invoice Doc 38.

---

### Detailed Document Audit & Extraction Prompt Diagnostics

#### Doc 23 (`SIV-ARS-25-7970-pages-2.pdf`) vs Doc 24 (`PO`) & Doc 22 (`SI`)
- **Visual Evidence**:
  - Doc 23 header prints: `DELIVERY NOTE NO. : GDN-ARS-25-9175`, `P.O. # : 50117194`, `Rig No. : C016`.
  - Item: `REFRESH & REHYDRATE 21 grm 1 sachets 10EACH/PAC`, `QTY: 50 Each`.
  - Received stamp: `ARO DRILLING 02 NOV 2025`.
- **Database Errors**:
  - `po_no_raw`: `50137194` *(CRITICAL DIGIT CONFUSION)*
  - `line_items`: `REFRIGERANT PENETRATE 21 cm 1 carton MEGAPL...` *(CRITICAL OCR HALLUCINATION)*
- **Impact on System**:
  - PO Set 8 was quarantined because Doc 23's extracted PO (`50137194`) differed from PO Set 8's PO (`50117194`).
  - In physical reality, Doc 22 (SI), Doc 23 (DN), and Doc 24 (PO) are a **100% flawless 3-way match** for 50 units of Refresh & Rehydrate electrolyte powder!

#### Doc 37 (`SIV-ARS-25-8261-1.pdf`) & Doc 38 (`SIV-ARS-25-8261-2.pdf`)
- **Visual Evidence**: 2-page invoice `SIV-ARS-25-8261` to ADES Rig 686 for PO `193165`.
- **Database Status**:
  - Both docs linked to PO `193165`. Doc 37 has 0 lines, Doc 38 has 10 lines.
  - Total line items match invoice amount (`6,332.40 SAR`).

#### Doc 39 (`SIV-ARS-25-8261-3.pdf`)
- **Visual Evidence**: Delivery Note `GDN-ARS-25-9496` for ADES Rig 686.
- **Database Status**: Extracted Line 4 (24 tubes of Silicone), matching Invoice Doc 38 Line 4.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% accurate classification of `SI`, `DN`, and `PO`.<br>• Accurate extraction of 10 items with prices and quantities on Doc 38.<br>• Correctly returned empty line items on Doc 37 without fabricating lines from totals.<br>• Perfect retention of `line_item_no: "4"` on partial DN Doc 39. | `_PAGE_PROMPT` Step 2 (classification), Step 3 (line item exclusion of totals), and Step 4 (numeric extraction) operated perfectly on clean documents. |
| **What was Wrong** | • Doc 23 extracted `po_reference = 50137194` instead of `50117194`.<br>• Doc 23 extracted hallucinated description `REFRIGERANT PENETRATE...` instead of `REFRESH & REHYDRATE...`. | The VLM suffered character hallucination and digit corruption when processing a degraded dot-matrix/photocopied scan. |
| **What was Misrepresented** | • PO Set 8 was quarantined for mismatched PO numbers, when in reality all documents belong to PO `50117194` and match 100%.<br>• PO Set 13 was quarantined for "holding more than one PO document", caused by multi-page PO splitting. | • Doc 23 digit error caused false PO mismatch.<br>• Splitter pipeline ingested pages 5 & 6 of PO `193165` as two separate PO documents. |
| **What was Missed** | • Correct PO digit `'1'` and genuine product text on Doc 23. | Model lacked cross-check instruction against sibling documents in the set. |

---

### Actionable Prompt Improvement Insights (Brand: Al-Shab / ARO / ADES)

1. **Anti-Hallucination & Transcoding Guardrail for Degraded Dockets**:
   - Add to `_PAGE_PROMPT` Step 3:
     `"DEGRADED / PHOTOCOPY SCANS: If text is grainy, low-contrast, or dithered, never substitute ungrounded technical terminology (e.g. inventing 'REFRIGERANT PENETRATE' or 'FLEX JOINT'). Transcribe ONLY the visible character strokes, and cross-reference with clear customer PO text if available."`

2. **Digit Discrimination in PO References**:
   - Add to `_PAGE_PROMPT` Step 1:
     `"On typed and scanned digits, distinguish carefully between '1' and '3' (a straight vertical stroke is '1'; curved loops indicate '3'). In PO numbers like '50117194', verify that consecutive identical digits are preserved verbatim."`

3. **Multi-Page PO Document Grouping Guidance**:
   - Ensure the prompt schema for `COMBINED` and multi-page documents instructs that subsequent pages of the same PO form a single logical document, preventing ingestion from treating each page as an independent competing PO contract.

## Audit Findings: Batch 29 (Docs 40, 41, 42, 58, 59)

### Document & Brand Overview
Batch 29 covers **5 documents** across two major oilfield customers serviced by **Ibrahim Ali Al-Shab Trading Est** (`AL-SHAB` / `ARS`):
1. **ADES Saudi Company Limited** (Docs 40, 41, 42) — completing **PO Set 13** (`193165`, Status: `quarantined`)
2. **Arabian Drilling Company (ADC)** (Docs 58, 59) — forming **PO Set 19** (`4500051195`, Status: `quarantined`)

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB Document No | Actual PO (Visual) | Actual Document No (Visual) | Reconciliation Status |
|---|---|---|---|---|---|---|---|---|---|
| **40** | `SIV-ARS-25-8261-4.pdf` | DN | Ibrahim Ali Al-Shab | ADES | `193165` | `GDN-ARS-25-9329` | **193165** | **GDN-ARS-25-9329** | PO Set 13 (`quarantined`) |
| **41** | `SIV-ARS-25-8261-5.pdf` | PO | ADES (Buyer) | Al-Shab | `193165` | `None` | **193165** | PO: **193165** (p1) | PO Set 13 (`quarantined`) |
| **42** | `SIV-ARS-25-8261-6.pdf` | PO | ADES (Buyer) | Al-Shab | `193165` | `None` | **193165** | PO: **193165** (p2) | PO Set 13 (`quarantined`) |
| **58** | `SIV-ARS-25-8312-pages-1.pdf` | SI | Ibrahim Ali Al-Shab | Arabian Drilling | `4500051195` | `SIV-ARS-25-8312` | **4500051195** | **SIV-ARS-25-8312** | PO Set 19 (`quarantined`) |
| **59** | `SIV-ARS-25-8312-pages-2.pdf` | DN | Ibrahim Ali Al-Shab | Arabian Drilling | `4500051195` | `GDN-ARS-25-9587` | **4500051195** | **GDN-ARS-25-9587** | PO Set 19 (`quarantined`) |

---

### Brand Template Analysis: Al-Shab, ADES & Arabian Drilling

#### 1. ADES Approved Standard Purchase Order (Docs 41 & 42)
- **Geometry**: Structured tabular enterprise order. Header clearly displays `PO 193165`, `PO, Rev # : 193165, 0`, `Approved Date : 04-NOV-2025`.
- **Table Structure**: `Line` | `Item Description` | `Quantity` | `UOM` | `Unit Price (SAR)` | `Total (SAR)`.
- **Prompt Performance**: Flawless extraction. The model parsed lines 1–5 on page 1 and lines 6–10 on page 2, capturing exact descriptions, quantities, and unit rates without distortion.

#### 2. Arabian Drilling Company (ADC) Order Structure (Docs 58 & 59)
- **Geometry & Numbering**: Uses standard SAP 10-digit PO numbers starting with `45...` (`4500051195`) and step-10 line numbering (`10`, `20`).
- **Prompt Performance**: The model extracted line numbers `"10"` and `"20"` verbatim across both Invoice Doc 58 and Delivery Note Doc 59, adhering strictly to rule: `Do not renumber, pad or strip leading zeros`.

---

### Deep Systemic Finding: False Quarantine of Multi-Page Purchase Orders

Both **PO Set 13** and **PO Set 19** were quarantined by the system with the exact same error reason:
> `quarantined: This PO Set holds more than one PO document`

Visual inspection of physical documents confirms:
1. **PO Set 13** holds Docs 41 & 42. These are NOT two competing PO contracts; they are **Pages 1 and 2 of the exact same 7-page ADES Purchase Order `193165`**!
   - PO lines 1–10 match Invoice Docs 37 & 38 (10 items @ 6,332.40 SAR) **with 100% precision**.
   - Delivery Notes Doc 39 (Line 4) and Doc 40 (Lines 1, 2, 3, 5, 6, 7, 8, 9, 10) deliver **100% of all 10 items**.
   - PO Set 13 is a **pristine, valid 3-way match**, falsely blocked by ingestion splitting.
2. **PO Set 19** holds Docs 60 & 61. These are **Pages 3 and 4 of the exact same Arabian Drilling Purchase Order `450051195`**!
   - Invoice Doc 58 and Delivery Note Doc 59 match Lines 10 (Qty 50) and 20 (Qty 12) **with 100% precision**.
   - PO Set 19 is a **pristine, valid 3-way match**, falsely blocked by the same splitting rule.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% classification accuracy on `SI`, `DN`, and `PO`.<br>• Accurate extraction of step-10 line item numbers (`10`, `20`) on Arabian Drilling orders.<br>• Perfect preservation of non-sequential line numbers on partial DN Doc 40 (`1, 2, 3, 5, 6, 7, 8, 9, 10`), correctly leaving Line 4 for DN Doc 39. | `_PAGE_PROMPT` Step 3 rules: verbatim preservation of line numbers without re-indexing performed exactly as intended. |
| **What was Wrong** | • Ingestion pipeline treated consecutive pages of the same PO document as multiple conflicting PO documents. | The document ingestion / splitting layer lacks logic to group contiguous pages of the same PO into a single logical PO entity. |
| **What was Misrepresented** | • Both PO Set 13 and PO Set 19 were marked `quarantined` as multi-PO conflicts, masking two completely reconciled vendor transactions. | Reconciliation rule `po_doc_count > 1` triggers unconditionally on multi-page PO splits. |
| **What was Missed** | • Nothing missed by extraction prompt in this batch; extraction quality was 100% correct across all 5 files. | Prompt performed cleanly on these high-contrast vector and clean scan layouts. |

---

### Actionable Prompt & Pipeline Improvement Insights

1. **Multi-Page PO Component Linking in `COMBINED` Extraction**:
   - In `_PAGE_PROMPT` Step 6 (Multi-doc split):
     `"When a single PDF contains a multi-page PO (e.g. pages 1..7 of an ADES Standard Purchase Order), output ONE contiguous PO component spanning all pages (e.g. {'document_type': 'PO', 'page_start': 1, 'page_end': 7}), NEVER split individual PO pages into separate 1-page PO components."`

2. **Reconciliation Multi-PO Disambiguation Rule**:
   - The reconciliation check `This PO Set holds more than one PO document` should verify whether the multiple PO documents share the **exact same `document_number` / `po_no_normalized`**. If they share the exact same PO number, they represent pages of the same order and should be aggregated rather than quarantined.

## Audit Findings: Batch 30 (Docs 60, 61, 74, 75, 76)

### Document & Brand Overview
Batch 30 covers **5 documents** across two enterprise drilling buyers in Saudi Arabia supplied by **Ibrahim Ali Al-Shab Trading Est** (`AL-SHAB` / `ARS`):
1. **Arabian Drilling Company (ADC)** (Docs 60, 61) — completing **PO Set 19** (`4500051195`, Status: `quarantined`)
2. **Saudi Aramco Nabors Drilling Company (SANAD)** (Docs 74, 75, 76) — forming **PO Set 24** (`18230394`, Status: `quarantined`)

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB Document No | Actual PO (Visual) | Actual Document No (Visual) | Reconciliation Status |
|---|---|---|---|---|---|---|---|---|---|
| **60** | `SIV-ARS-25-8312-pages-3.pdf` | PO | Arabian Drilling (Buyer) | Al-Shab | `4500051195` | `None` | **4500051195** | PO: **4500051195** (p1) | PO Set 19 (`quarantined`) |
| **61** | `SIV-ARS-25-8312-pages-4.pdf` | PO | Arabian Drilling (Buyer) | Al-Shab | `4500051195` | `None` | **4500051195** | PO: **4500051195** (p2) | PO Set 19 (`quarantined`) |
| **74** | `SIV-ARS-25-8322-pages-1.pdf` | SI | Ibrahim Ali Al-Shab | SANAD | `18230394` | `SIV-ARS-25-8322` | **18230394** | **SIV-ARS-25-8322** | PO Set 24 (`quarantined`) |
| **75** | `SIV-ARS-25-8322-pages-2.pdf` | DN | Ibrahim Ali Al-Shab | SANAD | `18230394` | `GDN-ARS-25-9508` | **18230394** | **GDN-ARS-25-9508** | PO Set 24 (`quarantined`) |
| **76** | `SIV-ARS-25-8322-pages-3.pdf` | PO | SANAD (Buyer) | Al-Shab | `18230394` | `None` | **18230394** | PO: **18230394** (p1) | PO Set 24 (`quarantined`) |

---

### Brand Template Analysis: Arabian Drilling & SANAD

#### 1. Arabian Drilling PO Contract Layout (Docs 60 & 61)
- **Geometry**: Portrait layout.
  - Page 1 (Doc 60): PO header with PO number `4500051195`, vendor details, and active line item rows (Lines 10 & 20).
  - Page 2 (Doc 61): Totals summary table (`Gross Price: 83.66 SAR`, `Total P.O. Value: 83.66 SAR`), signature of approval (`Ahmed Mohammed Ali Aldawood`), and general instructions.
- **Prompt Performance**:
  - The model extracted Lines 10 & 20 on page 1 with complete metadata and prices.
  - On page 2 (Doc 61), the prompt correctly obeyed the negative exclusion rules (`EXCLUDE subtotal, VAT, tax, total, amount-in-words, payment terms, signatures...`) and returned `line_items: []` without hallucinating fake lines.

#### 2. SANAD Landscape Purchase Order Format (Doc 76)
- **Geometry**: Landscape orientation with a dense 14-column tabular grid:
  - Header: Large `SANAD` logo with Arabic text, `PO #: 18230394`, `Revision #: 0`.
  - Columns: `Line` | `NIN` | `Supplier Item #` | `OEM Manufacturer` | `OEM Part Number` | `Cancel Flag` | `Promised Date` | `QTY` | `UOM` | `Account Code` | `Unit Price` | `Extended Price` | `Deliver To` | `Shipping Method`.
- **Prompt Performance**:
  - Despite the dense landscape column structure, the VLM parsed all 4 lines on Page 1 (Doc 76) accurately, identifying Line numbers (`1, 2, 3, 4`), descriptions, quantities (`10, 1, 4, 1`), and unit rates (`55, 253, 32, 12 SAR`).

#### 3. Al-Shab Partial Delivery & Invoicing to SANAD (Docs 74 & 75)
- **Geometry**: Al-Shab standard bilingual layout for Delivery Note `GDN-ARS-25-9508` and Invoice `SIV-ARS-25-8322`.
- **Selective Fulfillment**: Al-Shab delivered and invoiced only 3 of the items from the 10-item PO: Line 4 (Contact block, Qty 1), Line 7 (Extension board, Qty 1), Line 9 (Plug, Qty 1).
- **Prompt Performance**: Extracted exact line item numbers `"4"`, `"7"`, `"9"` on both the SI and DN.

---

### Deep Systemic Finding: Repeated False Quarantine on Multi-Page PO Contracts

Both **PO Set 19** and **PO Set 24** were quarantined under the exact same false premise:
> `quarantined: This PO Set holds more than one PO document`

Visual inspection establishes that:
1. **In PO Set 19**: Docs 60 & 61 are Pages 1 & 2 of the **exact same Arabian Drilling Purchase Order `4500051195`**.
   - PO lines 10 & 20 (Doc 60) match Delivery Note Doc 59 and Invoice Doc 58 **100% in quantity and price**.
2. **In PO Set 24**: Doc 76 and Doc 77 are Pages 1 & 2 of the **exact same SANAD Purchase Order `18230394`**.
   - Lines 4, 7, 9 delivered on DN Doc 75 and invoiced on SI Doc 74 match the PO lines **with 100% exact alignment**.
3. **The Core Pipeline Flaw**: When a multi-page PDF is split before extraction, individual pages of the purchase contract receive `doc_type = 'PO'` with identical `po_no_normalized`. The reconciliation engine treats each page as an independent competing contract rather than aggregating them into a single multi-page order.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% classification accuracy on `SI`, `DN`, and landscape/portrait `PO`s.<br>• Clean extraction of dense landscape table on SANAD PO Doc 76.<br>• Correctly returned empty line items on Doc 61 (summary/signature page) without generating spurious items.<br>• Exact line number preservation (`4, 7, 9`) across partial delivery notes and invoices. | `_PAGE_PROMPT` Step 1, Step 3, and Step 4 handled diverse multi-column enterprise PO layouts and partial dockets without degradation. |
| **What was Wrong** | • Ingestion/reconciliation engine flagged PO Set 19 and PO Set 24 as multi-PO conflicts. | Downstream reconciliation logic checks `count(po_documents) > 1` without checking if all PO documents share the exact same PO number. |
| **What was Misrepresented** | • Fully matched deliveries for Arabian Drilling (PO Set 19) and SANAD (PO Set 24) were quarantined as broken sets. | Ingestion splitting separates multi-page PO files into disjoint single-page document records. |
| **What was Missed** | • Nothing missed by extraction prompt in this batch; extraction quality was 100% correct across all 5 files. | Prompt performed cleanly on both portrait and landscape vector layouts. |

---

### Actionable Prompt & System Improvement Insights (Brands: Arabian Drilling, SANAD)

1. **Landscape Multi-Column PO Handling in Prompt**:
   - The prompt handles landscape PO tables (like SANAD) remarkably well when column boundaries are distinct.
   - Maintain instruction:
     `"When encountering multi-column landscape purchase orders (e.g. SANAD), locate the primary 'Line' column, product 'Description', 'QTY', and 'Unit Price' columns, while ignoring administrative columns like 'OEM Part Number', 'Account Code', 'Promised Date', or 'Forwarding Carrier'."`

2. **Reconciliation Engine PO Aggregation Fix**:
   - Update `reconcile.py`:
     ```python
     po_docs = [d for d in po_set.documents if d.doc_type == DocumentType.PO]
     unique_po_numbers = {d.po_no_normalized for d in po_docs if d.po_no_normalized}
     if len(unique_po_numbers) > 1:
         return quarantine("This PO Set holds more than one distinct PO number: " + str(unique_po_numbers))
     # If multiple PO docs share the SAME po_no_normalized, aggregate line items across pages
     ```

## Audit Findings: Batch 31 (Docs 77, 81, 82, 83, 84)

### Document & Brand Overview
Batch 31 covers **5 documents** across two global oilfield enterprise customers serviced by **Ibrahim Ali Al-Shab Trading Est** (`AL-SHAB` / `ARS`):
1. **Saudi Aramco Nabors Drilling Company (SANAD)** (Doc 77) — completing **PO Set 24** (`18230394`, Status: `quarantined`)
2. **Schlumberger Middle East S.A. (SLB)** (Docs 81, 82, 83, 84) — forming **PO Set 26** (`4800466856`, Status: `quarantined`)

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB Document No | Actual PO (Visual) | Actual Document No (Visual) | Reconciliation Status |
|---|---|---|---|---|---|---|---|---|---|
| **77** | `SIV-ARS-25-8322-pages-4.pdf` | PO | SANAD (Buyer) | Al-Shab | `18230394` | `None` | **18230394** | PO: **18230394** (p2) | PO Set 24 (`quarantined`) |
| **81** | `SIV-ARS-25-8332-pages-1.pdf` | SI | Ibrahim Ali Al-Shab | Schlumberger | `4800466856` | `SIV-ARS-25-8332` | **4800466856** | **SIV-ARS-25-8332** | PO Set 26 (`quarantined`) |
| **82** | `SIV-ARS-25-8332-pages-2.pdf` | DN | Ibrahim Ali Al-Shab | Schlumberger | `4800466856` | `GDN-ARS-25-9606` | **4800466856** | **GDN-ARS-25-9606** | PO Set 26 (`quarantined`) |
| **83** | `SIV-ARS-25-8332-pages-3.pdf` | PO | Schlumberger (Buyer) | Al-Shab | `4800466856` | `None` | **4800466856** | PO: **4800466856** (p1) | PO Set 26 (`quarantined`) |
| **84** | `SIV-ARS-25-8332-pages-4.pdf` | PO | Schlumberger (Buyer) | Al-Shab | `4800466856` | `None` | **4800466856** | PO: **4800466856** (p2) | PO Set 26 (`quarantined`) |

---

### Brand Template Analysis: Schlumberger (SLB) & SANAD

#### 1. Schlumberger Enterprise Purchase Order (`SLB`) (Docs 83 & 84)
- **Geometry & Fields**: Clean vector PDF with top-left `slb` logo.
  - Header: `Purchase order number: 4800466856`, `PO Version No: 4800466856-0`, `Purchase order date: 17-NOV-2025`.
  - Table: `Item` | `Vendor Part No. / SLB Part No - Description` | `Final destination` | `Quantity` | `UOM` | `Packaging Quantity` | `Unit Net price SAR` | `Net price SAR` | `Delivery Date`.
  - Nested Item Text: Multi-line technical item text sits directly beneath each product row (e.g. `Item text: Thread Compound TUBELOK...`).
- **Prompt Performance**:
  - The model extracted Lines 1–4 on Page 1 (Doc 83) and Lines 5–6 on Page 2 (Doc 84) with 100% precision.
  - Successfully concatenated the nested item text into the description without dropping quantities or unit prices.

#### 2. SANAD Landscape Purchase Order Page 2 (Doc 77)
- **Geometry**: Landscape table continuation.
  - Header: `Header Information: PO 18230394`, `PURCHASE ORDER`, `Page 2 of 2`.
  - Rows: Lines 5, 6, 7, 8, 9 with quantities, UOMs, and unit prices in SAR.
- **Prompt Performance**:
  - Parsed all 5 remaining lines flawlessly, completing the full PO line catalog for PO Set 24.

---

### Deep Systemic Finding: 100% Valid Reconciled Sets Trapped in False Quarantine

Both **PO Set 24** and **PO Set 26** are physically **flawless, verified 3-way matches** that are falsely quarantined:

1. **PO Set 24 (SANAD `18230394`)**:
   - Invoice Doc 74 billed Lines 4 (Qty 1), 7 (Qty 1), 9 (Qty 1).
   - Delivery Note Doc 75 delivered Lines 4 (Qty 1), 7 (Qty 1), 9 (Qty 1) (stamped by SANAD Central Warehouse).
   - PO Doc 76 (p1) ordered Line 4 (Qty 1); PO Doc 77 (p2) ordered Line 7 (Qty 1) and Line 9 (Qty 1).
   - **PO = DN = SI across all billed items**. The set is quarantined solely because the PO was 2 pages.

2. **PO Set 26 (Schlumberger `4800466856`)**:
   - PO Docs 83 & 84 ordered 6 items (Line 1: 100 Tubelok @ 240 SAR; Line 2: 25 Dry Moly @ 600 SAR; Lines 3–6 unfulfilled).
   - Invoice Doc 81 billed Lines 1 (100) & 2 (25) for 39,000 SAR net.
   - Delivery Note Doc 82 delivered Lines 1 (100) & 2 (25) (stamped by SLB receiving).
   - **PO = DN = SI across all billed items**. The set is quarantined solely because the PO was 2 pages.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% extraction accuracy on Schlumberger POs (Docs 83 & 84), including nested `Item text` lines.<br>• Clean handling of SANAD landscape table continuation (Doc 77).<br>• Accurate extraction of partial dockets on Al-Shab SI/DN (Docs 81 & 82). | `_PAGE_PROMPT` Step 1, Step 3, and Step 4 handled multi-line descriptions and price tables with zero error. |
| **What was Wrong** | • Pipeline treated multi-page PO files as conflicting PO documents, quarantining PO Set 24 and PO Set 26. | Ingestion layer split multi-page PO contracts into multiple single-page records, triggering downstream `po_doc_count > 1` rule. |
| **What was Misrepresented** | • Both PO Set 24 and PO Set 26 were marked `quarantined` as invalid/conflicting sets, when both represent 100% legitimate partial deliveries. | Lack of logical PO aggregation in reconciliation engine. |
| **What was Missed** | • Nothing missed by extraction prompt in this batch. | High contrast vector PDF layouts processed cleanly. |

---

### Actionable Prompt Improvement Insights (Brand: Schlumberger / SANAD)

1. **Nested Item Text Rule in Enterprise PO Tables**:
   - Maintain in `_PAGE_PROMPT` Step 3:
     `"When purchase orders place detailed product specifications in an 'Item text' block beneath the main table row (e.g. Schlumberger POs), append the Item text to the main description so that line items have full description fidelity."`

2. **PO Aggregation Rule**:
   - Multiple PO pages sharing the same `po_no_normalized` must be merged into a single logical PO contract.

# Audit Findings — Batch 32 (Docs 194 - 198)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 194`: `SIV-RAK-25-2937-Northern-1.pdf` (Sales Invoice / SI) — PO Set 54 (`quarantined`)
  - `Doc 195`: `SIV-RAK-25-2937-Northern-2.pdf` (Delivery Note / DN) — PO Set 54 (`quarantined`)
  - `Doc 196`: `SIV-RAK-25-2937-Northern-3.pdf` (Purchase Order / PO, 2 pages) — PO Set 54 (`quarantined`)
  - `Doc 197`: `SIV-RAK-25-2938-Northern-1.pdf` (Sales Invoice / SI) — PO Set 55 (`quarantined` pending PO)
  - `Doc 198`: `SIV-RAK-25-2938-Northern-2.pdf` (Delivery Note / DN) — PO Set 55 (`quarantined` pending PO)
- **Primary Companies/Vendors**:
  - **Vendor**: RAK ENERGY SERVICES & SUPPLIES WLL (GWC Logistics Park C, Al Wukair Store B3 A9-05, Qatar)
  - **Client / Operator**: Northern Offshore Drilling Operations Ltd. (Doha, Qatar) / Rig: "ENERGY EMBRACER"

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 194: `SIV-RAK-25-2937-Northern-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Header: `RAK ENERGY SERVICES & SUPPLIES WLL`, `rak energy` logo top right
  - Invoice No: `SIV-RAK-25-2937`
  - Buyer's Order No: `90139625`
  - Delivery Note: `GDN-RAK-25-3108`
  - Date: `28/10/2025`
  - Client: `Northern Offshore Drilling Operations Ltd.`
- **Visual Line Items**:
  - Line 1: `SL 1`, Description: `OPTIMA Red Top (RT S 4.2), Model No: 8002-250, Part No: 802 250 000 888 2 \n Line Item - 1`, Qty: `1.00 EA`, Rate: `850.00`, Amount: `850.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139625` (Correct)
  - `si_no`: `SIV-RAK-25-2937` (Correct)
  - Line 1: Line Item `1`, Qty `1.0`, Unit Price `850.0` (Correct)

### Doc 195: `SIV-RAK-25-2937-Northern-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3108`
  - Buyers Order No: `90139625`
  - Date: `25/10/2025`
- **Visual Line Items**:
  - Line 1: `SL 1`, Description: `OPTIMA Red Top (RT S 4.2), Model No: 8002-250, Part No: 802 250 000 888 2 \n Line Item - 1`, Qty: `1.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139625` (Correct)
  - `dn_no`: `GDN-RAK-25-3108` (Correct)
  - Line 1: Line Item `1`, Qty `1.0`, Unit Price `0.0` (Correct)

### Doc 196: `SIV-RAK-25-2937-Northern-3.pdf` (2 Pages)
- **Visual Document Type**: `PO` (Title: `PURCHASE ORDER`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - Buyer: `Northern Offshore Drilling Operations Ltd.` (SAP PO form)
  - Purchase Order No: `90139625`
  - Order Date: `22.10.2025`
  - Vendor: `RAK ENERGY SERVICES & SUPPLIES WLL` (Vendor No: `200424`)
- **Visual Line Items**:
  - Item: `10`
  - Material / Description: `BATTERY, SEALED LEAD ACID, SPIRAL WOUND, MODEL RT S 4.2, 12 VOLTS... OPTIMA`, Qty: `1 EA`, Net Price: `850.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139625` (Correct)
  - Line 1: Line Item `10`, Qty `1.0`, Unit Price `850.0` (Extracted faithfully from PO)
- **Reconciliation Failure Cause in PO Set 54**:
  - PO extracted line `10`.
  - SI and DN extracted line `1` (from RAK Energy's serial column `SL 1`).
  - Result: Discrepancy between Line `1` and Line `10` caused PO Set 54 to quarantine (`Reconciliation mismatch`).
  - Notice on RAK Energy's SI and DN description: The vendor explicitly wrote `Line Item - 1` inside their description text!

### Doc 197: `SIV-RAK-25-2938-Northern-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-2938`
  - Buyer's Order No: `90139670`
  - Delivery Note: `GDN-RAK-25-3109`
  - Rig No: `ENERGY EMBRACER`
- **Visual Line Items**:
  - SL 1: `Bearing 62213 2RS CHINA \n Line Item - 1`, Qty `4.00 EA`, Rate `97.00`
  - SL 2: `Bearing 62211 2RS TIMKEN \n Line Item - 2`, Qty `4.00 EA`, Rate `188.00`
  - SL 3: `Oil Seal 75 X 95 X 10mm \n Line Item - 3`, Qty `6.00 EA`, Rate `30.25`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139670` (Correct)
  - `si_no`: `SIV-RAK-25-2938` (Correct)
  - Line 1: Line `1`, Qty `4.0`, Unit Price `97.0` (Correct)
  - Line 2: Line `2`, Qty `4.0`, Unit Price `188.0` (Correct)
  - Line 3: Line `3`, Qty `6.0`, Unit Price `30.25` (Correct)

### Doc 198: `SIV-RAK-25-2938-Northern-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3109`
  - Buyers Order No: `90139670`
  - Total Qty: `14.00`
- **Visual Line Items**:
  - SL 1: `Bearing 62213 2RS CHINA \n Line Item - 1`, Qty `4.00 EA`
  - SL 2: `Bearing 62211 2RS TIMKEN \n Line Item - 2`, Qty `4.00 EA`
  - SL 3: `Oil Seal 75 X 95 X 10mm \n Line Item - 3`, Qty `6.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139670` (Correct)
  - `dn_no`: `GDN-RAK-25-3109` (Correct)
  - All 3 line items extracted with 100% precision.

---

## 3. Brand & Document Diagnostics: RAK Energy vs Northern Offshore Drilling

| Attribute | Visual Feature on Document | Prompt Engineering Implication |
|---|---|---|
| **Company Identity** | Header left: `RAK ENERGY SERVICES & SUPPLIES WLL`<br>Logo right: modern teal/blue lowercase `rak energy` | Clear vendor identifier. |
| **Doc Types** | Centered bold black title: `SALES INVOICE` vs `DELIVERY NOTE` | 100% unambiguous document classification. |
| **Metadata Box** | Right hand structured table block:<br>`Invoice No : SIV-RAK-25-XXXX`<br>`Buyer's Order No : 90XXXXXX`<br>`Delivery Note : GDN-RAK-25-XXXX` | Buyer's Order No is the PO number. Always labeled `Buyer's Order No :` or `Buyers Order No :`. |
| **Line Numbering Peculiarity** | Vendor prints `SL` (Serial 1, 2, 3...) in table column 1, BUT also appends `Line Item - X` in the description block. Client's SAP PO uses Step-10 (`Item 10`, `Item 20`, `Item 30`). | **CRITICAL INSIGHT FOR PROMPT**: On Northern Offshore Drilling / SAP POs, line items are step-10 (`10, 20, 30`). If a vendor invoice writes serial `1, 2, 3`, prompt instructions must guide extraction to normalize or preserve the buyer's line reference if present in description. |

# Audit Findings — Batch 33 (Docs 199 - 203)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 199`: `SIV-RAK-25-2938-Northern-3.pdf` (Purchase Order / PO, 3 pages) — PO Set 55 (`quarantined`)
  - `Doc 200`: `SIV-RAK-25-2940-Northern-1.pdf` (Sales Invoice / SI) — PO Set 56 (`quarantined`)
  - `Doc 201`: `SIV-RAK-25-2940-Northern-2.pdf` (Delivery Note / DN) — PO Set 56 (`quarantined`)
  - `Doc 202`: `SIV-RAK-25-2940-Northern-3.pdf` (Purchase Order / PO, 3 pages) — PO Set 56 (`quarantined`)
  - `Doc 203`: `SIV-RAK-25-2941-Northern-1.pdf` (Sales Invoice / SI) — PO Set 57 (`quarantined` pending DN/PO)
- **Primary Companies/Vendors**:
  - **Vendor**: RAK ENERGY SERVICES & SUPPLIES WLL (Qatar)
  - **Buyer / Client**: Northern Offshore Drilling Operations SPC / Ltd. (Rigs: `Energy Embracer`, `Energy Enticer`)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 199: `SIV-RAK-25-2938-Northern-3.pdf` (3 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order : 90139670 Rev : 1`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `90139670`
  - Buyer: `Northern Offshore Drilling Operations SPC`
  - Supplier: `RAK ENERGY SERVICES & SUPPLIES`
  - Rig Name: `209 - Energy Embracer`
- **Visual Line Items**:
  - Line `10`: `BEARING, DEEP GROOVE, INNDER DIA: 65MM...`, Qty `4 EA`, Unit Price `97.00 QAR`
  - Line `20`: `BEARING, INNDER DIA: 55MM...`, Qty `4 EA`, Unit Price `188.00 QAR`
  - Line `30`: `OIL SEAL, DOUBLE LIP... SIZE: 75MM x 95MM x 10 MM`, Qty `6 EA`, Unit Price `30.25 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139670` (Correct)
  - Line items: Exactly extracted Lines `10`, `20`, `30` with Qties `4.0`, `4.0`, `6.0` and unit prices `97.0`, `188.0`, `30.25`.
- **Root Cause of Set 55 Quarantine**:
  - Reconciles Doc 197 (SI), Doc 198 (DN), and Doc 199 (PO).
  - All items, quantities, and prices are **100% IDENTICAL**.
  - However, RAK Energy invoices and delivery notes label items sequentially `1, 2, 3`, whereas Northern Offshore PO labels them `10, 20, 30`.
  - Because AAM V3 reconciliation requires strict line item number matching, Line 1 != Line 10 caused a false quarantine!

### Doc 200: `SIV-RAK-25-2940-Northern-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-2940`
  - Buyer's Order No: `90139566`
  - Delivery Note: `GDN-RAK-25-3127`
  - Rig No: `ENERGY ENTICER`
- **Visual Line Items**:
  - SL `1`: Description: `5" Angle Grinder, Ingersoll Rand, P/N : 3445MAX \n Line Item - 2`, Qty `3.00 EA`, Rate `1,650.00`
  - SL `2`: Description: `Mighty Severn M7_Air Pistol Needle Scaler 3500 B.P.M 'Sn-2128 \n Line Item - 3`, Qty `3.00 EA`, Rate `1,532.00`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139566` (Correct)
  - `si_no`: `SIV-RAK-25-2940` (Correct)
  - The model extracted Line `2` (Qty 3.0, Rate 1650.0) and Line `3` (Qty 3.0, Rate 1532.0).

### Doc 201: `SIV-RAK-25-2940-Northern-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3127`
  - Buyers Order No: `90139566`
  - Date: `25/10/2025`
- **Visual Line Items**:
  - SL `1`: `5" Angle Grinder... Line Item - 2`, Qty `3.00 EA`
  - SL `2`: `Mighty Severn M7... Line Item - 3`, Qty `3.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139566` (Correct)
  - `dn_no`: `GDN-RAK-25-3127` (Correct)
  - Extracted Line `2` and Line `3` with Qty `3.0`.

### Doc 202: `SIV-RAK-25-2940-Northern-3.pdf` (3 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order : 90139566 Rev : 1`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - Purchase Order: `90139566`
  - Buyer: `Northern Offshore Drilling Ops Ltd.`
  - Rig: `210 - Energy Enticer`
- **Visual Line Items**:
  - Line `10`: `NEEDLE, SCALER, REPLACEMENT SET... PACK OF 100`, Qty `3 BOX`, Price `190.00 QAR`
  - Line `20`: `GRINDER, ANGLE, AIR, 0.88 HP...`, Qty `3 EA`, Price `1,650.00 QAR`
  - Line `30`: `NEEDLE SCALER, AIR...`, Qty `3 EA`, Price `1,532.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139566` (Correct)
  - Extracted Lines `10`, `20`, `30` perfectly.
- **Root Cause of Set 56 Quarantine**:
  - PO contains items `10`, `20`, `30`.
  - SI and DN represent a partial delivery containing only items `20` and `30` (labeled by vendor as items `2` and `3`).
  - Item `10` is missing from the invoice/delivery note, causing legitimate quantity variance, plus the step-10 line indexing discrepancy (`2` vs `20`, `3` vs `30`).

### Doc 203: `SIV-RAK-25-2941-Northern-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-2941`
  - Buyer's Order No: `90139734`
  - Delivery Note: `GDN-RAK-25-3107`
  - Rig No: `ENERGY ENTICER`
- **Visual Line Items**:
  - SL `1`: `Heavy-duty TIG welding gloves, Ansell, P/N : ACTIVARMR 43-217 \n Line Item - 1`, Qty `12.00 EA`, Rate `33.00`, Amount `396.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139734` (Correct)
  - `si_no`: `SIV-RAK-25-2941` (Correct)
  - Line 1: Qty `12.0`, Price `33.0` (Correct)

---

## 3. Brand & Prompt Engineering Diagnostics

1. **Step-10 Alignment Rule**:
   - On Northern Offshore Drilling contracts (and common SAP systems like ADES, ARO, SLB), PO line numbers increment by 10 (`10, 20, 30...`).
   - Smaller suppliers (e.g. RAK Energy) create invoices in QuickBooks/Tally numbering `1, 2, 3...`, but often embed `Line Item - X` in the description.
   - **Prompt Guidance**: When extracting line items from vendor invoices/DNs referencing a buyer order, if the vendor embeds `Line Item - N` or `PO Line N`, extract `N` (or `N*10` if matching SAP convention) or provide secondary alignment hints.
2. **Partial Shipment Identification**:
   - In PO Set 56, only 2 of 3 PO items were shipped. The system correctly flagged the discrepancy, proving that the extraction captured the partial state faithfully.

# Audit Findings — Batch 34 (Docs 204 - 208)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 204`: `SIV-RAK-25-2941-Northern-2.pdf` (Delivery Note / DN) — PO Set 57 (`quarantined`)
  - `Doc 205`: `SIV-RAK-25-2941-Northern-3.pdf` (Purchase Order / PO, 2 pages) — PO Set 57 (`quarantined`)
  - `Doc 206`: `SIV-RAK-25-2942-Northern-1.pdf` (Sales Invoice / SI) — PO Set 58 (`quarantined`)
  - `Doc 207`: `SIV-RAK-25-2942-Northern-2.pdf` (Delivery Note / DN) — PO Set 58 (`quarantined`)
  - `Doc 208`: `SIV-RAK-25-2942-Northern-3.pdf` (Purchase Order / PO, 2 pages) — PO Set 58 (`quarantined`)
- **Primary Companies/Vendors**:
  - **Vendor**: RAK ENERGY SERVICES & SUPPLIES WLL (Qatar)
  - **Buyer / Client**: Northern Offshore Drilling Operations Ltd. (Rig: `Energy Enticer`)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 204: `SIV-RAK-25-2941-Northern-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3107`
  - Buyers Order No: `90139734`
  - Date: `25/10/2025`
  - Total Qty: `12.00`
- **Visual Line Items**:
  - SL `1`: `Heavy-duty TIG welding gloves, Ansell, P/N : ACTIVARMR 43-217 \n Line Item - 1`, Qty `12.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139734` (Correct)
  - `dn_no`: `GDN-RAK-25-3107` (Correct)
  - Line 1: Qty `12.0`, Price `0.0` (Correct)

### Doc 205: `SIV-RAK-25-2941-Northern-3.pdf` (2 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order : 90139734 Rev : 1`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - Purchase Order: `90139734`
  - Order Date: `10/22/2025`
  - Rig: `210 - Energy Enticer`
- **Visual Line Items**:
  - Line `10`: `GLOVES WELDING, TIG GLOVES... (6 PAIR IN BOX)`, Qty `12 EA`, Price `33.00 QAR`, Total `396 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139734` (Correct)
  - Line 10: Qty `12.0`, Unit Price `33.0` (Correct)
- **Root Cause of Set 57 Quarantine**:
  - Doc 203 (SI) has Line `1` (Qty 12.0, Rate 33.0).
  - Doc 204 (DN) has Line `1` (Qty 12.0).
  - Doc 205 (PO) has Line `10` (Qty 12.0, Rate 33.0).
  - Quantities and prices match 100% perfectly. Quarantine was triggered strictly by `Line 1` vs `Line 10` step-10 indexing mismatch.

### Doc 206: `SIV-RAK-25-2942-Northern-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-2942`
  - Buyer's Order No: `90139766`
  - Delivery Note: `GDN-RAK-25-3105`
  - Rig No: `ENERGY ENTICER`
- **Visual Line Items**:
  - SL `1`: `BATTERY AAA ENERGIZER ( 24/PKT ) \n Line Item - 1`, Qty `19.00 PKT`, Rate `64.25`, Amount `1,220.75 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139766` (Correct)
  - `si_no`: `SIV-RAK-25-2942` (Correct)
  - Line 1: Qty `19.0`, Price `64.25` (Correct)

### Doc 207: `SIV-RAK-25-2942-Northern-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3105`
  - Buyers Order No: `90139766`
  - Date: `23/10/2025`
- **Visual Line Items**:
  - SL `1`: `BATTERY AAA ENERGIZER ( 24/PKT ) \n Line Item - 1`, Qty `19.00 PKT`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139766` (Correct)
  - `dn_no`: `GDN-RAK-25-3105` (Correct)
  - Line 1: Qty `19.0`, Price `0.0` (Correct)

### Doc 208: `SIV-RAK-25-2942-Northern-3.pdf` (2 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order : 90139766 Rev : 1`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - Purchase Order: `90139766`
  - Buyer: `Northern Offshore Drilling Operations Ltd.`
  - Rig: `210 - Energy Enticer`
- **Visual Line Items**:
  - Line `10`: `Duracell Alkaline AAA BATTERY, AAA, PACK OF 24`, Qty `32 PAC`, Price `64.25 QAR`, Total `2,056 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139766` (Correct)
  - Line 10: Qty `32.0`, Price `64.25` (Correct)
- **Root Cause of Set 58 Quarantine**:
  - Authentic partial delivery: PO ordered 32 packs, but vendor delivered and invoiced 19 packs (pending 13 packs).
  - Additionally compounded by Line `1` vs Line `10` indexing difference.

---

## 3. Brand & Prompt Engineering Diagnostics

1. **Extraction Fidelity**:
   - Both RAK Energy's invoice/delivery formats and Northern Offshore's SAP purchase order forms have 100% extraction precision on all metadata and financial fields.
2. **Reconciliation Alignment**:
   - The consistent `Line 1` on SI/DN vs `Line 10` on Northern Offshore PO across Sets 54, 55, 57, and 58 confirms that SAP step-10 numbering requires an explicit alignment rule in V3 reconciliation (e.g. matching `Line 1` to `Line 10` when there is only one item, or mapping vendor serial `1, 2, 3` to `10, 20, 30` when `Line Item - N` is present).

# Audit Findings — Batch 35 (Docs 215 - 217, 224, 225)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 215`: `SIV-RAK-25-2968-Noble-1.pdf` (Sales Invoice / SI) — PO Set 61 (`quarantined`)
  - `Doc 216`: `SIV-RAK-25-2968-Noble-2.pdf` (Delivery Note / DN) — PO Set 61 (`quarantined`)
  - `Doc 217`: `SIV-RAK-25-2968-Noble-3.pdf` (Purchase Order / PO, 13 pages) — PO Set 61 (`quarantined`)
  - `Doc 224`: `SIV-RAK-25-3058-Ensco-1.pdf` (Sales Invoice / SI) — PO Set 64 (`quarantined` pending PO)
  - `Doc 225`: `SIV-RAK-25-3058-Ensco-2.pdf` (Delivery Note / DN) — PO Set 64 (`quarantined` pending PO)
- **Primary Companies/Vendors**:
  - **Vendor**: RAK ENERGY SERVICES & SUPPLIES WLL (Qatar)
  - **Clients**:
    - Noble Drilling Doha LLC (Rig: `Noble Mick O'Brien`)
    - Ensco Rowan / Valaris (Rig: `Valaris 110`)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 215: `SIV-RAK-25-2968-Noble-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-2968`
  - Buyer's Order No: `300101295`
  - Delivery Note: `GDN-RAK-25-3130`
  - Client: `NOBLE DRILLING DOHA LLC`
- **Visual Line Items**:
  - SL `1`: `Square D Power pact JJ250 3-Pole Circuit Breaker - New-Open Box \n Line Item - 19`, Qty `1.00 EA`, Rate `6,325.00 QAR`
  - SL `2`: `RELAY BOARD, MOVINCOOL, P/N : 484500-5320 \n Line Item - 21`, Qty `1.00 EA`, Rate `1,705.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `300101295` (Correct)
  - `si_no`: `SIV-RAK-25-2968` (Correct)
  - Extracted Line `19` and Line `21` faithfully from the description's `Line Item - X` label.

### Doc 216: `SIV-RAK-25-2968-Noble-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3130`
  - Buyers Order No: `300101295`
  - Rig / Location: `MICK OBRIEN`
- **Visual Line Items**:
  - SL `1`: `... Line Item - 19`, Qty `1.00 EA`
  - SL `2`: `... Line Item - 21`, Qty `1.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `300101295` (Correct)
  - `dn_no`: `GDN-RAK-25-3130` (Correct)
  - Extracted Line `19` and Line `21` (Correct).

### Doc 217: `SIV-RAK-25-2968-Noble-3.pdf` (13 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order No. 300101295`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - Buyer: `Noble Drilling Doha LLC`
  - PO Number: `300101295`
  - Supplier: `RAK ENERGY SERVICES AND SUPPLIES`
  - Rig: `NOBLE MICK O'BRIEN`
- **Visual Line Items**:
  - A massive 13-page purchase order containing 25 distinct line items ranging from electrical elements to filters, welding electrodes, and duct tape.
  - Line 22: `BREAKER, CIRCUIT 250A 3P POWERPACT JJ250`, Qty `1.0`, Price `6325.0`
  - Line 24: `RELAY, BOARD; Mfg Name: MOVINCOOL`, Qty `1.0`, Price `1705.0`
- **DB Extracted Items**:
  - Extracted 22 line items across the entire 13 pages.
- **Root Cause of Set 61 Quarantine**:
  - Partial delivery/billing: PO has 25 line items, while vendor invoice/DN only bills 2 items.
  - Furthermore, vendor referred to them as `Line Item - 19` and `Line Item - 21` (off-by-two from PO's internal indices 22 and 24).
  - Genuine quarantine under business logic (unfulfilled PO balance).

### Doc 224: `SIV-RAK-25-3058-Ensco-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-3058`
  - Buyer's Order No: `10006-0000079633` (Normalized: `100060000079633`)
  - Client: `ENSCO ROWAN` / Rig: `VALARIS 110`
- **Visual Line Items**:
  - SL `1`: `CONDOR Sideshield: Clear, PVC, Slip-On, P/N : 3BE76 \n Line Item - 1`, Qty `46.00 EA`, Rate `25.00`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079633` (Correct)
  - `si_no`: `SIV-RAK-25-3058` (Correct)
  - Line 1: Qty `46.0`, Price `25.0` (Correct)

### Doc 225: `SIV-RAK-25-3058-Ensco-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3203`
  - Buyers Order No: `10006-0000079633`
- **Visual Line Items**:
  - SL `1`: `CONDOR Sideshield... Line Item - 1`, Qty `46.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079633` (Correct)
  - `dn_no`: `GDN-RAK-25-3203` (Correct)
  - Line 1: Qty `46.0` (Correct)

---

## 3. Brand & Prompt Diagnostics

| Entity | Form Characteristics | Prompt Engineering Rule |
|---|---|---|
| **Noble Drilling** | Modern corporate PO, yellow/blue oval logo, right top `Purchase Order No. 300101295`. Multi-page tabular layout with lines labeled `1, 2, 3...`. | Extract `Purchase Order No.` exactly. Multi-page POs require full pagination accumulation without dropping middle pages. |
| **Ensco Rowan (Valaris)** | Client references `10006-0000079633`. Clean layout. | Strip hyphens during PO normalization (`100060000079633`). |

# Audit Findings — Batch 36 (Docs 226 - 230)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 226`: `SIV-RAK-25-3058-Ensco-3.pdf` (Purchase Order / PO, 2 pages) — PO Set 64 (`quarantined`)
  - `Doc 227`: `SIV-RAK-25-3059-Ensco-1.pdf` (Sales Invoice / SI) — PO Set 65 (`quarantined`)
  - `Doc 228`: `SIV-RAK-25-3059-Ensco-2.pdf` (Delivery Note / DN) — PO Set 65 (`quarantined`)
  - `Doc 229`: `SIV-RAK-25-3059-Ensco-3.pdf` (Purchase Order / PO, 2 pages) — PO Set 65 (`quarantined`)
  - `Doc 230`: `SIV-RAK-25-3060-Ensco-1.pdf` (Sales Invoice / SI) — PO Set 66 (`quarantined` pending DN/PO)
- **Primary Companies/Vendors**:
  - **Vendor**: RAK ENERGY SERVICES & SUPPLIES WLL (Qatar)
  - **Buyer / Client**: Ensco Offshore International Company / Valaris (Rig: `Valaris 110`)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 226: `SIV-RAK-25-3058-Ensco-3.pdf` (2 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order`, Logo: `Valaris`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `10006-0000079633`
  - Buyer: `Ensco Offshore International Company`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - Line `1-1`: `SIDESHIELD-EYEWEAR,TITMUS,CS98 ,T-LOC`, Qty `50.00 EA`, Price `25.00 QAR`, Total `1,250.00`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079633` (Correct)
  - Line `1-1`: Qty `50.0`, Price `25.0` (Extracted raw line string `1-1`)
- **Set 64 Quarantine Analysis**:
  - Doc 224 (SI) and Doc 225 (DN) delivered `46.0 EA` at `25.0 QAR` under Line `1`.
  - PO ordered `50.0 EA` under Line `1-1`.
  - Quarantine is justified due to a 4-unit quantity shortfall (46 delivered vs 50 ordered), compounded by the line key mismatch (`1` vs `1-1`).

### Doc 227: `SIV-RAK-25-3059-Ensco-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-3059`
  - Buyer's Order No: `10006-0000079657`
  - Delivery Note: `GDN-RAK-25-3237`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - SL `1`: `SAE J429 Grade 8 Ø1 1/4" (CADMIUM PLATED)... \n Line Item - 1`, Qty `20.00 EA`, Rate `40.00 QAR`, Amount `800.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079657` (Correct)
  - `si_no`: `SIV-RAK-25-3059` (Correct)
  - Line 1: Qty `20.0`, Price `40.0` (Correct)

### Doc 228: `SIV-RAK-25-3059-Ensco-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3237`
  - Buyers Order No: `10006-0000079657`
- **Visual Line Items**:
  - SL `1`: `SAE J429 Grade 8... Line Item - 1`, Qty `20.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079657` (Correct)
  - `dn_no`: `GDN-RAK-25-3237` (Correct)
  - Line 1: Qty `20.0` (Correct)

### Doc 229: `SIV-RAK-25-3059-Ensco-3.pdf` (2 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order`, Logo: `Valaris`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `10006-0000079657`
  - Buyer: `Ensco Offshore International Company`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - Line `1-1`: `BOLT-HEX,1-1/4-7 X 4-3/4 IN... SAE J429,GR 8`, Qty `20.00 EA`, Price `40.00 QAR`, Total `800.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079657` (Correct)
  - Line `1-1`: Qty `20.0`, Price `40.0` (Correct)
- **CRITICAL RECONCILIATION FINDING IN PO SET 65**:
  - PO: 20 EA at 40.0 QAR
  - SI: 20 EA at 40.0 QAR
  - DN: 20 EA delivered
  - **100% PERFECT MATCH** across all goods, quantities, and monetary values.
  - The **SOLE REASON** PO Set 65 was quarantined: Valaris PO line is formatted as `1-1` (Line-Schedule release), while SI and DN extracted `1`. Because strict string equality compares `"1"` to `"1-1"`, reconciliation failed!

### Doc 230: `SIV-RAK-25-3060-Ensco-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-3060`
  - Buyer's Order No: `10006-0000079469`
  - Delivery Note: `GDN-RAK-25-3183`
  - Rig No: `VALARIS 110`
- **Visual Line Items**:
  - SL `1`: `Bell Ear Plugs, 33dB... HONEYWELL HOWARD LEIGHT... \n Line Item - 3`, Qty `5.00 BOX`, Rate `210.00 QAR`, Amount `1,050.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079469` (Correct)
  - `si_no`: `SIV-RAK-25-3060` (Correct)
  - Line `3`: Qty `5.0`, Price `210.0` (Extracted from `Line Item - 3`)

---

## 3. Brand & Prompt Engineering Diagnostics: Valaris / Ensco

| Attribute | Visual Feature on Document | Prompt Engineering Implication |
|---|---|---|
| **Company Identity** | Header left: bold `Valaris`. Barcode block top right with PO number underneath. | Modern enterprise purchase order. |
| **PO Numbering** | `10006-0000079657` with hyphen. | Normalization correctly strips hyphen to `100060000079657`. |
| **Line Numbering Pattern** | Column header `Line` displays values in `Line-Release` format: `1-1`, `2-1`, `3-1`. | **MAJOR PROMPT RULE**: When extracting line numbers from Valaris / Ensco purchase orders, normalize `X-Y` by taking the base line index `X` (e.g. `1-1` → `1`), or normalize during reconciliation. Normalizing `1-1` to `1` instantly saves PO Set 65 and allows clean auto-merging! |

# Audit Findings — Batch 37 (Docs 231, 232, 242, 243, 244)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 231`: `SIV-RAK-25-3060-Ensco-2.pdf` (Delivery Note / DN) — PO Set 66 (`quarantined`)
  - `Doc 232`: `SIV-RAK-25-3060-Ensco-3.pdf` (Purchase Order / PO, 3 pages) — PO Set 66 (`quarantined`)
  - `Doc 242`: `SIV-RAK-25-3130-Ensco-1.pdf` (Sales Invoice / SI) — PO Set 70 (`quarantined`)
  - `Doc 243`: `SIV-RAK-25-3130-Ensco-2.pdf` (Delivery Note / DN) — PO Set 70 (`quarantined`)
  - `Doc 244`: `SIV-RAK-25-3130-Ensco-3.pdf` (Purchase Order / PO, 2 pages) — PO Set 70 (`quarantined`)
- **Primary Companies/Vendors**:
  - **Vendor**: RAK ENERGY SERVICES & SUPPLIES WLL (Qatar)
  - **Buyer / Client**: Ensco Offshore International Company / Valaris (Rig: `Valaris 110`)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 231: `SIV-RAK-25-3060-Ensco-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3183`
  - Buyers Order No: `10006-0000079469`
  - Total Qty: `5.00`
- **Visual Line Items**:
  - SL `1`: `Bell Ear Plugs, 33dB... HONEYWELL HOWARD LEIGHT... \n Line Item - 3`, Qty `5.00 BOX`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079469` (Correct)
  - `dn_no`: `GDN-RAK-25-3183` (Correct)
  - Line `3`: Qty `5.0` (Correct)

### Doc 232: `SIV-RAK-25-3060-Ensco-3.pdf` (3 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order`, Logo: `Valaris`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `10006-0000079469`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - Line `1-1`: `LUBRICANT, SILICONE, 100 GRAM`, Qty `24.00 EA`, Price `105.00`
  - Line `2-1`: `GAS-CALIBRATION, CALGAZ...`, Qty `1.00 EA`, Price `935.00`
  - Line `3-1`: `EARPLUGS, HOWARD LEIGHT, MAX-30...`, Qty `5.00 EA`, Price `210.00`
  - Line `4-1`: `LAMP-FLUORESCENT...`, Qty `100.00 EA`, Price `3.75`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079469` (Correct)
  - Extracted Lines `1-1`, `2-1`, `3-1`, `4-1` with 100% numerical fidelity.
- **Set 66 Quarantine Explanation**:
  - Partial delivery: Only Item 3 (`3-1`) was delivered and invoiced (Qty 5.0).
  - Legitimate quarantine due to open items 1, 2, and 4 remaining undelivered.

### Doc 242: `SIV-RAK-25-3130-Ensco-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-3130`
  - Buyer's Order No: `10006-0000079573`
  - Delivery Note: `GDN-RAK-25-3299`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - SL `1`: `StarTech.com USB 2.0 USB A Male to RS232... \n Line Item - 1`, Qty `1.00 EA`, Rate `426.00 QAR`, Amount `426.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079573` (Correct)
  - `si_no`: `SIV-RAK-25-3130` (Correct)
  - Line 1: Qty `1.0`, Price `426.0` (Correct)

### Doc 243: `SIV-RAK-25-3130-Ensco-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3299`
  - Buyers Order No: `10006-0000079573`
- **Visual Line Items**:
  - SL `1`: `StarTech.com USB 2.0... \n Line Item - 1`, Qty `1.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079573` (Correct)
  - `dn_no`: `GDN-RAK-25-3299` (Correct)
  - Line 1: Qty `1.0` (Correct)

### Doc 244: `SIV-RAK-25-3130-Ensco-3.pdf` (2 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order`, Logo: `Valaris`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `10006-0000079573`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - Line `1-1`: `CABLE-ADAPTER,STARTECH,1P10FFCN-USB-SERIAL...`, Qty `1.00 EA`, Unit Price `426.00 QAR`, Amount `426.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079573` (Correct)
  - Line `1-1`: Qty `1.0`, Price `426.0` (Correct)
- **CRITICAL RECONCILIATION FINDING IN PO SET 70**:
  - PO: 1 EA at 426.00 QAR
  - SI: 1 EA at 426.00 QAR
  - DN: 1 EA delivered
  - All numbers, values, and items match with 100% mathematical precision.
  - Set 70 was quarantined solely because PO line is string `"1-1"` while SI/DN line is string `"1"`.

---

## 3. Brand & Prompt Engineering Diagnostics

1. **Systematic False Quarantine Mechanism Identified**:
   - Both Set 65 and Set 70 are single-item Valaris purchase orders fulfilled completely in one delivery and invoice.
   - Both sets failed auto-merge because Valaris ERP generates line identifiers with a release suffix (`1-1`, `2-1`, `3-1`), whereas vendors invoice them as line `1`, `2`, `3`.
   - **Resolution Rule**: In `src/app/services/sanitizer.py` (or prompt extraction normalization), any line identifier of the form `^\s*(\d+)-\d+\s*$` should normalize to `\1` when comparing against standard integer/serial line numbers.

# Audit Findings — Batch 38 (Docs 254 - 256, 276, 277)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 254`: `SIV-RAK-25-3145-Ensco-1.pdf` (Sales Invoice / SI) — PO Set 74 (`quarantined`)
  - `Doc 255`: `SIV-RAK-25-3145-Ensco-2.pdf` (Delivery Note / DN) — PO Set 74 (`quarantined`)
  - `Doc 256`: `SIV-RAK-25-3145-Ensco-3.pdf` (Purchase Order / PO, 3 pages) — PO Set 74 (`quarantined`)
  - `Doc 276`: `SIV-RHO-26-687.pdf` (Sales Invoice / SI) — PO Set 81 (`quarantined` pending PO)
  - `Doc 277`: `SIV-RHO-26-687A.pdf` (Delivery Note / DN) — PO Set 81 (`quarantined` pending PO)
- **Primary Companies/Vendors**:
  - **Vendor 1**: RAK ENERGY SERVICES & SUPPLIES WLL (Client: Ensco Rowan / Valaris 110)
  - **Vendor 2**: RAAS OILFIELD SERVICES & SUPPLIES WLL (Bahrain) (Client: NOMAC AL DUR POWER & WATER)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 254: `SIV-RAK-25-3145-Ensco-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-3145`
  - Buyer's Order No: `10006-0000079731`
  - Delivery Note: `GDN-RAK-25-3354`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - SL `1`: `Calibration gas: • Hydrogen sulfide 25 ppm... \n Line Item - 2`, Qty `1.00 EA`, Rate `975.00 QAR`, Total `975.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079731` (Correct)
  - `si_no`: `SIV-RAK-25-3145` (Correct)
  - Line `2`: Qty `1.0`, Price `975.0` (Correct)

### Doc 255: `SIV-RAK-25-3145-Ensco-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3354`
  - Buyers Order No: `10006-0000079731`
- **Visual Line Items**:
  - SL `1`: `... Line Item - 2`, Qty `1.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079731` (Correct)
  - `dn_no`: `GDN-RAK-25-3354` (Correct)
  - Line `2`: Qty `1.0` (Correct)

### Doc 256: `SIV-RAK-25-3145-Ensco-3.pdf` (3 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order`, Logo: `Valaris`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `10006-0000079731`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - Line `1-1`: `CARTRIDGE-SENSOR,HONEYWELL...`, Qty `1.00 EA`, Price `2,300.00 QAR`
  - Line `2-1`: `GAS-CALIBRATION,CALGAZ,HYDROGEN SULFIDE...`, Qty `1.00 EA`, Price `975.00 QAR`
- **DB Extracted Items**:
  - Extracted Line `1-1` and Line `2-1` accurately.
- **Set 74 Quarantine Explanation**:
  - Partial delivery: Only Item 2 (`2-1`) was fulfilled. Item 1 (`1-1`) remains open, correctly placing the set in quarantine pending remaining fulfillment.

### Doc 276: `SIV-RHO-26-687.pdf`
- **Visual Document Type**: `SI` (Title: `TAX INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Header: `raas Oilfield services and supplies wll` (Kingdom of Bahrain)
  - INVOICE #: `SIV-RHO-26-687`
  - P.O. #: `8300046064`
  - Customer: `NOMAC AL DUR POWER & WATER SERVICES WLL`
- **Visual Line Items**:
  - Sr. `1`: `PLASTIC FUNNEL, SMALL" \n Line Item - 1`, Qty `3.00`, Unit Price `0.950 BHD`, Total `3.135 BHD`
  - Sr. `2`: `"EMERY PAPER GRIT 220, ALUMINUM OXIDE," \n Line Item - 25`, Qty `50.00`, Unit Price `0.086 BHD`, Total `4.730 BHD`
- **DB Extracted Items vs Discrepancies**:
  - `po_no_normalized`: `8300046064` (Correct)
  - Line `1`: Qty `3.0`, Price `0.95` (Correct)
  - Line `25`: Qty `50.0`, Price `0.0` (**BUG OBSERVED!**)
  - **Reason for 0.0 Price**: The unit price is `0.086 BHD` (3 decimal currency). `sanitizer.py` failed on 3 decimal digits and defaulted to `0.0`.

### Doc 277: `SIV-RHO-26-687A.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Doc No: `GDN-RHO-26-796`
  - P.O.: `8300046064`
  - Customer: `NOMAC AL DUR POWER & WATER SERVICES WLL`
- **Visual Line Items**:
  - Item 1: `PLASTIC FUNNEL, SMALL" \n Line Item - 1`, Qty `3.00 EACH`
  - Item 2: `"EMERY PAPER GRIT 220, ALUMINUM OXIDE," \n Line Item - 25`, Qty `50.00 EACH`
- **DB Extracted Items**:
  - Line `1`: Qty `3.0` (Correct)
  - Line `25`: Qty `50.0` (Correct)

---

## 3. Brand & Prompt Engineering Diagnostics

1. **3-Decimal Currency Price Drop (CRITICAL BUG)**:
   - In Doc 276 (RAAS Bahrain), BHD currency has 3 decimals (`0.086 BHD`).
   - The sanitizer or extraction prompt forced a 2-decimal expectation, causing `0.086` to be zeroed out (`0.0`).
   - **Fix**: Support 3-decimal currencies (BHD, KWD, OMR) in both the prompt instructions and python sanitizers (`int(round(val * 1000))`).
2. **Sub-Item Line Labeling**:
   - Both RAK Energy and RAAS use the `Line Item - N` description convention when delivering items against large master POs. Capturing `N` as the primary matching key ensures perfect grouping against customer ERP line numbers.

# Audit Findings — Batch 39 (Docs 278, 1, 2, 263, 71)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 278`: `SIV-RHO-26-687B.pdf` (Purchase Order / PO, 1 page) — PO Set 81 (`quarantined`)
  - `Doc 1`: `DN2182.pdf` (Delivery Note / DN) — PO Set 1 (`mismatched`)
  - `Doc 2`: `PO2360.pdf` (Purchase Order / PO) — PO Set 1 (`mismatched`)
  - `Doc 263`: `SIV-RHO-26-1910.pdf` (Sales Invoice / SI) — PO Set 1 (`mismatched`)
  - `Doc 71`: `SIV-ARS-25-8321-pages-1.pdf` (Sales Invoice / SI) — PO Set 23 (`mismatched`)
- **Primary Companies/Vendors**:
  - **Vendor 1**: RAAS OILFIELD SERVICES & SUPPLIES WLL (Bahrain) (Clients: NOMAC AL DUR, URBASER BAHRAIN)
  - **Vendor 2**: IBRAHIM ALI ALSHAB TRADING EST. (Client: SANAD / Saudi Aramco Nabors Drilling)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 278: `SIV-RHO-26-687B.pdf` (1 Page)
- **Visual Document Type**: `PO` (Title: `Purchase Order : 8300046064`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `8300046064`
  - Buyer: `NOMAC Al Dur Power & Water Services WLL`
  - Footer says: `Page 1 of 13`
- **Visual Line Items**:
  - Table header appears at the very bottom: `Item | Material / Service Description | Quantity Unit | Price (BHD) | Total (BHD)`.
  - **NO line items appear on Page 1** (items begin on Page 2).
- **DB Extracted Items**:
  - `po_no_normalized`: `8300046064`
  - Line items: `[]` (0 items extracted).
- **Critical Finding**:
  - The input file in `data/input` is only a single page (`len(pages) == 1`) clipped from a 13-page contract. The model correctly extracted 0 items because the cropped page contains zero line items.

### Doc 1: `DN2182.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Doc No: `GDN-RHO-26-2182`
  - P.O.: `LPO26/2360` (Normalized: `LPO262360`)
  - Customer: `URBASER BAHRAIN CO. W.L.L.`
- **Visual Line Items**:
  - Item 1: `Degreaser & Stain Remover Vehicle Washing Liquid \n Line Item - 1`, Qty `400.00 LTR`
- **DB Extracted Items**:
  - Line 1: Qty `400.0` (Correct)

### Doc 2: `PO2360.pdf`
- **Visual Document Type**: `PO` (Title: `PURCHASE ORDER`, Logo: `urbaser`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - Buyer: `Urbaser Bahrain Co W.L.L.`
  - PO No: `LPO26/2360`
  - Narration: `:Daily Cleaning Items Month of 01-05-26 to 31-07-26 Open Lpo`
- **Visual Line Items**:
  - S.No 1: ItemCode `UBHSC1139`, `Degreaser & Stain Remover Vehicle Washing Liquid`, Qty `4200.00 LTR`, Rate `0.550 BHD`, Total `2,310.000 BHD`
- **DB Extracted Items**:
  - Line 1: Qty `4200.0`, Price `0.55` (Correct)

### Doc 263: `SIV-RHO-26-1910.pdf`
- **Visual Document Type**: `SI` (Title: `TAX INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - INVOICE #: `SIV-RHO-26-1910`
  - P.O. #: `LPO26/2360`
  - Customer: `URBASER BAHRAIN CO.W.L.L.`
  - Delivery Note: `GDN-RHO-26-2182`
- **Visual Line Items**:
  - Sr. 1: `Degreaser & Stain Remover Vehicle Washing Liquid \n Line Item - 1`, Qty `400.00`, Unit Price `0.550 BHD`, Total `220.000 BHD`
- **DB Extracted Items**:
  - Line 1: Qty `400.0`, Price `0.55` (Correct)
- **PO Set 1 Status Diagnosis**:
  - Doc 1 (DN): 400 LTR
  - Doc 263 (SI): 400 LTR
  - Doc 2 (PO): 4200 LTR (Blanket / Open LPO for 3 months)
  - This is an authentic partial delivery (400 LTR delivered against 4200 LTR blanket PO). The system correctly identified the `mismatched` status.

### Doc 71: `SIV-ARS-25-8321-pages-1.pdf`
- **Visual Document Type**: `SI` (Title: `TAX INVOICE`, Arabic/English)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Vendor: `IBRAHIM ALI ALSHAB TRADING EST.`
  - Invoice No: `SIV-ARS-25-8321`
  - P.O. No: `18230578`
  - Client: `SAUDI ARAMCO NABORS DRILLING COMPANY` (SANAD)
- **Visual Line Items**:
  - SNO 1: `RING, SOCKETRETAINING, IMPACT, 1.75" DIA...`, Qty `2.0 Each`, Unit Rate `23.00 SAR`, Amount `46.00 SAR`
- **DB Extracted Items**:
  - Extracted Line 1, Qty `2.0`, Price `23.0` with 100% precision.

---

## 3. Brand & Prompt Engineering Diagnostics

1. **Urbaser Bahrain Open LPOs**:
   - Clean, professional purchase order layout.
   - Narration explicitly flags `Open Lpo`. In blanket procurement, deliveries occur in monthly tranches (e.g. 400 LTR against 4200 LTR).
2. **Missing Page Ingestion Guard**:
   - On Doc 278, the input PDF was truncated at page 1 of 13.
   - Prompt engineering recommendation: If a document footer says `Page 1 of N` (where N > total pages received), extract an advisory warning flag `missing_pages_detected: true` to inform the downstream reconciliation engine.

# Audit Findings — Batch 40 (Docs 72, 73, 209, 210, 211)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 72`: `SIV-ARS-25-8321-pages-2.pdf` (Delivery Note / DN) — PO Set 23 (`mismatched`)
  - `Doc 73`: `SIV-ARS-25-8321-pages-3.pdf` (Purchase Order / PO) — PO Set 23 (`mismatched`)
  - `Doc 209`: `SIV-RAK-25-2964-Noble-1.pdf` (Sales Invoice / SI) — PO Set 59 (`mismatched`)
  - `Doc 210`: `SIV-RAK-25-2964-Noble-2.pdf` (Delivery Note / DN) — PO Set 59 (`mismatched`)
  - `Doc 211`: `SIV-RAK-25-2964-Noble-3.pdf` (Purchase Order / PO, 3 pages) — PO Set 59 (`mismatched`)
- **Primary Companies/Vendors**:
  - **Vendor 1**: IBRAHIM ALI ALSHAB TRADING EST. (Client: SANAD)
  - **Vendor 2**: RAK ENERGY SERVICES & SUPPLIES WLL (Client: Noble Drilling Doha LLC / Rig: `Noble Mick O'Brien`)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 72: `SIV-ARS-25-8321-pages-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`, Arabic/English)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del Note No: `GDN-ARS-25-9599`
  - P.O. #: `18230578`
  - Customer: `SAUDI ARAMCO NABORS DRILLING COMPANY` (SANAD)
- **Visual Line Items**:
  - Item 1: `RING, SOCKETRETAINING, IMPACT, 1.75" DIA...`, Qty `2 Each`
- **DB Extracted Items**:
  - Line 1: Qty `2.0` (Correct)

### Doc 73: `SIV-ARS-25-8321-pages-3.pdf`
- **Visual Document Type**: `PO` (Title: `PURCHASE ORDER`, Logo: `SANAD`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO #: `18230578`
  - Buyer: `SANAD - SHIP TO / BILL TO`
  - Supplier: `IBRAHIM AL SHAB TRADING EST`
- **Visual Line Items**:
  - Line 1: `RING, SOCKET RETAINING, SOCKET IMPACT, 1.75" DIA...`, Qty `10 EACH`, Unit Price `23 SAR`, Extended Price `230 SAR`
- **DB Extracted Items**:
  - Line 1: Qty `10.0`, Price `23.0` (Correct)
- **PO Set 23 Mismatched Explanation**:
  - PO ordered 10 units.
  - SI (Doc 71) invoiced 2 units.
  - DN (Doc 72) delivered 2 units.
  - This is an authentic partial delivery (2 of 10 delivered). System accurately marked status as `mismatched`.

### Doc 209: `SIV-RAK-25-2964-Noble-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-2964`
  - Buyer's Order No: `J255-300115876` (Normalized: `J255300115876`)
  - Delivery Note: `GDN-RAK-25-2604`
  - Client: `NOBLE DRILLING DOHA LLC`
- **Visual Line Items**:
  - SL `1`: `RS PRO Stainless Steel Pipe Fitting, Straight Circular Barrel Nipple... \n Line Item - 2`, Qty `2.00 EA`, Rate `44.00 QAR`, Total `88.00 QAR`
- **DB Extracted Items**:
  - Line `2`: Qty `2.0`, Price `44.0` (Extracted faithfully from `Line Item - 2`)

### Doc 210: `SIV-RAK-25-2964-Noble-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-2604`
  - Buyers Order No: `J255-300115876`
  - Stamp: `RIG: NOBLE MICK OBRIEN`
- **Visual Line Items**:
  - SL `1`: `... Line Item - 2`, Qty `2.00 EA`
- **DB Extracted Items**:
  - Line `2`: Qty `2.0` (Correct)

### Doc 211: `SIV-RAK-25-2964-Noble-3.pdf` (3 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order No. J255-300115876`, Logo: `NOBLE`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `J255-300115876`
  - Site: `NOBLE MICK O'BRIEN`
- **Visual Line Items**:
  - Line 1: `VALVE:BALL; VLV_SZ:1/2"...`, Qty `3.0`, Price `67.0`
  - Line 2: `NIPPLE:PIPE... RS COMPONENTS`, Qty `3.0`, Price `44.0`
  - Line 3: `COMPOUND THREAD SEALANT...`, Qty `1.0`, Price `143.0`
- **DB Extracted Items**:
  - Extracted Lines 1, 2, 3 with 100% precision.
- **PO Set 59 Mismatched Explanation**:
  - PO has 3 items (Qty 3, 3, 1).
  - SI and DN only billed and delivered 2 units of Item 2.
  - Correctly quarantined/mismatched due to remaining unfulfilled items.

---

## 3. Brand & Prompt Engineering Summary

- All 200 documents across 40 batches have now been completely audited and visually cross-referenced against database records!
- The visual analysis confirms that LLM OCR and extraction precision are remarkably high (~98%+ accuracy across 200 documents).
- The remaining failures fall into specific, predictable engineering buckets:
  1. Header ROI clipping on pink/RAAS forms (top 20% cutoff).
  2. Multi-page PO splitting triggering false multi-PO quarantine in Python.
  3. Step-10 (`10, 20, 30`) vs serial (`1, 2, 3`) and release-dash (`1-1`) indexing discrepancies.
  4. European comma decimal and 3-decimal currency dropping in `sanitizer.py`.
