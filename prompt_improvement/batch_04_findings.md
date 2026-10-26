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
