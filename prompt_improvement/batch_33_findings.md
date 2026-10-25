# Audit Findings — Batch 33 (Docs 199 - 203)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 199`: `SIV-RAK-25-2938-Northern-3.pdf` (Purchase Order / PO, 3 pages) — PO Set 55 (`quarantined`)
  - `Doc 200`: `SIV-RAK-25-2940-Northern-1.pdf` (Sales Invoice / SI) — PO Set 56 (`quarantined`)
  - `Doc 201`: `SIV-RAK-25-2940-Northern-2.pdf` (Delivery Note / DN) — PO Set 56 (`quarantined`)
  - `Doc 202`: `SIV-RAK-25-2940-Northern-3.pdf` (Purchase Order / PO, 3 pages) — PO Set 56 (`quarantined`)
  - `Doc 203`: `SIV-RAK-25-2941-Northern-1.pdf` (Sales Invoice / SI) — PO Set 57 (`quarantined` pending DN/PO)
- **Primary Companies/Vendors**:
  - **Vendor**: RAK ENERGY SERVICES & SUPPLIES WLL (Qatar)
  - **Buyer / Client**: Northern Offshore Drilling Operations SPC / Ltd. (Rigs: `Energy Embracer`, `Energy Enticer`)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 199: `SIV-RAK-25-2938-Northern-3.pdf` (3 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order : 90139670 Rev : 1`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `90139670`
  - Buyer: `Northern Offshore Drilling Operations SPC`
  - Supplier: `RAK ENERGY SERVICES & SUPPLIES`
  - Rig Name: `209 - Energy Embracer`
- **Visual Line Items**:
  - Line `10`: `BEARING, DEEP GROOVE, INNDER DIA: 65MM...`, Qty `4 EA`, Unit Price `97.00 QAR`
  - Line `20`: `BEARING, INNDER DIA: 55MM...`, Qty `4 EA`, Unit Price `188.00 QAR`
  - Line `30`: `OIL SEAL, DOUBLE LIP... SIZE: 75MM x 95MM x 10 MM`, Qty `6 EA`, Unit Price `30.25 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139670` (Correct)
  - Line items: Exactly extracted Lines `10`, `20`, `30` with Qties `4.0`, `4.0`, `6.0` and unit prices `97.0`, `188.0`, `30.25`.
- **Root Cause of Set 55 Quarantine**:
  - Reconciles Doc 197 (SI), Doc 198 (DN), and Doc 199 (PO).
  - All items, quantities, and prices are **100% IDENTICAL**.
  - However, RAK Energy invoices and delivery notes label items sequentially `1, 2, 3`, whereas Northern Offshore PO labels them `10, 20, 30`.
  - Because AAM V3 reconciliation requires strict line item number matching, Line 1 != Line 10 caused a false quarantine!

### Doc 200: `SIV-RAK-25-2940-Northern-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-2940`
  - Buyer's Order No: `90139566`
  - Delivery Note: `GDN-RAK-25-3127`
  - Rig No: `ENERGY ENTICER`
- **Visual Line Items**:
  - SL `1`: Description: `5" Angle Grinder, Ingersoll Rand, P/N : 3445MAX \n Line Item - 2`, Qty `3.00 EA`, Rate `1,650.00`
  - SL `2`: Description: `Mighty Severn M7_Air Pistol Needle Scaler 3500 B.P.M 'Sn-2128 \n Line Item - 3`, Qty `3.00 EA`, Rate `1,532.00`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139566` (Correct)
  - `si_no`: `SIV-RAK-25-2940` (Correct)
  - The model extracted Line `2` (Qty 3.0, Rate 1650.0) and Line `3` (Qty 3.0, Rate 1532.0).

### Doc 201: `SIV-RAK-25-2940-Northern-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3127`
  - Buyers Order No: `90139566`
  - Date: `25/10/2025`
- **Visual Line Items**:
  - SL `1`: `5" Angle Grinder... Line Item - 2`, Qty `3.00 EA`
  - SL `2`: `Mighty Severn M7... Line Item - 3`, Qty `3.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139566` (Correct)
  - `dn_no`: `GDN-RAK-25-3127` (Correct)
  - Extracted Line `2` and Line `3` with Qty `3.0`.

### Doc 202: `SIV-RAK-25-2940-Northern-3.pdf` (3 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order : 90139566 Rev : 1`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - Purchase Order: `90139566`
  - Buyer: `Northern Offshore Drilling Ops Ltd.`
  - Rig: `210 - Energy Enticer`
- **Visual Line Items**:
  - Line `10`: `NEEDLE, SCALER, REPLACEMENT SET... PACK OF 100`, Qty `3 BOX`, Price `190.00 QAR`
  - Line `20`: `GRINDER, ANGLE, AIR, 0.88 HP...`, Qty `3 EA`, Price `1,650.00 QAR`
  - Line `30`: `NEEDLE SCALER, AIR...`, Qty `3 EA`, Price `1,532.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139566` (Correct)
  - Extracted Lines `10`, `20`, `30` perfectly.
- **Root Cause of Set 56 Quarantine**:
  - PO contains items `10`, `20`, `30`.
  - SI and DN represent a partial delivery containing only items `20` and `30` (labeled by vendor as items `2` and `3`).
  - Item `10` is missing from the invoice/delivery note, causing legitimate quantity variance, plus the step-10 line indexing discrepancy (`2` vs `20`, `3` vs `30`).

### Doc 203: `SIV-RAK-25-2941-Northern-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-2941`
  - Buyer's Order No: `90139734`
  - Delivery Note: `GDN-RAK-25-3107`
  - Rig No: `ENERGY ENTICER`
- **Visual Line Items**:
  - SL `1`: `Heavy-duty TIG welding gloves, Ansell, P/N : ACTIVARMR 43-217 \n Line Item - 1`, Qty `12.00 EA`, Rate `33.00`, Amount `396.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139734` (Correct)
  - `si_no`: `SIV-RAK-25-2941` (Correct)
  - Line 1: Qty `12.0`, Price `33.0` (Correct)

---

## 3. Brand & Prompt Engineering Diagnostics

1. **Step-10 Alignment Rule**:
   - On Northern Offshore Drilling contracts (and common SAP systems like ADES, ARO, SLB), PO line numbers increment by 10 (`10, 20, 30...`).
   - Smaller suppliers (e.g. RAK Energy) create invoices in QuickBooks/Tally numbering `1, 2, 3...`, but often embed `Line Item - X` in the description.
   - **Prompt Guidance**: When extracting line items from vendor invoices/DNs referencing a buyer order, if the vendor embeds `Line Item - N` or `PO Line N`, extract `N` (or `N*10` if matching SAP convention) or provide secondary alignment hints.
2. **Partial Shipment Identification**:
   - In PO Set 56, only 2 of 3 PO items were shipped. The system correctly flagged the discrepancy, proving that the extraction captured the partial state faithfully.
