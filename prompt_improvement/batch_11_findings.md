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
