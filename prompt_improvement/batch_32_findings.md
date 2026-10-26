# Audit Findings — Batch 32 (Docs 194 - 198)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 194`: `SIV-RAK-25-2937-Northern-1.pdf` (Sales Invoice / SI) — PO Set 54 (`quarantined`)
  - `Doc 195`: `SIV-RAK-25-2937-Northern-2.pdf` (Delivery Note / DN) — PO Set 54 (`quarantined`)
  - `Doc 196`: `SIV-RAK-25-2937-Northern-3.pdf` (Purchase Order / PO, 2 pages) — PO Set 54 (`quarantined`)
  - `Doc 197`: `SIV-RAK-25-2938-Northern-1.pdf` (Sales Invoice / SI) — PO Set 55 (`quarantined` pending PO)
  - `Doc 198`: `SIV-RAK-25-2938-Northern-2.pdf` (Delivery Note / DN) — PO Set 55 (`quarantined` pending PO)
- **Primary Companies/Vendors**:
  - **Vendor**: RAK ENERGY SERVICES & SUPPLIES WLL (GWC Logistics Park C, Al Wukair Store B3 A9-05, Qatar)
  - **Client / Operator**: Northern Offshore Drilling Operations Ltd. (Doha, Qatar) / Rig: "ENERGY EMBRACER"

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 194: `SIV-RAK-25-2937-Northern-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Header: `RAK ENERGY SERVICES & SUPPLIES WLL`, `rak energy` logo top right
  - Invoice No: `SIV-RAK-25-2937`
  - Buyer's Order No: `90139625`
  - Delivery Note: `GDN-RAK-25-3108`
  - Date: `28/10/2025`
  - Client: `Northern Offshore Drilling Operations Ltd.`
- **Visual Line Items**:
  - Line 1: `SL 1`, Description: `OPTIMA Red Top (RT S 4.2), Model No: 8002-250, Part No: 802 250 000 888 2 \n Line Item - 1`, Qty: `1.00 EA`, Rate: `850.00`, Amount: `850.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139625` (Correct)
  - `si_no`: `SIV-RAK-25-2937` (Correct)
  - Line 1: Line Item `1`, Qty `1.0`, Unit Price `850.0` (Correct)

### Doc 195: `SIV-RAK-25-2937-Northern-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3108`
  - Buyers Order No: `90139625`
  - Date: `25/10/2025`
- **Visual Line Items**:
  - Line 1: `SL 1`, Description: `OPTIMA Red Top (RT S 4.2), Model No: 8002-250, Part No: 802 250 000 888 2 \n Line Item - 1`, Qty: `1.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139625` (Correct)
  - `dn_no`: `GDN-RAK-25-3108` (Correct)
  - Line 1: Line Item `1`, Qty `1.0`, Unit Price `0.0` (Correct)

### Doc 196: `SIV-RAK-25-2937-Northern-3.pdf` (2 Pages)
- **Visual Document Type**: `PO` (Title: `PURCHASE ORDER`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - Buyer: `Northern Offshore Drilling Operations Ltd.` (SAP PO form)
  - Purchase Order No: `90139625`
  - Order Date: `22.10.2025`
  - Vendor: `RAK ENERGY SERVICES & SUPPLIES WLL` (Vendor No: `200424`)
- **Visual Line Items**:
  - Item: `10`
  - Material / Description: `BATTERY, SEALED LEAD ACID, SPIRAL WOUND, MODEL RT S 4.2, 12 VOLTS... OPTIMA`, Qty: `1 EA`, Net Price: `850.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139625` (Correct)
  - Line 1: Line Item `10`, Qty `1.0`, Unit Price `850.0` (Extracted faithfully from PO)
- **Reconciliation Failure Cause in PO Set 54**:
  - PO extracted line `10`.
  - SI and DN extracted line `1` (from RAK Energy's serial column `SL 1`).
  - Result: Discrepancy between Line `1` and Line `10` caused PO Set 54 to quarantine (`Reconciliation mismatch`).
  - Notice on RAK Energy's SI and DN description: The vendor explicitly wrote `Line Item - 1` inside their description text!

### Doc 197: `SIV-RAK-25-2938-Northern-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-2938`
  - Buyer's Order No: `90139670`
  - Delivery Note: `GDN-RAK-25-3109`
  - Rig No: `ENERGY EMBRACER`
- **Visual Line Items**:
  - SL 1: `Bearing 62213 2RS CHINA \n Line Item - 1`, Qty `4.00 EA`, Rate `97.00`
  - SL 2: `Bearing 62211 2RS TIMKEN \n Line Item - 2`, Qty `4.00 EA`, Rate `188.00`
  - SL 3: `Oil Seal 75 X 95 X 10mm \n Line Item - 3`, Qty `6.00 EA`, Rate `30.25`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139670` (Correct)
  - `si_no`: `SIV-RAK-25-2938` (Correct)
  - Line 1: Line `1`, Qty `4.0`, Unit Price `97.0` (Correct)
  - Line 2: Line `2`, Qty `4.0`, Unit Price `188.0` (Correct)
  - Line 3: Line `3`, Qty `6.0`, Unit Price `30.25` (Correct)

### Doc 198: `SIV-RAK-25-2938-Northern-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3109`
  - Buyers Order No: `90139670`
  - Total Qty: `14.00`
- **Visual Line Items**:
  - SL 1: `Bearing 62213 2RS CHINA \n Line Item - 1`, Qty `4.00 EA`
  - SL 2: `Bearing 62211 2RS TIMKEN \n Line Item - 2`, Qty `4.00 EA`
  - SL 3: `Oil Seal 75 X 95 X 10mm \n Line Item - 3`, Qty `6.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `90139670` (Correct)
  - `dn_no`: `GDN-RAK-25-3109` (Correct)
  - All 3 line items extracted with 100% precision.

---

## 3. Brand & Document Diagnostics: RAK Energy vs Northern Offshore Drilling

| Attribute | Visual Feature on Document | Prompt Engineering Implication |
|---|---|---|
| **Company Identity** | Header left: `RAK ENERGY SERVICES & SUPPLIES WLL`<br>Logo right: modern teal/blue lowercase `rak energy` | Clear vendor identifier. |
| **Doc Types** | Centered bold black title: `SALES INVOICE` vs `DELIVERY NOTE` | 100% unambiguous document classification. |
| **Metadata Box** | Right hand structured table block:<br>`Invoice No : SIV-RAK-25-XXXX`<br>`Buyer's Order No : 90XXXXXX`<br>`Delivery Note : GDN-RAK-25-XXXX` | Buyer's Order No is the PO number. Always labeled `Buyer's Order No :` or `Buyers Order No :`. |
| **Line Numbering Peculiarity** | Vendor prints `SL` (Serial 1, 2, 3...) in table column 1, BUT also appends `Line Item - X` in the description block. Client's SAP PO uses Step-10 (`Item 10`, `Item 20`, `Item 30`). | **CRITICAL INSIGHT FOR PROMPT**: On Northern Offshore Drilling / SAP POs, line items are step-10 (`10, 20, 30`). If a vendor invoice writes serial `1, 2, 3`, prompt instructions must guide extraction to normalize or preserve the buyer's line reference if present in description. |
