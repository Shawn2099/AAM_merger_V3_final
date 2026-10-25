## Batch 08 (PDFs 36 to 40) — Pending PO Sets 35 & 36

### Documents Audited
36. **Doc 116 (`SIV-DTS-25-519-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 35
37. **Doc 118 (`SIV-DTS-25-519-pages-3.pdf`)** — Purchase Order (PO) Page 1 | PO Set 35
38. **Doc 119 (`SIV-DTS-25-519-pages-4.pdf`)** — Purchase Order (PO) Page 2 | PO Set 35
39. **Doc 120 (`SIV-DTS-25-538-pages-1.pdf`)** — Sales Invoice (SI) | PO Set 36
40. **Doc 122 (`SIV-DTS-25-538-pages-3.pdf`)** — Purchase Order (PO) Page 1 | PO Set 36

### Detailed Image Comparison & Verification
- **Doc 116 (SI `SIV-DTS-25-519`)**: Visually verifies Drilling Tools Tax Invoice for Arabian Drilling Company PO `4500044740`.
  - Delivery Note reference: `GDN-DTS-25-555`.
  - Item 1: `Body Solid Pro Clubline Commercial Squat Stand`, Qty `1.00 PCS`, Rate `1,856.00 SAR`.
  - Stored in DB as line `1`, qty `1000`, price `1856000`. **Exact match**.
- **Doc 118 (PO `4500044740` Page 1)**: Visually verifies Arabian Drilling Company PO `4500044740`.
  - Item printed: `10`.
  - Description: `Rakc: Squat F/ Gym Room, Mfg: SportsArt; Body Solid Pro Clubine Commercial Squat Stand...`, Qty `1 EA`, Unit Price `1856.00 SAR`.
  - Stored in DB as line `10`, qty `1000`, price `1856000`. **Exact match**.
- **Doc 119 (PO `4500044740` Page 2)**: Visually verifies continuation sheet with 0 lines.
- **Doc 120 (SI `SIV-DTS-25-538`)**: Visually verifies Drilling Tools Tax Invoice for Arabian Drilling Company PO `4500047182`.
  - Delivery Note reference: `GDN-DTS-25-574`.
  - Contains 5 gym equipment items:
    - Line 1: `Insight Fitness DR010B Back Extension`, Qty `1.00`, Rate `1,530.00`
    - Line 2: `Axox Fitness Aerobic Stepper`, Qty `1.00`, Rate `65.00`
    - Line 3: `Body Solid GFID100B FID Bench`, Qty `1.00`, Rate `1,050.00`
    - Line 4: `Axox Fitness Solid Tricep Press Down Bar`, Qty `1.00`, Rate `95.00`
    - Line 5: `Insight Fitness Leg Curl/Extension`, Qty `1.00`, Rate `5,570.00`
  - Total: `8,310.00 SAR`. Stored in DB across all 5 lines with 100% accuracy.
- **Doc 122 (PO `4500047182` Page 1)**: Visually verifies Arabian Drilling Company PO `4500047182`.
  - Item printed: `20`: `183371 Roman Machine: 45 Deg Positioning... Insight Fitness DR010B Back Extension...`, Qty `1`, Unit Price `1530.00 SAR`.
  - Corresponds exactly to SI Line 1 (`1,530.00 SAR`).

### Findings Summary
- **What was Correct:**
  - Complete, accurate extraction of all line items, quantities, and prices for gym equipment orders.
- **What was Wrong / Misrepresented:**
  - **L1 Numbering Convention**: Set 35 PO item `10` vs invoice item `1`. Set 36 PO item `20` vs invoice item `1`.
  - **Page-Split Ingestion**: Doc 119 (terms only) ingested as a distinct PO document.
- **What was Missed:**
  - Both delivery notes (`SIV-DTS-25-519-pages-2.pdf` Doc 117 and `SIV-DTS-25-538-pages-2.pdf` Doc 121) were unattached due to lost DTS headers.
- **What We Need to Do to Improve:**
  - Link DTS delivery notes to unblock Sets 35 and 36.
  - Apply step-10 line normalizer on Arabian Drilling POs.

---
