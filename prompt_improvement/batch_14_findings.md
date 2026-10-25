## Audit Findings: Batch 14 (Docs 161, 162, 163, 166, 167)

### Document Details
- **Doc 161**: `SIV-DTS-25-583-3.pdf` (PO 193781 p1, PO Set 45, Status: `pending`)
- **Doc 162**: `SIV-DTS-25-583-4.pdf` (PO 193781 p2, PO Set 45, Status: `pending`)
- **Doc 163**: `SIV-DTS-25-584-1.pdf` (SI SIV-DTS-25-584, PO Set 46, Status: `pending`)
- **Doc 166**: `SIV-DTS-25-584-4.pdf` (PO 192123 p1, PO Set 46, Status: `pending`)
- **Doc 167**: `SIV-DTS-25-584-5.pdf` (PO 192123 p2, PO Set 46, Status: `pending`)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 45: Doc 161 (PO p1) & Doc 162 (PO p2)
- **Doc 161**: ADES PO 193781, 4 lines (welding electrodes and abrasive flap disc):
  - Line 1: Qty 25.0, Unit Price 96.8 SAR
  - Line 2: Qty 15.0, Unit Price 85.8 SAR
  - Line 3: Qty 15.0, Unit Price 85.8 SAR
  - Line 4: Qty 10.0, Unit Price 3.3 SAR
  - Extraction matches printed values with 100% precision.
- **Doc 162**: ADES PO 193781 page 2 of 7 (totals and standard T&Cs). DB correctly records 0 line items.
- **Reconciliation Status**: Set 45 has exact 1:1 match between PO lines and SI lines (from Doc 159 audited in Batch 13), but is pending solely due to the missing DN link (`GDN-DTS-25-627` on unattached Doc 160).

#### 2. PO Set 46: Doc 163 (SI), Doc 166 (PO p1), Doc 167 (PO p2)
- **Doc 163**: Drilling Tools Trading Est Tax Invoice `SIV-DTS-25-584`.
  - Header explicitly specifies `Buyers Order No : 192123` and `Delivery Note : GDN-DTS-25-628`.
  - In DB: `po_no_normalized = 192123` was extracted, but `dn_no` was left `NULL`!
  - Lines 1–7 (Adapters and connectors, Qty 4.0 each, Rates 37.0, 58.0, 62.0, 37.0, 33.0, 25.0, 49.0) are extracted perfectly.
- **Doc 166**: ADES PO 192123 page 1 of 7. Extracted lines 1–5 perfectly (Qty 4.0 each).
- **Doc 167**: ADES PO 192123 page 2 of 7. Extracted lines 6–12 perfectly (Qty 4.0 each, rates 25, 49, 37, 58, 37, 49, 33).
- **Reconciliation Reality**:
  - PO 192123 has 12 items. Invoice SIV-DTS-25-584 invoices items 1 to 7 only (partial delivery / partial fulfillment).
  - Lines 8–12 remain unbilled, which is commercially legitimate partial delivery.
  - The missing `dn_no` (`GDN-DTS-25-628`) on the invoice prevents automatic cross-referencing with the vendor's delivery note.

---

### Core Findings Summary
1. **What was Correct**:
   - High fidelity extraction of all 12 items on ADES PO 192123 split across pages 1 and 2.
   - Exact extraction of all 4 items on PO 193781.
   - Exact numerical extraction of all 7 items on Invoice SIV-DTS-25-584.
2. **What was Wrong**:
   - Ingestion split multi-page PO documents into individual PDF files (`-pages-1.pdf`, `-pages-2.pdf`), causing multiple PO rows per set and tripping `single_po_document` guards.
3. **What was Misrepresented**:
   - PO Set 46 `pending` status reflects an incomplete set, but without partial delivery tracking, the system cannot distinguish between a data error and an in-progress partial delivery.
4. **What was Missed**:
   - Invoice `dn_no = GDN-DTS-25-628` printed clearly on Doc 163 was missed and stored as `NULL`.
5. **What We Need to Do to Improve**:
   - Extract the `Delivery Note : GDN-DTS-25-628` reference from the top-right metadata table of Drilling Tools invoices.
