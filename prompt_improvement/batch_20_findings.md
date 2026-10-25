## Audit Findings: Batch 20 (Docs 270, 272, 86, 91, 96)

### Document Details
- **Doc 270**: `SIV-RHO-26-234.pdf` (SI SIV-RHO-26-234, PO Set 79, Status: `pending`)
- **Doc 272**: `SIV-RHO-26-234B.pdf` (PO C13717 p1, PO Set 79, Status: `pending`)
- **Doc 86**: `SIV-DTS-25-452-pages-2.pdf` (DN GDN-DTS-25-478, PO Set `None` / Unattached)
- **Doc 91**: `SIV-DTS-25-456-pages-3.pdf` (DN GDN-DTS-25-493, PO Set `None` / Unattached)
- **Doc 96**: `SIV-DTS-25-477-pages-2.pdf` (DN GDN-DTS-25-505, PO Set `None` / Unattached)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 79: Doc 270 (SI) & Doc 272 (PO p1)
- **Doc 270**: RAAS Tax Invoice `SIV-RHO-26-234` to International Maritime Industries (IMI).
  - P.O. #: `C13717`, Delivery Note: `GDN-RHO-26-270` (in table column, missed in DB).
  - Line 1: `Air vent ducting Flexible... 50 meter`, Qty 120.00, Unit Price 128.000 USD, Total: 15,360.00 USD. Extracted cleanly into DB.
- **Doc 272**: IMI Purchase Order `C13717` (Page 1 of 2).
  - PO Value: 15,360.00 USD.
  - Page 1 contains only metadata and terms; items table is on Page 2 (which was not ingested or split). Extracted 0 lines in DB.

#### 2. The Unattached Delivery Notes Revelation: Docs 86, 91, 96
- **Doc 86**: Drilling Tools Delivery Note:
  - Header: `Del. Note No: GDN-DTS-25-478`, `Buyers Order No: 4500026808`, Client: `ARABIAN DRILLING COMPANY`.
  - Line 1: Anti-spark hammer 7lb, Qty 1.00 EA.
  - **SYSTEM FAILURE**: DB has `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`!
  - **Root Cause & Impact**: The extractor failed to parse the pink-tinted header table. This is the **missing Delivery Note for PO Set 29** (`4500026808`)! PO Set 29 has been sitting in `pending` because this document was orphaned.
- **Doc 91**: Drilling Tools Delivery Note:
  - Header: `Del. Note No: GDN-DTS-25-493`, `Buyers Order No: 4500039614`, Client: `ARABIAN DRILLING COMPANY`.
  - 17 tool items (wrenches, pliers, hammer, pry bar).
  - **SYSTEM FAILURE**: DB has `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`!
  - **Root Cause & Impact**: This is the **missing Delivery Note for PO Set 30** (`4500039614`)!
- **Doc 96**: Drilling Tools Delivery Note:
  - Header: `Del. Note No: GDN-DTS-25-505`, `Buyers Order No: TOGA-PO-25-26-163`, Client: `TUBESTAR OIL & GAS ARABIA`.
  - Lines 1–5: Stationery supplies, Qty 24 PKT total.
  - **SYSTEM FAILURE**: DB has `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`!
  - **Root Cause & Impact**: This is the **missing Delivery Note for PO Set 31** (`TOGA-PO-25-26-163`)!

---

### Core Findings Summary
1. **What was Correct**:
   - High OCR line item extraction fidelity on the item tables of Docs 86, 91, and 96 (even reading hand-annotated strike-throughs on Doc 91).
2. **What was Wrong**:
   - Complete loss of header data (`Buyers Order No` and `Del. Note No`) across all Drilling Tools scanned delivery notes, leaving dozens of valid delivery notes completely unattached.
3. **What was Misrepresented**:
   - Dozens of delivery notes appear as "unattached orphan documents" with no PO reference, when in fact their PO numbers are clearly printed in the upper right quadrant.
4. **What was Missed**:
   - `4500026808` / `GDN-DTS-25-478` on Doc 86
   - `4500039614` / `GDN-DTS-25-493` on Doc 91
   - `TOGA-PO-25-26-163` / `GDN-DTS-25-505` on Doc 96
5. **What We Need to Do to Improve**:
   - Retrain or anchor the Vision/LLM prompt to explicitly target the top-right metadata box (`Del. Note No:` and `Buyers Order No:`) on Drilling Tools delivery note scans.
   - Run an automated linking pass that re-attaches these orphaned delivery notes to their corresponding PO sets.
