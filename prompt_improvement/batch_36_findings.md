# Audit Findings — Batch 36 (Docs 226 - 230)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 226`: `SIV-RAK-25-3058-Ensco-3.pdf` (Purchase Order / PO, 2 pages) — PO Set 64 (`quarantined`)
  - `Doc 227`: `SIV-RAK-25-3059-Ensco-1.pdf` (Sales Invoice / SI) — PO Set 65 (`quarantined`)
  - `Doc 228`: `SIV-RAK-25-3059-Ensco-2.pdf` (Delivery Note / DN) — PO Set 65 (`quarantined`)
  - `Doc 229`: `SIV-RAK-25-3059-Ensco-3.pdf` (Purchase Order / PO, 2 pages) — PO Set 65 (`quarantined`)
  - `Doc 230`: `SIV-RAK-25-3060-Ensco-1.pdf` (Sales Invoice / SI) — PO Set 66 (`quarantined` pending DN/PO)
- **Primary Companies/Vendors**:
  - **Vendor**: RAK ENERGY SERVICES & SUPPLIES WLL (Qatar)
  - **Buyer / Client**: Ensco Offshore International Company / Valaris (Rig: `Valaris 110`)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 226: `SIV-RAK-25-3058-Ensco-3.pdf` (2 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order`, Logo: `Valaris`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `10006-0000079633`
  - Buyer: `Ensco Offshore International Company`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - Line `1-1`: `SIDESHIELD-EYEWEAR,TITMUS,CS98 ,T-LOC`, Qty `50.00 EA`, Price `25.00 QAR`, Total `1,250.00`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079633` (Correct)
  - Line `1-1`: Qty `50.0`, Price `25.0` (Extracted raw line string `1-1`)
- **Set 64 Quarantine Analysis**:
  - Doc 224 (SI) and Doc 225 (DN) delivered `46.0 EA` at `25.0 QAR` under Line `1`.
  - PO ordered `50.0 EA` under Line `1-1`.
  - Quarantine is justified due to a 4-unit quantity shortfall (46 delivered vs 50 ordered), compounded by the line key mismatch (`1` vs `1-1`).

### Doc 227: `SIV-RAK-25-3059-Ensco-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-3059`
  - Buyer's Order No: `10006-0000079657`
  - Delivery Note: `GDN-RAK-25-3237`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - SL `1`: `SAE J429 Grade 8 Ø1 1/4" (CADMIUM PLATED)... \n Line Item - 1`, Qty `20.00 EA`, Rate `40.00 QAR`, Amount `800.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079657` (Correct)
  - `si_no`: `SIV-RAK-25-3059` (Correct)
  - Line 1: Qty `20.0`, Price `40.0` (Correct)

### Doc 228: `SIV-RAK-25-3059-Ensco-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3237`
  - Buyers Order No: `10006-0000079657`
- **Visual Line Items**:
  - SL `1`: `SAE J429 Grade 8... Line Item - 1`, Qty `20.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079657` (Correct)
  - `dn_no`: `GDN-RAK-25-3237` (Correct)
  - Line 1: Qty `20.0` (Correct)

### Doc 229: `SIV-RAK-25-3059-Ensco-3.pdf` (2 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order`, Logo: `Valaris`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `10006-0000079657`
  - Buyer: `Ensco Offshore International Company`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - Line `1-1`: `BOLT-HEX,1-1/4-7 X 4-3/4 IN... SAE J429,GR 8`, Qty `20.00 EA`, Price `40.00 QAR`, Total `800.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079657` (Correct)
  - Line `1-1`: Qty `20.0`, Price `40.0` (Correct)
- **CRITICAL RECONCILIATION FINDING IN PO SET 65**:
  - PO: 20 EA at 40.0 QAR
  - SI: 20 EA at 40.0 QAR
  - DN: 20 EA delivered
  - **100% PERFECT MATCH** across all goods, quantities, and monetary values.
  - The **SOLE REASON** PO Set 65 was quarantined: Valaris PO line is formatted as `1-1` (Line-Schedule release), while SI and DN extracted `1`. Because strict string equality compares `"1"` to `"1-1"`, reconciliation failed!

### Doc 230: `SIV-RAK-25-3060-Ensco-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-3060`
  - Buyer's Order No: `10006-0000079469`
  - Delivery Note: `GDN-RAK-25-3183`
  - Rig No: `VALARIS 110`
- **Visual Line Items**:
  - SL `1`: `Bell Ear Plugs, 33dB... HONEYWELL HOWARD LEIGHT... \n Line Item - 3`, Qty `5.00 BOX`, Rate `210.00 QAR`, Amount `1,050.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079469` (Correct)
  - `si_no`: `SIV-RAK-25-3060` (Correct)
  - Line `3`: Qty `5.0`, Price `210.0` (Extracted from `Line Item - 3`)

---

## 3. Brand & Prompt Engineering Diagnostics: Valaris / Ensco

| Attribute | Visual Feature on Document | Prompt Engineering Implication |
|---|---|---|
| **Company Identity** | Header left: bold `Valaris`. Barcode block top right with PO number underneath. | Modern enterprise purchase order. |
| **PO Numbering** | `10006-0000079657` with hyphen. | Normalization correctly strips hyphen to `100060000079657`. |
| **Line Numbering Pattern** | Column header `Line` displays values in `Line-Release` format: `1-1`, `2-1`, `3-1`. | **MAJOR PROMPT RULE**: When extracting line numbers from Valaris / Ensco purchase orders, normalize `X-Y` by taking the base line index `X` (e.g. `1-1` → `1`), or normalize during reconciliation. Normalizing `1-1` to `1` instantly saves PO Set 65 and allows clean auto-merging! |
