# Extraction Layer — Research Findings & Candidate Improvements

**Date:** 2026-09-30
**Scope:** The VLM extraction layer (`src/app/services/extraction.py` + `splitting.py` + `sanitizer.py`), evaluated against current (2026) VLM-extraction literature.
**Status:** **Research input. Not a product amendment.** `AAM_merger_V3_PRODUCT.md` remains the contract. Nothing here has been implemented.

---

## 0. How to read this document

Three kinds of statement are tagged throughout. Do not conflate them:

| Tag | Meaning |
|---|---|
| **`[VERIFIED]`** | I read the code in this repo and confirmed it. |
| **`[RESEARCH]`** | External published result, with source. Reproduced second-hand — treat as a lead, not gospel. |
| **`[PROPOSED]`** | My recommendation. Unimplemented, untested, no evidence attached. |

`[VERIFIED]` findings are load-bearing. `[RESEARCH]` findings are dated and may be superseded or mis-transcribed. `[PROPOSED]` items are hypotheses to be tested, and several will need a product decision per `AGENTS.md` §7.3 before they can be built.

---

## 1. Baseline state — what we actually know

### 1.1 There is no measured extraction accuracy in this repo `[VERIFIED]`

| Artifact | State |
|---|---|
| `data/samples/` | `.gitkeep` only (0 bytes). No vendor PDFs have ever been committed. |
| `data/aam_merger.db` | 22 `documents` rows, **0** with non-NULL `raw_extraction_json`. 21 `line_items` rows. |
| `data/raw_extractions/` | Does not exist. (Gitignored; the dump only runs if the dir pre-exists or `AAM_SAVE_RAW=1`.) |
| Live API calls in tests | **Zero.** Every test stubs the `_call_vlm` boundary. |
| `data/logs/aam_merger.log` | 7.3 MB, entirely test-suite output. 30,110 of 31k OpenRouter mentions are `OPENROUTER_API_KEY not set` during tests. |

`REVIEW.md:245` still records *"Still open: real-sample VLM validation (needs API balance)."* `docs/superpowers/plans/2026-09-05-reliability-fixes.md:351` records the same item, deferred, never done.

**Consequence:** every accuracy claim about this system — mine included — is currently unfalsifiable.

### 1.2 The vendor knowledge in tests is hand-transcribed `[VERIFIED]`

`tests/test_real_samples.py` contains 13 fixtures with specific vendor line items (STS, IRE, Ensign, ADES, Bridon, Rho, RAK). These were transcribed from a research file (`specific_questions_answers.md`) that **is not in this repository**. They were never observed from a real extraction.

So the one test file that looks like vendor validation is actually a transcription of someone else's notes.

---

## 2. Findings that change the current design

These are the results that a reviewer should read first, because two of them invalidate design choices currently in the code.

### 2.1 ❌ Self-consistency does not work. Cross-*model* agreement does. `[RESEARCH]`

Two independent 2026 sources converge:

> *"Even sampling-based self-consistency is blind to systematic errors, where the model returns the same wrong value on every sample... **many VLM extraction errors are systematic**, so high agreement reflects determinism, not correctness."*
> — arXiv 2609.20110

> *"Self-consistency is not stronger than the single-call VLM baseline despite costing Kq calls... AUROC 0.540 on Gemini, 0.57 on Groq — essentially chance on both a strong and a weak extractor. When the model misreads a field it misreads it consistently across resamples."*
> — `ap-verify` repo, measured on 511 field rows / 143 real DocILE invoices

**AUROC — can we tell a correct extraction from a wrong one?**

| Signal | AUROC | Source |
|---|---|---|
| VLM verbalized confidence | 0.54–0.74 (*"at times worse than chance"*) | arXiv 2609.20110 |
| Self-consistency, 5 samples, same model | 0.54 | arXiv 2609.20110 / ap-verify |
| **Cross-model agreement + layout + validation** | **0.90–0.99** | arXiv 2609.20110 |

**Impact on this repo `[VERIFIED]`:** `extraction.py:429-450` retries the **same model** up to 3 times. That detects stochastic errors only — i.e. almost none of the errors this system actually has. The retry envelope is close to a no-op as a correctness mechanism.

**Non-obvious corollary `[RESEARCH]`:** the second model's value comes from **error decorrelation**, not accuracy.

