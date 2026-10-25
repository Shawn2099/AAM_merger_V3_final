# Initial Batches (01 - 23) Brand & Document Type Retrofit Analysis

## Executive Summary
In the initial audit phase (Batches 01 through 23, covering Documents 1–114, 116–125, 127, 132–146, 148, 151, 154, 157, 160, 161–186, 221–223, 257–262, 264–272), the audit reports focused primarily on comparing DB records to PDF contents. This document retrofits the visual characteristics, layout peculiarities, field terminologies, and prompt extraction rules for all unique companies and document types present in those initial batches.

---

## 1. KCA Deutag Drilling GmbH (SIES / KCA Deutag)
- **Role in Corpus**: Buyer / Operator for Al-Shab deliveries (Batches 01–04; PO numbers starting with `5011...` or `5013...`, e.g., `50116501`, `50118064`, `50117194`, `50114549`).
- **Document Type**: Purchase Order (`PO`)
- **Visual & Layout Characteristics**:
  - **Header Structure**: European corporate SAP purchase order with KCA Deutag or German subsidiary logo at top-left.
  - **Document Labels**: Labeled as `Purchase Order` or `Bestellung`.
  - **PO Number Placement**: Top right block labeled `Purchase Order No.` or `Bestell-Nr.`. Numbers are 8 digits starting with `5011` or `5013`.
  - **Table Format**: Columns include `Item` (step-10 numbering: `10, 20, 30...`), `Description`, `Quantity`, `Unit`, `Price/Unit`, `Total`.
- **Key Peculiarities & Failure Modes**:
  - **European Number Formatting**: Uses German notation: comma (`,`) as the decimal mark and period (`.`) as the thousands separator (e.g. `1.250,50 SAR` or `57,72 SAR`).
  - **Sanitizer Impact**: Standard float sanitizers drop decimals after comma or treat `,` as thousands separators, resulting in prices like `5772` or `0.0`.
- **Prompt & Normalization Directives**:
  1. Detect European decimal formatting if comma is followed by exactly 2 digits at the end of a price string.
  2. Map step-10 item numbering (`10, 20, 30`) to sequential supplier invoices (`1, 2, 3`) via position or line description cross-referencing.

---

## 2. SANAD (Saudi Aramco Nabors Drilling Company)
- **Role in Corpus**: Major Buyer / Operator for Al-Shab deliveries (Batches 05–07, 39, 40; PO numbers starting with `188...`, `193...`, or `1823...`, e.g., `188221`, `18232392`, `18231375`, `18230578`).
- **Document Type**: Purchase Order (`PO`)
- **Visual & Layout Characteristics**:
  - **Header Structure**: Distinctive SANAD stylized logo (green, black, orange curves) with text `SANAD` and Arabic subtitle `أرامكو السعودية نابورس للحفر`.
  - **Orientation**: **Dense Landscape Orientation** across 1 to 3 pages.
  - **PO Number Placement**: Labeled prominently above the main title as `Header Information: PO 18230578` and in the right-hand metadata box as `PO #: 18230578`, with `Revision #: 0`.
  - **Table Format**: 14 densely packed columns: `Line`, `NIN`, `Supplier Item #`, `OEM Manufacturer`, `OEM Part Number`, `Item Description`, `Cancel Flag`, `Promised Date`, `QTY`, `UOM`, `Account Code`, `Unit Price`, `Extended Price`, `Deliver To`.
- **Key Peculiarities & Failure Modes**:
  - **Landscape Table OCR Tracking**: Because columns are extremely narrow and landscape-oriented, VLMs without spatial guidance can accidentally merge the `OEM Part Number` column into the `Item Description`, or misread `QTY` from the adjacent date column.
- **Prompt & Normalization Directives**:
  1. Recognize landscape SANAD templates immediately by the green/black logo.
  2. Pinpoint column coordinate targets: `Line` (Column 1), `Item Description` (Column 6), `QTY` (Column 9), and `Unit Price` (Column 12).
  3. Strip administrative revision markers (`Revision #: X`).

---

## 3. Arabian Drilling Company (ADC)
- **Role in Corpus**: Major Rig Operator / Buyer (Batches 09–10; PO numbers starting with `450005...`, e.g., `4500051195`).
- **Document Type**: Purchase Order (`PO`)
- **Visual & Layout Characteristics**:
  - **Header Structure**: Prominent blue/red ADC logo (`Arabian Drilling Company` / `شركة الحفر العربية`).
  - **Orientation**: Standard Portrait.
  - **PO Number Placement**: Centered bold box: `PURCHASE ORDER`, `PO Number : 450005XXXX`, `Date : Month DD, YYYY`, `Land Rig : ADXX`.
  - **Table Format**: Clean grid with `Item` (`10, 20, 30`), `Material (Part No & Description)`, `Qty`, `UOM`, `Unit Price`, `Net Amount (SAR)`.
- **Key Peculiarities & Failure Modes**:
  - Multi-page orders conclude with a final page containing ONLY terms of payment, incoterms, bank details, and authorization stamps, with zero line items.
- **Prompt & Normalization Directives**:
  1. On ADC's trailing summary/signature page, return `line_items: []` to avoid hallucinating items from payment milestone amounts.
  2. Normalize step-10 line numbers (`10 → 1`, `20 → 2`).

---

