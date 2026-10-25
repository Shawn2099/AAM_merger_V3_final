## Audit Findings: Batch 23 (Docs 136, 137, 139, 142, 145)

### Document Details
- **Doc 136**: `SIV-DTS-25-566-2.pdf` (SI p2 SIV-DTS-25-566, PO Set `None` / Unattached)
- **Doc 137**: `SIV-DTS-25-566-3.pdf` (DN GDN-DTS-25-571, PO Set `None` / Unattached)
- **Doc 139**: `SIV-DTS-25-572-2.pdf` (DN GDN-DTS-25-606, PO Set `None` / Unattached)
- **Doc 142**: `SIV-DTS-25-575-2.pdf` (DN GDN-DTS-25-614, PO Set `None` / Unattached)
- **Doc 145**: `SIV-DTS-25-576-2.pdf` (DN GDN-DTS-25-615, PO Set `None` / Unattached)

---

### Visual Inspection vs DB Comparison & Cross-Set Resolution

#### 1. SIV-DTS-25-566 Match: Doc 136 (SI p2) & Doc 137 (DN)
- **Doc 136 Visual Evidence**:
  - Page 2 of Invoice `SIV-DTS-25-566` to Tubestar Oil & Gas Arabia.
  - Header: `Buyers Order No: TOGA-PO-25-26-204`, `Delivery Note: GDN-DTS-25-571`.
  - Lines 8 & 9: Paint brushes (Qty 10 DZN @ 21.00 SAR, Qty 10 DZN @ 35.00 SAR). Total invoice amount: 5,288.00 SAR net.
- **Doc 137 Visual Evidence**:
  - Drilling Tools Delivery Note `GDN-DTS-25-571`, Date: 22-10-2025.
  - Header: `Buyers Order No: TOGA-PO-25-26-204`. Client: `TUBESTAR OIL & GAS ARABIA`.
  - All 9 lines matching Invoice Docs 135 & 136 exactly (Total Qty: 188.000).
  - Stamped and signed by Tubestar Goods Receiving on 23/10/25.
- **Database Status**: Doc 137 header was missed (`PO Raw = None`, `PO Set = None`).
- **Reconciliation Impact**: Invoice SIV-DTS-25-566 (Docs 135, 136) and Delivery Note Doc 137 match with **100% perfect alignment** across all 9 lines and 188 units.

#### 2. Doc 139 (`SIV-DTS-25-572-2.pdf`) — Resolves ADES PO 22972
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-606`, `Buyers Order No: 22972`, Date: 10-11-2025.
  - Client: `ADES GCC FOR DRILLING(KSA)`.
  - Line 1: PS5 DualSense Controller (Qty 2.00 EA), Line 2: PS5 Charging Station (Qty 1.00 EA).
  - Stamped and signed by ADES Central W/H on 12 NOV 2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for ADES PO 22972** (Invoice Doc 138 audited in Batch 12), matching 100% in line items and quantities!

#### 3. Doc 142 (`SIV-DTS-25-575-2.pdf`) — Resolves Tubestar PO TOGA-PO-25-26-228
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-614`, `Buyers Order No: TOGA-PO-25-26-228`, Date: 11-11-2025.
  - Client: `TUBESTAR OIL & GAS ARABIA COMPANY FOR GAS`.
  - Line 1: `Ansell Edge Heavy-Duty Hi-Viz Impact Resistant Gloves-12 (XXL-LARGE)`, Qty: 20.00 Pair.
  - Stamped and signed by Tubestar on 12/11/2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for Tubestar PO TOGA-PO-25-26-228** (Invoice Doc 141 audited in Batch 12), matching 100% in line item and quantity!

#### 4. Doc 145 (`SIV-DTS-25-576-2.pdf`) — Resolves ADES PO 22398
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-615`, `Buyers Order No: 22398 , 0` (PO 22398 Rev 0), Date: 12-11-2025.
  - Client: `ADES GCC FOR DRILLING(KSA)`.
  - Line 1: `SpareLamp, Nova-Strobe Xenon Stroboscopes MONARCH 6280-030`, Qty: 2.00 EA.
  - Stamped and signed by ADES Central W/H on 13 NOV 2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for ADES PO 22398** (Invoice Doc 144 audited in Batch 12), matching 100% in line item and quantity!

---

### Core Findings Summary
1. **What was Correct**:
   - Flawless line extraction of items, part numbers, and quantities across all 5 delivery notes and invoices.
2. **What was Wrong**:
   - The extractor consistently failed to capture the top-right header block on all pink Drilling Tools delivery notes, leaving them orphaned without a PO Set ID.
3. **What was Misrepresented**:
   - These 4 delivery notes represent legitimate, fully accepted material receipts, but the database treated them as unreferenced garbage.
4. **What was Missed**:
   - `TOGA-PO-25-26-204` / `GDN-DTS-25-571` on Doc 137
   - `22972` / `GDN-DTS-25-606` on Doc 139
   - `TOGA-PO-25-26-228` / `GDN-DTS-25-614` on Doc 142
   - `22398` / `GDN-DTS-25-615` on Doc 145
5. **What We Need to Do to Improve**:
   - Automatically associate delivery notes using the printed `Buyers Order No` and `Del. Note No` to complete the 3-way matching sets.
