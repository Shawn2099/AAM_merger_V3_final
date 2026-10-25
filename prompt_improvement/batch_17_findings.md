## Audit Findings: Batch 17 (Docs 182, 183, 184, 185, 186)

### Document Details
- **Doc 182**: `SIV-RAK-25-2876-Ades-2.pdf` (DN GDN-RAK-25-3081, PO Set 50, Status: `pending`)
- **Doc 183**: `SIV-RAK-25-2876-Ades-3.pdf` (PO 13060, PO Set 50, Status: `pending`)
- **Doc 184**: `SIV-RAK-25-2884-Ades-1.pdf` (SI SIV-RAK-25-2884, PO Set 51, Status: `pending`)
- **Doc 185**: `SIV-RAK-25-2884-Ades-2.pdf` (DN GDN-RAK-25-3065, PO Set 51, Status: `pending`)
- **Doc 186**: `SIV-RAK-25-2884-Ades-3.pdf` (PO 13696, PO Set 51, Status: `pending`)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 50: Doc 182 (DN) & Doc 183 (PO)
- **Doc 182**: RAK Energy Services Delivery Note `GDN-RAK-25-3081`.
  - Header: Buyers Order No `13060`. Extracted correctly.
  - Line: `Ty-Down straps for helideck 9m 5000kg` (annotated `Line Item - 2`), Qty 4.0 EA. Extracted accurately as Line 2.
- **Doc 183**: Emerald Driller Co (ADES) PO 13060 (7 pages, items on p1):
  - Line 1: Wheel Chock, Qty 2, Price 380 QAR.
  - Line 2: Helideck Ty Down Straps, Qty 4, Price 270 QAR.
  - Line 3: IATA Dangerous Goods book, Qty 1, Price 2699 QAR.
  - Extraction matches printed PO lines 1–3 with 100% precision.
- **Reconciliation Status for Set 50**:
  - Combined with Doc 181 (SI audited in Batch 16), Line 2 has an **exact 3-way match**: PO ordered 4 @ 270 QAR, DN delivered 4, SI billed 4 @ 270 QAR.
  - Set status `pending` is commercially normal because lines 1 and 3 are pending delivery from the vendor.

#### 2. PO Set 51: Doc 184 (SI), Doc 185 (DN), Doc 186 (PO)
- **Doc 184**: RAK Energy Sales Invoice `SIV-RAK-25-2884`.
  - Header: `Buyer's Order No : 13696`, `Delivery Note : GDN-RAK-25-3065`.
  - In DB: `dn_no = NULL` (missed).
  - Line 1: `DANFOSS PRESSURE SWITCH`, Qty 1.0, Rate 1,030.00 QAR.
- **Doc 185**: RAK Energy Delivery Note `GDN-RAK-25-3065`.
  - Header: `Buyers Order No : 13696`, `Del. Note No : GDN-RAK-25-3065`.
  - Line 1: `DANFOSS PRESSURE SWITCH`, Qty 1.0. Extracted accurately.
- **Doc 186**: Emerald Driller Co PO 13696 (6 pages, items on p1):
  - Line 1: Danfoss Pressure Switch, Qty 1, Price 1030 QAR.
  - Line 2: Motorola Radio Microphone, Qty 2, Price 238 QAR.
  - Extracted lines 1 and 2 perfectly.
- **Reconciliation Status for Set 51**:
  - Line 1 has an **exact 3-way match** (PO 1 @ 1030 QAR, DN 1, SI 1 @ 1030 QAR).
  - Line 2 is awaiting fulfillment.

---

### Core Findings Summary
1. **What was Correct**:
   - Flawless extraction of multi-currency QAR amounts and line item details across RAK Energy POs, DNs, and SIs.
   - Exact line-item number alignment using descriptive line annotations (`Line Item - 2`).
2. **What was Wrong**:
   - Lack of partial-fulfillment handling causes sets with 100% matched delivered lines to remain indefinitely `pending`.
3. **What was Misrepresented**:
   - Nothing misrepresented in the line data; both sets reflect valid commercial partial deliveries.
4. **What was Missed**:
   - Delivery note reference `GDN-RAK-25-3065` printed on Invoice Doc 184 was missed and saved as `NULL`.
5. **What We Need to Do to Improve**:
   - Capture delivery note cross-references on RAK Energy sales invoices.
   - Implement partial delivery state tracking so sets with completely reconciled delivered lines can be flagged as "Partially Delivered & Reconciled".