## 4. Tubestar Oil & Gas Arabia Company
- **Role in Corpus**: Specialized Tubular Inspection & Handling Services Vendor / Partner (Batches 10, 14; PO numbers starting with `700...`).
- **Document Type**: Purchase Order & Inspection Certificates (`PO`, `CUSTOMS`, `SHIPPING`)
- **Visual & Layout Characteristics**:
  - **Header Structure**: Tubestar corporate logo with circular emblem.
  - **Table Format**: Lists tubular inspection, threading, hardbanding, and NDT testing line items.
- **Key Peculiarities & Failure Modes**:
  - Highly technical API casing specifications (e.g., `5-1/2" 20# L-80 VAM TOP`, `4-1/2" IF Tool Joint`).
  - Fractional inches (`5-1/2"`, `2-7/8"`) in descriptions can confuse naive decimal/float extractors if not properly shielded inside quotation strings.
- **Prompt & Normalization Directives**:
  1. Keep dimensional fractions (`5-1/2"`, `3/4"`) intact in `description` strings without attempting mathematical evaluation.

---

## 5. Schlumberger Private / SLB
- **Role in Corpus**: Global Oilfield Services Buyer (Batches 10, 11; PO numbers starting with `4800...`, e.g., `4800466856`).
- **Document Type**: Purchase Order (`PO`)
- **Visual & Layout Characteristics**:
  - **Header Structure**: Minimalist SLB / Schlumberger blue header.
  - **PO Number Placement**: Right-hand metadata box: `Purchase order number: 4800466856`, `PO Version No: 4800466856-0`.
  - **Table Format**: `Item`, `Vendor Part No. / Description`, `Quantity`, `Unit Net price SAR`.
  - **Paragraph Sub-Rows**: Directly under each row, SLB embeds an indented block labeled `Item text: <Detailed technical specifications and rig requirements>`.
- **Key Peculiarities & Failure Modes**:
  - If the prompt only reads the table cell, it misses the critical specification in `Item text:`, leading to truncated item descriptions.
  - PO number suffix `-0` (version) must not contaminate the normalized PO number.
- **Prompt & Normalization Directives**:
  1. Strip `-0` or `-1` version suffix from `4800466856-0` → `4800466856`.
  2. Append the text inside `Item text:` directly to the preceding line item's `description`.

---

## 6. ADES Saudi Limited / ADES Group
- **Role in Corpus**: Off-shore / On-shore Drilling Operator (Batches 09, 16, 17, 24; PO numbers e.g. `193165`, `22613`).
- **Document Type**: Purchase Order (`PO`)
- **Visual & Layout Characteristics**:
  - **Header Structure**: ADES green/navy logo with circular rig emblem.
  - **PO Placement**: Top left box: `Standard Purchase Order`, `PO 193165` or `PO, Rev # : 193165, 0`.
  - **Table Format**: `Line`, `Item Description`, `Quantity`, `UOM`, `Unit Price (SAR)`, `Total (SAR)`.
- **Key Peculiarities & Failure Modes**:
  - Revision format contains comma space: `193165 , 0`.
  - Drilling Tools dockets referencing ADES write handwritten rig numbers (e.g. `ADES 688`) directly in the header, which confused legacy extractors into thinking `688` was the PO number.
- **Prompt & Normalization Directives**:
  1. Explicit negative rule: Any rig identifier starting with `ADES` followed by 3 digits is a RIG NAME, NEVER a PO or DN number.
  2. Strip revision suffix ` , 0` to yield the clean integer PO.

---

## 7. Saudi Aramco Rowan (ARO Drilling)
- **Role in Corpus**: Offshore Jack-up Drilling Operator (Batches 01, 02; PO numbers starting with `450004...`, e.g., `4500043712`, `4500043354`).
- **Document Type**: Purchase Order (`PO` - SAP DPO Format)
- **Visual & Layout Characteristics**:
  - **Header Structure**: Dual Aramco / Rowan branding or ARO Drilling logo.
  - **PO Placement**: Labeled as `DPO Number : 4500043712`.
  - **Line Marker**: Contains secondary scheduling markers inside the table: `Line\Rel No: 1\1`.
- **Prompt & Normalization Directives**:
  1. Capture `DPO Number` as `document_number` and `po_reference`.
  2. Treat `Line\Rel No: X\Y` as primary line number `X`.

---

## 8. Urbaser Bahrain Co. W.L.L.
- **Role in Corpus**: Waste Management & Environmental Fleet Operator (Batches 01, 39; PO numbers e.g. `LPO26/2360`).
- **Document Type**: Purchase Order (`PO` - Blanket / Open LPO)
- **Visual & Layout Characteristics**:
  - **Header Structure**: Urbaser teal swirl logo with bilingual English/Arabic title `Urbaser Bahrain Co W.L.L.`.
  - **PO Placement**: `PO No : LPO26/2360`.
  - **Narration**: Explicitly notes `:Daily Cleaning Items Month of 01-05-26 to 31-07-26 Open Lpo`.
- **Key Peculiarities & Failure Modes**:
  - Blanket PO quantity is for the entire 3-month contract (e.g. `4200 LTR`), whereas invoices and delivery notes are monthly call-offs (e.g. `400 LTR`).
- **Prompt & Normalization Directives**:
  1. Strip slash in `LPO26/2360` → `LPO262360`.
  2. Extract blanket narration to alert downstream reconciliation of call-off status.
