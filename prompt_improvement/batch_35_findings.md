# Audit Findings — Batch 35 (Docs 215 - 217, 224, 225)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 215`: `SIV-RAK-25-2968-Noble-1.pdf` (Sales Invoice / SI) — PO Set 61 (`quarantined`)
  - `Doc 216`: `SIV-RAK-25-2968-Noble-2.pdf` (Delivery Note / DN) — PO Set 61 (`quarantined`)
  - `Doc 217`: `SIV-RAK-25-2968-Noble-3.pdf` (Purchase Order / PO, 13 pages) — PO Set 61 (`quarantined`)
  - `Doc 224`: `SIV-RAK-25-3058-Ensco-1.pdf` (Sales Invoice / SI) — PO Set 64 (`quarantined` pending PO)
  - `Doc 225`: `SIV-RAK-25-3058-Ensco-2.pdf` (Delivery Note / DN) — PO Set 64 (`quarantined` pending PO)
- **Primary Companies/Vendors**:
  - **Vendor**: RAK ENERGY SERVICES & SUPPLIES WLL (Qatar)
  - **Clients**:
    - Noble Drilling Doha LLC (Rig: `Noble Mick O'Brien`)
    - Ensco Rowan / Valaris (Rig: `Valaris 110`)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 215: `SIV-RAK-25-2968-Noble-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-2968`
  - Buyer's Order No: `300101295`
  - Delivery Note: `GDN-RAK-25-3130`
  - Client: `NOBLE DRILLING DOHA LLC`
- **Visual Line Items**:
  - SL `1`: `Square D Power pact JJ250 3-Pole Circuit Breaker - New-Open Box \n Line Item - 19`, Qty `1.00 EA`, Rate `6,325.00 QAR`
  - SL `2`: `RELAY BOARD, MOVINCOOL, P/N : 484500-5320 \n Line Item - 21`, Qty `1.00 EA`, Rate `1,705.00 QAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `300101295` (Correct)
  - `si_no`: `SIV-RAK-25-2968` (Correct)
  - Extracted Line `19` and Line `21` faithfully from the description's `Line Item - X` label.

### Doc 216: `SIV-RAK-25-2968-Noble-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3130`
  - Buyers Order No: `300101295`
  - Rig / Location: `MICK OBRIEN`
- **Visual Line Items**:
  - SL `1`: `... Line Item - 19`, Qty `1.00 EA`
  - SL `2`: `... Line Item - 21`, Qty `1.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `300101295` (Correct)
  - `dn_no`: `GDN-RAK-25-3130` (Correct)
  - Extracted Line `19` and Line `21` (Correct).

### Doc 217: `SIV-RAK-25-2968-Noble-3.pdf` (13 Pages)
- **Visual Document Type**: `PO` (Title: `Purchase Order No. 300101295`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - Buyer: `Noble Drilling Doha LLC`
  - PO Number: `300101295`
  - Supplier: `RAK ENERGY SERVICES AND SUPPLIES`
  - Rig: `NOBLE MICK O'BRIEN`
- **Visual Line Items**:
  - A massive 13-page purchase order containing 25 distinct line items ranging from electrical elements to filters, welding electrodes, and duct tape.
  - Line 22: `BREAKER, CIRCUIT 250A 3P POWERPACT JJ250`, Qty `1.0`, Price `6325.0`
  - Line 24: `RELAY, BOARD; Mfg Name: MOVINCOOL`, Qty `1.0`, Price `1705.0`
- **DB Extracted Items**:
  - Extracted 22 line items across the entire 13 pages.
- **Root Cause of Set 61 Quarantine**:
  - Partial delivery/billing: PO has 25 line items, while vendor invoice/DN only bills 2 items.
  - Furthermore, vendor referred to them as `Line Item - 19` and `Line Item - 21` (off-by-two from PO's internal indices 22 and 24).
  - Genuine quarantine under business logic (unfulfilled PO balance).

### Doc 224: `SIV-RAK-25-3058-Ensco-1.pdf`
- **Visual Document Type**: `SI` (Title: `SALES INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Invoice No: `SIV-RAK-25-3058`
  - Buyer's Order No: `10006-0000079633` (Normalized: `100060000079633`)
  - Client: `ENSCO ROWAN` / Rig: `VALARIS 110`
- **Visual Line Items**:
  - SL `1`: `CONDOR Sideshield: Clear, PVC, Slip-On, P/N : 3BE76 \n Line Item - 1`, Qty `46.00 EA`, Rate `25.00`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079633` (Correct)
  - `si_no`: `SIV-RAK-25-3058` (Correct)
  - Line 1: Qty `46.0`, Price `25.0` (Correct)

### Doc 225: `SIV-RAK-25-3058-Ensco-2.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Del. Note No: `GDN-RAK-25-3203`
  - Buyers Order No: `10006-0000079633`
- **Visual Line Items**:
  - SL `1`: `CONDOR Sideshield... Line Item - 1`, Qty `46.00 EA`
- **DB Extracted Items**:
  - `po_no_normalized`: `100060000079633` (Correct)
  - `dn_no`: `GDN-RAK-25-3203` (Correct)
  - Line 1: Qty `46.0` (Correct)

---

## 3. Brand & Prompt Diagnostics

| Entity | Form Characteristics | Prompt Engineering Rule |
|---|---|---|
| **Noble Drilling** | Modern corporate PO, yellow/blue oval logo, right top `Purchase Order No. 300101295`. Multi-page tabular layout with lines labeled `1, 2, 3...`. | Extract `Purchase Order No.` exactly. Multi-page POs require full pagination accumulation without dropping middle pages. |
| **Ensco Rowan (Valaris)** | Client references `10006-0000079633`. Clean layout. | Strip hyphens during PO normalization (`100060000079633`). |
