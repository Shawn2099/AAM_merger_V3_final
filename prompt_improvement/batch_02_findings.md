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