> *"With Groq as the second model against Gemini, that number was 0.30 — below chance: Groq is weak enough that it disagrees even when Gemini is right, flooding false flags... Swapping in a genuinely independent second model bears this out."*
> — ap-verify

A weak second model is worse than none. If this direction is pursued, **error decorrelation must be measured, not assumed.**

### 2.2 ❌ Strict structured output can *reduce* accuracy and manufacture confident errors `[RESEARCH]`

This one is uncomfortable because `extraction.py:238-257` uses `response_model` via `instructor`.

| Approach | Parse rate | Field F1 | Confidently-wrong rate |
|---|---|---|---|
| Prompted JSON + retries | 92% | 0.87 | **3%** |
| OpenAI function calling | 99% | 0.90 | **4%** |
| OpenAI Structured Outputs (constrained) | 100% | 0.89 | **8%** |

> *"The 100% conformance run had the highest 'confidently wrong' rate because **the model could no longer refuse**. When the borrower's income wasn't on the document, the prompted version would emit `{"income": null, "confidence": "low"}` or just decline. The constrained version would emit `{"income": 75000}` because the grammar said income had to be a number and the FSM couldn't get to EOS without one."*
> — datarekha, "Structured outputs engineering"

Supporting: BAML benchmarks show unconstrained+parse at **93.63%** vs constrained at **91.37%** on the same model. Lee et al. measure a **3–9pp accuracy drop** from forced formats, >15pp on reasoning tasks.

**Impact on this repo `[VERIFIED]`:** mixed, and partly good news.
- ✅ Most fields are already nullable with `None` defaults (`_VLMLineItem`, `extraction.py:91-118`) — so the model *can* decline. That is the correct pattern.
- ⚠️ `document_type: Literal[...] = Field(...)` (`:139`) is **required**. An unreadable document must still emit one of six enum values. Per the finding above, that's exactly the shape that forces a guess instead of a refusal.

**The published mitigation** `[RESEARCH]`:

> *"Pair structured outputs with a self-consistency check. Run the same extraction **unconstrained** alongside the constrained version, and flag rows where the two disagree by more than a token. The disagreement set is your audit queue."*
> — datarekha

> *"A 92% parse rate with a 1% confident-wrong rate is, for most production pipelines, a better operating point than a 100% parse rate with an 8% confident-wrong rate."*

### 2.3 ⚠️ "Lost in the middle of the table" is a documented, quantified failure mode `[RESEARCH]`

This is the most likely mechanical explanation for `line_item_no` drift and mid-table quantity errors.

> *"There exists a **lost-in-the-middle-table phenomenon** for LLMs including GPT-4o... models possess better perception of table cells in the first row and the last row, but **struggle with cells in the middle part of tables**. As the table size increases, LLMs suffer a significant performance decline."*
> — NEEDLEINATABLE, NeurIPS 2025 (750 tables, 287K questions)

> *"Current LLMs can retrieve answer cells from input tables using the attention mechanism, but they **struggle in interpreting the basic two-dimensional table structures in a human-like perspective**."*
> — same

Foundational mechanism (TACL 2024, "Lost in the Middle"): U-shaped performance curve — primacy bias and recency bias, middle degrades. GPT-3.5-Turbo in the middle performed *worse than closed-book* (56.1%).

**A free fix from the same paper** `[RESEARCH]`:

> *"We can [improve models] by placing the query **before and after** the data... query-aware contextualization **dramatically improves** performance on the key-value retrieval task—all models achieve near-perfect performance at 75, 140, and 300 key-value pair settings. GPT-3.5-Turbo (16K) with query-aware contextualization achieves perfect performance at 300 key-value pairs. In contrast, without it, worst-case was 45.6%."*

**Impact on this repo `[VERIFIED]`:** `_SYSTEM_PROMPT` and `_PAGE_PROMPT` (`extraction.py:34-88`) are both placed **before** the PDF attachment. The instructions are ~4,000 tokens away from the point of generation on a large document. Appending a compressed restatement of the critical rules *after* the file is a zero-cost change.

**Also relevant `[VERIFIED]`:** `extraction.py:59` instructs *"scan ALL pages sequentially"* — one call covering every page. That is precisely the long-context, middle-of-document regime where the above degradation is measured.

