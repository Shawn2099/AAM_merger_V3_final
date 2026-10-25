# Audit Findings — Batch 38 (Docs 254 - 256, 276, 277)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 254`: `SIV-RAK-25-3145-Ensco-1.pdf` (Sales Invoice / SI) — PO Set 74 (`quarantined`)
  - `Doc 255`: `SIV-RAK-25-3145-Ensco-2.pdf` (Delivery Note / DN) — PO Set 74 (`quarantined`)
  - `Doc 256`: `SIV-RAK-25-3145-Ensco-3.pdf` (Purchase Order / PO, 3 pages) — PO Set 74 (`quarantined`)
  - `Doc 276`: `SIV-RHO-26-687.pdf` (Sales Invoice / SI) — PO Set 81 (`quarantined` pending PO)
  - `Doc 277`: `SIV-RHO-26-687A.pdf` (Delivery Note / DN) — PO Set 81 (`quarantined` pending PO)
- **Primary Companies/Vendors**:
  - **Vendor 1**: RAK ENERGY SERVICES & SUPPLIES WLL (Client: Ensco Rowan / Valaris 110)
  - **Vendor 2**: RAAS OILFIELD SERVICES & SUPPLIES WLL (Bahrain) (Client: NOMAC AL DUR POWER & WATER)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 254: `SIV-RAK-25-3145-Ensco-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-3145`
  - Buyer's Order No: `10006-0000079731`
  - Delivery Note: `GDN-RAK-25-3354`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - SL `1`: `Calibration gas: • Hydrogen sulfide 25 ppm... \n Line Item - 2`, Qty `1.00 EA`, Rate `975.00 QAR`, Total `975.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079731` (Correct)
  - `si_no`: `SIV-RAK-25-3145` (Correct)
  - Line `2`: Qty `1.0`, Price `975.0` (Correct)

### Doc 255: `SIV-RAK-25-3145-Ensco-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3354`
  - Buyers Order No: `10006-0000079731`
- **Visual Line Items**:
  - SL `1`: `... Line Item - 2`, Qty `1.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079731` (Correct)
  - `dn_no`: `GDN-RAK-25-3354` (Correct)
  - Line `2`: Qty `1.0` (Correct)

### Doc 256: `SIV-RAK-25-3145-Ensco-3.pdf` (3 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order`, Logo: `Valaris`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `10006-0000079731`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - Line `1-1`: `CARTRIDGE-SENSOR,HONEYWELL...`, Qty `1.00 EA`, Price `2,300.00 QAR`
  - Line `2-1`: `GAS-CALIBRATION,CALGAZ,HYDROGEN SULFIDE...`, Qty `1.00 EA`, Price `975.00 QAR`
- **DB Extracted Items**:
  - Extracted Line `1-1` and Line `2-1` accurately.
- **Set 74 Quarantine Explanation**:
  - Partial delivery: Only Item 2 (`2-1`) was fulfilled. Item 1 (`1-1`) remains open, correctly placing the set in quarantine pending remaining fulfillment.

### Doc 276: `SIV-RHO-26-687.pdf`
- **Visual Document Type**: `SI` (Title: `TAX INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Header: `raas Oilfield services and supplies wll` (Kingdom of Bahrain)
  - INVOICE #: `SIV-RHO-26-687`
  - P.O. #: `8300046064`
  - Customer: `NOMAC AL DUR POWER & WATER SERVICES WLL`
- **Visual Line Items**:
  - Sr. `1`: `PLASTIC FUNNEL, SMALL" \n Line Item - 1`, Qty `3.00`, Unit Price `0.950 BHD`, Total `3.135 BHD`
  - Sr. `2`: `"EMERY PAPER GRIT 220, ALUMINUM OXIDE," \n Line Item - 25`, Qty `50.00`, Unit Price `0.086 BHD`, Total `4.730 BHD`
- **DB Extracted Items vs Discrepancies**:
  - `po_no_normalized`: `8300046064` (Correct)
  - Line `1`: Qty `3.0`, Price `0.95` (Correct)
  - Line `25`: Qty `50.0`, Price `0.0` (**BUG OBSERVED!**)
  - **Reason for 0.0 Price**: The unit price is `0.086 BHD` (3 decimal currency). `sanitizer.py` failed on 3 decimal digits and defaulted to `0.0`.

### Doc 277: `SIV-RHO-26-687A.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Doc No: `GDN-RHO-26-796`
  - P.O.: `8300046064`
  - Customer: `NOMAC AL DUR POWER & WATER SERVICES WLL`
- **Visual Line Items**:
  - Item 1: `PLASTIC FUNNEL, SMALL" \n Line Item - 1`, Qty `3.00 EACH`
  - Item 2: `"EMERY PAPER GRIT 220, ALUMINUM OXIDE," \n Line Item - 25`, Qty `50.00 EACH`
- **DB Extracted Items**:
  - Line `1`: Qty `3.0` (Correct)
  - Line `25`: Qty `50.0` (Correct)

---

## 3. Brand & Prompt Engineering Diagnostics

1. **3-Decimal Currency Price Drop (CRITICAL BUG)**:
   - In Doc 276 (RAAS Bahrain), BHD currency has 3 decimals (`0.086 BHD`).
   - The sanitizer or extraction prompt forced a 2-decimal expectation, causing `0.086` to be zeroed out (`0.0`).
   - **Fix**: Support 3-decimal currencies (BHD, KWD, OMR) in both the prompt instructions and python sanitizers (`int(round(val * 1000))`).
2. **Sub-Item Line Labeling**:
   - Both RAK Energy and RAAS use the `Line Item - N` description convention when delivering items against large master POs. Capturing `N` as the primary matching key ensures perfect grouping against customer ERP line numbers.
