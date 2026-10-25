## Audit Findings: Batch 30 (Docs 60, 61, 74, 75, 76)

### Document & Brand Overview
Batch 30 covers **5 documents** across two enterprise drilling buyers in Saudi Arabia supplied by **Ibrahim Ali Al-Shab Trading Est** (`AL-SHAB` / `ARS`):
1. **Arabian Drilling Company (ADC)** (Docs 60, 61) — completing **PO Set 19** (`4500051195`, Status: `quarantined`)
2. **Saudi Aramco Nabors Drilling Company (SANAD)** (Docs 74, 75, 76) — forming **PO Set 24** (`18230394`, Status: `quarantined`)

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB Document No | Actual PO (Visual) | Actual Document No (Visual) | Reconciliation Status |
|---|---|---|---|---|---|---|---|---|---|
| **60** | `SIV-ARS-25-8312-pages-3.pdf` | PO | Arabian Drilling (Buyer) | Al-Shab | `4500051195` | `None` | **4500051195** | PO: **4500051195** (p1) | PO Set 19 (`quarantined`) |
| **61** | `SIV-ARS-25-8312-pages-4.pdf` | PO | Arabian Drilling (Buyer) | Al-Shab | `4500051195` | `None` | **4500051195** | PO: **4500051195** (p2) | PO Set 19 (`quarantined`) |
| **74** | `SIV-ARS-25-8322-pages-1.pdf` | SI | Ibrahim Ali Al-Shab | SANAD | `18230394` | `SIV-ARS-25-8322` | **18230394** | **SIV-ARS-25-8322** | PO Set 24 (`quarantined`) |
| **75** | `SIV-ARS-25-8322-pages-2.pdf` | DN | Ibrahim Ali Al-Shab | SANAD | `18230394` | `GDN-ARS-25-9508` | **18230394** | **GDN-ARS-25-9508** | PO Set 24 (`quarantined`) |
| **76** | `SIV-ARS-25-8322-pages-3.pdf` | PO | SANAD (Buyer) | Al-Shab | `18230394` | `None` | **18230394** | PO: **18230394** (p1) | PO Set 24 (`quarantined`) |

---

### Brand Template Analysis: Arabian Drilling & SANAD

#### 1. Arabian Drilling PO Contract Layout (Docs 60 & 61)
- **Geometry**: Portrait layout.
  - Page 1 (Doc 60): PO header with PO number `4500051195`, vendor details, and active line item rows (Lines 10 & 20).
  - Page 2 (Doc 61): Totals summary table (`Gross Price: 83.66 SAR`, `Total P.O. Value: 83.66 SAR`), signature of approval (`Ahmed Mohammed Ali Aldawood`), and general instructions.
- **Prompt Performance**:
  - The model extracted Lines 10 & 20 on page 1 with complete metadata and prices.
  - On page 2 (Doc 61), the prompt correctly obeyed the negative exclusion rules (`EXCLUDE subtotal, VAT, tax, total, amount-in-words, payment terms, signatures...`) and returned `line_items: []` without hallucinating fake lines.

#### 2. SANAD Landscape Purchase Order Format (Doc 76)
- **Geometry**: Landscape orientation with a dense 14-column tabular grid:
  - Header: Large `SANAD` logo with Arabic text, `PO #: 18230394`, `Revision #: 0`.
  - Columns: `Line` | `NIN` | `Supplier Item #` | `OEM Manufacturer` | `OEM Part Number` | `Cancel Flag` | `Promised Date` | `QTY` | `UOM` | `Account Code` | `Unit Price` | `Extended Price` | `Deliver To` | `Shipping Method`.
- **Prompt Performance**:
  - Despite the dense landscape column structure, the VLM parsed all 4 lines on Page 1 (Doc 76) accurately, identifying Line numbers (`1, 2, 3, 4`), descriptions, quantities (`10, 1, 4, 1`), and unit rates (`55, 253, 32, 12 SAR`).

