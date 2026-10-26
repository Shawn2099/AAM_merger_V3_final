## Audit Findings: Batch 25 (Docs 169, 170, 174, 179, 258)

### Document & Brand Overview
Batch 25 consists of **5 Delivery Notes (DN)** spanning two major vendor brands:
1. **Drilling Tools Trading Est** (Docs 169, 170, 174, 179) — pink-tinted scanned delivery notes for clients **Saudi International Energy Services (SIES)** and **ADES GCC for Drilling**.
2. **RAAS Oilfield Services and Supplies WLL** (Doc 258) — clean tabular delivery note from Bahrain for client **NOMAC Al Dur Power & Water Services**.

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB DN No | Actual PO (Visual) | Actual DN No (Visual) | Associated Parent PO Set |
|---|---|---|---|---|---|---|---|---|---|
| **169** | `SIV-DTS-25-588-2.pdf` | DN | Drilling Tools Trading Est | SIES | `None` | `None` | **4500496313** | **GDN-DTS-25-520** | PO Set 47 (Stuck in `pending`) |
| **170** | `SIV-DTS-25-588-3.pdf` | DN | Drilling Tools Trading Est | SIES | `None` | `None` | **4500496313** | **GDN-DTS-25-626** | PO Set 47 (Stuck in `pending`) |
| **174** | `SIV-DTS-25-589-2.pdf` | DN | Drilling Tools Trading Est | SIES | `None` | `None` | **4500497953** | **GDN-DTS-25-627** | PO Set 48 (Stuck in `pending`) |
| **179** | `SIV-DTS-25-596-2.pdf` | DN | Drilling Tools Trading Est | ADES | `None` | `None` | **23073** | **GDN-DTS-25-632** | PO Set 49 (Stuck in `pending`) |
| **258** | `SIV-RHO-26-1669A.pdf` | DN | RAAS Oilfield Services | NOMAC | **260988** | **GDN-RHO-26-1799** | **8300052050** *(handwritten)* / `260988` *(printed)* | **GDN-RHO-26-1799** | PO Set 75 (Stuck in `pending`) |

---

### Comparative Brand Template Analysis

#### 1. Brand: Drilling Tools Trading Est (Pink Scanned Form)
- **Visual Geometry**:
  - Header right box sits between **16% and 32% of page height**, containing:
    - `Date : DD-MM-YYYY`
    - `Del. Note No : GDN-DTS-YY-NNN`
    - `Buyers Order No : <PO Number>`
    - `Rig / Location : <Location>`
  - Visual inspection confirms all documents (Docs 169, 170, 174, 179) have high contrast, machine-printed text in this box.
- **Systemic Extraction Failure**:
  - All 4 delivery notes extracted `po_no_raw = None` and `dn_no = None`.
  - **Severe Downstream Impact**:
    - **PO Set 47** contains Invoice Doc 168 and PO Docs 171–172. Because Docs 169 & 170 failed extraction, PO Set 47 is missing its delivery notes and cannot reconcile. In reality, Docs 169 & 170 represent a **multi-DN split delivery** (DN 520 delivered Line 1: 10 Brother tapes on 24/09/25; DN 626 delivered Line 2: 2 Sledge hammers on 17/11/25). Together, they deliver 100% of PO Set 47!
    - **PO Set 48** contains Invoice Doc 173 and PO Docs 175–177. Missing DN Doc 174 leaves it permanently pending.
    - **PO Set 49** contains Invoice Doc 178 and PO Doc 180. Missing DN Doc 179 leaves it permanently pending.

#### 2. Brand: RAAS Oilfield Services (Bahrain Template)
- **Visual Geometry**:
  - Top header: Black-and-white vector layout.
  - Left Box: `Doc No : GDN-RHO-26-1799`, `Customer Name : NOMAC`.
  - Right Box: `P.O. : 260988`.
  - **Handwritten Annotation**: At the very top right, above the logo banner, the receiving/finance team wrote: `Po: 83000 52050`.
- **Extraction Behavior**:
  - The model extracted `document_type = DN`, `document_number = GDN-RHO-26-1799`, and `po_reference = 260988`.
  - **The Discrepancy**: The printed PO box (`260988`) is NOMAC's internal local order number, whereas the corporate SAP PO used by the vendor for Invoice (Doc 257) and PO contract (Doc 259) is `8300052050` (belonging to **PO Set 75**).
  - Because the model extracted `260988` instead of `8300052050`, Doc 258 was not attached to PO Set 75. Consequently, PO Set 75 is stuck in `pending` missing its DN, while Doc 258 sits unattached!

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% classification accuracy on `document_type: DN`.<br>• Accurate table extraction across all 5 files, including line numbering, multi-line descriptions, and quantity parsing.<br>• Doc 258 successfully followed the embedded line marker rule: `Line Item - 1` and `Line Item - 2` were correctly parsed as line numbers 1 and 2. | `_PAGE_PROMPT` Step 2 (doc type), Step 3 (line item markers), and Step 4 (quantity numbers) operate reliably on clean table layouts. |
| **What was Wrong** | • Doc 258 extracted `po_reference = 260988` instead of flagging `po_reference_ambiguous = true` or prioritizing the 10-digit SAP PO number `8300052050` written on the document. | Prompt instructs: `If MORE THAN ONE distinct PO number is visible on a document, set po_reference to the first and set po_reference_ambiguous true`. The VLM failed to identify the handwritten `Po: 83000 52050` at the top as an active PO reference. |
| **What was Misrepresented** | • Docs 169 and 170 are legitimate partial deliveries completing PO Set 47, but were recorded as useless orphans without a PO reference. | The database records `po_no_raw = NULL`, fracturing a valid 2-DN split delivery into disconnected files. |
| **What was Missed** | • PO references missed on Drilling Tools DNs: `4500496313` (Docs 169 & 170), `4500497953` (Doc 174), and `23073` (Doc 179).<br>• DN numbers missed: `GDN-DTS-25-520`, `GDN-DTS-25-626`, `GDN-DTS-25-627`, `GDN-DTS-25-632`. | Prompt instruction `first page top 20% for po_reference/document_number` limits the vision crop boundary, consistently ignoring the metadata table spanning 15%–33% on Drilling Tools forms. |

---

### Actionable Prompt Improvement Insights

1. **Header Boundary Revision (`_PAGE_PROMPT` Step 1)**:
   - **Current Rule**: `first page top 20% for po_reference/document_number`
   - **Updated Rule**: `first page top 35% for po_reference/document_number`
   - **Rationale**: On both Drilling Tools and RAAS templates, header blocks extend down to 33% of page height. A 20% cutoff blindfolds the model to `Buyers Order No` and `Del. Note No`.

2. **Handle Multi-PO Discrepancies & Handwritten Overrides on Delivery Notes**:
   - Add explicit guidance in `_PAGE_PROMPT` Step 1:
     `"On delivery notes, look for handwritten PO numbers (e.g. 'Po: 83000 52050') often annotated by receiving clerks at the top of the page. If a document displays both a printed internal order number (e.g. 6-digit 260988) and a handwritten standard SAP PO number (e.g. 10-digit starting with 83... or 45...), record the standard SAP PO number as po_reference, or set po_reference_ambiguous: true if both are valid candidate numbers."`

3. **Multi-Delivery Note Set Association Rule**:
   - Add guidance that multiple Delivery Notes referencing the same `Buyers Order No` (e.g. GDN 520 and GDN 626 both referencing `4500496313`) are expected and must both be extracted with the exact same `po_reference` to allow 3-way reconciliation across split shipments.
