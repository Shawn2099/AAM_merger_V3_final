## Batch 03 (PDFs 11 to 15) — Pending PO Sets 22 & 27

### Documents Audited
11. **Doc 69 (`SIV-ARS-25-8320-pages-2.pdf`)** — Delivery Note (DN) | PO Set 22
12. **Doc 70 (`SIV-ARS-25-8320-pages-3.pdf`)** — Purchase Order (PO) | PO Set 22
13. **Doc 85 (`SIV-DTS-25-452-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 27
14. **Doc 87 (`SIV-DTS-25-452-pages-3.pdf`)** — Purchase Order (PO) | PO Set 27
15. **Doc 88 (`SIV-DTS-25-452-pages-4.pdf`)** — Purchase Order (PO) | PO Set 27

### Detailed Image Comparison & Verification
- **Doc 69 (DN `GDN-ARS-25-9520`)**: Visually verifies Al-Shab Delivery Note for SANAD PO `18232411`.
  - Printed Item No: `4`.
  - Printed Description: `IMPEX 65" TV WEBOS 4K UHD`, Qty `1 Each`.
  - **Misread in DB**: The DB stored description as `IMPEX 85" TV WEBOS 4K UHD` (misread `6` as `8`).
  - Stored quantity `1000` (1.0) and line item number `4` are correct.
- **Doc 70 (PO `18232411`)**: Visually verifies SANAD PO `18232411` Rev `3`. Page 1 of 2 contains 5 lines:
  - Line 1: `JACKET, JEANS, XL...`, Qty `1`, Unit Price `397`
  - Line 2: `[PROTO - J2146]...`, Qty `1`, Unit Price `131`
  - Line 4: `IMPEX 65" TV WEBOS 4K UHD`, Qty `1`, Unit Price `1553` (DB has minor typo `WEBOBS`)
  - Line 5: `[FACOM - NS.3100GPB]...`, Qty `1`, Unit Price `2286`
  - Line 9: `B 53 Optibelt`, Qty `2`, Unit Price `27`
  Extracted accurately. Set 22 holds in `pending` because only Line 4 was delivered/invoiced.
- **Doc 85 (SI `SIV-DTS-25-452`)**: Visually verifies Tax Invoice from Drilling Tools Trading Est. (DrillTools) for Arabian Drilling Company.
  - Buyers Order No: `4500026808`, Invoice No `SIV-DTS-25-452`, Delivery Note reference `GDN-DTS-25-478`.
  - Line `1`: `HAMMER, ANTI-SPARK HEAD, 7LB (4KG), 16" (305MM) HANDLE...`, Qty `1.00 EA`, Rate `2,700.00 SAR`.
  - Stored in DB as line `1`, qty `1000`, price `2700000`. **Exact match**.
- **Doc 87 (PO `4500026808`)**: Visually verifies Arabian Drilling Company PO `4500026808`, Page 1 of 8.
  - Printed Item: `10`.
  - Description: `125152 Hammer: Sledge 7 Lbs, 16" Fiberglass Handle, Anti-Spark...`, Qty `1 EA`, Unit Price `2700.00 SAR`.
  - Stored in DB as line `10`, qty `1000`, price `2700000`. **Exact match**.
- **Doc 88 (PO `4500026808`)**: Visually verifies Arabian Drilling Company PO `4500026808`, Page 2 of 8.
  - Contains only total summary (`Total P.O. Value: 2,700.00`) and instructions to vendor. No line item table.
  - Stored in DB as a PO document with 0 line items.
  - **Structural Input Issue**: Ingestion of page-split input creates a second PO document for the same PO number with 0 lines, tripping the `single_po_document` guard.

### Findings Summary
- **What was Correct:**
  - Header numbers (`18232411`, `4500026808`, `SIV-DTS-25-452`, `GDN-ARS-25-9520`) extracted accurately.
  - Quantities and unit prices across all documents match the printed pages.
- **What was Wrong / Misrepresented:**
  - **Doc 69 Description Misread**: Digit `6` misread as `8` (`65"` → `85"`).
  - **Set 27 Line Number Convention (L1)**: PO numbers the item as `10`, while vendor invoice numbers the item as `1`.
  - **Missing Delivery Note for Set 27**: Set 27 is pending with `"No delivery note in this set yet"` because the corresponding DN (`SIV-DTS-25-452-pages-2.pdf`, Doc 86) lost its header and became unattached.
- **What was Missed:**
  - The continuation page Doc 88 has zero line items and is merely totals/terms. Treating it as a distinct PO document causes structural set pollution.
- **What We Need to Do to Improve:**
  - Ignore or classify 0-line continuation pages as `PO_CONTINUATION` rather than registering them as a full secondary PO.
  - Add OCR/VLM post-check on critical digit/token specifications in descriptions.
  - Support Step-10 mapping (`10 ↔ 1`) for SAP/Arabian Drilling POs.

---
