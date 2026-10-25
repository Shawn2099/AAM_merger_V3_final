## Audit Findings: Batch 18 (Docs 221, 222, 223, 257, 259)

### Document Details
- **Doc 221**: `SIV-RAK-25-3057-Noble-1.pdf` (SI SIV-RAK-25-3057, PO Set 63, Status: `pending`)
- **Doc 222**: `SIV-RAK-25-3057-Noble-2.pdf` (DN GDN-RAK-25-3215, PO Set 63, Status: `pending`)
- **Doc 223**: `SIV-RAK-25-3057-Noble-3.pdf` (PO 300140833, PO Set 63, Status: `pending`)
- **Doc 257**: `SIV-RHO-26-1669.pdf` (SI SIV-RHO-26-1669, PO Set 75, Status: `pending`)
- **Doc 259**: `SIV-RHO-26-1669B.pdf` (PO 8300052050, PO Set 75, Status: `pending`)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 63: Doc 221 (SI), Doc 222 (DN), Doc 223 (PO)
- **Doc 221**: RAK Energy Sales Invoice `SIV-RAK-25-3057` to Noble Drilling Doha LLC.
  - Header: Buyers Order No `300140833`, Delivery Note `GDN-RAK-25-3215`. In DB, `dn_no = NULL` (missed).
  - Line 3: `Canon LS555H Handheld Foldable Pocket Calculator`, Qty 5.0 EA, Rate 192.50 QAR.
- **Doc 222**: RAK Energy Delivery Note `GDN-RAK-25-3215`. Line 3: Qty 5.0 EA. Extracted accurately.
- **Doc 223**: Noble Drilling PO 300140833 (4 pages, items on p2):
  - Extracted 7 lines (Lubricant, flap discs, calculator, lithium battery, pilot markers).
  - Line 3: Canon Foldable Pocket Calculator LS555H, Qty 5 EA, Price 192.50 QAR.
  - **Reconciliation Reality**:
    - Line 3 has an **exact 3-way match** (PO 5 @ 192.50, DN 5, SI 5 @ 192.50).
    - Status is `pending` because this is a legitimate partial fulfillment (other 6 PO lines awaiting delivery).

#### 2. PO Set 75: Doc 257 (SI) & Doc 259 (PO) — The Blended Average / Line-Split Discrepancy
- **Doc 257**: RAAS Oilfield Services & Supplies WLL Tax Invoice `SIV-RHO-26-1669` to NOMAC Al Dur Power & Water Services (Kingdom of Bahrain).
  - Delivery Note No column on lines: `GDN-RHO-26-1799`. In DB, `dn_no = NULL`.
  - Line 1: `STORAGE BIN 350L X 200W X 200H mm (LF322)`, Qty 50.00, Unit Price 4.600 BHD, Amount: 230.000 BHD.
  - Line 2: `STORAGE BIN 500L X 300W X 200H mm (LF532)`, Qty 50.00, Unit Price 5.900 BHD, Amount: 295.000 BHD.
  - Total: 525.000 BHD.
- **Doc 259**: NOMAC PO 8300052050.
  - Line 1: `1400026995 STORAGE BINS`, Qty 100.00 EA, Price: 5.250 BHD, Total: 525.000 BHD.
- **Reconciliation Analysis**:
  - The buyer created a single lump-sum PO line for 100 storage bins at the weighted average price of 5.250 BHD ($50 \times 4.60 + 50 \times 5.90 = 525.00$).
  - The supplier invoiced as two distinct lines of 50 each with size-specific pricing (4.600 and 5.900).
  - Total quantity (100) and total value (525.000 BHD) match 100%, but under line-item matching rules, line 1 fails on quantity (100 vs 50) and price (5.25 vs 4.60), and line 2 is an orphan vendor line!

---

### Core Findings Summary
1. **What was Correct**:
   - Flawless visual extraction of line items, currencies (BHD, QAR), quantities, and prices for all 5 documents.
2. **What was Wrong**:
   - PO Set 75 cannot be reconciled by standard 1:1 matcher despite total order quantity and total financial value matching exactly.
3. **What was Misrepresented**:
   - Set 75 appears as an unreconciled failure in standard reports, masking that it is a 1-to-many line split with 0% total financial variance.
4. **What was Missed**:
   - Delivery note numbers `GDN-RAK-25-3215` (Doc 221) and per-line delivery note `GDN-RHO-26-1799` (Doc 257) were omitted from DB records.
5. **What We Need to Do to Improve**:
   - Add a "Set-Level Total Quantity & Total Amount Sanity Check" in reconciliation reporting to flag when an apparent line mismatch actually sums to identical order totals.
   - Extract the `Delivery Note No` table column on RAAS Oilfield invoices.
