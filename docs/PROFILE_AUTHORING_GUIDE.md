# Profile Authoring Guide — follow exactly, no guessing

**Who:** anyone assigned profile duty (intern-friendly). No prior AAM knowledge needed.
**What you produce:** one YAML file per brand × document-type (12 total: STS/IRE/Ensign × PO/SI/DN/COMBINED).
**Rule zero:** if anything in a PDF is unclear, write it down as a question. Never invent. Never guess. An empty field with a question beats a filled field you are unsure of.
**Reference example:** `profiles/STS/DN.yaml` (read it after Section 2).

---

## 1. What a profile is (30 seconds)

A profile is a fact sheet about one brand's one document type. The software reads it to understand that brand's PDFs. It contains ONLY:

- names and labels the brand uses (terminology),
- brand habits and traps (quirks, hazards),
- number formats (patterns),
- check rules (validation).

It NEVER contains: positions, coordinates, colors, fonts, "top-right", pixels. If you catch yourself describing *where* something is, stop — describe *what it is called* instead.

---

## 2. Your 12 files (the full scope — check them off)

| # | File | Status |
|---|---|---|
| 1 | `profiles/STS/PO.yaml` | ☐ |
| 2 | `profiles/STS/SI.yaml` | ☐ |
| 3 | `profiles/STS/DN.yaml` | ☐ example exists — read it first |
| 4 | `profiles/STS/COMBINED.yaml` | ☐ |
| 5 | `profiles/IRE/PO.yaml` | ☐ |
| 6 | `profiles/IRE/SI.yaml` | ☐ |
| 7 | `profiles/IRE/DN.yaml` | ☐ |
| 8 | `profiles/IRE/COMBINED.yaml` | ☐ |
| 9 | `profiles/Ensign/PO.yaml` | ☐ |
| 10 | `profiles/Ensign/SI.yaml` | ☐ |
| 11 | `profiles/Ensign/DN.yaml` | ☐ |
| 12 | `profiles/Ensign/COMBINED.yaml` | ☐ |

Work one file at a time. Finish all steps 3–8 for one file before starting the next.

---

## 3. Collect samples (do this first, every file)

1. Open `data/samples/`.
2. Find ALL PDFs for your brand + type. Ask your supervisor if you cannot tell which is which.
3. You need **minimum 5 PDFs**. If fewer than 5 exist, write down the exact count and continue with what exists — do not stop, do not substitute other brands.
4. Your set MUST include (check each, write "N/A + why" if genuinely absent):
   - [ ] a normal single-document PDF,
   - [ ] a multi-page PDF (if any exists for this brand+type),
   - [ ] a bundle/twin PDF — titles like "(2 DNs)", "(3 DNs)" (DN only),
   - [ ] a combined/stamped PDF (COMBINED only — CA "Combined" stamp, 3 sections),
   - [ ] the ugliest scan you can find (crooked, faint, stamped-over).
5. Write the chosen filenames in a list named `samples-<brand>-<type>.txt`, one per line. This list ships with your YAML. Example (`samples-STS-DN.txt`):
```text
SIV-ARS-26-4035 (2 DNs).pdf
GDN-ARS-26-4619.pdf
<... every file you used ...>
```

---

## 4. Fill one worksheet PER PDF (the core task — repeat for every sample)

Copy this block into a text file, one block per PDF, and fill every line. Write "NONE SEEN" instead of leaving blanks.

```text
WORKSHEET — file: <exact filename>
1. Letterhead company name(s) printed on page 1: <copy exactly>
2. Big document title on page 1 (e.g. DELIVERY NOTE): <copy exactly>
3. Document's own number + its label (e.g. label "GDN No", value "GDN-ARS-26-4619"): <label> / <value>
4. PO reference + its label (e.g. label "PO No", value "210851"): <label> / <value>
   (If NO PO reference printed anywhere: write NO-PO-PRINTED.)
5. Line-number column: header text (e.g. "Item No"): <...>. Values look like (e.g. 1,2,3 or 10,20,30 or letters): <...>
6. Description column: header text: <...>. Anything odd inside descriptions (sub-lines, codes in brackets, two-line splits)? <copy an example or NONE>
7. Quantity column: header text: <...>. Values look like (e.g. 50 or 12.5): <...>
8. Price columns present (unit price? total? tax?): <list each with header text or NONE>
9. Extra numbers on a line (part/SKU codes? second references like DN numbers on SI lines?): <copy examples or NONE>
10. Total rows at the bottom (subtotal/VAT/grand total — header words used): <copy or NONE>
11. Footer junk (payment terms, signatures, T&C, amount-in-words): <list or NONE>
12. Page count: <N>. Do later pages repeat headers, continue one long table, or start new documents? <answer>
13. Anything confusing or unusual: <describe or NONE>
```

