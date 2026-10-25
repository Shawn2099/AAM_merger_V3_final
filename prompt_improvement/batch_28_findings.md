## Audit Findings: Batch 28 (Docs 23, 24, 37, 38, 39)

### Document & Brand Overview
Batch 28 covers **5 documents** from **Ibrahim Ali Al-Shab Trading Est** (`AL-SHAB` / `ARS`) and buyer contracts from **ARO Drilling** and **ADES Saudi Company Limited**:
- **PO Set 8** (`50117194`, Status: `quarantined`) — Docs 23, 24 (and Doc 22 from Batch 27)
- **PO Set 13** (`193165`, Status: `quarantined`) — Docs 37, 38, 39 (along with Docs 40–42)

| Doc ID | Original Filename | Doc Type | Brand / Supplier | Target Client | DB PO Raw | DB Document No | Actual PO (Visual) | Actual Document No (Visual) | Reconciliation Status |
|---|---|---|---|---|---|---|---|---|---|
| **23** | `SIV-ARS-25-7970-pages-2.pdf` | DN | Ibrahim Ali Al-Shab | ARO Drilling | `50137194` *(OCR digit error)* | `GDN-ARS-25-9175` | **50117194** | **GDN-ARS-25-9175** | PO Set 8 (`quarantined`) |
| **24** | `SIV-ARS-25-7970-pages-3.pdf` | PO | ARO Drilling (Buyer) | Al-Shab | `50117194` | `None` | **50117194** | DPO: **50117194** | PO Set 8 (`quarantined`) |
| **37** | `SIV-ARS-25-8261-1.pdf` | SI | Ibrahim Ali Al-Shab | ADES | `193165` | `SIV-ARS-25-8261` | **193165** | **SIV-ARS-25-8261** (p1) | PO Set 13 (`quarantined`) |
| **38** | `SIV-ARS-25-8261-2.pdf` | SI | Ibrahim Ali Al-Shab | ADES | `193165` | `SIV-ARS-25-8261` | **193165** | **SIV-ARS-25-8261** (p2) | PO Set 13 (`quarantined`) |
| **39** | `SIV-ARS-25-8261-3.pdf` | DN | Ibrahim Ali Al-Shab | ADES | `193165` | `GDN-ARS-25-9496` | **193165** | **GDN-ARS-25-9496** | PO Set 13 (`quarantined`) |

---

### Brand Template Analysis: Al-Shab & ADES / ARO Orders

#### 1. Degraded Photocopied Delivery Notes (Doc 23)
- **Visual Appearance**: High-contrast, noisy photocopy scan. Header contains `P.O. # : 50117194` and `DELIVERY NOTE NO. : GDN-ARS-25-9175`.
- **Systemic Vulnerability**: On these low-fidelity photocopies, the VLM displays two acute vulnerabilities:
  1. **Digit Confusion**: Misreads `'1'` as `'3'` (`50117194` → `50137194`).
  2. **Chemical/Industrial Hallucination**: Emits completely fabricated item descriptions (e.g. hallucinating `REFRIGERANT PENETRATE 21 cm 1 carton MEGAPL...` when the visible text is `REFRESH & REHYDRATE 21 grm 1 sachets 10EACH/PAC`).

#### 2. Clean Multi-Page Invoice Split (Docs 37 & 38)
- **Visual Appearance**: Invoice `SIV-ARS-25-8261` to ADES:
  - Page 1 (Doc 37): Top company header, customer details, empty item table grid (0 items), subtotal and VAT totals (`6,332.40 SAR` net / `7,282.26 SAR` gross).
  - Page 2 (Doc 38): Identical header block followed by 10 detailed line items (Cotton rags, WD-40, Permatex anti-seize, Silicone, Duct tape, Floor squeegees, Mops, Brooms, Kolor Kut paste).
- **Prompt Performance**:
  - The extractor behaved flawlessly on both pages: Doc 37 returned `line_items: []` without making up rows for the summary box, while Doc 38 extracted all 10 lines with 100% accurate quantities, units, and unit rates.

#### 3. Single-Item Partial Delivery Note (Doc 39)
- **Visual Appearance**: Clean scan of Delivery Note `GDN-ARS-25-9496` to ADES. Contains 1 line item: `Item No: 4`, `SILICONE, CLEAR (TUBE) - MFG: GULF - P/N# 1200`, `Qty: 24 Each`.
- **Prompt Performance**: The model accurately retained the vendor's line number `4` (`line_item_no: "4"`), perfectly matching Line 4 of Invoice Doc 38.

---

### Detailed Document Audit & Extraction Prompt Diagnostics

