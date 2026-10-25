## Audit Findings: Batch 27 (Docs 18, 19, 20, 21, 22)

### Document & Brand Overview
Batch 27 covers **5 documents** belonging to two major corporate entities:
1. **Vendor**: **Ibrahim Ali Al-Shab Trading Est** (`AL-SHAB` / `ARS`, Al Khobar, KSA)
2. **Buyer / Client**: **Saudi Aramco Rowan Offshore Drilling Co.** (`ARO Drilling`, KSA)

These documents form the backbone of:
- **PO Set 7** (`50119501`, Status: `quarantined`) — Docs 18, 19, 20, 21
- **PO Set 8** (`50117194`, Status: `quarantined`) — Doc 22

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB DN / SI No | Actual PO (Visual) | Actual Document No (Visual) | Status in DB |
|---|---|---|---|---|---|---|---|---|---|
| **18** | `SIV-ARS-25-7969-pages-1.pdf` | SI | Ibrahim Ali Al-Shab | ARO Drilling | `50119501` | SI: `SIV-ARS-25-7969` | **50119501** | **SIV-ARS-25-7969** | PO Set 7 (`quarantined`) |
| **19** | `SIV-ARS-25-7969-pages-2.pdf` | DN | Ibrahim Ali Al-Shab | ARO Drilling | `50119501` | DN: `GDN-ARS-25-9355` | **50119501** | **GDN-ARS-25-9355** | PO Set 7 (`quarantined`) |
| **20** | `SIV-ARS-25-7969-pages-3.pdf` | PO | ARO Drilling (Buyer) | Al-Shab | `50119501` | `None` | **50119501** | DPO: **50119501** (p1) | PO Set 7 (`quarantined`) |
| **21** | `SIV-ARS-25-7969-pages-4.pdf` | PO | ARO Drilling (Buyer) | Al-Shab | `50119501` | `None` | **50119501** | DPO: **50119501** (p2) | PO Set 7 (`quarantined`) |
| **22** | `SIV-ARS-25-7970-pages-1.pdf` | SI | Ibrahim Ali Al-Shab | ARO Drilling | `50117194` | SI: `SIV-ARS-25-7970` | **50117194** | **SIV-ARS-25-7970** | PO Set 8 (`quarantined`) |

---

### Brand Template Analysis: Al-Shab & ARO Drilling

#### 1. Ibrahim Ali Al-Shab Invoices (`TAX INVOICE` / `SIV-ARS-*`)
- **Layout**: Crisp bilingual (English/Arabic) boxed header.
- **Key Fields**: `Invoice No. / رقم الفاتورة`, `P.O. No. / رقم امر الشراء`, `Rig No. / رقم الحفار` (e.g. `C016`), `Date of Supply`.
- **Table**: SNO, bilingual Description of Goods, Qty, Unit, Unit Rate, Total Amount, VAT%.
- **Extraction Performance**: **100% flawless**. The prompt extracted invoice numbers, PO references, line numbers, quantities, and unit rates without a single error on both Doc 18 and Doc 22.

#### 2. ARO Drilling Purchase Orders (`DPO Number`)
- **Layout**: Standard SAP ARO Drilling purchase contract across multiple pages.
- **Key Fields**: Top-right header box lists `DPO Number : 50119501`, `Revision : 0`, `Date : 28/10/2025`.
- **Table**: `Item` | `Material` | `Description` | `PO Type` | `Quantity` | `Unit` | `Unit Price` | `Delivery Date`. Descriptions contain embedded metadata lines: `Line\Rel No: 1\1`, `Line\Rel No: 6\1`.
- **Extraction Performance**: Extracted all lines cleanly, matching prices and quantities accurately across pages (Docs 20 & 21).

#### 3. Ibrahim Ali Al-Shab Delivery Notes (`DELIVERY NOTE` / `GDN-ARS-*`)
- **Layout**: Photocopied raster scan with heavy black borders and dotted background noise.
- **Key Fields**: `DELIVERY NOTE NO. : GDN-ARS-25-9355`, `P.O. # : 50119501`, `Rig No. : C016`.
- **Table**: Columns for `Item No.`, `DESCRIPTION`, `Part No.`, `QTY.`

---

### Deep Prompt Diagnostic: The "Flex Joint" Hallucination in Doc 19

Visual comparison between the physical page image (`doc019_p1.png`) and the database record uncovered a **critical VLM hallucination failure**:

1. **Visual Reality on Doc 19**:
   - Header: `GDN-ARS-25-9355`, PO `50119501`, Rig `C016`.
   - Table:
     - `Item No.`: 6 (or faint 1)
     - `DESCRIPTION`: `CLIP, GLOVE GRABBER; W/BELTCLIP, LIME`
     - `Part No.`: `3405`
     - `QTY.`: `50 Each`
2. **What the Extractor Stored in DB**:
   - `line_item_no`: `1`
   - `description`: `FLEX JOINT 3/4" NPT FEMALE X 1/2" NPT MALE` *(COMPLETE HALLUCINATION!)*
   - `quantity`: `50.0`
3. **Catastrophic Reconciliation Impact**:
   - PO Set 7 consists of:
     - PO (Docs 20 & 21): 6 items (Lines 1–5: Gloves; Line 6: 50 Glove Grabber clips).
     - SI (Doc 18): Line 6 (50 Glove Grabber clips @ 3.00 SAR).
     - DN (Doc 19): Extracted as Line 1: `FLEX JOINT` (Qty 50).
   - Because the DN extracted a phantom "FLEX JOINT" on Line 1, the matcher detected an orphan vendor item on Line 1 and a missing delivery on Line 6, immediately plunging PO Set 7 into **`quarantined`**!
   - In physical reality, Doc 18 (SI) and Doc 19 (DN) deliver and invoice Line 6 **with 100% perfect 3-way alignment** (50 clips ordered, delivered, and invoiced)!
4. **Prompt Root Cause Analysis**:
   - On photocopied scans with dithered dot patterns, VLMs without strict visual grounding guidelines can hallucinate plausible industrial terms ("FLEX JOINT") when trying to resolve fuzzy letterforms.
   - The current `_PAGE_PROMPT` contains zero anti-hallucination instructions for low-contrast/degraded scans.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% accurate classification of `SI`, `DN`, and multi-page `PO`.<br>• Accurate extraction of bilingual Al-Shab invoices (Docs 18 & 22) and ARO Drilling PO tables (Docs 20 & 21).<br>• Correct header capture of `DPO Number : 50119501` on ARO PO and `P.O. No : 50119501` on Al-Shab SI/DN. | `_PAGE_PROMPT` Step 1 (`DPO Number` / `P.O. No`) and Step 2 handled standard high-contrast layouts effectively. |
| **What was Wrong** | • Doc 19 table extracted phantom item `FLEX JOINT 3/4" NPT FEMALE X 1/2" NPT MALE` instead of `CLIP, GLOVE GRABBER; W/BELTCLIP, LIME`.<br>• Extracted line item number as `1` instead of line item `6`. | Severe VLM OCR hallucination on low-contrast photocopied scan, combined with lack of cross-document grounding in prompt. |
| **What was Misrepresented** | • PO Set 7 was quarantined as a reconciliation failure, when in reality it is a 100% valid partial delivery/invoice of Line 6. | Caused directly by the hallucinated item description and erroneous line number on Doc 19. |
| **What was Missed** | • Genuine item text `CLIP, GLOVE GRABBER; W/BELTCLIP, LIME` and Part No `3405` were completely lost on Doc 19. | OCR degradation caused model to discard actual visual tokens. |

---

### Actionable Prompt Improvement Insights (Brand: Al-Shab / ARO | Types: SI, DN, PO)

1. **Anti-Hallucination & Visual Grounding Rule (`_PAGE_PROMPT` Step 3)**:
   - Add explicit instruction:
     `"GROUNDING RULE: Never invent, extrapolate, or hallucinate industrial descriptions (e.g. inventing 'FLEX JOINT' or 'FLANGE' on noisy scans). Transcribe ONLY what is visually discernible. If characters are grainy, transcribe the best visual match (e.g. 'CLIP, GLOVE GRABBER') or check the customer PO description for reference."`

2. **DPO Number Recognition for ARO Drilling Contracts**:
   - Confirm alias in `_PAGE_PROMPT` Step 1:
     `"ARO Drilling purchase orders print 'DPO Number' in the top right box; extract this as document_number for POs."`

3. **Line Item Numbering on Degraded Dockets**:
   - Instruct the model:
     `"When reading the side column (Item No / SNO) on a Delivery Note that corresponds to a multi-line PO, inspect the digit carefully (do not mistake '6' for '1' or bullet point). If the description matches a specific PO line item, ensure the line_item_no aligns with the PO line."`