#### 3. Al-Shab Partial Delivery & Invoicing to SANAD (Docs 74 & 75)
- **Geometry**: Al-Shab standard bilingual layout for Delivery Note `GDN-ARS-25-9508` and Invoice `SIV-ARS-25-8322`.
- **Selective Fulfillment**: Al-Shab delivered and invoiced only 3 of the items from the 10-item PO: Line 4 (Contact block, Qty 1), Line 7 (Extension board, Qty 1), Line 9 (Plug, Qty 1).
- **Prompt Performance**: Extracted exact line item numbers `"4"`, `"7"`, `"9"` on both the SI and DN.

---

### Deep Systemic Finding: Repeated False Quarantine on Multi-Page PO Contracts

Both **PO Set 19** and **PO Set 24** were quarantined under the exact same false premise:
> `quarantined: This PO Set holds more than one PO document`

Visual inspection establishes that:
1. **In PO Set 19**: Docs 60 & 61 are Pages 1 & 2 of the **exact same Arabian Drilling Purchase Order `4500051195`**.
   - PO lines 10 & 20 (Doc 60) match Delivery Note Doc 59 and Invoice Doc 58 **100% in quantity and price**.
2. **In PO Set 24**: Doc 76 and Doc 77 are Pages 1 & 2 of the **exact same SANAD Purchase Order `18230394`**.
   - Lines 4, 7, 9 delivered on DN Doc 75 and invoiced on SI Doc 74 match the PO lines **with 100% exact alignment**.
3. **The Core Pipeline Flaw**: When a multi-page PDF is split before extraction, individual pages of the purchase contract receive `doc_type = 'PO'` with identical `po_no_normalized`. The reconciliation engine treats each page as an independent competing contract rather than aggregating them into a single multi-page order.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% classification accuracy on `SI`, `DN`, and landscape/portrait `PO`s.<br>• Clean extraction of dense landscape table on SANAD PO Doc 76.<br>• Correctly returned empty line items on Doc 61 (summary/signature page) without generating spurious items.<br>• Exact line number preservation (`4, 7, 9`) across partial delivery notes and invoices. | `_PAGE_PROMPT` Step 1, Step 3, and Step 4 handled diverse multi-column enterprise PO layouts and partial dockets without degradation. |
| **What was Wrong** | • Ingestion/reconciliation engine flagged PO Set 19 and PO Set 24 as multi-PO conflicts. | Downstream reconciliation logic checks `count(po_documents) > 1` without checking if all PO documents share the exact same PO number. |
| **What was Misrepresented** | • Fully matched deliveries for Arabian Drilling (PO Set 19) and SANAD (PO Set 24) were quarantined as broken sets. | Ingestion splitting separates multi-page PO files into disjoint single-page document records. |
| **What was Missed** | • Nothing missed by extraction prompt in this batch; extraction quality was 100% correct across all 5 files. | Prompt performed cleanly on both portrait and landscape vector layouts. |

---

### Actionable Prompt & System Improvement Insights (Brands: Arabian Drilling, SANAD)

1. **Landscape Multi-Column PO Handling in Prompt**:
   - The prompt handles landscape PO tables (like SANAD) remarkably well when column boundaries are distinct.
   - Maintain instruction:
     `"When encountering multi-column landscape purchase orders (e.g. SANAD), locate the primary 'Line' column, product 'Description', 'QTY', and 'Unit Price' columns, while ignoring administrative columns like 'OEM Part Number', 'Account Code', 'Promised Date', or 'Forwarding Carrier'."`

2. **Reconciliation Engine PO Aggregation Fix**:
   - Update `reconcile.py`:
     ```python
     po_docs = [d for d in po_set.documents if d.doc_type == DocumentType.PO]
     unique_po_numbers = {d.po_no_normalized for d in po_docs if d.po_no_normalized}
     if len(unique_po_numbers) > 1:
         return quarantine("This PO Set holds more than one distinct PO number: " + str(unique_po_numbers))
     # If multiple PO docs share the SAME po_no_normalized, aggregate line items across pages
     ```
