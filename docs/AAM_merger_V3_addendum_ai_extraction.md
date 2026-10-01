# AAM_merger_V3 — AI Extraction & Numeric Handling Addendum

**Status:** Design Addendum, Revision 3 (2026-09-06) — draft for review, not yet committed.
**Revises:** Rev.2 (adds: accuracy-over-cost directives — full-PDF classification always; no custom parser code; doc-level quarantine staging for SKIP; cheap-model option removed).
**Human directives (2026-09-06, binding):** accuracy/confidence is never traded for cost; no custom content-parsing code (no OCR, no regex parsers, no local text extraction — all document reading via VLM only, nothing built now that must be rewritten later); skipped junk goes to human review staging.
**Purpose:** Records architectural decisions made after the original V3 Business Logic and Technical Specification were finalized. Refines and supersedes only identification, AI extraction, extraction-validation, and numeric-representation behavior. The original V3 business logic and SPEC remain authoritative everywhere else.

**Research basis (verified 2026-09-06):** two-stage classify→extract is industry-standard production practice, but misclassification is a documented single point of failure requiring a fallback path; verbalized LLM confidence is systematically overconfident (80–100% clustering; ECE 0.06–0.13 across frontier models) and shall therefore be advisory only; prompt-cache reuse is prefix-based and byte-exact (1024-token floor, 5–10 min TTL, break-even after 1–2 reads) which dictates prompt layout; template/positional extraction rules degrade 12–28 F1 points on unseen layouts versus 2–6 for schema-guided LLM extraction.

---

# 1. Design Goals

1. Prevent the AI model from owning document-format assumptions representable deterministically in the application.
2. Separate document identification from document extraction.
3. Use a local, deterministic profile per known company/document-format family.
4. Allow new formats via profile addition, never pipeline redesign.
5. Keep AI to observation and extraction; normalization, arithmetic, scaling, validation, matching, reconciliation, and business decisions stay in deterministic code.
6. Minimize repeated PDF processing and API/token cost — measured, not assumed.
7. Route uncertain, unsupported, invalid, or suspicious results to human review; the AI never guesses.
8. Preserve the existing deterministic PO/DN/SI quantity reconciliation model.
9. Keep the AI provider replaceable without touching the business/reconciliation layer.
10. Make every architecture claim empirically verifiable (telemetry, held-out samples, calibration records).

---

# 2. Revised AI Architecture — Two Stages

## 2.1 Stage 1 — Document Classification

The PDF (always the full document, never a subset) is provided to GPT-5.6 Luna with a dedicated classification prompt. The response shall conform exactly to:

```python
class ClassificationResult(BaseModel):
    company: str | None
    # canonical format family, e.g. "STS"; null when unidentifiable.
    # Open string in-prompt (never a closed enum — no force-fitting).

    document_type: Literal["PO", "SI", "DN", "COMBINED", "UNKNOWN"]
    # UNKNOWN covers covers / T&C / blanks / unreadable / random PDFs
    # (today's SKIP folds into UNKNOWN).

    evidence: str
    # verbatim marker snippet, 1-200 chars, required. Observational only.
```

Example:

```json
{
  "company": "STS",
  "document_type": "DN",
  "evidence": "IBRAHIM ALI ALSHAB TRADING EST. letterhead + DELIVERY NOTE header"
}
```

App rules: `company` is uppercased/stripped and matched against the profile registry; empty `evidence` is treated as `UNKNOWN` (an unverifiable claim is no claim). No confidence field. No numbers. No layout descriptions.

The classification response shall NOT contain: line items, quantities, prices, PO/DN/SI numbers, field locations, inferred schemas, parser configuration, profile contents, normalization, arithmetic, or ×1000 conversions.

The classification stage answers only which format family and which document type. It is not extraction.

## 2.2 Classification outcome table (exhaustive)

| # | company | document_type | System action |
|---|---|---|---|
| C1 | known | PO/SI/DN/COMBINED | Profile lookup (§4/§5) |
| C2 | known | UNKNOWN | No Stage 2. Doc-level quarantine staging (§13a) |
| C3 | `null`/unknown | PO/SI/DN/COMBINED | Generic extraction prompt (§6), flagged higher-risk |
| C4 | `null` | UNKNOWN | No Stage 2. Doc-level quarantine staging (§13a) |
| C5 | any | COMBINED | COMBINED profile if one exists, else generic-COMBINED prompt; single extraction op (§25) |