## 5. Consolidate across your samples (turn worksheets into rules)

Stack all worksheets for this brand+type and apply these rules literally:

- **Rule A (pattern needs 2 witnesses):** a label/format/habit becomes a YAML entry ONLY if seen in ≥2 sample PDFs. Seen once → goes to `hazards:` as "seen once in <filename>, unconfirmed" — never a rule.
- **Rule B (conflict):** two samples disagree (e.g. "Qty" vs "Quantity" headers) → record BOTH variants in the terminology list. Lists may be long; that is correct.
- **Rule C (uncertain):** anything you cannot explain → `hazards:` entry + question for your supervisor. Never promote it to a rule or quirk.
- **Rule D (no positions):** rewrite any location wording into naming wording. "PO number top-right" → FORBIDDEN. "PO number labeled 'PO No' in the header block" → allowed (names the label, not the pixels).

## 6. Write the YAML (field by field — fill in this order)

1. `profile_id`: `"<BRAND>/<TYPE>"` (e.g. `"STS/DN"`). `profile_version: "1.0"`. `status: "draft"`. Never write any other status — activation is your supervisor's job.
2. `company_identifiers`: exact letterhead strings from worksheet line 1 (only ones seen in ≥2 samples).
3. `document_titles`: exact big titles from worksheet line 2 (all variants seen).
4. `terminology`: copy label variants per Rule B — `po_reference_labels` (line 4), `dn_number_labels`/`si_number_labels` (line 3), `line_number_labels` (line 5), `quantity_labels` (line 7), `unit_price_labels` (line 8).
5. `numbering_style`: write the SHAPE of numbers as patterns. Use `\d+` for digits, `[A-Z]+` for capitals, keep separators literal. Example: `GDN-ARS-26-4619` → `"^GDN-[A-Z]+-\\d+-\\d+$"`. If unsure of a pattern, copy 3 real examples into a comment and mark `[ILLUSTRATIVE]` — never fake certainty.
6. `line_references`: list which per-line extras this brand prints (`po_reference`? `dn_reference`? `invoice_reference`? from worksheet line 9). None seen → empty list `[]` plus one-line comment saying so.
7. `quirks`: brand habits from lines 6, 12, 13 that the software must know (see good/bad examples below).
8. `hazards`: total-row shapes (line 10), footer junk (line 11), split descriptions (line 6), every Rule-A single-witness item, every Rule-C question.
9. `numeric_rules`: value shapes from line 7–8 (integers? decimals? how many places?). `max_supported_decimals: 3` is fixed — copy it unchanged.
10. `validation`: one entry per checkable fact: number formats (from step 5), PO-ref presence (unless line 4 ever says NO-PO-PRINTED — then write the exception explicitly), positive quantities.
11. `activation:` block: leave everything `null`/`[]`. Do not touch it.

## 7. Good vs bad entries (memorize these)

GOOD quirk (names what, never where):
```yaml
- id: "LINE-ITEM-N-SUBLINE"
  description: >
    RAAS embeds a "Line Item - N" sub-line inside the description field,
    distinct from the actual line_item_no column. It is NEVER a line item.
```
BAD quirk (positional — forbidden):
```yaml
- id: "PO-LOCATION"
  description: "PO number is at the top right corner."
```

GOOD terminology (all observed variants kept):
```yaml
po_reference_labels: ["PO No", "P.O. No", "PO Reference", "Order No"]
```
BAD terminology (invented canonicalization):
```yaml
po_reference_labels: ["PO No"]   # WRONG if samples also show "Order No"
```

GOOD hazard (single witness, honest):
```yaml
- id: "FAINT-STAMP-COVER"
  description: "Seen once in GDN-ARS-26-4401.pdf: faint stamp overlapping quantities. Unconfirmed."
  severity: "medium"
```
BAD hazard (vague, unactionable):
```yaml
- id: "WEIRD"
  description: "Something odd on one PDF."
```

