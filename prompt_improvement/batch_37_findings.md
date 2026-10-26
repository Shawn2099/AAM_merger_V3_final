# Audit Findings — Batch 37 (Docs 231, 232, 242, 243, 244)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 231`: `SIV-RAK-25-3060-Ensco-2.pdf` (Delivery Note / DN) — PO Set 66 (`quarantined`)
  - `Doc 232`: `SIV-RAK-25-3060-Ensco-3.pdf` (Purchase Order / PO, 3 pages) — PO Set 66 (`quarantined`)
  - `Doc 242`: `SIV-RAK-25-3130-Ensco-1.pdf` (Sales Invoice / SI) — PO Set 70 (`quarantined`)
  - `Doc 243`: `SIV-RAK-25-3130-Ensco-2.pdf` (Delivery Note / DN) — PO Set 70 (`quarantined`)
  - `Doc 244`: `SIV-RAK-25-3130-Ensco-3.pdf` (Purchase Order / PO, 2 pages) — PO Set 70 (`quarantined`)
- **Primary Companies/Vendors**:
  - **Vendor**: RAK ENERGY SERVICES & SUPPLIES WLL (Qatar)
  - **Buyer / Client**: Ensco Offshore International Company / Valaris (Rig: `Valaris 110`)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 231: `SIV-RAK-25-3060-Ensco-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3183`
  - Buyers Order No: `10006-0000079469`
  - Total Qty: `5.00`
- **Visual Line Items**:
  - SL `1`: `Bell Ear Plugs, 33dB... HONEYWELL HOWARD LEIGHT... \n Line Item - 3`, Qty `5.00 BOX`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079469` (Correct)
  - `dn_no`: `GDN-RAK-25-3183` (Correct)
  - Line `3`: Qty `5.0` (Correct)

### Doc 232: `SIV-RAK-25-3060-Ensco-3.pdf` (3 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order`, Logo: `Valaris`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `10006-0000079469`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - Line `1-1`: `LUBRICANT, SILICONE, 100 GRAM`, Qty `24.00 EA`, Price `105.00`
  - Line `2-1`: `GAS-CALIBRATION, CALGAZ...`, Qty `1.00 EA`, Price `935.00`
  - Line `3-1`: `EARPLUGS, HOWARD LEIGHT, MAX-30...`, Qty `5.00 EA`, Price `210.00`
  - Line `4-1`: `LAMP-FLUORESCENT...`, Qty `100.00 EA`, Price `3.75`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079469` (Correct)
  - Extracted Lines `1-1`, `2-1`, `3-1`, `4-1` with 100% numerical fidelity.
- **Set 66 Quarantine Explanation**:
  - Partial delivery: Only Item 3 (`3-1`) was delivered and invoiced (Qty 5.0).
  - Legitimate quarantine due to open items 1, 2, and 4 remaining undelivered.

### Doc 242: `SIV-RAK-25-3130-Ensco-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-3130`
  - Buyer's Order No: `10006-0000079573`
  - Delivery Note: `GDN-RAK-25-3299`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - SL `1`: `StarTech.com USB 2.0 USB A Male to RS232... \n Line Item - 1`, Qty `1.00 EA`, Rate `426.00 QAR`, Amount `426.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079573` (Correct)
  - `si_no`: `SIV-RAK-25-3130` (Correct)
  - Line 1: Qty `1.0`, Price `426.0` (Correct)

### Doc 243: `SIV-RAK-25-3130-Ensco-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3299`
  - Buyers Order No: `10006-0000079573`
- **Visual Line Items**:
  - SL `1`: `StarTech.com USB 2.0... \n Line Item - 1`, Qty `1.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079573` (Correct)
  - `dn_no`: `GDN-RAK-25-3299` (Correct)
  - Line 1: Qty `1.0` (Correct)

### Doc 244: `SIV-RAK-25-3130-Ensco-3.pdf` (2 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order`, Logo: `Valaris`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `10006-0000079573`
  - Rig: `VALARIS 110`
- **Visual Line Items**:
  - Line `1-1`: `CABLE-ADAPTER,STARTECH,1P10FFCN-USB-SERIAL...`, Qty `1.00 EA`, Unit Price `426.00 QAR`, Amount `426.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079573` (Correct)
  - Line `1-1`: Qty `1.0`, Price `426.0` (Correct)
- **CRITICAL RECONCILIATION FINDING IN PO SET 70**:
  - PO: 1 EA at 426.00 QAR
  - SI: 1 EA at 426.00 QAR
  - DN: 1 EA delivered
  - All numbers, values, and items match with 100% mathematical precision.
  - Set 70 was quarantined solely because PO line is string `"1-1"` while SI/DN line is string `"1"`.

---

## 3. Brand & Prompt Engineering Diagnostics

1. **Systematic False Quarantine Mechanism Identified**:
   - Both Set 65 and Set 70 are single-item Valaris purchase orders fulfilled completely in one delivery and invoice.
   - Both sets failed auto-merge because Valaris ERP generates line identifiers with a release suffix (`1-1`, `2-1`, `3-1`), whereas vendors invoice them as line `1`, `2`, `3`.
   - **Resolution Rule**: In `src/app/services/sanitizer.py` (or prompt extraction normalization), any line identifier of the form `^\s*(\d+)-\d+\s*$` should normalize to `\1` when comparing against standard integer/serial line numbers.
