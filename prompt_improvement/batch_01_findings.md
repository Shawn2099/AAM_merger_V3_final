# Audit Findings — Batch 01 (Docs 9 - 29)

## 1. Batch Overview
- **Documents Audited**:
  - `Doc 9`: `SIV-ARS-25-7296-pages-1.pdf` (SI) — PO Set 4 (`pending`)
  - `Doc 10`: `SIV-ARS-25-7296-pages-2.pdf` (DN) — PO Set 4 (`pending`)
  - `Doc 11`: `SIV-ARS-25-7296-pages-3.pdf` (PO) — PO Set 4 (`pending`)
  - `Doc 28`: `SIV-ARS-25-8144-pages-1.pdf` (SI) — PO Set 10 (`pending`)
  - `Doc 29`: `SIV-ARS-25-8144-pages-2.pdf` (DN) — PO Set 10 (`pending`)
- **Primary Companies/Vendors**:
  - Ibrahim Ali Al-Shab Trading Est. (Vendor)
  - ARO Drilling (Client)
  - SANAD / Saudi Aramco Nabors Drilling (Client)

---

## 2. Document-by-Document Visual vs Database Audit

### Doc 9: `SIV-ARS-25-7296-pages-1.pdf`
- **Visual Document Type**: `SI` (Tax Invoice)
- **DB Document Type**: `SI`
- **Visual Metadata**:
  - Document Number: `SIV-ARS-25-7296`
  - PO Reference: `50116501`
- **Visual Line Items**:
  - Line 2: `LIFTING BRIDLE,4 POINT,STX 519`, Qty `2.0 Each`, Unit Rate `3,250.00 SAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `50116501`
  - `si_no`: `SIV-ARS-25-7296`
  - Line `2`: `LIFTING BRIDLE,4 POINT,STX 519` | Qty `2.0` | Unit Price `3250.0` (3250000)
- **Status**: Exact match.

### Doc 10: `SIV-ARS-25-7296-pages-2.pdf`
- **Visual Document Type**: `DN` (Delivery Note)
- **DB Document Type**: `DN`
- **Visual Metadata**:
  - Document Number: `GDN-ARS-25-8417`
  - PO Reference: `50116501`
- **Visual Line Items**:
  - Item 2: `LIFTING BRIDLE,4 POINT,STX 519`, Part No `ST04519C`, Qty `2 Each`
- **DB Extracted Items**:
  - `po_no_normalized`: `50116501`
  - `dn_no`: `GDN-ARS-25-8417`
  - Line `2`: `LIFTING BRIDLE,4 POINT,STX 519` | Qty `2.0` | Unit Price `None`
- **Status**: Exact match.

### Doc 11: `SIV-ARS-25-7296-pages-3.pdf`
- **Visual Document Type**: `PO` (Purchase Order / DPO)
- **DB Document Type**: `PO`
- **Visual Metadata**:
  - Document Number: `50116501` Rev `0`
- **Visual Line Items**:
  - Line 1: Material `AD048963`, `PRESERVATIVE,EYE WASH;8OZ,6-20GAL/BOTTLE`, Qty `4 EA`, Price `110.00`
  - Line 2: Material `AD090028`, `LIFTING BRIDLE,4 POINT,STX 519`, Qty `2 EA`, Price `3,250.00`
- **DB Extracted Items**:
  - `po_no_normalized`: `50116501`
  - Line `1`: Material `AD048963` | Qty `4.0` | Unit Price `110.0`
  - Line `2`: Material `AD090028` | Qty `2.0` | Unit Price `3250.0`
- **Status**: Exact match on numbers. Note: Minor header leakage `LineRel No: 11 Part Main Group: CONS` appended to item description.

### Doc 28: `SIV-ARS-25-8144-pages-1.pdf`
- **Visual Document Type**: `SI` (Tax Invoice)
- **DB Document Type**: `SI`
- **Visual Metadata**:
  - Document Number: `SIV-ARS-25-8144`
  - PO Reference: `18232392`
- **Visual Line Items**:
  - Line 2: `PLUG, HEX HEAD, 3/8 NPT`, Qty `6.0 Each`, Rate `46.00 SAR`
- **DB Extracted Items**:
  - `po_no_normalized`: `18232392`
  - `si_no`: `SIV-ARS-25-8144`
  - Line `2`: `PLUG, HEX HEAD, 3/8 NPT` | Qty `6.0` | Unit Price `46.0`
- **Status**: Exact match.

### Doc 29: `SIV-ARS-25-8144-pages-2.pdf`
- **Visual Document Type**: `DN` (Delivery Note)
- **DB Document Type**: `DN`
- **Visual Metadata**:
  - Document Number: `GDN-ARS-25-9377`
  - PO Reference: `18232392`
- **Visual Line Items**:
  - Line 2: `PLUG, HEX HEAD, 3/8 NPT`, Qty `6 Each`, Part No `6-PH-SS`
- **DB Extracted Items**:
  - `po_no_normalized`: `18232392`
  - `dn_no`: `GDN-ARS-25-9377`
  - Line `2`: `PLUG, HEX HEAD, 3/8 NPT` | Qty `6.0`
- **Status**: Exact match.

---

## 3. Brand & Document Diagnostics

| Attribute | Visual Feature on Document | Prompt Engineering Implication |
|---|---|---|
| **Header Placement** | Al-Shab invoices have clear two-column header with `INVOICE NO: SIV-ARS-...` and `CUSTOMER P.O. NO: ...` in upper 20%. | Standard top 20% ROI captures headers accurately on clean computer-generated invoices. |
| **Line Numbering** | `S.No` / `Item` column provides clear sequential numbering (`1`, `2`). Line 2 corresponds to PO Line 2. | Preservation of exact numeric string matching is critical. |
| **Financial Fields** | Unit Rate formatted with standard comma thousands and period decimals (`3,250.00 SAR`). Currency stated in header/summary. | Handled accurately by current numeric parser. |

---

## 4. Prompt Engineering & Pipeline Recommendations
1. **Description Cleaning**: Strip trailing ERP metadata like `LineRel No: ...` and `Part Main Group: ...` that appear beneath description lines in ARO DPO purchase orders.
2. **Partial Fulfillment Visibility**: Set 4 reconcile reason (`delivered 0 of 4; invoiced 0 of 4`) is accurate because PO Line 1 was not yet delivered by the vendor. This is a legitimate business wait state, not an extraction defect.
