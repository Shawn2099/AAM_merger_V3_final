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
