# PDF ↔ Database extraction audit — 2026-10-23 (rev. 2, image-verified)

**Revision note.** Rev. 1 of this report compared the database against the **embedded
text layer** of the stored PDFs. That was wrong for these files: most are scans whose text
layer is OCR, and even the vector PDFs extract with columns in the wrong visual order. Rev.
2 re-verifies every decisive claim by rendering the page and reading the image. One
"critical" finding from rev. 1 was an artefact of the text layer and is **retracted** at §7.

**Verdict.** The extracted values are substantially correct. The quantity/unit-price fields
match the printed documents on every page checked. The real problems are: **one delivery-note
template whose header is never read**, **three misread document numbers**, **one wrong
description**, and a set of **input/preparation problems** that look like extraction failures.

---

## 0. Scope and method

| Item | Count |
|---|---|
| Documents in DB | 284 |
| Line items in DB | 616 |
| PO sets | 83 |
| Sets merged | 27 |
| Sets not merged | 56 |
| Pages read as images for this revision | 25 |

Every claim marked **image-verified** below was read from a rendered page, not from text.
Claims marked **structural** come from the DB alone and do not need a page (e.g. "this row
has no line number"). Claims marked **unverified** are explicitly flagged.

---

## 1. Confirmed extraction defects

### X1 — CRITICAL: Drilling Tools delivery notes lose the whole header

All 25 `SIV-DTS-*` delivery notes are stored with `po_no_raw = NULL`, `po_no_normalized =
NULL` **and** `dn_no = NULL`, although both numbers are printed on the page. They are never
attached to a PO set.

**Image-verified, `doc 86`** (`SIV-DTS-25-452-pages-2.pdf`):

```
Del. Note No   : GDN-DTS-25-478
Buyers Order No: 4500026808
L/I # 1  HAMMER, ANTI-SPARK HEAD ... Qty 1.00 EA
```

DB: `document_number=NULL`, `po_no_normalized=NULL`, `dn_no=NULL`, `po_set_id=NULL`. The
line item was read correctly.

**Image-verified, `doc 121`** (`SIV-DTS-25-538-pages-2.pdf`): `Del. Note No: GDN-DTS-25-574`,
`Buyers Order No: 4500047182`, five lines read correctly (2–6, qty 1.00 each), header fields
all NULL.

The table is read; the header box is not. The DTS header is a narrow, rotated block down the
left edge of the page, and the prompt only scans "the first page top 20%".

**Impact.** 12 non-merged sets have no DN attached. **10 of them have PO and SI in exact
agreement on every line** and would merge on the next run if the header were read:

`29, 32, 37, 38, 39, 40, 42, 43, 44, 49`

This is the largest addressable win in the corpus.

---

### X2 — PO revision digit concatenated onto the PO number

**Image-verified, `doc 113`** (`SIV-DTS-25-517-pages-1.pdf`): the Buyers Order No field reads
`22613 , 0` — PO number `22613`, revision `0`. DB stored `po_no_normalized = 226130`.

The same model got `22398 , 0` right on `doc 144`, so this is non-deterministic.

---

### X3 — one-digit PO misread produces a false quarantine

**Image-verified, `doc 23`** (`SIV-ARS-25-7970-pages-2.pdf`): the printed P.O. is
**`50117194`**. DB stored `50137194`.

Set 8 is `quarantined` with *"A document references a different PO number"*, yet its PO, DN
and SI quantities agree exactly (50 = 50 = 50). This is a false quarantine caused by a single
misread digit.

---

### X4 — wrong description stored for a delivery-note line

**Image-verified, `doc 19`** (`SIV-ARS-25-7969-pages-2.pdf`). The page shows one line:

```
Item No 1   CLIP,GLOVE GRABBER,W/BELT CLIP,LIME   Part No 3405   Qty 50 Each
```

DB stored the description as `FLEX JOINT 3/4" NPT FEMALE X 1/2" NPT MALE`. That part is not
on this page and not on the PO. The header (`GDN-ARS-25-9355`, `P.O. 50119501`) and the
quantity (50) are correct.

**This is not cosmetic.** Set 7's PO line 6 is `CLIP,GLOVE GRABBER` qty 50
(**image-verified, `doc 21`**). Because the DN line number is a running `1` and its
description was hallucinated, *neither* matching signal can find PO line 6 — see X5.

---

### X5 — the same printed line marker is normalised two different ways

**Image-verified.** Two pages of one ARO PO (50119501):

| Doc | Page shows | DB `line_item_no` |
|---|---|---|
| 20 | `LineRel No: 1\1` | `1-1` |
| 20 | `LineRel No: 2\1` | `2-1` |
| 20 | `LineRel No: 3\1` | `3-1` |
| 21 | `LineRel No: 4\1` | `4` |
| 21 | `LineRel No: 5\1` | `5` |
| 21 | `LineRel No: 6\1` | `6` |

Identical printed format, two different outputs. Whichever convention is right, the PO cannot
be both. Page 1 keeps the `\1` suffix, page 2 drops it.

Combined with X4, set 7 has: PO keys `1-1,2-1,3-1,4,5,6`; DN key `1`; SI key `6`. Result:
the DN's only line cannot match anything, even though the goods and quantity (50) are right.

---

### X6 — meaningless delivery-note number

`doc 154` (`SIV-DTS-25-581-2.pdf`) stores `dn_no = '688'`. A DTS note number is
`GDN-DTS-25-nnn`. `688` is a fragment of something else. **Unverified** — not read as an
image. Low impact (the note is unattached either way), but the field is wrong.

---

### X7 — a wrapped description becomes an extra line item with qty 0

`line_item id 294` on `doc 125` (`SIV-DTS-25-538-pages-6.pdf`): `line_item_no = NULL`,
`quantity = 0`, description `"Non-absorbing PVC compound retained with aluminum collars;
Heavy-duty ..."` — a continuation of the item above. **Structural.**

The code deliberately stores zero-quantity rows rather than dropping them, and
`non_positive_quantity` would quarantine the set. Here it landed on a set pending for other
reasons, so it is latent. A PO can still be held for a row that is not a row.

---

## 2. Document ambiguity — the guard works, but the set stalls

### A1 — the delivery note prints two different PO numbers

**Image-verified, `doc 268`** (`SIV-RHO-26-233A.pdf`) prints **both**:

- top-right header: `PO : 4500362256`
- boxed field: `P.O : 260030`

**Image-verified, `doc 258`** (`SIV-RHO-26-1669A.pdf`) prints a boxed `P.O : 260988` and a
**handwritten** `Po: 83000 52050` across the top.

In both cases the model picked the boxed number and set `po_reference_ambiguous = 1`, which is
exactly what the prompt tells it to do, and the document was left unattached rather than
guessed at. **This is not an extraction defect.** It is a document that needs a human, and the
system has no human-resolution path for it — so sets 75 and 78 simply sit there.

### A2 — PO consolidated a line, the invoice itemised it

**Image-verified.** Set 75:

- PO 8300052050 (`doc 259`, page 1 of 9): one line, `1400026995 STORAGE BINS`,
  qty **100.00 EA**, price 5.250 → 525.000
- SI SIV-RHO-26-1669 (`doc 257`): **two** lines, `STORAGE BIN 350L` qty 50 @ 4.600 and
  `STORAGE BIN 500L` qty 50 @ 5.900 → 525.000

Same total, same units, but the invoice's line 2 has no PO line to match. Under the
line-number rule this is an unmatched vendor line → quarantine. No amount of extraction work
fixes it; it is a document-shape mismatch.

---

## 3. Input and data problems that look like extraction failures

These are **not** model errors.

### D1 — incomplete PO PDFs (3 sets)

- **Image-verified, `doc 272`** (`SIV-RHO-26-234B.pdf`): `Page 1 of 2`, PO C13717, value
  15,360.00, **all terms and conditions, no item table.** The item table is on page 2, which
  was never supplied (set 79).
- `doc 149` (`SIV-DTS-25-579-3.pdf`): text layer reads `Page 1 of 3`, no items (set 41).
  *Text-layer only, not image-verified.*
- `doc 278` (`SIV-RHO-26-687B.pdf`): text layer reads `Page 1 of 13`, no items (set 81).
  *Text-layer only, not image-verified.*

The DB correctly stores zero line items and the set quarantines with *"The PO document was
read but contains no line items — re-extract or contact the VLM operator"*. The message is
accurate; the cause is the missing pages.

### D2 — page-per-file input creates "multiple PO documents" (15 sets)

Sets `7, 13, 19, 24, 26, 27, 28, 30, 31, 35, 36, 45, 46, 47, 48`.

The input folder contains `...-pages-1.pdf`, `...-pages-2.pdf`, … i.e. each page was uploaded
as its own PDF. A two-page PO therefore becomes two PO documents and trips the
`single_po_document` guard. **Image-verified** that the pages are complementary, not
duplicates (doc 20 = PO lines 1–3, doc 21 = PO lines 4–6 of the same PO 50119501).

Nine of these "PO documents" have zero line items and are pure continuation/totals pages.

### D3 — two stored PDFs are truncated

`doc 164` and `doc 165` (`SIV-DTS-25-584-2/-3.pdf`) fail with `Stream has ended unexpectedly`.
Both are `UNKNOWN` / `failed` after 3 attempts — correct handling, but the files need
re-supplying.

---

## 4. Documented limitations, confirmed on live data

### L1 — line-number conventions (10 sets)

**Image-verified, `doc 92` vs `doc 89`** (PO 4500039614):

- PO item **10** = `Wrench: Adjustable 10"`, qty 1, 104.50
- SI line **1** = `[PROTO - J710B] PROTO WR ADJUSTABLE 10`, qty 1.00, 104.50

Same item, same quantity, same price — different number. Likewise PO 20 ↔ SI 2, PO 30 ↔ SI 3.
**Image-verified, `doc 217` vs `doc 215`** (PO 300101295): PO lines **22** (breaker 6,325.00)
and **24** (relay 1,705.00); the invoice prints `Line Item - 19` and `Line Item - 21`.

Valaris shows the other convention: PO `1-1` ↔ SI `1`.

All 10 sets (54, 55, 56, 57, 58, 64, 65, 66, 70, 74) are blocked on this, not on quantities.
This is documented as *"M — step-10 numbering"* and *"M — differing line-number conventions"*
in PRODUCT §8.

### L2 — description conflict on a merged set

**Set 69, PO 13952, status `merged`.** Same line (1), same quantity (40), same rate (61):

- PO (`doc 241`, image-verified): `BAG, COMPACTOR 40 CU FT 2000 LB CAPACITY — MFG: TECH OIL
  PRODUCTS — PN: HV96030`
- SI (`doc 239`, image-verified): `PP Woven Jumbo bags, Size 100 x 100 x 120 CM, Colour:
  White, Top: Skirt Bottom: closed …`

The descriptions conflict, and the engine merged without flagging it — the documented H risk.
**However**, the PO's own Supplier Note reads `PR 8737 // JUMBO BAG // REPEAT ORDER`, which
supports the invoice. So this is a **contradictory PO description**, not provably the wrong
goods. It should still be visible to the reviewer; today it is silent.

---

## 5. Behaviour that is correct — do not change

- **7 sets pending on a genuine partial delivery** (`4, 10, 11, 22, 50, 51, 63`). The PO has
  more lines than the invoice covers; the reason strings are accurate.
- **Set 1** — **image-verified**: PO 2360 orders **4200.00 LTR**; the DN and SI both show 400.
  A genuine commercial discrepancy, correctly `mismatched`.
- **Set 23** — **image-verified**: PO 18230578 orders **10** RING; the DN/SI show 2. Correctly
  `mismatched`.
- **Sets 76 and 77** are waiting for the other leg. Correct.

---

## 6. Why the 56 sets did not merge

| Primary blocker | Sets | Fix? |
|---|---:|---|
| Multiple PO documents (page-split PO) | 15 | Input prep, or count distinct PO *numbers* not PO *documents* |
| No DN attached (DTS header lost) | 12 | **Fix X1 — 10 merge immediately** |
| Line-number convention | 10 | Documented limitation L1 |
| Partial delivery — correct | 7 | Nothing |
| PO has no line items (incomplete PDF) | 3 | Re-supply the full PO (D1) |
| Ambiguous DN reference (75, 78) | 2 | Human resolution path (A1) |
| Genuine quantity mismatch (1, 23) | 2 | Human review |
| Vendor line-number mismatch (61) | 1 | Vendor/PO numbering (L1 variant) |
| No DN supplied — waiting (76) | 1 | Nothing |
| No SI supplied (77) | 1 | Nothing |
| False quarantine, misread digit (8) | 1 | **Fix X3** |
| Quantity differs — unverified (59) | 1 | Human review |

Single-document false quarantines worth calling out:

| Set | Stored status | Reality |
|---|---|---|
| 8 | quarantined | quantities agree exactly; DN PO digit misread (X3) |
| 19 | quarantined | quantities agree exactly; the only extra PO document is an empty page (D2) |
| 13 | quarantined | quantities agree after aggregation; two PO pages (D2) |

---

## 7. Retracted from rev. 1

**"Quantity and unit price are swapped on every Valaris/Ensco PO."** This was false. It came
from `pypdf` extracting the row as `25.00EA 50.00 1,250.00` while the page visibly shows
`50.00 EA | 25.00 | 1,250.00`. The model read the page correctly; the text layer reversed the
columns.

**Image-verified on all 5 documents, 9 of 9 lines — the DB matches the paper:**

| Doc | Line | Page shows (qty, unit price) | DB stores |
|---|---|---|---|
| 226 | 1-1 | 50.00, 25.00 | 50, 25 ✓ |
| 229 | 1-1 | 20.00, 40.00 | 20, 40 ✓ |
| 232 | 1-1 | 24.00, 105.00 | 24, 105 ✓ |
| 232 | 2-1 | 1.00, 935.00 | 1, 935 ✓ |
| 232 | 3-1 | 5.00, 210.00 | 5, 210 ✓ |
| 232 | 4-1 | 3.75, 100.00 | 3.75, 100 ✓ |
| 244 | 1-1 | 1.00, 426.00 | 1, 426 ✓ |
| 256 | 1-1 | 1.00, 2,300.00 | 1, 2300 ✓ |
| 256 | 2-1 | 1.00, 975.00 | 1, 975 ✓ |

Also corrected from rev. 1:

- **"Quotation number stored as the PO number"** → reclassified (§2 A1). The documents
  genuinely print two PO numbers and the model correctly flagged ambiguity.
- **Set 61 "quantity differs"** → it is a line-number mismatch (§4 L1).
- **"Wrong item merged"** for set 69 → softened to a description conflict (§4 L2).

**Lesson for the next audit:** for this corpus the text layer is not evidence. Values may be
right while their column association is wrong, and OCR digits are unreliable (`LPO2612360`,
`GDN'RH0'26-796`). Always render the page.

---

## 8. Recommended fixes, in priority order

1. **Read the whole DN/SI header, including rotated and left-margin blocks (X1).** Widen STEP 1
   from "top 20% of the first page" to the whole first page, and require `document_number`
   non-null when `document_type = DN`; retry once with a header-focused prompt if it comes back
   null. 10 sets merge the same day this works.

2. **Validate extracted numbers against the page when a text layer exists (X2, X3).** A
   post-check "does `po_no_normalized` appear on the page?" would have caught `226130` and
   `50137194`. For scans, this needs the rendered text, not the stored OCR layer.

3. **Strip a trailing revision token from PO numbers (X2).** `22613 , 0` → `22613`.

4. **Make the line-marker rule consistent (X5).** If `LineRel No: N\1` means `N-1`, apply it on
   every page. The current split (`1-1` vs `4`) fragments a single PO.

5. **Do not store a page with zero line items as a PO document.** Mark it `SKIP` /
   `PO_CONTINUATION`, or make the `single_po_document` guard count distinct PO *numbers*. This
   alone unblocks sets 13 and 19.

6. **Add a description-conflict flag on shared line numbers.** PRODUCT §8 already sketches the
   ~15-line check. Set 69 shows the risk is live.

7. **Give ambiguous PO references a human path (A1).** Sets 75 and 78 are stuck with no way
   forward in the UI. Surface the two candidates and let the operator pick.

8. **Treat a wrapped description row as part of the row above (X7).** A NULL `line_item_no`
   with `quantity = 0` is not an item.

---

## 9. Verification log

Pages read as images for this revision:

| Doc | File | Why |
|---|---|---|
| 2 | PO2360.pdf | set 1 quantity (4200 vs 400) |
| 19 | SIV-ARS-25-7969-pages-2.pdf | DN description (X4) |
| 20 | SIV-ARS-25-7969-pages-3.pdf | line marker `1\1` (X5) |
| 21 | SIV-ARS-25-7969-pages-4.pdf | line marker `4\1` (X5) |
| 23 | SIV-ARS-25-7970-pages-2.pdf | PO digit (X3) |
| 73 | SIV-ARS-25-8321-pages-3.pdf | PO qty 10 (set 23) |
| 86 | SIV-DTS-25-452-pages-2.pdf | DTS header (X1) |
| 89 | SIV-DTS-25-456-pages-1.pdf | SI line 1 (L1) |
| 92 | SIV-DTS-25-456-pages-4.pdf | PO item 10 (L1) |
| 113 | SIV-DTS-25-517-pages-1.pdf | PO revision (X2) |
| 121 | SIV-DTS-25-538-pages-2.pdf | DTS header (X1) |
| 215 | SIV-RAK-25-2968-Noble-1.pdf | invoice `Line Item - 19/21` (L1) |
| 217 p5 | SIV-RAK-25-2968-Noble-3.pdf | PO lines 22/24 (L1) |
| 226 | SIV-RAK-25-3058-Ensco-3.pdf | retraction §7 |
| 229 | SIV-RAK-25-3059-Ensco-3.pdf | retraction §7 |
| 232 | SIV-RAK-25-3060-Ensco-3.pdf | retraction §7 |
| 239 | SIV-RAK-25-3126-Ades-1.pdf | set 69 invoice (L2) |
| 241 | SIV-RAK-25-3126-Ades-3.pdf | set 69 PO (L2) |
| 244 | SIV-RAK-25-3130-Ensco-3.pdf | retraction §7 |
| 256 | SIV-RAK-25-3145-Ensco-3.pdf | retraction §7 |
| 257 | SIV-RHO-26-1669.pdf | set 75 invoice (A2) |
| 258 | SIV-RHO-26-1669A.pdf | two PO numbers (A1) |
| 259 | SIV-RHO-26-1669B.pdf | set 75 PO (A2) |
| 268 | SIV-RHO-26-233A.pdf | two PO numbers (A1) |
| 272 | SIV-RHO-26-234B.pdf | incomplete PO (D1) |

Not read as images and therefore flagged as unverified in the text: doc 149, doc 154,
doc 278, set 59.

Raw dumps are in `scratch_dump/` (`db_dump.json`, `pdf_texts.json`, `render/`). No file under
`src/`, no database row and no git state was modified.