### 2.4 ⚠️ Row-level, header-injected extraction is the consensus structure-aware approach `[RESEARCH]`

Across multiple independent sources, the recommendation for tables is identical:

> *"Preserve structure before you chunk... keep the whole table as one chunk when it fits. Otherwise split by row or short row group — **with headers repeated**... Every row-level or row-group chunk should **repeat the minimum column headers** inside the same unit as the values."*
> — Structure-Aware Tabular Chunking (STC), arXiv 2605.00318

> *"The fix: pair the header (column names) with each row, as a dictionary or key-value object... **The column names travel with the values**, so the reader sees a self-explaining line instead of a naked cell."*
> — Towards Data Science, "Retrieve One Row from a Table"

STC's concrete representation: a hierarchical **Row Tree**, each row encoded as `column_name: value` KV pairs, split only at row boundaries, greedy-merged under a token budget.

**Serialization format finding `[RESEARCH]`** (SiReF, arXiv 2305.16344, financial reports): comparing PLAIN / CSV / XML / HTML table serialization:

> *"The PLAIN and the CSV formats **outperform** the XML and HTML formats in terms of accuracy, likely due to their concise table representation, which reduces table fragmentation."*

**Impact on this repo `[VERIFIED]`:** `extraction.py:48-64` (STEP 3) asks the model to *itself* resolve column semantics and the side-column-vs-`Line Item - N` marker problem in one shot, while scanning all pages. The alternative is to separate the two concerns: extract the table as a header-anchored row structure first, then map columns to fields. This is a real architectural option, not just a prompt tweak.

### 2.5 ⚠️ Native-PDF-one-call is on the losing end of the cost/accuracy frontier `[RESEARCH]`

arXiv 2609.29933, "An Empirical Study of VLM Pipelines for Long-Document QA" — directly head-to-head on input modality:

- **Raw PDF**: strongest static mode for Sonnet on MMLongBench (**0.522**) but **80k input tokens per question**, because the API sends *both* a rendered image and an extracted text layer for every page.
- **All-images**: *weaker* than raw PDF (no text layer).
- **Table questions are the exception:**

> *"Table questions are the one exception, where a **layout-aware extractor recovers structured cell content and matches or beats images** on MMLongBench. MinerU lifts Sonnet's text-only accuracy by about +8.8pp over plain-text extraction with PyMuPDF, and the gain concentrates on table and figure questions."*

- Best accuracy/cost: a **6-tool function-calling agent** at 0.616–0.625 for 20–33k tokens — *more accurate and ~2.5–4× cheaper* than raw PDF.
- *"Only top-k retrieval and the agent lie on the Pareto frontier: raw PDF and extracted text are dominated."*
- ~13pp of oracle headroom remains across configurations.

**This is a correction to my earlier assessment.** I previously dismissed Docling/MinerU on *architectural* grounds (they introduce a text layer this system deliberately removed — `extraction.py:32`, `:237`). That reasoning is sound for general documents but **the accuracy evidence is table-specific, and this system is ~100% table work.** A layout-aware extractor's structured cells matching or beating native PDF is a finding that deserves an actual test, not a philosophical objection.

### 2.6 ⚠️ Two-stage VLM pipelines compound the model's own errors `[RESEARCH]`

arXiv 2603.23511 (stage-wise protocol, GPT-5-mini/GPT-5-nano/Claude-3.5-Sonnet vs Azure DI / Mistral OCR):

| Strategy | Answer-containment |
|---|---|
| OCR-first, then query | **0.514** |
| Direct VQA (no intermediate parsing) | 0.493 |
| **VLM does both parsing AND extraction (2-stage)** | **0.371** |

> *"The performance gap between QA_OCR and QA_VLM-2stage is substantial (14.2 percentage points), indicating that VLM-based text extraction introduces errors that propagate... if VLM-based parsing is required, using **different models or architectures for each stage may mitigate error propagation**."*

**Impact:** this is the strongest published argument for the cross-model design in §2.1. Two stages of the *same* model is the worst configuration.

### 2.7 📉 The ceiling is real. "Tension-free" is not achievable. `[RESEARCH]`