#### Doc 23 (`SIV-ARS-25-7970-pages-2.pdf`) vs Doc 24 (`PO`) & Doc 22 (`SI`)
- **Visual Evidence**:
  - Doc 23 header prints: `DELIVERY NOTE NO. : GDN-ARS-25-9175`, `P.O. # : 50117194`, `Rig No. : C016`.
  - Item: `REFRESH & REHYDRATE 21 grm 1 sachets 10EACH/PAC`, `QTY: 50 Each`.
  - Received stamp: `ARO DRILLING 02 NOV 2025`.
- **Database Errors**:
  - `po_no_raw`: `50137194` *(CRITICAL DIGIT CONFUSION)*
  - `line_items`: `REFRIGERANT PENETRATE 21 cm 1 carton MEGAPL...` *(CRITICAL OCR HALLUCINATION)*
- **Impact on System**:
  - PO Set 8 was quarantined because Doc 23's extracted PO (`50137194`) differed from PO Set 8's PO (`50117194`).
  - In physical reality, Doc 22 (SI), Doc 23 (DN), and Doc 24 (PO) are a **100% flawless 3-way match** for 50 units of Refresh & Rehydrate electrolyte powder!

#### Doc 37 (`SIV-ARS-25-8261-1.pdf`) & Doc 38 (`SIV-ARS-25-8261-2.pdf`)
- **Visual Evidence**: 2-page invoice `SIV-ARS-25-8261` to ADES Rig 686 for PO `193165`.
- **Database Status**:
  - Both docs linked to PO `193165`. Doc 37 has 0 lines, Doc 38 has 10 lines.
  - Total line items match invoice amount (`6,332.40 SAR`).

#### Doc 39 (`SIV-ARS-25-8261-3.pdf`)
- **Visual Evidence**: Delivery Note `GDN-ARS-25-9496` for ADES Rig 686.
- **Database Status**: Extracted Line 4 (24 tubes of Silicone), matching Invoice Doc 38 Line 4.

---

### Discrepancy Matrix

| Discrepancy Category | Description & Impact | Root Cause in Prompt / System |
|---|---|---|
| **What was Correct** | • 100% accurate classification of `SI`, `DN`, and `PO`.<br>• Accurate extraction of 10 items with prices and quantities on Doc 38.<br>• Correctly returned empty line items on Doc 37 without fabricating lines from totals.<br>• Perfect retention of `line_item_no: "4"` on partial DN Doc 39. | `_PAGE_PROMPT` Step 2 (classification), Step 3 (line item exclusion of totals), and Step 4 (numeric extraction) operated perfectly on clean documents. |
| **What was Wrong** | • Doc 23 extracted `po_reference = 50137194` instead of `50117194`.<br>• Doc 23 extracted hallucinated description `REFRIGERANT PENETRATE...` instead of `REFRESH & REHYDRATE...`. | The VLM suffered character hallucination and digit corruption when processing a degraded dot-matrix/photocopied scan. |
| **What was Misrepresented** | • PO Set 8 was quarantined for mismatched PO numbers, when in reality all documents belong to PO `50117194` and match 100%.<br>• PO Set 13 was quarantined for "holding more than one PO document", caused by multi-page PO splitting. | • Doc 23 digit error caused false PO mismatch.<br>• Splitter pipeline ingested pages 5 & 6 of PO `193165` as two separate PO documents. |
| **What was Missed** | • Correct PO digit `'1'` and genuine product text on Doc 23. | Model lacked cross-check instruction against sibling documents in the set. |

---

### Actionable Prompt Improvement Insights (Brand: Al-Shab / ARO / ADES)

1. **Anti-Hallucination & Transcoding Guardrail for Degraded Dockets**:
   - Add to `_PAGE_PROMPT` Step 3:
     `"DEGRADED / PHOTOCOPY SCANS: If text is grainy, low-contrast, or dithered, never substitute ungrounded technical terminology (e.g. inventing 'REFRIGERANT PENETRATE' or 'FLEX JOINT'). Transcribe ONLY the visible character strokes, and cross-reference with clear customer PO text if available."`

2. **Digit Discrimination in PO References**:
   - Add to `_PAGE_PROMPT` Step 1:
     `"On typed and scanned digits, distinguish carefully between '1' and '3' (a straight vertical stroke is '1'; curved loops indicate '3'). In PO numbers like '50117194', verify that consecutive identical digits are preserved verbatim."`

3. **Multi-Page PO Document Grouping Guidance**:
   - Ensure the prompt schema for `COMBINED` and multi-page documents instructs that subsequent pages of the same PO form a single logical document, preventing ingestion from treating each page as an independent competing PO contract.
