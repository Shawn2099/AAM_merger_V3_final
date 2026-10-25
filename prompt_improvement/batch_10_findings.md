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