| Benchmark | Finding |
|---|---|
| KDD 2026 Table Extraction Benchmark (86k pages, 15 methods) | *"**no method achieves F1 TEDS > 90%**"*; *"VLMs hallucinate on heterogeneous data"* |
| arXiv 2603.18652 (21 parsers, LLM-as-judge) | *"Even the top-scoring Gemini 3 models exhibit errors... misaligned spanning cells, subtly altered values, and incorrect header-cell associations, confirming that accurate table extraction from PDFs remains an **unsolved problem**"* |
| WACV 2026 (business docs, zero-shot) | Commercial VLMs: Column Err 0.22–0.26, **Row Err 0.40–0.46**, Header Err 0.34–0.40 |
| DocILE | **Line-item F1 ≈ 0.66** — the hardest task on the benchmark (vs ≈1.0 for currency/subtotal/tax) |

**This reframes the goal.** AWS Textract's line-item accuracy sits in this same band. Chasing "tension-free like Textract" is chasing a level the entire field has not reached. The realistic target is **a documented, bounded error rate with high detectability** — not zero errors.

### 2.8 📉 Error compounding is severe, and it is why the checksum architecture is right `[RESEARCH]`

> *"Per-field F1 0.92 **compounds** over ~6 fields to ≈0.9⁶ ≈ **50% of full-page invoices having at least one wrong field**."*
> — ap-verify, 511 field rows / 143 real invoices

At ~50 line items per document and an assumed 98% per-line accuracy: `0.98⁵⁰ ≈ 36%` of documents contain at least one wrong line.

**But this is the strongest argument for the existing design `[VERIFIED]`.** Because reconciliation requires PO quantity == DN aggregate == SI aggregate exactly, a wrong line almost always breaks the checksum and quarantines. The quantity-only rule (`models.py:121`, enforced by `test_price_is_never_compared`) converts "per-line accuracy" into "per-*set* accuracy," which is dramatically higher.

**The metric nobody in this repo has stated:** not field accuracy, but **rate of wrong-lines-that-still-reconcile**. That is the real defect rate. It is currently unmeasured.

---

## 3. Errors already found in this repo `[VERIFIED]`

### 3.1 No line amount is extracted

Zero occurrences of `line_amount` / `line_total` / `extended_amount` / `total_amount` anywhere in the repo. `LineItem` (`models.py:114-131`) has exactly: `id`, `document_id`, `line_item_no`, `description`, `quantity`, `unit_price`, `dn_no`.

### 3.2 A deliberate guard test forbids using price in the comparison path

`tests/test_synthetic_spec.py:736` — `test_price_is_never_compared`:

```python
src = inspect.getsource(group_by_line_no) + inspect.getsource(rec.compare_po_set_lines)
assert "unit_price" not in src
```

Its docstring: *"If a price comparison is ever reintroduced this fails, which is the point: it would have to be a product decision, and a flag type with it, documented in PRODUCT 3.2."*

**Consequence:** any arithmetic-consistency check (`qty × unit_price ≈ line_amount`) that lands in reconciliation is blocked by design. Adding `line_amount` to the schema is also blocked in spirit, since the five few-shots (`extraction.py:83-87`) contain no amount column — suggesting the vendors may not print one.

**This is why the "arithmetic line check" idea was dropped.** It was ranked first in an earlier draft of this research on the assumption that a line amount existed and was simply unused. Both halves of that assumption were wrong.

### 3.3 `prefect.max_concurrent_extraction_tasks` is configured but never read

`extraction.py` is sequential in `flows/sync.py:230-286`. Documented as "trivially satisfied" in `sync.py:150-155`. It is not a concurrency control.

### 3.4 `redo_extract` runs with zero retries

`api/routes/po_sets.py:263` calls `extract_document` in a plain loop with no retry envelope, unlike the Prefect path.

---

## 4. Tool / library survey — what was evaluated and why it was rejected

Recorded so the decision is not re-litigated without new information.

