## Audit Findings: Batch 16 (Docs 176, 177, 178, 180, 181)

### Document Details
- **Doc 176**: `SIV-DTS-25-589-4.pdf` (PO 4500497953 p2, PO Set 48, Status: `pending`)
- **Doc 177**: `SIV-DTS-25-589-5.pdf` (PO 4500497953 p3, PO Set 48, Status: `pending`)
- **Doc 178**: `SIV-DTS-25-596-1.pdf` (SI SIV-DTS-25-596, PO Set 49, Status: `pending`)
- **Doc 180**: `SIV-DTS-25-596-3.pdf` (PO 23073 p1, PO Set 49, Status: `pending`)
- **Doc 181**: `SIV-RAK-25-2876-Ades-1.pdf` (SI SIV-RAK-25-2876, PO Set 50, Status: `pending`)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 48: Doc 176 (PO p2) & Doc 177 (PO p3)
- **Doc 176**: KCA Deutag PO 4500497953 page 2 of 3 (item table):
  - Item 10: `SERVICE KIT: AIR COMPRESSORS... 1000 AND 5000 HOURS`, Qty 6 SET.
    - Printed Unit Price: `1.650,00` SAR (European format: thousands dot, comma decimal).
    - **CRITICAL PARSER DEFECT**: In DB, `unit_price = 0.0`! The parser failed on `1.650,00` and stored zero.
  - Item 20: `SERVICE KIT: AIR COMPRESSORS... 2000 AND 6000 HOURS`, Qty 4 SET.
    - Printed Unit Price: `5,300.00` SAR (Anglo format: comma thousands, dot decimal).
    - In DB: `unit_price = 5300.0` (correct).
  - **Reconciliation Impact**: Invoice SIV-DTS-25-589 (Doc 173) charges Line 1 @ 1,650.00 SAR and Line 2 @ 5,300.00 SAR. The PO and SI match 100% numerically, but DB records a price discrepancy due to the comma decimal drop.
- **Doc 177**: Page 3 of 3 (certifications and total 31,100.00 SAR). Extracted correctly with 0 line items.

#### 2. PO Set 49: Doc 178 (SI) & Doc 180 (PO p1)
- **Doc 178**: Drilling Tools Invoice `SIV-DTS-25-596`.
  - Header: `Buyers Order No : 23073`, `Delivery Note : GDN-DTS-25-632`.
  - In DB: `dn_no = NULL` (missed).
  - Lines 1–3: Hex Bolt (Qty 32, Rate 15.68), Spring Washer (Qty 32, Rate 0.80), Nylock Nut (Qty 32, Rate 11.98).
- **Doc 180**: ADES Standard PO 23073 page 1 of 7.
  - Lines 1–3: Exactly identical quantities (32 each) and unit prices (15.68, .8, 11.98). Total: 910.72 SAR.
  - **Reconciliation Reality**:
    - **100% exact numerical agreement** across all lines, quantities, and prices!
    - Set remains pending purely because of the unlinked delivery note.

#### 3. PO Set 50: Doc 181 (SI)
- **Doc 181**: RAK Energy Services & Supplies Sales Invoice `SIV-RAK-25-2876`.
  - Header: `Buyer's Order No : 13060`, `Delivery Note : GDN-RAK-25-3081`.
  - In DB: `dn_no = NULL` (missed).
  - Item: `Ty-Down straps for helideck 9m 5000kg`, Qty 4.0 EA, Rate 270.00 QAR.
  - Extracted accurately as Line 2 (based on text note `Line Item - 2`).

---

### Core Findings Summary
1. **What was Correct**:
   - Flawless line extraction for PO Set 49 (Doc 178 and Doc 180) with 100% mathematical match (910.72 SAR).
   - Correct extraction of RAK Energy invoice item, qty, and price.
2. **What was Wrong**:
   - European thousand/decimal representation `1.650,00` on Doc 176 caused unit price to drop to `0.0`.
3. **What was Misrepresented**:
   - PO Set 49 is marked `pending` despite perfect 3-way line item and price alignment.
4. **What was Missed**:
   - `Delivery Note : GDN-DTS-25-632` on Doc 178 and `GDN-RAK-25-3081` on Doc 181 were completely omitted.
5. **What We Need to Do to Improve**:
   - Implement locale-aware float parser that detects European number formatting (`1.650,00` -> `1650.00`).
   - Add RAK Energy invoice template parser to capture `Delivery Note : GDN-RAK-*` and `Buyer's Order No`.