Cases C2/C4 perform no second AI call. Case C3 always proceeds but under the generic confidence policy (§14).

---

# 3. Meaning of `company`

`company` is the canonical company/document-format identifier used to select exactly one local extraction profile. It does NOT necessarily mean buyer, vendor, supplier, issuer, or legal entity. Example: `{company: STS, document_type: DN}` → `profiles/STS/DN.yaml`.

The identifier shall be chosen from actual PDF samples during profile building. It must be specific and stable enough to select the correct profile. Where a document carries multiple company names, the decision shall rest on the identity that best identifies the format family (letterhead + document-title combination), not the first name encountered. The observed `evidence` string (§2.1) records which markers decided it.

---

# 4. Classification Result Validation

The application shall never trust classification blindly. After Stage 1 it validates `company + document_type` against the profile registry (`profiles/<company>/<document_type>.yaml`; exact directory layout is implementation detail, the mapping is mandatory: classification → zero or one applicable profile, never an invented one).

---

# 5. Known Profile Path

On a registry hit: load profile → build the specialized extraction prompt from it. Profiles may contain ONLY: expected terminology and field-name variants; known company quirks; known extraction hazards; field-specific interpretation rules; numeric-format rules; validation rules (identifier formats, expected presence, precision limits); `line_references:` (which per-line references this brand emits, if any); global-vs-per-line numbering style.

Profiles shall NEVER contain: page coordinates, positional field rules, crop regions, or template geometry. Rationale (research-verified): positional rules break silently when vendors restyle documents, while schema-guided extraction degrades gracefully. The AI never owns this knowledge; the profile is deterministic application configuration.

---

# 6. Missing Profile Path

A miss shall never cause guessing or invented company rules. The application uses a **generic extraction prompt** (standard AAM extraction, no company-specific knowledge). The generic path is a fallback mechanism, not permission to invent. It is therefore higher-risk (§14).

---

# 7. Stage 2 — Structured Extraction

Uses GPT-5.6 Luna, native PDF input, structured output, Pydantic schema validation, profile-generated prompt (hit) or generic prompt (miss). The response shall conform exactly to:

```python
class LineEvidence(BaseModel):
    source_text: str   # verbatim snippet, MAX 150 chars (model truncates, app enforces)
    page: int          # 1-based, required; validated 1 <= page <= N (N = page count)

class ExtractionLineItem(BaseModel):
    line_item_no: str | None = None
    description: str                       # required, complete, never truncated
    quantity: str                          # decimal string, ^\d+(\.\d+)?$, ANY precision
    unit_price: str                        # decimal string, same pattern
    total_price: str | None = None         # audit only, never reconciled
    item_code: str | None = None           # audit only, never matched
    po_reference: str | None = None        # audit only, brand-dependent
    dn_reference: str | None = None        # audit only, brand-dependent
    invoice_reference: str | None = None   # audit only, brand-dependent
    evidence: LineEvidence                 # required

class ExtractionResult(BaseModel):
    document_type: Literal["PO", "SI", "DN", "COMBINED", "UNKNOWN"]
    has_po_section: bool = False
    has_dn_section: bool = False
    has_si_section: bool = False
    document_number: str | None = None     # own number (SI No / DN No / PO No)
    po_reference: str | None = None        # PO ref on SI/DN/COMBINED; null on PO
    vendor_name: str | None = None         # audit + cross-check vs classification
    grand_total: str | None = None         # decimal string; soft cross-check only
    line_items: list[ExtractionLineItem] = []
```

Excluded everywhere: `confidence`, `uom`, `part_no`. Deliberate precision rule: `quantity`/`unit_price` accept ANY precision at schema level — `3.0001` must pass schema so Layer 2 can route it to review; a schema rejection would burn Prefect retries on a non-technical failure.

Document-level: document type; `po_no`; `dn_no`; `si_no`; invoice number; `grand_total` (decimal string | null, audit only — feeds the soft line-total cross-check, never a hard gate).
Line-item-level: `line_item_no`; description; quantity; unit price; `total_price`, `item_code` (audit only); brand-dependent per-line references (audit only — see below); evidence (source snippet ≤150 chars + 1-based page, required).
`part_no` excluded. UOM excluded (§21).

