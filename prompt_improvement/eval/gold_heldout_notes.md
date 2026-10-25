# Held-out gold set notes

29 documents not in gold.json, same schema. Transcribed from PyMuPDF renders (1.5x full pages,
3.5-4x crops of header/digit fields; DB opened read-only only to locate files). No text layer or DB
extracted value used. Quantity convention as gold_notes.md. Extra optional keys: `dn_reference`,
`sl`, `line_key_ambiguity`, `ambiguous`/`po_candidates`, `po_printed_raw`, `unscored_reads`.

Composition: Borr SI/DN/PO (6, new vendor), ADES DN (2) + SI (1) + PO (1), Ensco/Valaris SI+DN (2),
Noble SI/DN/PO (3, first Noble SI/DN), RAAS SI/DN/PO to GARMCO (3, new customer, dual PO), RAAS DN
+ PO to Bahrain Airport Services (2, `-SNN` PO), RAAS DN to IMI (1), Drilling Tools pink DNs (5),
Al-Shab raster DNs (3). Excluded SANAD/SLB/NOMAC etc. were not re-sampled (already in gold).

## Uncertain / unscored

- **4, 7**: Al-Shab doc number last digits crossed by the box border (8330 / 8326). Moved to
  `unscored_reads`; document_number not scored. PO, lines, qty certain.
- **40**: Al-Shab DN with a smudged item number on the duct-tape row (qty 50 Each, between lines 3
  and 6). That row is in `unscored_reads`, not in `lines`; a prediction that includes it will count
  as an "extra" line in score.py, so treat 40 as partial. Doc number `GDN-ARS-25-9329` also crossed by
  a border (unscored). Lines 1,2,3,6,7,8,9,10 certain.
- **181/182/209/210** (RAK SI/DN where SL prints 1 but description says `Line Item - 2`): gold key is
  `2` (the Line Item label, equal to the PO line). Decided in the Opus audit below: `Line Item` wins.
- **261** two PO numbers (`60213576`, `13085`): po_reference null, ambiguous, same rule as 258.
- Pages not viewed (assumed terms): 183 p3-7, 211 p3-10, 238 p3, 262 p2-3.
- 114: file sits in the 517 group but prints GDN-DTS-25-548; transcribed as printed.

## Label table (exact printed strings; doc id where seen)

| Vendor / type | PO reference | Own document no. | Delivery-note ref | Quantity | Line no. |
|---|---|---|---|---|---|
| Drilling Tools DN (96,104,114,133,137) | `Buyers Order No :` | `Del. Note No :` | n/a | `Qty` (+`Units`), `Total Qty` | `L/I.#` |
| Al-Shab DN (4,7,40) | `P.O. #` (Arabic `أمر شراء`) | `DELIVERY NOTE NO.` (Arabic `رقم اذن التسليم`) | n/a (`S.O. #SOD-ARS-25-nnnn` is a sales order, not an id) | `QTY.` (`الكمية`) values like `12 Each` | `Item No.` (`رقم`) |
| RAK SI (181,209,224,233,236) | `Buyer's Order No :` | `Invoice No :` | `Delivery Note :` | `Qty` | `SL` and `Line Item - N` under description |
| RAK DN (182,210,225,234,237,240) | `Buyers Order No :` | `Del. Note No :` | n/a | `Qty` (+`Units`), `Total Qty` | `SL` and `Line Item - N` |
| RAAS SI (260) | `P.O. # :` | `INVOICE # :` | table col `Delivery Note No` (wrapped `GDN-RHO-26-` / `2105`) | `Qty` | `Sr.` and `Line Item - N` |
| RAAS DN (261,264,271) | `P.O :` box; also free text `PO - 60213576` header (261) | `Doc No :` (wraps in 261,264) | n/a | `Qty` with unit text (`3.00 CANS`, `1.00 NOS`, `120.00 EACH`) | `Item No` and `Line Item - N` |
| ADES PO (183) | title `PO 13060`; `PO , Rev # 13060 , 0` | same (PO no.) | n/a | `Quantity` + `UOM` | `Line` |
| Borr PO (235,238) | `Purchase Order` header value `P213899` | same | n/a | `Qty Due` (+`Quantity UoM` `4 EA`) | `Line` (header `LineYour Part No`) |
| Noble PO (211) | `Purchase Order No.` `J255-300115876`, `Revision 0` | same | n/a | `Quantity` + `UoM` | `Line` / `Rel` (col `Line Supplier Part No` / `Rel Our Part No`) |
| GARMCO PO (262) | `PURCHASE ORDER #` / `PURCHASE ORDER #` box `60213576 OPG`; `SUPPLIER REF. #` 13085; `PURCHASE REQUEST #` 10368526 | same | n/a | `QTY` + `U/M` | `L/ITEM #` |
| Bahrain Airport Services PO (266) | `P.O. Number` `4270006645 - SNN` | same | n/a | `Quantity` + `Unit` | `No` |

