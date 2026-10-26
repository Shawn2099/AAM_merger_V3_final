## Audit Findings: Batch 26 (Docs 260, 261, 265, 268, 271)

### Document & Brand Overview
Batch 26 consists of **5 documents** (2 Sales Invoices and 3 Delivery Notes) all issued by **RAAS Oilfield Services and Supplies WLL** (`raas`, Kingdom of Bahrain) across diverse industrial clients:
- **Gulf Aluminium Rolling Mill (GARMCO)** (Docs 260, 261)
- **Bahrain Airport Services (BAS)** (Doc 265)
- **Armacell Middle East Company** (Doc 268)
- **International Maritime Industries (IMI)** (Doc 271)

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB DN No | Actual PO (Visual) | Actual DN No (Visual) | Target PO Set |
|---|---|---|---|---|---|---|---|---|---|
| **260** | `SIV-RHO-26-1896.pdf` | SI | RAAS Oilfield Services | GARMCO | `60213576` | `None` *(on header)* | **60213576** | **GDN-RHO-26-2105** *(in table)* | PO Set 76 (`60213576OPG`) |
| **261** | `SIV-RHO-26-1896A.pdf` | DN | RAAS Oilfield Services | GARMCO | `60213576` | `GDN-RHO-26-2105` | **60213576** / `13085` | **GDN-RHO-26-2105** | PO Set 76 (`60213576OPG`) |
| **265** | `SIV-RHO-26-1914.pdf` | SI | RAAS Oilfield Services | BAS | `4270006645` | `None` *(on header)* | **4270006645 -SNN** | **GDN-RHO-26-2183** *(in table)* | PO Set 77 (`4270006645SNN`) |
| **268** | `SIV-RHO-26-233A.pdf` | DN | RAAS Oilfield Services | Armacell | `260030` | `GDN-RHO-26-92` | **4500362256** *(header)* / `260030` *(box)* | **GDN-RHO-26-92** | PO Set 78 (`4500362256`) |
| **271** | `SIV-RHO-26-234A.pdf` | DN | RAAS Oilfield Services | IMI | `None` | `None` | **C13717** | **GDN-RHO-26-270** | PO Set 79 (`C13717`) |

---

### Brand Template Analysis: RAAS Oilfield Services (Kingdom of Bahrain)

#### 1. Invoices (`TAX INVOICE` / `SIV-RHO-*`)
- **Header Structure**: Top-left contains company logo and address; top-right contains a 5-row bordered metadata grid (`INVOICE #`, `Date`, `P.O. #`, `Reference`, `Payment Terms`).
- **Table Structure**: Includes an explicit **`Delivery Note No` column** alongside `Description`, `Qty`, `Unit Price`, and `Amount`.
- **Currency**: Bahraini Dinar (`BHD`), using **3 decimal places** (e.g. `11.250` BHD = 11 Dinar 250 fils).

#### 2. Delivery Notes (`DELIVERY NOTE` / `GDN-RHO-*`)
- **Header Structure**: Sits at **22% to 32% page height**, beneath company contact lines:
  - Left Box: `Doc No : GDN-RHO-YY-NNN`, `Doc Date`, `Customer Name`.
  - Right Box: `P.O. : <Order No>`, `Rig.No : <Rig>`.
  - Top Region (above or outside box): Client SAP PO number often printed in bold (e.g. `PO : 4500362256` or `PO - 60213576`), or handwritten (`Po: 83000 52050`).
- **Line Item Numbering**: Descriptions systematically include an embedded marker on line 2: `"Line Item - 1"`, `"Line Item - 2"`.

---

### Detailed Document Audit & Extraction Prompt Diagnostics

#### Docs 260 & 261 (GARMCO Order 60213576 / PO Set 76)
- **Visual Truth**:
  - Invoice Doc 260: `INVOICE # : SIV-RHO-26-1896`, `P.O. # : 60213576`. Table lines specify `Delivery Note No : GDN-RHO-26-2105`.
  - Delivery Note Doc 261: `Doc No : GDN-RHO-26-2105`. Top right prints `PO - 60213576`; box prints `P.O. : 13085` (local internal requisition).
  - Line Items: Line 1 (3 cans Thinner No. 02 @ 11.250 BHD), Line 2 (3 cans Thinner No. 07 @ 11.500 BHD).