| Candidate | Verdict | Reason |
|---|---|---|
| **Unstract** (platform) | ✗ | Requires Django+React+Celery+RabbitMQ+Redis+**PostgreSQL/pgvector**+MinIO via Docker. Quickstart says *"Linux or macOS, Docker, 8 GB RAM minimum."* Production is Win2016 / 2 cores / SQLite WAL / NSSM. AGPL-3.0. |
| **Unstract** (SDK) | ✗ | `requires-python >=3.12,<3.13`; this repo is `>=3.11`. Framework for writing tools *for* the platform, not an extraction library. The `unstract` PyPI name is a dead 0.0.1 stub from 2023. |
| **ContextGem** | ✗ | Apache-2.0 and anti-RAG (philosophically aligned), but **no PDF input** — `Document` takes `raw_text`/`paragraphs`/`images`; the only converter (`DocxConverter`) is deprecated as of 0.22.0. Concept types are all scalars; **no array/row concept**, so `line_items[]` models worse in existing code. Adds `litellm==1.96.2` + `openai==2.54.0`, both pinned exact. v0.27.0, 211 commits, one vendor. |
| **Docling** | **⚠️ Re-test warranted** | MIT, CPU-viable, best clean-scan OCR in one benchmark (0.988 char similarity) — but scored *lowest* of the pipeline tools (50.3 olmOCR-bench) and one benchmark reports it **hallucinates values on dense tables**. Its table role is exactly what §2.5 says to test. |
| **MinerU** | **⚠️ Re-test warranted** | Basic tier is pure CPU, ~2GB RAM — the only real fit for Win2016. **Cross-page table merging + header/footer removal** are directly relevant to multi-page DNs. Licence (Apache-2.0 + terms) is fine below 100M MAU / $20M revenue. Caveat: no Python 3.13 on Windows. Lifts table accuracy ~8.8pp over PyMuPDF text (§2.5). |
| **Marker 2** | ✗ | Best accuracy (76.0) and genuinely CPU-capable, but **model weights carry a modified OpenRAIL-M licence free only below $5M revenue**. Landmine at any real company size. |
| **LlamaParse** | ✗ | **Proprietary, explicitly not open source.** Cloud-only (VPC at Enterprise). Agentic tiers run an LLM per page, so extraction runs twice — once in their parser, once in Luna. Cheap at our volume (~$37/mo for Agentic) but adds a second vendor holding commercial documents and depends on a service whose pitch is *"write regex per vendor templates."* |
| **LiteParse** | ~ | Apache-2.0, fully local. Outputs **bounding boxes** — genuinely useful for provenance. But OCR is Tesseract.js, a downgrade vs. a good VLM. No markdown, no figure understanding. |
| **3-way-match vendors** (Dost, Hubler, Lido, OmniPATH, Phacet, Riff, Doxis, Medius, Coupa, Basware, Kofax) | ✗ | All require SAP/Oracle/Dynamics/NetSuite integration, are SaaS-only, and target AP teams with hundreds of vendors. This is a whole-system replacement, not a component. |

---

## 5. The candidate improvements, ranked

Each is `[PROPOSED]` — untested. Effort assumes familiarity with this codebase.

### Tier 1 — cheap, no product decision needed

| # | Change | Rationale | Effort |
|---|---|---|---|
| 1 | **Append a compressed restatement of the critical rules *after* the PDF** in the message array | §2.3 — query-aware contextualization, TACL 2024. Near-zero cost, large reported effect (45.6% → perfect on KV retrieval). | ~1 hour |
| 2 | **Constrained + unconstrained dual call; flag disagreements** | §2.2 — constrained output has an 8% confidently-wrong rate vs 3% for prompted JSON. The disagreement set is the audit queue. | ~1 day |
| 3 | **`source_quote` per line item — verbatim raw row text**, into `raw_extraction_json` | Pure detectability. Does not touch price, matching, or the DB schema. Turns an integer the operator must trust into text they can check. Repo with a full verification cascade exists: `narnacle/SAV-Grounding`. | ~half day |
| 4 | **Cross-model extraction; disagree → quarantine** | §2.1 + §2.6. Must also *measure* error decorrelation (§2.1) or the signal is worse than useless. Reuses the existing attempt-cap plumbing. | ~1 day |

### Tier 2 — needs real vendor PDFs

