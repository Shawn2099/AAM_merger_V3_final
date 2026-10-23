# Extraction Prompt Audit — GPT-6 Luna vs Real Vendor PDFs

**Scope:** `src/app/services/extraction.py` (`_SYSTEM_PROMPT`, `_PAGE_PROMPT`, `_VLMPageExtraction`) vs 284 real vendor PDFs actually ingested (282 extracted, 2 corrupt). No code changed. Numbers below come from `data/aam_merger.db` and the stored PDFs.

## 1. Current prompt — what it does

- One native-PDF call per document via `instructor` + OpenRouter (`openai/gpt-6-luna`), `response_model=_VLMPageExtraction`. No rasterization.
- System role = parser identity + schema. User role = STEP 1–6 instructions + few-shots + the PDF (instructions are placed **before** the file).
- Strict JSON schema; fields mostly nullable (`None` defaults) except `document_type: Literal[...] = Field(...)` which is **required**.
- Quantity is `str` "NUMBER-ONLY as printed"; UOM stripped; no scaling in the model.
- `line_item_no` resolution order: (a) embedded `Line Item - N` marker, else (b) printed side column; copied verbatim.
- Combined branch: page ranges + `page_count`, no line items.
- `po_reference_ambiguous` flag when more than one distinct PO number is visible.

## 2. What it gets right (measured)

- 282 / 284 documents extracted successfully; only 2 are corrupt all-zero files, not VLM failures.
- 616 line items stored; **1** with a zero quantity and **1** with a null line number. The "keep, don't drop" rule is working.
- 0 empty descriptions.
- `page_count` matches pypdf on every document with a declared count.
- Multi-page documents are handled in one call (e.g. a 13-page RAK PO produced 6 pages of items + SKIP pages 7–13).
- Embedded `Line Item - N` markers are captured into the description in 104 of 114 marker-bearing lines; the marker is used as the line number for RAK and RAAS.

## 3. Issues found vs the real files

### 3.1 False "ambiguous" on replacement notes — 5 documents
Rule: "if MORE THAN ONE distinct PO number is visible … set ambiguous." Real case: `SIV-DTS-25-456-pages-6.pdf` prints `4500039614 PO #` as the PO, then in the footer `REPLACEMENT OF DELETED PR#10022850 /PO# 4500026535`. The second PO number is historical, not a second active PO. The same pattern occurs in 4 RAAS DNs where the only visible PO is the primary one. These documents are quarantined for no reason.

### 3.2 Non-item pages classified as PO — 14 documents
Pages that carry a PO header but no item table (terms, certification, signature, "Material Certification" pages such as `SIV-DTS-25-589-5.pdf`) are classified `PO` with 0 line items. SKIP is defined only as "covers/T&C/blank pages", so a certification/attachment page does not qualify. Result: a PO set ends up with 2+ PO documents → `multiple_po_documents` quarantine (5 sets).

### 3.3 Line-number mismatch across documents — 11 quarantines (`unmatched_vendor_line`)
The prompt extracts the number exactly as printed, which is correct, but vendors print incompatible schemes:
- RAK PO: step-10 side column (`10, 20, 30`); the DN/SI carry `Line Item - 1` markers. PO line `10` never matches DN line `1`.
- Arabian Drilling: step-10 (`30, 40, …, 180`).
- ADES / SANAD / Schlumberger: non-sequential starts (`6, 7, …`, `5, 6, …`).

The prompt already prefers the embedded marker, but only when it exists. Where the PO has no marker, the side column is used and the set cannot reconcile. This is a product-level cost of the "line_item_no is the only key" rule, but the prompt can at least flag it.

### 3.4 European decimal commas in `unit_price` — ~12 distinct values
Examples in raw JSON: `"1,70"`, `"0,33"`, `"1.650,00"`. The prompt says "raw as printed" and the sanitizer parses with `en_IN` locale, so a comma decimal is read as a thousands separator (or rejected). Prices are not compared, but merged packets carry the wrong value.

