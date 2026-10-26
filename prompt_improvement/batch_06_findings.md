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
