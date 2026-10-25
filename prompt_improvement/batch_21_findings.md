## Audit Findings: Batch 21 (Docs 99, 104, 108, 111, 114)

### Document Details
All 5 documents in this batch are scanned Delivery Notes from Drilling Tools Trading Est that were completely orphaned (`PO Set: None`, `Status: None`) due to header parsing failures:
- **Doc 99**: `SIV-DTS-25-480-pages-2.pdf`
- **Doc 104**: `SIV-DTS-25-486-pages-2.pdf`
- **Doc 108**: `SIV-DTS-25-498-pages-2.pdf`
- **Doc 111**: `SIV-DTS-25-509-pages-2.pdf`
- **Doc 114**: `SIV-DTS-25-517-pages-2.pdf`

---

### Visual Inspection vs DB Comparison & Cross-Set Resolution

#### 1. Doc 99 (`SIV-DTS-25-480-pages-2.pdf`) — Resolves PO Set 32
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-512`, `Buyers Order No: 4500496007`, Date: 15/09/2025.
  - Client: `SAUDI INTERNATIONAL ENERGY SERVICES COMPANY LTD` (KCA Deutag).
  - Lines 1–5: Split Cotter Pins 3mm x 40mm, Qtys: 200, 100, 100, 100, 200 EA. Total: 700 EA.
  - Stamped and signed by KCA Deutag Goods Receiving on 17/09/25.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 32** (`4500496007`), matching PO Doc 101 and Invoice Doc 98 with 100% exact quantities!

#### 2. Doc 104 (`SIV-DTS-25-486-pages-2.pdf`) — Resolves PO Set 33
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-521`, `Buyers Order No: 4500496514`, Date: 20-09-2025.
  - Client: `SAUDI INTERNATIONAL ENERGY SERVICES COMPANY LTD`.
  - Line 1: `Cotter Pin Galvanized M5X152MM`, Qty: 250.00 EACH.
  - Stamped by Goods Receiving on 21/09/25.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 33** (`4500496514`), matching PO Doc 106 and Invoice Doc 103 with 100% exact quantity!

#### 3. Doc 108 (`SIV-DTS-25-498-pages-2.pdf`) — Resolves PO Set 34
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-504`, `Buyers Order No: ARM-PO-25-26-029`, Date: 13-09-2025.
  - Client: `TUBESTAR OIL & GAS ARABIA COMPANY FOR GAS`.
  - Lines 1–3: Steel files flat (7 EA), Single color rope (2 ROLL), LED flashlight (3 EACH). Total: 12.000.
  - Stamped and signed by Tubestar on 16/09/2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 34** (`ARM-PO-25-26-029`), matching PO Doc 109 and Invoice Doc 107 with 100% exact quantities!

#### 4. Doc 111 (`SIV-DTS-25-509-pages-2.pdf`) — Resolves PO Set 35
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-551`, `Buyers Order No: 22843`, Date: 12-10-2025.
  - Client: `ADES GCC FOR DRILLING(KSA)`.
  - Lines 1–3: SAT Power and Sprinter Batteries, Qtys: 2 EACH, 6 EACH, 2 EACH. Total: 10.000.
  - Stamped by ADES Central W/H on 13 OCT 2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 35** (`22843`), matching PO Doc 112 and Invoice Doc 110 with 100% exact quantities!

#### 5. Doc 114 (`SIV-DTS-25-517-pages-2.pdf`) — Resolves PO Set 36
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-548`, `Buyers Order No: 22613 , 0`, Date: 12-10-2025.
  - Client: `ADES GCC FOR DRILLING(KSA)`.
  - Line 1: `Drill Pipe Connection Guide 4-1/2"`, Qty: 1.00 EACH.
  - Stamped by ADES Central W/H on 14 OCT 2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 36** (`22613`). Notice the printed PO is `22613 , 0` (PO 22613, Rev 0), which explains why the Invoice extractor erroneously generated `226130`.

---

### Core Findings Summary
1. **What was Correct**:
   - The line item tables and descriptions on all 5 delivery notes were extracted cleanly into the database.
2. **What was Wrong**:
   - **Systemic Header Blindness**: The extraction model failed completely to read the top-right metadata table (`Buyers Order No` and `Del. Note No`) on all 5 pink-tinted Drilling Tools delivery notes.
3. **What was Misrepresented**:
   - 5 valid, fully executed, physically stamped delivery notes were flagged as orphaned, unattached documents.
   - 5 corresponding PO sets (Sets 32, 33, 34, 35, 36) were left stuck in `pending` under the false premise that no delivery documentation had been provided.
4. **What was Missed**:
   - All 5 PO numbers (`4500496007`, `4500496514`, `ARM-PO-25-26-029`, `22843`, `22613`) and DN numbers (`GDN-DTS-25-512`, `GDN-DTS-25-521`, `GDN-DTS-25-504`, `GDN-DTS-25-551`, `GDN-DTS-25-548`).
5. **What We Need to Do to Improve**:
   - Fix the header region ROI detection for pink Drilling Tools delivery notes.
   - Run a reconciliation link pass to attach Docs 99, 104, 108, 111, 114 to Sets 32, 33, 34, 35, 36, unlocking all 5 sets for immediate automatic merge.