Brand-dependent per-line references (all optional strings, never matching keys unless a future design change explicitly promotes one): `po_reference`, `dn_reference`, `invoice_reference`. Some brands print only a global document-level number; others print per-line references (e.g. each SI line cites its DN, occasionally its PO/invoice). Each company profile records which references its brand emits (`line_references:`), and Layer-3 validation checks presence/format where declared.

Telemetry (tokens, latency, model) comes from API response metadata, never from model output — the extraction schema carries no telemetry fields.

---

# 8. AI Responsibility Boundary (mandatory)

AI: read the PDF; identify format family + document type (+ evidence); extract values; return structured fields.
Application: profile selection; prompt construction; schema validation; extraction validation; confidence/review decision; Decimal parsing; normalization; precision enforcement; ×1000; integer storage; matching; aggregation; reconciliation; status; quarantine; merge eligibility; merging; audit.
The AI never makes the final business decision.

---

# 9. PDF Context Reuse and Cost (measured, with required prompt layout)

Two stages double the API calls per document versus today. Reuse is therefore structural. Because accuracy is never traded for cost (human directive), input is NEVER reduced to save money — classification always receives the full PDF, never a first-page subset. Cost control comes exclusively from: (a) prefix-cache reuse below, (b) SKIP short-circuit (no Stage 2 for junk), (c) classification reuse on redo (§28), (d) no retry burn on semantic failures (§27).

1. **Prompt layout (mandatory):** `[identical static prefix] + [PDF block] + [stage-specific instructions last]`. Rationale: caches are prefix-based and byte-exact — Stage 2 hits Stage 1's cached PDF parse ONLY if everything before the PDF is identical across both calls. Stage-specific prompts go last, never first. Minimum cacheable size (1024 tokens) is trivially met by PDF blocks.
2. **Routing:** OpenRouter `session_id` stickiness keeps both stages on the same provider backend (caches are per-backend); stickiness alone does not cache anything.
3. **Traffic fit:** the sync loop processes a document's two stages seconds apart — inside the 5–10 min TTL — so same-document reuse is expected; cross-document reuse is not (different PDFs). Break-even is 1–2 reads per write.
4. **Verification (mandatory):** record per attempt `input_tokens, cached_tokens, cache_writes, output_tokens, latency, provider/model`. Track the **cache hit rate** (`cached / (cached + full-rate input)`), not just spend. Non-zero cache-creation on every Stage 2 means the prefix is drifting — fix the layout.
5. **Pilot gate:** before building the first profile, run classification + generic-only extraction over a labeled sample set with telemetry on. The measured cost delta vs single-call is the go/no-go for profile investment.
6. The business logic shall never depend on a theoretical discount.

---

# 10. Provider Abstraction

Luna via OpenRouter is the default. The AI layer exposes `DocumentClassifier` / `DocumentExtractor` (or equivalent) and the rest of AAM consumes internal schemas, never raw provider objects. Grouping, matching, reconciliation, normalization, state machine, and merge logic shall not reference provider concepts. A future provider (e.g. Google Document AI) must satisfy the same internal contract. Default stays Luna unless a benchmark on actual AAM documents proves otherwise.

---

# 11. Extraction Confidence and Human Review (advisory-only confidence)

V3 treated extraction as binary schema-valid/invalid with no confidence (SPEC FR-6.6, hereby superseded as §33 states). That is now insufficient: structurally valid is not trustworthy.

But the only confidence signal the VLM path can produce is **model self-report** (`high/medium/low`), which research shows is systematically overconfident (verbalized confidence clusters 80–100%; ECE 0.06–0.13 even in frontier models; RLHF degrades calibration). Therefore:

- Self-reported confidence is **one advisory input** for review prioritization — never a bypass. No extraction, whatever its reported confidence, skips schema validation, value/profile validation, or cross-document reconciliation.
- Deterministic gates remain the authoritative safety net.
- During profile activation (§30), record self-report vs outcome to build a **calibration record** per profile; re-check it when prompts, profiles, or models change.

---

# 12. Validation Layers (all must pass for automatic processing)

