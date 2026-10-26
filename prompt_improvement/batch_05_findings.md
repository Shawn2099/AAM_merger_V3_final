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