| # | Change | Rationale |
|---|---|---|
| 5 | **Header-anchored two-stage extraction** — extract the table as `col: value` row units with headers repeated, then map columns to fields | §2.4. Directly targets the STEP 3 compromise (`extraction.py:48-64`) and the lost-in-middle effect. |
| 6 | **Per-vendor `line_item_no` resolution rules**, keyed on vendor from the header call | Bridon prints `1-1` on PO and `1` on DN; Rho needs the embedded `Line Item - N` marker; ADES uses step-10. This is the highest-accuracy lever for a heterogeneous vendor estate and is the thing Unstract's "Prompt Studio / Prompt Coverage" was selling. |
| 7 | **Page-group calls instead of "scan ALL pages"** | §2.3 + §2.6. Also the `max_paragraphs_to_analyze_per_call` idea from ContextGem. |

### Tier 3 — needs a product decision per `AGENTS.md` §7.3

| # | Change | Blocker |
|---|---|---|
| 8 | **Automated prompt optimisation (GEPA / DSPy)** — an LLM reflects on failures and rewrites `_SYSTEM_PROMPT`/`_PAGE_PROMPT` | Reported **+22pp exact match** over a hand-written baseline, ~$2–3 and 5–10 min per run. Requires a labelled set (~40 examples was sufficient in a published run). **Would amend the product's prompt as a governed artifact.** |
| 9 | **Re-test native PDF vs layout-aware extraction (Docling/MinerU) for the table layer only** | §2.5 contradicts the earlier architectural objection. Introducing a text layer is an explicit spec amendment (`extraction.py:32`). |
| 10 | **Make `document_type` nullable**, so an unreadable document can decline instead of guessing | §2.2 — required enums are the shape that manufactures confident guesses. Changes a documented behaviour (`SESSION_HANDOFF.md:250`). |

---

## 6. The metric that should replace "field accuracy"

Everything in §2.8 points at the same conclusion. The system does not need to be correct per field; it needs to be **correct per PO Set**, and **loud** when it is not.

```
reconciling_while_wrong = (sets that merged) / (sets where ≥1 line was wrong)
```

That single number is the defect rate that matters. A secondary metric:

```
silent_drift = (line items whose quantity differs from ground truth
                but still reconciles) / (all line items)
```

Both require a labelled set. Neither is currently computed. Neither is currently computable.

---

## 7. What blocks everything

**One item blocks the entire Tier 1–3 sequence: there are no real vendor PDFs in this repository.**

It is needed for:
- the baseline (§6) — impossible without it
- Tier 2 entirely — vendor rules cannot be written from transcriptions
- GEPA (§8 of Tier 3) — the optimiser needs a training set; the reason for building it is "the optimiser needs it," not "let's go measuring"
- §2.5's Docling/MinerU re-test — needs documents, not benchmarks

The published GEPA result is instructive here: ~40 hand-labelled examples was enough to move a metric 22 points. The cost is a bounded, one-off labelling task, not an open-ended programme.

---

## 8. Sources

ArXiv / preprints (2026 unless noted):
- [2609.20110](https://arxiv.org/pdf/2609.20110v1.pdf) — Decomposed confidence (perception/layout/validation) + conformal risk control for financial documents
- [2609.29933](https://arxiv.org/html/2609.29933v1) — Empirical study of VLM pipelines for long-document QA; input modality head-to-head
- [2609.23742](https://arxiv.org/abs/2609.23742) — Constrained decoding: structural vs semantic correctness
- [2603.23511](https://arxiv.org/pdf/2603.23511) — OCR-or-VLM stage-wise comparison; error compounding in two-stage VLM pipelines
- [2603.18652](https://arxiv.org/html/2603.18652v1) — Benchmarking 21 PDF parsers with LLM-as-judge
- [2605.00318](https://arxiv.org/html/2605.00318v1) — Structure-Aware Tabular Chunking (Row Tree, KV blocks)
- NEEDLEINATABLE, NeurIPS 2025 — lost-in-the-middle-table phenomenon
- "Lost in the Middle", TACL 2024 — query-aware contextualization
- 2501.10868 — JSONSchemaBench, constrained-decoding comparison

Code:
- `ap-verify` (invoice verification, FPR-constrained) — per-field/error-compounding/cross-model measurements
- `narnacle/SAV-Grounding` — Source-Anchor Verification, character-level grounding
- `dspy` / `dspy.GEPA` — reflective prompt optimiser
- `RossumAI/docile` — Line Item Recognition benchmark

Surveys:
- Docling · MinerU · Marker 2 · LlamaParse · LiteParse (see §4)