Observations useful for aliases:
- RAK SI/DN print both `SL` and `Line Item - N`; they disagree when the PO line is not 1 (181, 209).
- RAK DN labels: `Del. Note No :` / `Buyers Order No :`; RAK SI labels: `Invoice No :` / `Buyer's Order No :`
  (apostrophe differs). `Rig No :` (SI) vs `Rig / Location :` (DN) are never ids.
- Al-Shab DN has no per-line `Line Item` text; item numbers step 10,20 (4,7) or 1..N (40).
- RAAS DN: header free-text `PO - <SAP PO>` sits outside the boxed `P.O :` which may hold a supplier ref.
- Non-numeric PO forms seen: `P213899`, `P212528`, `C13717`, `J255-300115876`, `10006-0000079633`,
  `TOGA-PO-25-26-163`, `ARM-PO-25-26-035`, `4270006645 -SNN`, `22613 , 0`, `60213576 OPG`.
- Handwritten non-ids to ignore: DTS rig `505` (114), `PR-10368526` (261), `30285547` (225), Al-Shab `10 Each` (40).

## Opus audit (2026-10-23)

Independent re-read from my own PyMuPDF renders (DB opened `mode=ro` only to find `stored_path`;
1.5x full pages, 5-14x crops of every scored id field, and of qty/line columns on scans without a
Total cross-check). No text layer, DB extracted value or results/ folder used. I read each page before
opening its gold entry. Caveat: this notes file (label table, observations) was read first and names
many of the PO strings and the 4/7/40 doc numbers, so those reads were not fully blind.

**Docs checked: all 29.** 4, 7, 40, 96, 104, 114, 133, 137, 181, 182, 183, 209, 210, 211, 224, 225,
233, 234, 235, 236, 237, 238, 240, 260, 261, 262, 264, 266, 271.

**Transcription errors on scored fields: none.** Every document_type, document_number, po_reference,
line_item_no and quantity matched my read. Cross-checks: Total Qty on 96 (24), 104 (250), 114 (1),
133 (28), 137 (188), 225 (46), 234 (4), 237 (12), 240 (40); amount sums on 236 (3,602.00), 238
(3,602.00), 211 (476.00), 260 (68.250; 3x11.250=33.750), 262 (33.750+34.500=68.250), 183 (4,539.00).

**Change made to gold_heldout.json:**
- 40: `lines` moved to `unscored_reads.lines_certain_rows`. The page has 9 rows; the duct-tape row's
  item number is obliterated (unreadable at 14x). `score.py` ignores `confidence`, so "partial" did
  nothing: any faithful prediction that includes that row (keyed 4, 5 or null -> `''`) is an "extra"
  line and fails the doc, which makes the gold wrong for a correct extractor. Same treatment as
  gold.json 121. Still scored: document_type, po_reference `193165`. Also corrected its note: the
  `KOLOR KUT ... 10 Each` text at the page top is a printed, misregistered overprint of line 10, not
  handwriting.
- 4, 7, 40 `unscored_reads` notes: the box border crosses the third serial digit (`83|30`, `83|26`,
  `93|29`), not the last digits. Values left unscored (my reads agree but the crossed digit is not
  certain).

**RAK `SL` vs `Line Item - N`: Line Item wins; gold key `2` stands for 181/182/209/210.** Evidence
from the PO line: PO 183 line 2 = helideck tie-down straps P/N SG03860, 4 EACH (SI/DN 181/182:
`Line Item - 2`, 4.00, SL 1). PO 211 line 2 = nipple Mfg Part 499-3243 (SI/DN 209/210: `Line Item -
2`, P/N 499-3243, SL 1). SL is just a per-document row counter. `extraction.py` already tells the
model to prefer the embedded `Line Item` marker. (209/210 deliver 2 vs PO 3: a reconciliation fact,
not a gold issue.)

**Page-coverage claims:**
- 183 p3-7, 211 p3-10, 238 p3: viewed (thumbnails). All terms text, no line items. 211 p3 is the
  tail of "Page 3 (3)" of the order; p4-10 are Noble General T&C pages 1-7.
- 262 p2-3: **these pages are not in the file.** The stored PDF has 1 page although it prints
  `Page 1 of 3`. No lines are missing: the two lines sum to the printed TOTAL ORDER VALUE 68.250.

**Conventions checked (not errors):**
- 211 keys Noble lines by the bare line number (Rel printed 1 for all), consistent with gold.json 217.
- 262 `60213576 OPG` and 266 `4270006645 - SNN` are transcribed as printed. normalize_po_no gives
  `60213576OPG` and `4270006645SNN`. 264's `4270006645 -SNN` normalises to the same string as 266,
  but GARMCO's SI 260 and DN 261 print `60213576` with no `OPG`, so a prediction of `60213576` for
  262 scores WRONG. Whether `OPG` is part of the PO number is a product decision, not a reading
  question, and is left open.
- 261 is ambiguous by the never-guess rule (`PO - 60213576` header vs boxed `P.O : 13085`, where
  13085 is the GARMCO `SUPPLIER REF. #` on 262), so it is held correctly with po_reference null.

**Remaining unscored fields:** 4 document_number, 7 document_number, 40 document_number and lines.
