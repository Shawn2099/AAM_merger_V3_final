## Audit Findings: Batch 19 (Docs 262, 264, 266, 267, 269)

### Document Details
- **Doc 262**: `SIV-RHO-26-1896B.pdf` (PO 60213576OPG, PO Set 76, Status: `pending`)
- **Doc 264**: `SIV-RHO-26-1914 A.pdf` (DN GDN-RHO-26-2183, PO Set 77, Status: `pending`)
- **Doc 266**: `SIV-RHO-26-1914B.pdf` (PO 4270006645SNN, PO Set 77, Status: `pending`)
- **Doc 267**: `SIV-RHO-26-233.pdf` (SI SIV-RHO-26-233, PO Set 78, Status: `pending`)
- **Doc 269**: `SIV-RHO-26-233B.pdf` (PO 4500362256, PO Set 78, Status: `pending`)

---

### Visual Inspection vs DB Comparison

#### 1. PO Set 76: Doc 262 (PO)
- **Doc 262**: Gulf Aluminium Rolling Mill (GARMCO) PO `60213576 OPG` to RAAS Oilfield Services.
  - Line 1: `JOTUN THINNER NO.2`, Qty 15 LITRE, Unit Price 2.2500 BD.
  - Line 2: `JOTUN THINNER NO.7`, Qty 15 LITRE, Unit Price 2.3000 BD.
  - Total: 68.250 BD. Extracted into DB with 100% precision.

#### 2. PO Set 77: Doc 264 (DN) & Doc 266 (PO)
- **Doc 264**: RAAS Delivery Note `GDN-RHO-26-2183` to Bahrain Airport Services (BAS).
  - P.O: `4270006645 -SNN`. Line 1: `Mixer/Grinder Machine With Stainless Ste`, Qty 1.0 NOS.
- **Doc 266**: BAS Purchase Order `4270006645 - SNN`.
  - Line 1: `Mixer/Grinder Machine With Stainless Ste`, Qty 1 NOS, Unit Price 42.000 BHD. Total: 42.000 BHD.
  - Perfect 1:1 match between PO and DN. Set is pending awaiting invoice submission.

#### 3. PO Set 78: Doc 267 (SI) & Doc 269 (PO) — Critical 3-Decimal (Fils) Price Bug Uncovered
- **Doc 267**: RAAS Tax Invoice `SIV-RHO-26-233` to Armacell Middle East Company S.P.C.
  - P.O. #: `4500362256`. Total Amount: 92.650 BHD.
  - Line 1: `SPRAY PAINT - RAL6011 RESEDA GREEN`, Qty 12.00, Unit Price printed clearly as `6.325` BHD!
  - Lines 2–4: Self Drill Screws (Qty 1 @ 6.500, Qty 1 @ 6.000) and Silicone Sealant (Qty 5 @ 0.850).
  - **DEFECT**: In DB, Line 1 `unit_price = 0.0`!
- **Doc 269**: Armacell Purchase Order `4500362256`.
  - Line 10: `Paint Spray - RAL 6011`, Qty 12 PC, Unit Price printed clearly as `6.325` BHD, Total: 75.900 BHD.
  - Lines 20–40: Screws and sealant matching 6.500, 6.000, 0.850 BHD.
  - Total: 92.650 BHD.
  - **DEFECT**: In DB, Line 10 `unit_price = 0.0`!
- **ROOT CAUSE DIAGNOSIS**:
  - Bahraini Dinar (BHD) prices use 3 decimal places (1 Dinar = 1,000 fils).
  - Lines with ending zeroes (`6.500`, `6.000`, `0.850`) were parsed because they collapsed to standard 1 or 2 decimals (`6.5`, `6.0`, `0.85`).
  - Line 1 (`6.325`) has 3 non-zero decimal digits, violating a hardcoded 2-decimal regex or float assumption, causing price parsing to abort and default to `0.0` on both PO and Invoice!
- **Reconciliation Reality**:
  - The documents are in **100% mathematical and commercial agreement**: 4 lines, total 92.650 BHD net, 101.915 BHD gross.
  - The system records both line prices as `0.0`, masking this perfect match.

---

### Core Findings Summary
1. **What was Correct**:
   - Clean extraction of BAS and GARMCO purchase orders.
   - Exact line extraction of quantities and descriptions across Armacell PO and RAAS invoice.
2. **What was Wrong**:
   - **3-Decimal Price Parser Failure**: Unit price `6.325` BHD was dropped to `0.0` on both Doc 267 and Doc 269.
3. **What was Misrepresented**:
   - Armacell PO Set 78 shows zero-price line items in the database, despite having full commercial pricing printed.
4. **What was Missed**:
   - Delivery note numbers in table columns (`GDN-RHO-26-92`) on Doc 267 were missed.
5. **What We Need to Do to Improve**:
   - Support arbitrary decimal precision (specifically 3 decimals for BHD/OMR/KWD/JOD currencies) in numerical price parsers.
