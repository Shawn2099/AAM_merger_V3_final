# Audit Findings — Batch 40 (Docs 72, 73, 209, 210, 211)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 72`: `SIV-ARS-25-8321-pages-2.pdf` (Delivery Note / DN) — PO Set 23 (`mismatched`)
  - `Doc 73`: `SIV-ARS-25-8321-pages-3.pdf` (Purchase Order / PO) — PO Set 23 (`mismatched`)
  - `Doc 209`: `SIV-RAK-25-2964-Noble-1.pdf` (Sales Invoice / SI) — PO Set 59 (`mismatched`)
  - `Doc 210`: `SIV-RAK-25-2964-Noble-2.pdf` (Delivery Note / DN) — PO Set 59 (`mismatched`)
  - `Doc 211`: `SIV-RAK-25-2964-Noble-3.pdf` (Purchase Order / PO, 3 pages) — PO Set 59 (`mismatched`)
- **Primary Companies/Vendors**:
  - **Vendor 1**: IBRAHIM ALI ALSHAB TRADING EST. (Client: SANAD)
  - **Vendor 2**: RAK ENERGY SERVICES & SUPPLIES WLL (Client: Noble Drilling Doha LLC / Rig: `Noble Mick O'Brien`)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 72: `SIV-ARS-25-8321-pages-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`, Arabic/English)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del Note No: `GDN-ARS-25-9599`
  - P.O. #: `18230578`
  - Customer: `SAUDI ARAMCO NABORS DRILLING COMPANY` (SANAD)
- **Visual Line Items**:
  - Item 1: `RING, SOCKETRETAINING, IMPACT, 1.75" DIA...`, Qty `2 Each`
- **DB Extracted Items**:
  - Line 1: Qty `2.0` (Correct)

### Doc 73: `SIV-ARS-25-8321-pages-3.pdf`
- **Visual Document Type**: `PO` (Title: `PURCHASE ORDER`, Logo: `SANAD`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO #: `18230578`
  - Buyer: `SANAD - SHIP TO / BILL TO`
  - Supplier: `IBRAHIM AL SHAB TRADING EST`
- **Visual Line Items**:
  - Line 1: `RING, SOCKET RETAINING, SOCKET IMPACT, 1.75" DIA...`, Qty `10 EACH`, Unit Price `23 SAR`, Extended Price `230 SAR`
- **DB Extracted Items**:
  - Line 1: Qty `10.0`, Price `23.0` (Correct)
- **PO Set 23 Mismatched Explanation**:
  - PO ordered 10 units.
  - SI (Doc 71) invoiced 2 units.
  - DN (Doc 72) delivered 2 units.
  - This is an authentic partial delivery (2 of 10 delivered). System accurately marked status as `mismatched`.

### Doc 209: `SIV-RAK-25-2964-Noble-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-2964`
  - Buyer's Order No: `J255-300115876` (Normalized: `J255300115876`)
  - Delivery Note: `GDN-RAK-25-2604`
  - Client: `NOBLE DRILLING DOHA LLC`
- **Visual Line Items**:
  - SL `1`: `RS PRO Stainless Steel Pipe Fitting, Straight Circular Barrel Nipple... \n Line Item - 2`, Qty `2.00 EA`, Rate `44.00 QAR`, Total `88.00 QAR`
- **DB Extracted Items**:
  - Line `2`: Qty `2.0`, Price `44.0` (Extracted faithfully from `Line Item - 2`)

### Doc 210: `SIV-RAK-25-2964-Noble-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-2604`
  - Buyers Order No: `J255-300115876`
  - Stamp: `RIG: NOBLE MICK OBRIEN`
- **Visual Line Items**:
  - SL `1`: `... Line Item - 2`, Qty `2.00 EA`
- **DB Extracted Items**:
  - Line `2`: Qty `2.0` (Correct)

### Doc 211: `SIV-RAK-25-2964-Noble-3.pdf` (3 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order No. J255-300115876`, Logo: `NOBLE`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `J255-300115876`
  - Site: `NOBLE MICK O'BRIEN`
- **Visual Line Items**:
  - Line 1: `VALVE:BALL; VLV_SZ:1/2"...`, Qty `3.0`, Price `67.0`
  - Line 2: `NIPPLE:PIPE... RS COMPONENTS`, Qty `3.0`, Price `44.0`
  - Line 3: `COMPOUND THREAD SEALANT...`, Qty `1.0`, Price `143.0`
- **DB Extracted Items**:
  - Extracted Lines 1, 2, 3 with 100% precision.
- **PO Set 59 Mismatched Explanation**:
  - PO has 3 items (Qty 3, 3, 1).
  - SI and DN only billed and delivered 2 units of Item 2.
  - Correctly quarantined/mismatched due to remaining unfulfilled items.

---

## 3. Brand & Prompt Engineering Summary

- All 200 documents across 40 batches have now been completely audited and visually cross-referenced against database records!
- The visual analysis confirms that LLM OCR and extraction precision are remarkably high (~98%+ accuracy across 200 documents).
- The remaining failures fall into specific, predictable engineering buckets:
  1. Header ROI clipping on pink/RAAS forms (top 20% cutoff).
  2. Multi-page PO splitting triggering false multi-PO quarantine in Python.
  3. Step-10 (`10, 20, 30`) vs serial (`1, 2, 3`) and release-dash (`1-1`) indexing discrepancies.
  4. European comma decimal and 3-decimal currency dropping in `sanitizer.py`.