GOOD pattern (exact, with examples behind it):
```yaml
pattern: "^GDN-[A-Z]+-\\d+-\\d+$"   # e.g. GDN-ARS-26-4619, GDN-DTS-25-505
```
BAD pattern (guessed):
```yaml
pattern: "^.*$"   # matches everything = validates nothing
```

## 8. AI-use rules (you will use AI — under these constraints)

1. You MAY ask AI to summarize a PDF set, propose candidate patterns, or draft YAML from your worksheets.
2. EVERY AI-proposed entry must be verified against ≥2 real PDFs by YOUR eyes before it stays. Unverified AI output goes in the bin, not in the file.
3. NEVER let AI invent examples, pattern variants, or sample IDs. All examples in the file must be copied from real PDFs by you.
4. NEVER ask AI to "fill gaps" in sections you didn't observe. Gaps stay empty with a question note.
5. If AI and your worksheets disagree, worksheets win. Always.

## 9. Brand traps checklist (confirm EACH per brand, write the outcome)

From real AAM history — check every one, even if the answer is "not present":

- [ ] `Line Item - N` sub-line inside descriptions? (Outcome: ...)
- [ ] DN bundles "(2 DNs)"/"(3 DNs)"? (Outcome: ...)
- [ ] Blank part/SKU column? (Outcome: ...)
- [ ] SI lines citing DN numbers? (Outcome: ...)
- [ ] Filename variants (-1/-2 suffixes, "(2 DNs)" in name)? (Outcome: ...)
- [ ] Split descriptions (header line + detail block)? (Outcome: ...)
- [ ] Combined/CA stamps spanning sections? (Outcome, COMBINED files: ...)
- [ ] Step-10 line numbering (10/20/30 vs 1/2/3)? (Outcome: ...)
- [ ] Handwritten corrections on any sample? (Outcome: ...)
- [ ] T&C/payment-terms tails that could confuse page counts? (Outcome: ...)

## 10. Done criteria (your file is finished ONLY when all are true)

- [ ] Samples list file exists with ≥5 PDFs (or exact smaller count + reason).
- [ ] One filled worksheet per sample, no blanks (NONE SEEN where applicable).
- [ ] Every YAML entry traces to ≥2 worksheets (spot-checkable by your supervisor).
- [ ] Zero positional language (search your file for: top, bottom, left, right, corner, pixel, coordinate, x=, y= — zero hits required).
- [ ] Every pattern has ≥2 real examples in comments (or is marked `[ILLUSTRATIVE]`).
- [ ] `activation:` block untouched (`null`/`[]`).
- [ ] Traps checklist (§9) filled with outcomes.
- [ ] Open questions listed at the top of your submission message, each naming the exact file + line.

## 11. Worked mini-example (STS/DN, first PDF → YAML)

Worksheet excerpt (`SIV-ARS-26-4035 (2 DNs).pdf`):
```text
1. Letterhead: IBRAHIM ALI ALSHAB TRADING EST.
2. Title: DELIVERY NOTE (filename says "(2 DNs)")
3. Own number: label "GDN No" / value "GDN-ARS-26-4619"
4. PO ref: label "PO No" / value "210851"
13. Confusing: title says NOTE singular but file holds 2 DNs.
```
Derived YAML (after confirming the same shapes in a 2nd PDF):
```yaml
company_identifiers: ["IBRAHIM ALI ALSHAB TRADING EST."]
document_titles: ["DELIVERY NOTE"]
terminology:
  dn_number_labels: ["DN No", "Delivery Note No", "GDN No"]
  po_reference_labels: ["PO No"]
quirks:
  - id: "DN-BUNDLE-SUFFIX"
    description: >
      Multi-DN PDFs titled "(2 DNs)"/"(3 DNs)" are still DN, never
      COMBINED. First po_reference rules; ALL pages' lines included.
hazards: []            # this PDF added none; others might
validation:
  - id: "DN-NUMBER-FORMAT"
    rule: "document_number must match ^GDN-[A-Z]+-\\d+-\\d+$"
    on_fail: "review"
```
Note what did NOT happen: no positions recorded, no invented labels, the "(2 DNs)" oddity became a quirk (observed twice before promotion), and the full file is `profiles/STS/DN.yaml` — your reference for shape and tone.

---

*Submit per file: the YAML + samples list + worksheets + open questions. Your supervisor activates profiles; you never self-activate. When in doubt, ask — a blocked question is cheaper than a wrong profile.*
