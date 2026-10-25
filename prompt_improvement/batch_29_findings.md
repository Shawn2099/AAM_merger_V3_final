## Audit Findings: Batch 29 (Docs 40, 41, 42, 58, 59)

### Document & Brand Overview
Batch 29 covers **5 documents** across two major oilfield customers serviced by **Ibrahim Ali Al-Shab Trading Est** (`AL-SHAB` / `ARS`):
1. **ADES Saudi Company Limited** (Docs 40, 41, 42) — completing **PO Set 13** (`193165`, Status: `quarantined`)
2. **Arabian Drilling Company (ADC)** (Docs 58, 59) — forming **PO Set 19** (`4500051195`, Status: `quarantined`)

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB Document No | Actual PO (Visual) | Actual Document No (Visual) | Reconciliation Status |
|---|---|---|---|---|---|---|---|---|---|
| **40** | `SIV-ARS-25-8261-4.pdf` | DN | Ibrahim Ali Al-Shab | ADES | `193165` | `GDN-ARS-25-9329` | **193165** | **GDN-ARS-25-9329** | PO Set 13 (`quarantined`) |
| **41** | `SIV-ARS-25-8261-5.pdf` | PO | ADES (Buyer) | Al-Shab | `193165` | `None` | **193165** | PO: **193165** (p1) | PO Set 13 (`quarantined`) |
| **42** | `SIV-ARS-25-8261-6.pdf` | PO | ADES (Buyer) | Al-Shab | `193165` | `None` | **193165** | PO: **193165** (p2) | PO Set 13 (`quarantined`) |
| **58** | `SIV-ARS-25-8312-pages-1.pdf` | SI | Ibrahim Ali Al-Shab | Arabian Drilling | `4500051195` | `SIV-ARS-25-8312` | **4500051195** | **SIV-ARS-25-8312** | PO Set 19 (`quarantined`) |
| **59** | `SIV-ARS-25-8312-pages-2.pdf` | DN | Ibrahim Ali Al-Shab | Arabian Drilling | `4500051195` | `GDN-ARS-25-9587` | **4500051195** | **GDN-ARS-25-9587** | PO Set 19 (`quarantined`) |

---

### Brand Template Analysis: Al-Shab, ADES & Arabian Drilling

#### 1. ADES Approved Standard Purchase Order (Docs 41 & 42)
- **Geometry**: Structured tabular enterprise order. Header clearly displays `PO 193165`, `PO, Rev # : 193165, 0`, `Approved Date : 04-NOV-2025`.
- **Table Structure**: `Line` | `Item Description` | `Quantity` | `UOM` | `Unit Price (SAR)` | `Total (SAR)`.
- **Prompt Performance**: Flawless extraction. The model parsed lines 1–5 on page 1 and lines 6–10 on page 2, capturing exact descriptions, quantities, and unit rates without distortion.

#### 2. Arabian Drilling Company (ADC) Order Structure (Docs 58 & 59)
- **Geometry & Numbering**: Uses standard SAP 10-digit PO numbers starting with `45...` (`4500051195`) and step-10 line numbering (`10`, `20`).
- **Prompt Performance**: The model extracted line numbers `"10"` and `"20"` verbatim across both Invoice Doc 58 and Delivery Note Doc 59, adhering strictly to rule: `Do not renumber, pad or strip leading zeros`.

---

### Deep Systemic Finding: False Quarantine of Multi-Page Purchase Orders

Both **PO Set 13** and **PO Set 19** were quarantined by the system with the exact same error reason:
> `quarantined: This PO Set holds more than one PO document`

Visual inspection of physical documents confirms:
1. **PO Set 13** holds Docs 41 & 42. These are NOT two competing PO contracts; they are **Pages 1 and 2 of the exact same 7-page ADES Purchase Order `193165`**!
   - PO lines 1–10 match Invoice Docs 37 & 38 (10 items @ 6,332.40 SAR) **with 100% precision**.
   - Delivery Notes Doc 39 (Line 4) and Doc 40 (Lines 1, 2, 3, 5, 6, 7, 8, 9, 10) deliver **100% of all 10 items**.
   - PO Set 13 is a **pristine, valid 3-way match**, falsely blocked by ingestion splitting.
2. **PO Set 19** holds Docs 60 & 61. These are **Pages 3 and 4 of the exact same Arabian Drilling Purchase Order `450051195`**!
   - Invoice Doc 58 and Delivery Note Doc 59 match Lines 10 (Qty 50) and 20 (Qty 12) **with 100% precision**.
   - PO Set 19 is a **pristine, valid 3-way match**, falsely blocked by the same splitting rule.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% classification accuracy on `SI`, `DN`, and `PO`.<br>• Accurate extraction of step-10 line item numbers (`10`, `20`) on Arabian Drilling orders.<br>• Perfect preservation of non-sequential line numbers on partial DN Doc 40 (`1, 2, 3, 5, 6, 7, 8, 9, 10`), correctly leaving Line 4 for DN Doc 39. | `_PAGE_PROMPT` Step 3 rules: verbatim preservation of line numbers without re-indexing performed exactly as intended. |
| **What was Wrong** | • Ingestion pipeline treated consecutive pages of the same PO document as multiple conflicting PO documents. | The document ingestion / splitting layer lacks logic to group contiguous pages of the same PO into a single logical PO entity. |
| **What was Misrepresented** | • Both PO Set 13 and PO Set 19 were marked `quarantined` as multi-PO conflicts, masking two completely reconciled vendor transactions. | Reconciliation rule `po_doc_count > 1` triggers unconditionally on multi-page PO splits. |
| **What was Missed** | • Nothing missed by extraction prompt in this batch; extraction quality was 100% correct across all 5 files. | Prompt performed cleanly on these high-contrast vector and clean scan layouts. |

---

### Actionable Prompt & Pipeline Improvement Insights

1. **Multi-Page PO Component Linking in `COMBINED` Extraction**:
   - In `_PAGE_PROMPT` Step 6 (Multi-doc split):
     `"When a single PDF contains a multi-page PO (e.g. pages 1..7 of an ADES Standard Purchase Order), output ONE contiguous PO component spanning all pages (e.g. {'document_type': 'PO', 'page_start': 1, 'page_end': 7}), NEVER split individual PO pages into separate 1-page PO components."`

2. **Reconciliation Multi-PO Disambiguation Rule**:
   - The reconciliation check `This PO Set holds more than one PO document` should verify whether the multiple PO documents share the **exact same `document_number` / `po_no_normalized`**. If they share the exact same PO number, they represent pages of the same order and should be aggregated rather than quarantined.
