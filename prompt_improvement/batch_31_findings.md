## Audit Findings: Batch 31 (Docs 77, 81, 82, 83, 84)

### Document & Brand Overview
Batch 31 covers **5 documents** across two global oilfield enterprise customers serviced by **Ibrahim Ali Al-Shab Trading Est** (`AL-SHAB` / `ARS`):
1. **Saudi Aramco Nabors Drilling Company (SANAD)** (Doc 77) — completing **PO Set 24** (`18230394`, Status: `quarantined`)
2. **Schlumberger Middle East S.A. (SLB)** (Docs 81, 82, 83, 84) — forming **PO Set 26** (`4800466856`, Status: `quarantined`)

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB Document No | Actual PO (Visual) | Actual Document No (Visual) | Reconciliation Status |
|---|---|---|---|---|---|---|---|---|---|
| **77** | `SIV-ARS-25-8322-pages-4.pdf` | PO | SANAD (Buyer) | Al-Shab | `18230394` | `None` | **18230394** | PO: **18230394** (p2) | PO Set 24 (`quarantined`) |
| **81** | `SIV-ARS-25-8332-pages-1.pdf` | SI | Ibrahim Ali Al-Shab | Schlumberger | `4800466856` | `SIV-ARS-25-8332` | **4800466856** | **SIV-ARS-25-8332** | PO Set 26 (`quarantined`) |
| **82** | `SIV-ARS-25-8332-pages-2.pdf` | DN | Ibrahim Ali Al-Shab | Schlumberger | `4800466856` | `GDN-ARS-25-9606` | **4800466856** | **GDN-ARS-25-9606** | PO Set 26 (`quarantined`) |
| **83** | `SIV-ARS-25-8332-pages-3.pdf` | PO | Schlumberger (Buyer) | Al-Shab | `4800466856` | `None` | **4800466856** | PO: **4800466856** (p1) | PO Set 26 (`quarantined`) |
| **84** | `SIV-ARS-25-8332-pages-4.pdf` | PO | Schlumberger (Buyer) | Al-Shab | `4800466856` | `None` | **4800466856** | PO: **4800466856** (p2) | PO Set 26 (`quarantined`) |

---

### Brand Template Analysis: Schlumberger (SLB) & SANAD

#### 1. Schlumberger Enterprise Purchase Order (`SLB`) (Docs 83 & 84)
- **Geometry & Fields**: Clean vector PDF with top-left `slb` logo.
  - Header: `Purchase order number: 4800466856`, `PO Version No: 4800466856-0`, `Purchase order date: 17-NOV-2025`.
  - Table: `Item` | `Vendor Part No. / SLB Part No - Description` | `Final destination` | `Quantity` | `UOM` | `Packaging Quantity` | `Unit Net price SAR` | `Net price SAR` | `Delivery Date`.
  - Nested Item Text: Multi-line technical item text sits directly beneath each product row (e.g. `Item text: Thread Compound TUBELOK...`).
- **Prompt Performance**:
  - The model extracted Lines 1–4 on Page 1 (Doc 83) and Lines 5–6 on Page 2 (Doc 84) with 100% precision.
  - Successfully concatenated the nested item text into the description without dropping quantities or unit prices.

#### 2. SANAD Landscape Purchase Order Page 2 (Doc 77)
- **Geometry**: Landscape table continuation.
  - Header: `Header Information: PO 18230394`, `PURCHASE ORDER`, `Page 2 of 2`.
  - Rows: Lines 5, 6, 7, 8, 9 with quantities, UOMs, and unit prices in SAR.
- **Prompt Performance**:
  - Parsed all 5 remaining lines flawlessly, completing the full PO line catalog for PO Set 24.

---

### Deep Systemic Finding: 100% Valid Reconciled Sets Trapped in False Quarantine

Both **PO Set 24** and **PO Set 26** are physically **flawless, verified 3-way matches** that are falsely quarantined:

1. **PO Set 24 (SANAD `18230394`)**:
   - Invoice Doc 74 billed Lines 4 (Qty 1), 7 (Qty 1), 9 (Qty 1).
   - Delivery Note Doc 75 delivered Lines 4 (Qty 1), 7 (Qty 1), 9 (Qty 1) (stamped by SANAD Central Warehouse).
   - PO Doc 76 (p1) ordered Line 4 (Qty 1); PO Doc 77 (p2) ordered Line 7 (Qty 1) and Line 9 (Qty 1).
   - **PO = DN = SI across all billed items**. The set is quarantined solely because the PO was 2 pages.

2. **PO Set 26 (Schlumberger `4800466856`)**:
   - PO Docs 83 & 84 ordered 6 items (Line 1: 100 Tubelok @ 240 SAR; Line 2: 25 Dry Moly @ 600 SAR; Lines 3–6 unfulfilled).
   - Invoice Doc 81 billed Lines 1 (100) & 2 (25) for 39,000 SAR net.
   - Delivery Note Doc 82 delivered Lines 1 (100) & 2 (25) (stamped by SLB receiving).
   - **PO = DN = SI across all billed items**. The set is quarantined solely because the PO was 2 pages.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% extraction accuracy on Schlumberger POs (Docs 83 & 84), including nested `Item text` lines.<br>• Clean handling of SANAD landscape table continuation (Doc 77).<br>• Accurate extraction of partial dockets on Al-Shab SI/DN (Docs 81 & 82). | `_PAGE_PROMPT` Step 1, Step 3, and Step 4 handled multi-line descriptions and price tables with zero error. |
| **What was Wrong** | • Pipeline treated multi-page PO files as conflicting PO documents, quarantining PO Set 24 and PO Set 26. | Ingestion layer split multi-page PO contracts into multiple single-page records, triggering downstream `po_doc_count > 1` rule. |
| **What was Misrepresented** | • Both PO Set 24 and PO Set 26 were marked `quarantined` as invalid/conflicting sets, when both represent 100% legitimate partial deliveries. | Lack of logical PO aggregation in reconciliation engine. |
| **What was Missed** | • Nothing missed by extraction prompt in this batch. | High contrast vector PDF layouts processed cleanly. |

---

### Actionable Prompt Improvement Insights (Brand: Schlumberger / SANAD)

1. **Nested Item Text Rule in Enterprise PO Tables**:
   - Maintain in `_PAGE_PROMPT` Step 3:
     `"When purchase orders place detailed product specifications in an 'Item text' block beneath the main table row (e.g. Schlumberger POs), append the Item text to the main description so that line items have full description fidelity."`

2. **PO Aggregation Rule**:
   - Multiple PO pages sharing the same `po_no_normalized` must be merged into a single logical PO contract.
