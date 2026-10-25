# Audit Findings — Batch 34 (Docs 204 - 208)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 204`: `SIV-RAK-25-2941-Northern-2.pdf` (Delivery Note / DN) — PO Set 57 (`quarantined`)
  - `Doc 205`: `SIV-RAK-25-2941-Northern-3.pdf` (Purchase Order / PO, 2 pages) — PO Set 57 (`quarantined`)
  - `Doc 206`: `SIV-RAK-25-2942-Northern-1.pdf` (Sales Invoice / SI) — PO Set 58 (`quarantined`)
  - `Doc 207`: `SIV-RAK-25-2942-Northern-2.pdf` (Delivery Note / DN) — PO Set 58 (`quarantined`)
  - `Doc 208`: `SIV-RAK-25-2942-Northern-3.pdf` (Purchase Order / PO, 2 pages) — PO Set 58 (`quarantined`)
- **Primary Companies/Vendors**:
  - **Vendor**: RAK ENERGY SERVICES & SUPPLIES WLL (Qatar)
  - **Buyer / Client**: Northern Offshore Drilling Operations Ltd. (Rig: `Energy Enticer`)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 204: `SIV-RAK-25-2941-Northern-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3107`
  - Buyers Order No: `90139734`
  - Date: `25/10/2025`
  - Total Qty: `12.00`
- **Visual Line Items**:
  - SL `1`: `Heavy-duty TIG welding gloves, Ansell, P/N : ACTIVARMR 43-217 \n Line Item - 1`, Qty `12.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139734` (Correct)
  - `dn_no`: `GDN-RAK-25-3107` (Correct)
  - Line 1: Qty `12.0`, Price `0.0` (Correct)

### Doc 205: `SIV-RAK-25-2941-Northern-3.pdf` (2 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order : 90139734 Rev : 1`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - Purchase Order: `90139734`
  - Order Date: `10/22/2025`
  - Rig: `210 - Energy Enticer`
- **Visual Line Items**:
  - Line `10`: `GLOVES WELDING, TIG GLOVES... (6 PAIR IN BOX)`, Qty `12 EA`, Price `33.00 QAR`, Total `396 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139734` (Correct)
  - Line 10: Qty `12.0`, Unit Price `33.0` (Correct)
- **Root Cause of Set 57 Quarantine**:
  - Doc 203 (SI) has Line `1` (Qty 12.0, Rate 33.0).
  - Doc 204 (DN) has Line `1` (Qty 12.0).
  - Doc 205 (PO) has Line `10` (Qty 12.0, Rate 33.0).
  - Quantities and prices match 100% perfectly. Quarantine was triggered strictly by `Line 1` vs `Line 10` step-10 indexing mismatch.

### Doc 206: `SIV-RAK-25-2942-Northern-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-2942`
  - Buyer's Order No: `90139766`
  - Delivery Note: `GDN-RAK-25-3105`
  - Rig No: `ENERGY ENTICER`
- **Visual Line Items**:
  - SL `1`: `BATTERY AAA ENERGIZER ( 24/PKT ) \n Line Item - 1`, Qty `19.00 PKT`, Rate `64.25`, Amount `1,220.75 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139766` (Correct)
  - `si_no`: `SIV-RAK-25-2942` (Correct)
  - Line 1: Qty `19.0`, Price `64.25` (Correct)

### Doc 207: `SIV-RAK-25-2942-Northern-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3105`
  - Buyers Order No: `90139766`
  - Date: `23/10/2025`
- **Visual Line Items**:
  - SL `1`: `BATTERY AAA ENERGIZER ( 24/PKT ) \n Line Item - 1`, Qty `19.00 PKT`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139766` (Correct)
  - `dn_no`: `GDN-RAK-25-3105` (Correct)
  - Line 1: Qty `19.0`, Price `0.0` (Correct)

### Doc 208: `SIV-RAK-25-2942-Northern-3.pdf` (2 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order : 90139766 Rev : 1`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - Purchase Order: `90139766`
  - Buyer: `Northern Offshore Drilling Operations Ltd.`
  - Rig: `210 - Energy Enticer`
- **Visual Line Items**:
  - Line `10`: `Duracell Alkaline AAA BATTERY, AAA, PACK OF 24`, Qty `32 PAC`, Price `64.25 QAR`, Total `2,056 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139766` (Correct)
  - Line 10: Qty `32.0`, Price `64.25` (Correct)
- **Root Cause of Set 58 Quarantine**:
  - Authentic partial delivery: PO ordered 32 packs, but vendor delivered and invoiced 19 packs (pending 13 packs).
  - Additionally compounded by Line `1` vs Line `10` indexing difference.

---

## 3. Brand & Prompt Engineering Diagnostics

1. **Extraction Fidelity**:
   - Both RAK Energy's invoice/delivery formats and Northern Offshore's SAP purchase order forms have 100% extraction precision on all metadata and financial fields.
2. **Reconciliation Alignment**:
   - The consistent `Line 1` on SI/DN vs `Line 10` on Northern Offshore PO across Sets 54, 55, 57, and 58 confirms that SAP step-10 numbering requires an explicit alignment rule in V3 reconciliation (e.g. matching `Line 1` to `Line 10` when there is only one item, or mapping vendor serial `1, 2, 3` to `10, 20, 30` when `Line Item - N` is present).
