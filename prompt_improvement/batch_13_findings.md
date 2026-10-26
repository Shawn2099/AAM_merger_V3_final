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