## Layer 1 — Schema validation
Pydantic conformance. Failure = technical failure → Prefect retry (§27).

## Layer 2 — Value validation
Parseable decimals; sane identifier structure; no zero/negative where V3 forbids; precision within supported limits (§20); required line fields present; no duplicate/impossible lines.

## Layer 3 — Profile validation
Profile-specific rules on profiled extractions. **Escape hatch (mandatory):** if a profile-selected extraction fails Layer 3, retry **once** with the generic prompt — a wrong-family classification plus a specialized prompt is a documented single point of failure, and generic retry is the recovery. Log the mismatch (expected vs observed evidence) for profile tuning. Generic-path results skip this layer.

## Layer 4 — Cross-document validation
Existing V3 matching/reconciliation unchanged (`PO == AggDN AND PO == AggSI`, exact ints). This is the downstream safety net for extraction errors that survive Layers 1–3.

---

# 13a. SKIP / Unsuitable-Document Handling (doc-level quarantine staging)

A classification of `UNKNOWN` (covers, T&C, blanks, unreadable, random PDFs) skips extraction entirely and stages the document for human review WITHOUT inventing a PO Set:

```text
SKIP/UNKNOWN → extraction_status = failed, reason = skipped_unsuitable
  → stored copy retained (never deleted) + copy staged under
    quarantine_folder/unclassified-<sha256[:8]>/
  → DB row kept, po_set_id stays NULL, visible for operator delete/review
```

Rationale: set-quarantine is per-PO-Set and a junk PDF has no PO — but the human-review outcome is preserved via doc-level staging, not the unclassified holding flow. No VLM call beyond Stage 1 is ever spent on these documents.

# 13b. No Custom Parser Code (binding)

No OCR code, no regex field parsers, no local PDF text extraction — ever. All document *content* reading is VLM-only; nothing is built now that must be rewritten later. The sole deterministic PDF touch permitted is page-count metadata (for §24 multi-page completeness checks), which is file metadata, not content parsing. Profiles are YAML configuration, not code; validation rules compare VLM-returned values only.

# 13. Manual Review Rule

Any result that is low-confidence, structurally inconsistent, numerically invalid, unsupported by its profile, suspicious under validation rules, inconsistent across documents, or otherwise unreliable shall NOT auto-proceed. It routes to the existing quarantine/human-review workflow. Uncertain → review, never guess. The AI never invents a value to resolve uncertainty.

---

# 14. Generic-Profile Confidence Policy

Generic-path extractions are higher-risk by construction. Any generic result that is low-confidence or fails deterministic validation goes to manual review. Thresholds are calibrated empirically on real AAM documents during implementation/testing — never arbitrarily selected — and recorded per §30.

---

# 15–19. Numerics (unchanged from draft Rev.1 — ratified)

AI returns source decimal strings (`"3.00"`, never `3000`, never scaled); application parses with `Decimal` (never via `float`); `"3" = "3.0" = "3.00" = "3.000" → 3000`, `"0.5" = "0.50" → 500`; canonical store is int ×1000 performed ONLY by application code. This ratifies current `_parse_scaled_int` behavior.

---

# 20. Precision Limit (mandatory, closes a live bug)

Maximum three decimals. `3.0001` shall NOT be silently rounded/truncated (current code rounds via `to_integral_value` — that behavior is hereby removed for >3dp inputs). Over-precision is a numeric normalization exception → manual review. Implementation: reject `Decimal.as_tuple().exponent < -3` before scaling. Future precision needs are explicit design changes, never runtime behavior. Note: sustained 4dp+ volume (e.g. VAT-heavy unit prices) goes to review by design — monitor quarantine volume after activation.

---

# 21. No UOM Processing (unchanged)

UOM is not extracted as a required field and never participates in reconciliation (`3 BOX = 3.00 EACH = 3 PCS` numerically). Preserves the original V3 decision.

---

# 22. Raw vs Canonical Quantity + DB Mapping (concrete, final)

Preserve both: `quantity_raw`/`unit_price_raw` (nullable TEXT on `line_items`, verbatim AI strings) alongside existing scaled-int columns. Historical rows keep `raw_extraction_json` as the raw record; new rows populate both. The application never overwrites raw with scaled values. (Deferred consideration, explicitly rejected for now: OCR-text + PDF dual input, which research shows beats image-only extraction — reserved as the evidence-backed next lever IF profile-driven extraction disappoints. Not in scope.)

