## Audit Findings: Batch 15 (Docs 168, 171, 172, 173, 175)

### Document Details
- **Doc 168**: `SIV-DTS-25-588-1.pdf` (SI SIV-DTS-25-588, PO Set 47, Status: `pending`)
- **Doc 171**: `SIV-DTS-25-588-4.pdf` (PO 4500496313 p1, PO Set 47, Status: `pending`)
- **Doc 172**: `SIV-DTS-25-588-5.pdf` (PO 4500496313 p2, PO Set 47, Status: `pending`)
- **Doc 173**: `SIV-DTS-25-589-1.pdf` (SI SIV-DTS-25-589, PO Set 48, Status: `pending`)
- **Doc 175**: `SIV-DTS-25-589-3.pdf` (PO 4500497953 p1, PO Set 48, Status: `pending`)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 47: Doc 168 (SI), Doc 171 (PO p1), Doc 172 (PO p2)
- **Doc 168**: Drilling Tools Invoice `SIV-DTS-25-588` to Saudi International Energy Services Co (KCA Deutag).
  - Explicit header: `Buyers Order No : 4500496313`, `Delivery Note : GDN-DTS-25-520, GDN-DTS-25-626`.
  - In DB: `dn_no = NULL` (missed!).
  - Line 1: `BROTHER TAPE TZE-241 18MM BLACK ON WHITE`, Qty 10.0, Rate 57.72 SAR.
  - Line 2: `SLEDGE HAMMER, 6LB, 24" RUBBER REINFORCED HANDLE`, Qty 2.0, Rate 867.30 SAR.
- **Doc 171**: KCA Deutag PO 4500496313 page 1 of 3 (header/terms). DB correctly records 0 lines.
- **Doc 172**: KCA Deutag PO 4500496313 page 2 of 3 (items table).
  - Item 10: Brother Labelling Tape, Qty 10 PC, Unit Price printed as `57,72` SAR (comma decimal!).
    - **CRITICAL PARSER BUG**: In DB, `unit_price = 0.0`! The comma decimal parsing failure struck again, dropping the price to zero.
  - Item 20: Sledge Hammer 6 lbs, Qty 2 EA, Unit Price printed as `867.30` SAR (dot decimal!).
    - In DB: `unit_price = 867.3`!
  - **Reconciliation Reality**:
    - Both lines match 100% in quantities (10.0 and 2.0) and real unit prices (57.72 and 867.30).
    - The PO set is pending due to unlinked Delivery Notes and artificial price mismatch caused by the comma decimal drop bug.

#### 2. PO Set 48: Doc 173 (SI) & Doc 175 (PO p1)
- **Doc 173**: Drilling Tools Invoice `SIV-DTS-25-589` to KCA Deutag.
  - Header: `Buyers Order No : 4500497953`, `Delivery Note : GDN-DTS-25-627`.
  - In DB: `dn_no = NULL` (missed).
  - Line 1: `SERVICE KIT -1000/3000/5000/7000HRS`, Qty 6.0, Rate 1,650.0 SAR.
  - Line 2: `SERVICE KIT - 2000HRS 6000HRS`, Qty 4.0, Rate 5,300.0 SAR.
  - Extracted lines match the printed document 100%.
- **Doc 175**: KCA Deutag PO 4500497953 page 1 of 3 (terms/instructions). DB correctly records 0 lines.

---

### Core Findings Summary
1. **What was Correct**:
   - Clean extraction of item descriptions and quantities for all lines across both sets.
   - Successful handling of dot-decimal price `867.30` on Doc 172.
2. **What was Wrong**:
   - **Comma Decimal Bug Reproduced**: Doc 172 Item 10 printed `57,72` SAR was parsed as `0.0` in the database.
3. **What was Misrepresented**:
   - Item 10 price in DB is `0.0`, creating a false discrepancy with the supplier's invoice rate of `57.72`.
4. **What was Missed**:
   - Delivery note numbers `GDN-DTS-25-520, GDN-DTS-25-626` on Doc 168 and `GDN-DTS-25-627` on Doc 173 were missed.
5. **What We Need to Do to Improve**:
   - Implement universal comma/period decimal normalisation (`str.replace(',', '.')`) across all price parsing routines.
