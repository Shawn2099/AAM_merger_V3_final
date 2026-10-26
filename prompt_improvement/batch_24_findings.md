## Audit Findings: Batch 24 (Docs 148, 151, 154, 157, 160)

### Document & Brand Overview
This entire batch consists of **Delivery Notes (DN)** issued by **Drilling Tools Trading Est** (`DrillTools`) for clients **ADES Saudi Company Limited** and **Saudi International Energy Services (SIES)**.

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB DN No | Actual PO (Visual) | Actual DN No (Visual) |
|---|---|---|---|---|---|---|---|---|
| **148** | `SIV-DTS-25-579-2.pdf` | DN | Drilling Tools Trading Est | SIES | `None` | `None` | **4500498641** | **GDN-DTS-25-617** |
| **151** | `SIV-DTS-25-580-2.pdf` | DN | Drilling Tools Trading Est | ADES | `None` | `None` | **192641** | **GDN-DTS-25-619** |
| **154** | `SIV-DTS-25-581-2.pdf` | DN | Drilling Tools Trading Est | ADES | `None` | `688` *(rig hallucination)* | **192701** | **GDN-DTS-25-620** |
| **157** | `SIV-DTS-25-582-2.pdf` | DN | Drilling Tools Trading Est | ADES | `None` | `None` | **192910** | **GDN-DTS-25-604** |
| **160** | `SIV-DTS-25-583-2.pdf` | DN | Drilling Tools Trading Est | ADES | `None` | `None` | **193781** | **GDN-DTS-25-622** |

---

### Brand Template Analysis: Drilling Tools Trading Est (Delivery Note)
- **Visual Layout Geometry**:
  - **Header Region**: Prominent top banner with `DrillTools` logo (left) and Arabic company name (right), spanning the top 0%–12%.
  - **Metadata Grid**: A boxed two-column table located between **15% and 33% page height**:
    - *Left Column*: Supplier details, VAT number, Client name (`ADES`, `SIES`, `Tubestar`), and Client address.
    - *Right Column*: 4 to 5 bordered rows:
      1. `Date : DD-MM-YYYY`
      2. `Del. Note No : GDN-DTS-YY-NNN`
      3. `Buyers Order No : <PO Number>` (often formatted with revision, e.g. `192701` or `22398 , 0`)
      4. `Rig / Location : ADES <Rig#>` (frequently contains handwritten rig numbers like `688`, `689`, `690`)
      5. `Remarks :` (frequently stamped or annotated by receiving warehouse)
  - **Table Region**: Spans 35% to 75% page height:
    - Columns: `L/I.#` | `Description` | `P/N` | `Qty` | `Units`
  - **Footer Region**: 75% to 100%: Customer goods receipt stamps (e.g. `ADES K.S.A CENTRAL W/H`, `GOODS RECEIVED CONTENT UNCHECKED`), signatures, and company registration footer.

---

### Detailed Document Audit & Extraction Prompt Diagnostics

#### Doc 148 (`SIV-DTS-25-579-2.pdf`)
- **Visual Truth**:
  - Header right box: `Del. Note No : GDN-DTS-25-617`, `Buyers Order No : 4500498641`, `Date : 12-11-2025`.
  - Client: `SAUDI INTERNATIONAL ENERGY SERVICES COMPANY LTD`.
  - Line 1: `DWE492-220V` | Qty: `1.00 EA`
  - Line 2: `DWE4120-220V` | Qty: `1.00 EA`
- **DB Record**:
  - `doc_type`: DN | `po_no_raw`: None | `dn_no`: None | `po_set_id`: None
  - Line items: Extracted correctly (2 lines, Qty 1.0 each).
- **Prompt Diagnostic**:
  - **Success**: Correctly classified as DN; lines extracted with pristine accuracy.
  - **Failure**: Missed both `Buyers Order No` (`4500498641`) and `Del. Note No` (`GDN-DTS-25-617`).
  - **Prompt Root Cause**: The instruction `first page top 20% for po_reference/document_number` cuts off the `Buyers Order No` line, which sits at ~24% page height on this template.

#### Doc 151 (`SIV-DTS-25-580-2.pdf`)
- **Visual Truth**:
  - Header right box: `Del. Note No : GDN-DTS-25-619`, `Buyers Order No : 192641`, `Rig / Location : ADES 689`.
  - Client: `ADES SAUDI COMPANY LIMITED`.
  - Line 1: `COB Buckles BB-5` (Qty 1.00 EACH), Line 2: `Tensioner CT-20 - 7500000` (Qty 2.00 EACH), Line 3: `BC 50/2 - Composite Strap - SBCS1002` (Qty 6.00 EACH).
- **DB Record**:
  - `doc_type`: DN | `po_no_raw`: None | `dn_no`: None | `po_set_id`: None
  - Line items: Extracted correctly (3 lines, exact quantities).
- **Prompt Diagnostic**:
  - **Failure**: `po_reference` and `document_number` both returned `None`.
  - **Prompt Root Cause**: Label `Del. Note No` is not in the explicit alias list of `_PAGE_PROMPT` (`'DN No'/'Delivery Note No'/'GDN No'`); 20% header ceiling truncated the search area.

#### Doc 154 (`SIV-DTS-25-581-2.pdf`)
- **Visual Truth**:
  - Header right box: `Del. Note No : GDN-DTS-25-620`, `Buyers Order No : 192701`, `Rig / Location : ADES 688` (with prominent handwritten `688` in Remarks and Rig field).
  - Client: `ADES SAUDI COMPANY LIMITED`.
  - Line 1: Ductile Iron Butterfly Valve 12" (Qty 3.00 EA), Line 2: Ductile Iron Butterfly Valve 8" (Qty 7.00 EA).