Exact field-to-storage mapping:

| Response field | Storage |
|---|---|
| `document_type` | `documents.doc_type` |
| `po_reference` (doc) | `documents.po_no_raw` → `po_no_normalized` |
| `document_number` | `si_no` + `invoice_no` (SI) · `dn_no` (DN) · `invoice_no` (COMBINED) |
| `line_item_no`, `description` | `line_items` columns |
| `quantity`, `unit_price` verbatim | **new** `line_items.quantity_raw`, `unit_price_raw` TEXT |
| scaled ints | existing ×1000 columns (app-computed; `exponent < -3` → review, never rounded) |
| `vendor_name`, `item_code`, `total_price`, `grand_total`, per-line trio, all evidences, section flags | `documents.raw_extraction_json` (no dedicated columns) |
| classification + evidence + profile id/version + generic flag + per-layer outcome + usage (`input_tokens`, `cached_tokens`, `output_tokens`, `latency_ms`) | new per-attempt audit record |
| SKIP docs | `extraction_status=failed`, reason `skipped_unsuitable`, `po_set_id` NULL, stored copy kept + staged under `quarantine_folder/unclassified-<sha[:8]>/` |

---

# 23. Responsibility for Numeric Transformation (mandatory)

`PDF → AI extracts "3.00" → schema validation → Decimal("3.00") → precision validation → ×1000 → 3000 → database`. Never `AI extracts "3000" → database`.

---

# 24. Revised End-to-End AI Flow

```text
Input PDF → stability → SHA-256 dedup → permanent copy → AI-eligible?
  NO → manual-upload path (§26)
  YES → Stage 1 classification {company, document_type, evidence}
    → UNKNOWN/null → doc-level quarantine staging, no Stage 2 (§13a)
    → profile lookup → hit: specialized prompt / miss: generic prompt
    → Stage 2 extraction (shared-prefix layout per §9)
    → schema validation → value/profile validation (+generic-retry hatch)
    → confidence/review decision (advisory confidence + deterministic gates)
    → unacceptable → manual review
    → acceptable → Decimal normalization → precision validation → ×1000
    → canonical int + raw strings → DB → grouping → matching →
      aggregation → reconciliation → existing status/merge logic
```

---

# 25. Combined Documents

COMBINED stays a single extraction op (never split into three uploads). Classifier note: concatenated multi-section PDFs are a known classifier-confusion case in production systems — therefore a COMBINED classification additionally requires the existing whole-understanding gate (all three section flags, FR-6.7), and any COMBINED result failing it follows the semantic-failure path (§27), not retry burn.

---

# 26. Manual-Upload Documents (unchanged)

`CUSTOMS`/`SHIPPING`/`COMMERCIAL_INVOICE` are never sent to the VLM. Two-stage AI applies to AI-eligible processing only.

---

# 27. Retry Behavior (mechanics specified)

Prefect owns retries; no custom retry loops. Split is mechanical:
- **Technical** (API/network/provider failure, schema-invalid output): raise → Prefect retries per policy.
- **Semantic** (well-formed response failing Layers 2–3, e.g. COMBINED gate): return `failed` with a `validation_outcome` record, do NOT raise — zero retry burn. (Today this burns 3 attempts; that ends here.)
`redo_extract`'s per-doc error capture already supports both shapes.

---

# 28. Redo/Re-extract vs Redo matching

Redo matching: unchanged, no AI. Redo/re-extract re-runs classification → profile → extraction → validation → normalization. Classification result (company, type, evidence, profile id/version) is persisted per document and redo **SHOULD** reuse it, skipping Stage 1 — unless the operator invalidates it or the profile version changed, in which case Stage 1 re-runs and the audit trail records both attempts.

---

# 29. Audit Requirements

Per extraction attempt retain: document ID; provider; model; classification result + evidence; selected profile + version (or generic flag + reason); attempt number; validation outcome per layer; manual-review reason; raw + canonical numerics; API usage metadata (tokens/cached/latency from response metadata). Must answer: what did AI extract, which profile, what deterministic transforms ran, why did it proceed or route to review.

