## Audit Findings: Batch 22 (Docs 117, 121, 127, 133, 135)

### Document Details
- **Doc 117**: `SIV-DTS-25-519-pages-2.pdf` (DN GDN-DTS-25-555, PO Set `None` / Unattached)
- **Doc 121**: `SIV-DTS-25-538-pages-2.pdf` (DN GDN-DTS-25-574, PO Set `None` / Unattached)
- **Doc 127**: `SIV-DTS-25-548-pages-2.pdf` (DN GDN-DTS-25-584, PO Set `None` / Unattached)
- **Doc 133**: `SIV-DTS-25-565-2.pdf` (DN GDN-DTS-25-603, PO Set `None` / Unattached)
- **Doc 135**: `SIV-DTS-25-566-1.pdf` (SI SIV-DTS-25-566, PO Set `None` / Unattached)

---

### Visual Inspection vs DB Comparison & Cross-Set Resolution

#### 1. Doc 117 (`SIV-DTS-25-519-pages-2.pdf`) — Resolves PO Set 37
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-555`, `Buyers Order No: 4500044740`, Date: 14-10-2025.
  - Client: `ARABIAN DRILLING COMPANY`.
  - Line 1: `Body Solid Pro Clubline Commercial Squat Stand`, Qty: 1.00 PCS.
  - Stamped and signed by Arabian Drilling on 15 OCT 2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 37** (`4500044740`), matching PO Doc 119 and Invoice Doc 116 with 100% exact quantity!

#### 2. Doc 121 (`SIV-DTS-25-538-pages-2.pdf`) & Doc 127 (`SIV-DTS-25-548-pages-2.pdf`) — Resolves PO Set 38
- **Doc 121 Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-574`, `Buyers Order No: 4500047182`, Date: 27-10-2025.
  - Client: `ARABIAN DRILLING COMPANY`.
  - Lines 2–6: Insight Fitness Back Extension (1), Axox Stepper (1), Body Solid FID Bench (1), Axox Tricep Press Down Bar (1), Insight Leg Curl/Extension (1). Total: 5.000.
  - Stamped and signed on 29 OCT 2025.
- **Doc 127 Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-584`, `Buyers Order No: 4500047182`, Date: 01-11-2025.
  - Line 7: `Axox Fitness 1sqm Rubber Interlocking Mat | Single Piece`, Qty: 1.00 Each.
  - Stamped and signed on 02 NOV 2025.
- **Database Status**: Both documents have `PO Raw = None`, `DN = None`, `PO Set = None`.
- **System Impact**: Together, Docs 121 and 127 deliver lines 2 through 7 of **PO Set 38** (`4500047182`), matching Invoice Doc 124 (audited in Batch 11) lines 2 through 7 with 100% precision! Both delivery notes were orphaned due to the unread header table.

#### 3. Doc 133 (`SIV-DTS-25-565-2.pdf`) — Resolves PO Set 40
- **Visual Evidence**:
  - Header: `Del. Note No: GDN-DTS-25-603`, `Buyers Order No: ARM-PO-25-26-035`, Date: 10-11-2025.
  - Client: `TUBESTAR OIL & GAS ARABIA COMPANY FOR GAS`.
  - Lines 1–3: Cup wire brush (15 EACH), Wire brush (5 DZN), Cotton rags (8 BND). Total: 28.000.
  - Stamped and signed on 11/11/2025.
- **Database Status**: `PO Raw = None`, `Norm = None`, `DN = None`, `PO Set = None`.
- **System Impact**: This is the **missing Delivery Note for PO Set 40** (`ARM-PO-25-26-035`), matching PO Doc 134 and Invoice Doc 132 with 100% exact quantities!

#### 4. Doc 135 (`SIV-DTS-25-566-1.pdf`) — Unattached Supplier Invoice
- **Visual Evidence**:
  - Drilling Tools Tax Invoice `SIV-DTS-25-566` to Tubestar Oil & Gas Arabia.
  - `Buyers Order No : TOGA-PO-25-26-204`, `Delivery Note : GDN-DTS-25-571`.
  - 7 lines (cup brush, apron, paints, rags, pad locks).
- **Database Status**: Extracted `po_no_normalized = TOGAPO2526204`, but `po_set_id = NULL`.
- **System Impact**: No PO document was uploaded for `TOGA-PO-25-26-204` (only invoice pages and delivery note Doc 137). In the absence of an ingested PO document, the system left the invoice unattached.

---

### Core Findings Summary
1. **What was Correct**:
   - Exact line-item, quantity, and unit-of-measure extraction on all 4 scanned delivery notes.
   - Clean 7-line extraction of Invoice Doc 135.
2. **What was Wrong**:
   - Extraction model failed to capture `Buyers Order No` on Docs 117, 121, 127, and 133, stranding them as unattached documents.
3. **What was Misrepresented**:
   - Multi-part delivery for PO Set 38 (split across delivery notes Doc 121 and Doc 127) was lost entirely from the reconciliation pipeline.
4. **What was Missed**:
   - `4500044740` on Doc 117
   - `4500047182` on Docs 121 and 127
   - `ARM-PO-25-26-035` on Doc 133
5. **What We Need to Do to Improve**:
   - Re-link Docs 117, 121, 127, 133 to their respective PO sets (37, 38, 40), allowing Sets 37 and 40 to merge automatically, and Set 38 to achieve full reconciliation of lines 2–7.
