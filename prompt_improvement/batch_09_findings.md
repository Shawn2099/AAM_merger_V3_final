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