---

# 30. Profile Versioning + Activation Gate

Profiles are versioned config (`STS/DN v1.0`); extracted records reference the producing version. **Activation gate (mandatory):** no profile goes live until it passes a held-out set of ≥5 representative documents of that company×type (disjoint from building samples): correct classification, schema-valid extraction, reconciliation agreement with human-verified values, plus a calibration record (self-report vs outcome). Record activation (profile, version, date, sample IDs). Deactivation returns the family to generic.

---

# 31. Provider Independence (unchanged §10 intent)

Grouping, matching, reconciliation, normalization, state machine, merge logic stay provider-free. Future providers satisfy the internal contract. Default Luna unless benchmarked otherwise on AAM documents.

---

# 32. Validation Philosophy (unchanged)

Never make the AI perfect; make uncertainty unable to silently merge: extraction → structured validation → deterministic validation → reconciliation → merge only when all gates pass. Consistent with vendor guidance (Microsoft/Google route low-confidence predictions to human review).

---

# 33. What This Addendum Changes From Original V3

1. One VLM stage → two (`classification → profile selection → extraction`).
2. Classification is minimal (`company, document_type, evidence`); UNKNOWN/null is a first-class outcome (§2.2 table).
3. Company/format knowledge moves to local profiles — terminology/variants/quirks/hazards/validation ONLY, never coordinates or positional rules.
4. Binary valid/invalid → schema validity + deterministic validation + advisory confidence + cross-document validation.
5. AI never returns scaled/internal numerics (ratifies current verbatim behavior).
6. Numeric correctness = AI observation + Decimal parsing + precision validation + deterministic scaling; >3dp routes to review (removes silent rounding).
7. UOM stays excluded.
8. Semantic failures fail fast to review (no retry burn); technical failures keep Prefect retries.
9. Accuracy is never traded for cost: full-PDF classification always; cost levers are cache reuse, SKIP short-circuit, redo-reuse, and no-retry-burn only.
10. No custom content-parsing code of any kind (13b); skipped junk is staged at doc level for human review (13a).
9. SPEC FR-6.6 (no confidence) superseded within the bounds of §11 (advisory-only).

---

# 34. Non-Goals (unchanged + one addition)

No OCR outside the provider; no local PDF text extraction (dual OCR+PDF input explicitly reserved as a future evidence-backed lever, not current scope); no UOM reconciliation; no AI arithmetic/reconciliation/merge decisions; no silent rounding/truncation; no invented profiles; no third verification LLM call; no provider switch; no new scheduler/orchestration/vector DB/agent framework.

---

# 35. Implementation Details Intentionally Deferred

YAML schema; registry implementation; Pydantic names; exact confidence thresholds; manual-review UI; OpenRouter annotation/session mechanics; raw-column DDL (proposed: §22); versioning mechanism; abstraction class names; prompt wording; classification schema internals; telemetry format. Selectable during implementation iff consistent with this addendum.

---

# 36. Acceptance Criteria

- Classification: known STS/IRE/Ensign docs identify family + type; response has no extraction data; UNKNOWN/null → unclassified, never an invented profile; evidence snippet present.
- Profiles: correct selection on hits; generic on misses (flagged); ≥5-doc held-out activation gate each, with calibration record.
- Numerics: `3/3.0/3.00/3.000 → 3000`; `0.5/0.50/0.500 → 500`; `3.0001 → review` (no rounding); AI never computes ×1000.
- References: per-line references captured where the brand emits them (profile-declared); global numbers captured at document level; `grand_total` present where printed, feeding the soft cross-check only.
- Reconciliation: PO `3` reconciles DN `3.00` + SI `3.000`.
- Safety: suspicious-but-valid never auto-merges; generic-retry hatch fires on profile mismatch (logged).
- Cost: telemetry shows same-document Stage-2 cache reuse (hit rate tracked); pilot delta measured before profile spend.

---

# 37. Final Principle (unchanged)

> **The AI observes and extracts. The application interprets, validates, normalizes, reconciles, and decides.**
> AI: "What does this document say?" Local profile: "How is this family normally structured?" Application: "Is it valid? How is it represented? Do PO/DN/SI reconcile? Is merging permitted?" Human: final review.
