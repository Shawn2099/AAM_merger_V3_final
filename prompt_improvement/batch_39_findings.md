# Audit Findings — Batch 39 (Docs 278, 1, 2, 263, 71)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 278`: `SIV-RHO-26-687B.pdf` (Purchase Order / PO, 1 page) — PO Set 81 (`quarantined`)
  - `Doc 1`: `DN2182.pdf` (Delivery Note / DN) — PO Set 1 (`mismatched`)
  - `Doc 2`: `PO2360.pdf` (Purchase Order / PO) — PO Set 1 (`mismatched`)
  - `Doc 263`: `SIV-RHO-26-1910.pdf` (Sales Invoice / SI) — PO Set 1 (`mismatched`)
  - `Doc 71`: `SIV-ARS-25-8321-pages-1.pdf` (Sales Invoice / SI) — PO Set 23 (`mismatched`)
- **Primary Companies/Vendors**:
  - **Vendor 1**: RAAS OILFIELD SERVICES & SUPPLIES WLL (Bahrain) (Clients: NOMAC AL DUR, URBASER BAHRAIN)
  - **Vendor 2**: IBRAHIM ALI ALSHAB TRADING EST. (Client: SANAD / Saudi Aramco Nabors Drilling)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 278: `SIV-RHO-26-687B.pdf` (1 Page)
- **Visual Document Type**: `PO` (Title: `Purchase Order : 8300046064`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - PO Number: `8300046064`
  - Buyer: `NOMAC Al Dur Power & Water Services WLL`
  - Footer says: `Page 1 of 13`
- **Visual Line Items**:
  - Table header appears at the very bottom: `Item | Material / Service Description | Quantity Unit | Price (BHD) | Total (BHD)`.
  - **NO line items appear on Page 1** (items begin on Page 2).
- **DB Extracted Items**:
  - `po_no_normalized`: `8300046064`
  - Line items: `[]` (0 items extracted).
- **Critical Finding**:
  - The input file in `data/input` is only a single page (`len(pages) == 1`) clipped from a 13-page contract. The model correctly extracted 0 items because the cropped page contains zero line items.

### Doc 1: `DN2182.pdf`
- **Visual Document Type**: `DN` (Title: `DELIVERY NOTE`)
- **DB Document Type**: `DN` (Correct)
- **Visual Metadata**:
  - Doc No: `GDN-RHO-26-2182`
  - P.O.: `LPO26/2360` (Normalized: `LPO262360`)
  - Customer: `URBASER BAHRAIN CO. W.L.L.`
- **Visual Line Items**:
  - Item 1: `Degreaser & Stain Remover Vehicle Washing Liquid \n Line Item - 1`, Qty `400.00 LTR`
- **DB Extracted Items**:
  - Line 1: Qty `400.0` (Correct)

### Doc 2: `PO2360.pdf`
- **Visual Document Type**: `PO` (Title: `PURCHASE ORDER`, Logo: `urbaser`)
- **DB Document Type**: `PO` (Correct)
- **Visual Metadata**:
  - Buyer: `Urbaser Bahrain Co W.L.L.`
  - PO No: `LPO26/2360`
  - Narration: `:Daily Cleaning Items Month of 01-05-26 to 31-07-26 Open Lpo`
- **Visual Line Items**:
  - S.No 1: ItemCode `UBHSC1139`, `Degreaser & Stain Remover Vehicle Washing Liquid`, Qty `4200.00 LTR`, Rate `0.550 BHD`, Total `2,310.000 BHD`
- **DB Extracted Items**:
  - Line 1: Qty `4200.0`, Price `0.55` (Correct)

### Doc 263: `SIV-RHO-26-1910.pdf`
- **Visual Document Type**: `SI` (Title: `TAX INVOICE`)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - INVOICE #: `SIV-RHO-26-1910`
  - P.O. #: `LPO26/2360`
  - Customer: `URBASER BAHRAIN CO.W.L.L.`
  - Delivery Note: `GDN-RHO-26-2182`
- **Visual Line Items**:
  - Sr. 1: `Degreaser & Stain Remover Vehicle Washing Liquid \n Line Item - 1`, Qty `400.00`, Unit Price `0.550 BHD`, Total `220.000 BHD`
- **DB Extracted Items**:
  - Line 1: Qty `400.0`, Price `0.55` (Correct)
- **PO Set 1 Status Diagnosis**:
  - Doc 1 (DN): 400 LTR
  - Doc 263 (SI): 400 LTR
  - Doc 2 (PO): 4200 LTR (Blanket / Open LPO for 3 months)
  - This is an authentic partial delivery (400 LTR delivered against 4200 LTR blanket PO). The system correctly identified the `mismatched` status.

### Doc 71: `SIV-ARS-25-8321-pages-1.pdf`
- **Visual Document Type**: `SI` (Title: `TAX INVOICE`, Arabic/English)
- **DB Document Type**: `SI` (Correct)
- **Visual Metadata**:
  - Vendor: `IBRAHIM ALI ALSHAB TRADING EST.`
  - Invoice No: `SIV-ARS-25-8321`
  - P.O. No: `18230578`
  - Client: `SAUDI ARAMCO NABORS DRILLING COMPANY` (SANAD)
- **Visual Line Items**:
  - SNO 1: `RING, SOCKETRETAINING, IMPACT, 1.75" DIA...`, Qty `2.0 Each`, Unit Rate `23.00 SAR`, Amount `46.00 SAR`
- **DB Extracted Items**:
  - Extracted Line 1, Qty `2.0`, Price `23.0` with 100% precision.

---

## 3. Brand & Prompt Engineering Diagnostics

1. **Urbaser Bahrain Open LPOs**:
   - Clean, professional purchase order layout.
   - Narration explicitly flags `Open Lpo`. In blanket procurement, deliveries occur in monthly tranches (e.g. 400 LTR against 4200 LTR).
2. **Missing Page Ingestion Guard**:
   - On Doc 278, the input PDF was truncated at page 1 of 13.
   - Prompt engineering recommendation: If a document footer says `Page 1 of N` (where N > total pages received), extract an advisory warning flag `missing_pages_detected: true` to inform the downstream reconciliation engine.