- **DB Record**:
  - `doc_type`: DN | `po_no_raw`: None | `dn_no`: `688` *(CRITICAL ERROR)* | `po_set_id`: None
  - Line items: Extracted correctly (2 lines, Qty 3.0 and 7.0).
- **Prompt Diagnostic**:
  - **Critical Failure**: The VLM extracted `688` as `document_number` (stored in `dn_no`), completely hallucinating the rig location as a Delivery Note number!
  - **Prompt Root Cause**: The prompt lacks negative constraints for location/rig numbers: `Rig numbers, well names, or handwritten warehouse tags (e.g. ADES 688) are NEVER document_number or po_reference`.
  - In addition, `Buyers Order No : 192701` was omitted.

#### Doc 157 (`SIV-DTS-25-582-2.pdf`)
- **Visual Truth**:
  - Header right box: `Del. Note No : GDN-DTS-25-604`, `Buyers Order No : 192910`, `Rig / Location : ADES 690`.
  - Client: `ADES SAUDI COMPANY LIMITED`.
  - Line 1: `[Gazelle - G3012] Gazelle - 12FT FIBERGLASS STEP LADDER (3.6M)` | Qty: `2.00 EACH`.
- **DB Record**:
  - `doc_type`: DN | `po_no_raw`: None | `dn_no`: None | `po_set_id`: None
  - Line items: Extracted correctly (1 line, Qty 2.0).
- **Prompt Diagnostic**:
  - **Failure**: Missing `po_reference` and `dn_no`.

#### Doc 160 (`SIV-DTS-25-583-2.pdf`)
- **Visual Truth**:
  - Header right box: `Del. Note No : GDN-DTS-25-622`, `Buyers Order No : 193781`, `Rig / Location : ADES 657`.
  - Client: `ADES SAUDI COMPANY LIMITED`.
  - 4 Lines: ESAB Welding electrodes 2.4mm (25.0 PKG), 3.2mm (15.0 BOX), 4mm (15.0 PKG), Flap disc 80 grit (10.0 EA).
- **DB Record**:
  - `doc_type`: DN | `po_no_raw`: None | `dn_no`: None | `po_set_id`: None
  - Line items: Extracted correctly (4 lines).
- **Prompt Diagnostic**:
  - **Failure**: Missing `po_reference` and `dn_no`.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • Document type classified as `DN` across all 5 files.<br>• 100% of line item descriptions, part numbers, and quantities parsed with zero errors.<br>• Clean unit stripping (`EA`, `EACH`, `PKG`, `BOX` removed, leaving raw float). | `_PAGE_PROMPT` Step 3 & Step 4 rules for tabular items and numeric-only quantities performed exceptionally well. |
| **What was Wrong** | • Doc 154 extracted `dn_no = 688` instead of `GDN-DTS-25-620`. | Prompt has no guardrail preventing Rig/Location numbers (`ADES 688`) from being selected as document numbers. |
| **What was Misrepresented** | • All 5 valid, signed Delivery Notes were recorded as orphan documents (`po_set_id = None`), causing their 3-way matching sets to remain permanently stuck in `pending`. | DB stores `po_no_raw = NULL`, isolating them from their corresponding Invoices (Docs 147, 150, 153, 156, 159). |
| **What was Missed** | • PO references missed: `4500498641`, `192641`, `192701`, `192910`, `193781`.<br>• DN numbers missed: `GDN-DTS-25-617`, `GDN-DTS-25-619`, `GDN-DTS-25-620`, `GDN-DTS-25-604`, `GDN-DTS-25-622`. | The `top 20%` header scanning restriction in `_PAGE_PROMPT` cuts off the metadata box located between 15% and 33% page height. |

---

### Actionable Prompt Improvement Insights (Brand: Drilling Tools | Type: DN)

To resolve the 100% failure rate of header metadata extraction on Drilling Tools Delivery Notes:

1. **Expand Header ROI Boundary in `_PAGE_PROMPT`**:
   - **Current Prompt**: `STEP 1 — HEADER SCAN (first page top 20% for po_reference/document_number...)`
   - **Fix**: Change `top 20%` to `top 35%`. On boxed supplier templates (Drilling Tools, RAK Energy, RAAS), logos and company credentials consume the top 15%, placing the metadata table between 15% and 35%.

2. **Add Explicit Label Aliases**:
   - Add `'Del. Note No'` and `'Del Note No'` to the document number trigger list:
     `Locate 'DN No'/'Delivery Note No'/'Del. Note No'/'GDN No'/'GDN-...' → document_number`
   - Emphasize that Drilling Tools delivery notes follow the pattern `GDN-DTS-*` and invoices follow `SIV-DTS-*`.

3. **Negative Instruction for Rig / Location Numbers**:
   - Explicitly instruct the model:
     `"Rig numbers, well numbers, location names, and warehouse codes (e.g. 'ADES 688', 'Rig 14', 'Loc: 690') are operational tags, NEVER the document_number or po_reference."`

4. **Add Few-Shot for Drilling Tools Pink Delivery Note Template**:
   ```json
   {
     "document_type": "DN",
     "document_number": "GDN-DTS-25-620",
     "po_reference": "192701",
     "vendor_name": "Drilling Tools Trading Est",
     "line_items": [
       {"line_item_no": "1", "description": "Ductile Iron Butterfly Valve Wafer Type 12\"", "quantity": "3"}
     ]
   }
   ```