### 3.5 Vendor name not normalised — 30+ variants
`"RAK ENERGY SERVICES & SUPPLIES WLL"` appears in 8 different spellings; `"IBRAHIM ALI …"` in 6. `vendor_name` is not used downstream, so this is data-quality noise rather than a merge defect. The prompt still pays tokens to extract it.

### 3.6 Required `document_type` enum
`document_type: Literal[...] = Field(...)` is required. An unreadable page must emit one of six values. Per the research (§2.2), a required enum is exactly the shape that turns a refusal into a confident guess. Two corrupt files are the only observed failures, but the risk exists for any borderline page.

### 3.7 Instructions precede the file
Both `_SYSTEM_PROMPT` and `_PAGE_PROMPT` (~4,000 tokens) are placed before the PDF. Research (TACL 2024, NEEDLEINATABLE) measures a U-shaped attention curve; for a 13-page table the middle rows degrade. Query-aware contextualization (restating the critical rules after the file) is reported to take KV retrieval from ~45% to near-perfect at no cost.

### 3.8 "Scan ALL pages sequentially" on long documents
One call covers every page. For the 13-page RAK PO this worked, but it is the regime where lost-in-the-middle is measured. Page-grouping is the lower-risk option for documents above ~6 pages.

### 3.9 Corrupt inputs are not pre-validated
The 2 failed files are all-zero byte streams. They reach the VLM, fail 3 times, and burn the attempt cap. A magic-byte check before `_call_vlm` would reject them instantly as `UNKNOWN` without a VLM call.

## 4. Best strategies for GPT-6 Luna (ranked, evidence-tagged)

Tier 1 — cheap, no product decision:
1. **Append a compressed restatement of the critical rules after the PDF** (query-aware contextualization). Zero cost, large reported effect.
2. **Expand the SKIP definition** to any page without an item table (terms, certification, signature, footer) — stops spurious PO documents.
3. **Refine the ambiguous rule** to the *primary* PO number; ignore historical/replacement PO references.
4. **Pre-validate PDF magic bytes** before the VLM call.
5. **Make `document_type` nullable** so an unreadable page declines instead of guessing.
6. **Source-quote per line item** (verbatim raw row) into `raw_extraction_json` for reviewer verification.

Tier 2 — needs the real PDFs (we now have them):
7. **Page-group extraction** for documents over ~6 pages instead of "scan ALL pages".
8. **Header-anchored row extraction** — extract the table as `column: value` row units with headers repeated, then map to fields. Targets the STEP 3 compromise.
9. **Per-vendor `line_item_no` guidance** — RAK/Arabian step-10, RAK DN markers, Rho `Line Item - N`. Document these in the prompt rather than re-deriving each run.
10. **European decimal handling** for `unit_price` (detect `,` as decimal when 2 trailing digits).

Tier 3 — needs a product decision (AGENTS.md §7.3):
11. **Constrained + unconstrained dual call**, flag disagreements (constrained has a higher confidently-wrong rate).
12. **Cross-model extraction** only after measuring error decorrelation (a weak second model is worse than none).
13. **Automated prompt optimisation (GEPA/DSPy)** — needs ~40 labelled examples; we now have 282 real extractions to label from.

## 5. What must not change (product constraints)

- Quantities are the only reconciliation signal; prices/SKUs/UOM are not compared (`test_price_is_never_compared`).
- `line_item_no` is the sole matching key; description is only a fallback for rows with no number.
- No filename-based matching (removed this session).
- No new DB columns without a migration.
- `max_concurrent_extraction_tasks` stays at 3.

## 6. Metrics to start tracking

- `reconciling_while_wrong = (sets that merged) / (sets with ≥1 wrong line)` — the real defect rate.
- `silent_drift = (lines whose quantity differs from ground truth but still reconciles) / (all lines)`.
- `ambiguous_false_positive_rate` — how many of the 5 ambiguous docs are actually single-PO.

Both require a labelled set; the 282 extracted documents are the raw material for it.