- **Prompt Diagnostic**:
  - **Success**: The prompt successfully prioritized `PO - 60213576` over the internal requisition `13085` on Doc 261. Table-level DN extraction (`GDN-RHO-26-2105`) on Doc 260 succeeded 100%. Line marker resolution (`Line Item - 1` → line_item_no 1) succeeded.
  - **System Grouping Failure**: Why are Docs 260 and 261 unattached (`po_set_id = None`)?
    - The actual PO file for this order (Doc 262) printed `Order: 60213576 OPG` (where `OPG` is GARMCO's Operations department code).
    - The PO extractor normalized this as `60213576OPG` and spawned **PO Set 76** with key `60213576OPG`.
    - Docs 260 and 261 extracted `60213576` (digits only).
    - Because grouping requires an exact normalized string match, `60213576` failed to join `60213576OPG`, leaving the entire set fractured!

#### Doc 265 (Bahrain Airport Services Order 4270006645 / PO Set 77)
- **Visual Truth**:
  - Invoice `SIV-RHO-26-1914`: Header clearly prints `P.O. # : 4270006645 -SNN`.
  - Table: Mixer/Grinder Machine with SS (Qty 1.0, Unit Price 42.000 BHD), `Delivery Note No : GDN-RHO-26-2183`.
- **Prompt Diagnostic**:
  - **Discrepancy**: The VLM extracted `po_no_raw: 4270006645`, dropping the `-SNN` suffix.
  - However, the corresponding DN (Doc 264) and PO (Doc 266) extracted `4270006645SNN` and joined **PO Set 77**.
  - **Result**: SI Doc 265 was isolated from PO Set 77 due to the dropped suffix.

#### Doc 268 (Armacell Order 4500362256 / PO Set 78)
- **Visual Truth**:
  - Delivery Note `GDN-RHO-26-92`.
  - In bold text at top right: `PO : 4500362256`.
  - In the bordered metadata box below: `P.O : 260030` (Armacell internal job/order number).
  - 4 lines: Spray paint (12 ea), Self drill screws (1 box, 1 box), Silicone sealant (5 ea). Stamped by Armacell Middle East on 08/01/26.
- **Prompt Diagnostic**:
  - **Prompt Failure**: The extractor picked `260030` from the box instead of `4500362256`!
  - **Root Cause**: The prompt lacks guidance on multiple PO labels. It found `P.O :` in the box first and ignored the standard 10-digit SAP PO number `4500362256` printed in bold directly above it.
  - **Result**: Doc 268 was orphaned under PO `260030`, stranding **PO Set 78** (`4500362256`) without its delivery note.

#### Doc 271 (International Maritime Industries Order C13717 / PO Set 79)
- **Visual Truth**:
  - Delivery Note `GDN-RHO-26-270`, Date `26/01/2026`.
  - In the metadata grid at 24%–28% page height:
    - `Doc No : GDN-RHO-26-270`
    - `P.O : C13717`
  - Line 1: Air vent ducting Flexible (Qty 120.00 EACH). Stamped by IMI Warehouse on 27 JAN 2026.
- **Prompt Diagnostic**:
  - **Critical Failure**: DB recorded `po_no_raw: None`, `dn_no: None`!
  - **Smoking Gun Root Cause**:
    - `_PAGE_PROMPT` Step 1 explicitly specifies: `STEP 1 — HEADER SCAN (first page top 20% for po_reference/document_number...)`.
    - On Doc 271, the company contact information occupies the top 0%–22% of the page. The `Doc No` and `P.O` boxes sit at **24% to 28% of page height**.
    - The model followed the `top 20%` limit, scanned only the address lines, found no PO or DN, and returned `null` for both!
    - **Result**: Doc 271 was orphaned, leaving **PO Set 79** (`C13717`) pending without its DN.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% accurate classification of Invoices (`SI`) and Delivery Notes (`DN`).<br>• Table-level `Delivery Note No` extraction on invoices (Doc 260 extracted `GDN-RHO-26-2105` into `line_items.dn_no`).<br>• Accurate parsing of embedded `"Line Item - N"` markers into `line_item_no`. | `_PAGE_PROMPT` Step 2, Step 3, and table column instructions for `dn_no` performed as designed on clean invoice tables. |
| **What was Wrong** | • Doc 268 picked internal job number `260030` instead of the 10-digit SAP PO `4500362256` printed in bold above it.<br>• Doc 265 stripped suffix `-SNN` from `4270006645 -SNN`, while PO and DN kept it. | • Prompt has no heuristics to prioritize 10-digit SAP PO numbers (`45...`, `60...`, `42...`) over short internal requisition numbers.<br>• Lack of unified suffix handling rule (e.g. whether trailing departmental codes like `-SNN` or `OPG` should be stripped or retained). |
| **What was Misrepresented** | • Docs 260 & 261 were extracted as PO `60213576` while PO Doc 262 was extracted as `60213576OPG`, causing grouping to treat them as two completely separate orders. | System lacks fuzzy/prefix grouping for department letter suffixes appended to standard numeric PO numbers. |
| **What was Missed** | • Doc 271 completely missed `Doc No : GDN-RHO-26-270` and `P.O : C13717`. | The strict `top 20%` header scan limit in `_PAGE_PROMPT` actively blinded the model to the metadata grid located at 24%–28% page height. |

---

### Actionable Prompt Improvement Insights (Brand: RAAS | Types: SI, DN)

1. **Eliminate the 20% Header Restriction**:
   - Update `_PAGE_PROMPT` Step 1:
     `"STEP 1 — HEADER SCAN (first page top 35% for po_reference/document_number): On documents with logos and contact headers (e.g. RAAS, Drilling Tools), the metadata box is located between 15% and 35% page height. Scan the full upper 35% of the page."`

2. **PO Number Resolution Heuristic (Corporate PO vs Internal Requisition)**:
   - Add explicit disambiguation in `_PAGE_PROMPT` Step 1:
     `"If a delivery note displays two order numbers (e.g. a bold header 'PO : 4500362256' AND a box 'P.O : 260030'): ALWAYS prioritize the corporate customer PO number (typically 8–10 digits, such as 45..., 60..., 83..., 42...) over internal 5-to-6 digit job/ticket/requisition numbers."`

3. **Standardize Suffix Handling for Department / Plant Codes**:
   - Add clear instruction on trailing alphabetic suffixes:
     `"If a PO number is printed with a departmental or plant suffix separated by a space or hyphen (e.g. '4270006645 -SNN' or '60213576 OPG'): include the suffix verbatim in po_reference (strip hyphen, keep alnum: '4270006645SNN', '60213576OPG'). Do NOT drop trailing letters on invoices while keeping them on POs."`
