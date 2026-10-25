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
