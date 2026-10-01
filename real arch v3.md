# AAM Merger V3 â€” Real Architecture (derived from source code only)

> Written by reading the actual `.py`, `.yaml`, `.html`, `.toml` files. No `.md`
> spec/product document was used as a source for this document.

---

## 1. What the project actually is

**AAM Merger V3** is a document-reconciliation web application.

It watches an `input/` folder for vendor PDFs (Purchase Order, Delivery Note,
Sales/Tax Invoice), has a VLM (OpenRouter `openai/gpt-6-luna`) read each PDF into
structured JSON, groups documents into **PO Sets** by normalized PO number,
verifies that **quantities match exactly** across PO / DN / SI for every line, and
then concatenates the pages into a single merged PDF packet in `output/`.

Nothing is guessed. Every decision after the VLM call is deterministic code.
Ambiguity is refused and routed to quarantine or to a human-visible `pending`
state â€” never resolved by inference.

### Stack (from `pyproject.toml`)

| Layer | Technology |
|---|---|
| Web | FastAPI + Uvicorn (all handlers are sync `def` â†’ threadpool) |
| Orchestration | Prefect 3, work pool `aam-merger-process-pool`, cron `0 0 * * *` |
| DB | SQLAlchemy 2.x **sync** + SQLite WAL + Alembic |
| Extraction | `instructor` + OpenAI SDK â†’ OpenRouter, strict Pydantic `response_model` |
| PDF | `pypdf` (reader/writer), no `PyPDF2`, no rasterization |
| Matching | `rapidfuzz` `token_sort_ratio` (fallback only) |
| Number parsing | `babel` `parse_decimal` strict, locale `en_IN` |
| Frontend | Jinja2 + HTMX 1.9 + Alpine.js 3.13 (server-rendered, no SPA build) |
| Locking | `filelock` (inter-process) + DB columns (per-PO-Set) |
| Tests | `pytest` + `pytest-cov` + `hypothesis` + `ruff` + `ty` + `pip-audit` |

Test tooling declared in `pyproject.toml`: `pytest`, `pytest-asyncio`,
`pytest-cov`, `hypothesis`, `ruff`, `ty`, `pip-audit`. Coverage is configured with
`--cov=src --cov-fail-under=0`, so coverage is measured but never gates the run.

### Canonical module map

| File | Role |
|---|---|
| `src/app/main.py` | FastAPI app, logging, router wiring, `/health` |
| `src/app/core/config.py` | `AppConfig` pydantic-settings, fail-fast loader, validators |
| `src/app/core/database.py` | SQLite engine cache, WAL/`foreign_keys` pragmas |
| `src/app/core/limits.py` | Upload size cap constant |
| `src/app/models/models.py` | 4 tables: `documents`, `line_items`, `po_sets`, `audit_log` |
| `src/app/flows/sync.py` | Prefect `sync_flow` + `extract_task` â€” the pipeline orchestrator |
| `src/app/services/ingestion.py` | PDF discovery, stability poll, SHA-256 dedup, input clearing |
| `src/app/services/extraction.py` | VLM prompts, Pydantic schemas, `_call_vlm`, `extract_document` |
| `src/app/services/splitting.py` | Layer-1 multi-document PDF cutter (pure function) |
| `src/app/services/sanitizer.py` | Strict babel quantity parsing â†’ integer Ã—1000 |
| `src/app/services/grouping.py` | PO-number normalization, PO Set mint/attach, unattached resolution |
| `src/app/services/matching.py` | The reconciliation rule (group + sum + compare) |
| `src/app/services/reconciliation.py` | Gate chain, verdict, plain-language reasons |
| `src/app/services/customs.py` | Customs/shipping toggle gate |
| `src/app/services/merge.py` | Auto-merge, force-merge, packet ordering and naming |
| `src/app/services/quarantine.py` | Quarantine folders/reports, DB delete, manual PDF merge |
| `src/app/services/locking.py` | Per-PO-Set DB lock (acquire / release / is-locked) |
| `src/app/services/sync_lock.py` | Inter-process file lock + stale watchdog |
| `src/app/api/routes/sync.py` | `POST /sync`, `GET /sync/status` |
| `src/app/api/routes/po_sets.py` | 7 operator action endpoints (locked, 409 on contention) |
| `src/app/api/routes/dashboard.py` | Dashboard, detail, upload, audit, quarantine, unclassified |
| `src/app/api/routes/manual_merger.py` | Isolated manual PDF merge UI |
| `scripts/deploy_prefect.py` | Registers the midnight cron deployment |

---

## 2. Data model (as built)

| Table | Columns |
|---|---|
| `documents` | `id` PK Â· `sha256_hash` (unique, indexed â€” the dedup key) Â· `original_filename` Â· `stored_path` (never auto-deleted) Â· `doc_type` enum(PO, DN, SI, COMBINED, CUSTOMS, SHIPPING, UNKNOWN) Â· `raw_extraction_json` Â· `po_no_raw` Â· `po_no_normalized` Â· `po_reference_ambiguous` bool Â· `dn_no` Â· `si_no` Â· `invoice_no` Â· `extraction_status` enum(pending, processing, valid, failed) Â· `extraction_attempt_count` Â· `po_set_id` FK nullable Â· `parent_document_id` FK self Â· `is_split_parent` bool Â· `split_completed_at` Â· `created_at` Â· `updated_at` |
| `line_items` | `id` PK Â· `document_id` FK Â· `line_item_no` (nullable â€” **the** matching key) Â· `description` Â· `quantity` int Ã—1000 Â· `unit_price` int Ã—1000 Â· `dn_no` (per-row DN reference) |
| `po_sets` | `id` PK Â· `po_no_normalized` (indexed â€” the grouping key) Â· `status` enum(pending, mismatched, quarantined, blocked_customs, merged) Â· `has_customs_toggle` Â· `customs_doc_count` Â· `merged_output_path` Â· `reconcile_reason` (plain-language) Â· `merged_at` (immutable) Â· `locked_by_action` Â· `locked_at` Â· `created_at` Â· `updated_at` |
| `audit_log` | `id` PK Â· `po_set_id` FK nullable ON DELETE SET NULL Â· `action` enum(force_merge, quarantine_delete, manual_status_change) Â· `detail` (JSON) Â· `justification` (â‰¥20 chars when given) Â· `timestamp` Â· `source` |

**`line_items` deliberately has NO `part_no`, NO `line_type`, NO UOM.** These are
not matching inputs in this product.

**All quantities and prices are integers scaled Ã—1000.** Every comparison is on
those integers; floats are never used for money or counts.

---

## 3. End-to-end flow chart

```
                      TRIGGER
     â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
     â”‚ A) Prefect cron "0 0 * * *"  (worker)      â”‚
     â”‚ B) POST /sync            (dashboard button)â”‚
     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                    â”‚
      â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
      â”‚ sync_flow()               â”‚  src/app/flows/sync.py:163
      â”‚ acquire_sync_lock()       â”‚  services/sync_lock.py  (filelock, DB dir)
      â”‚  â””â”€ held?  YES â†’ return {"status":"skipped"}   (POST â†’ HTTP 409)
      â”‚  â””â”€ held?  NO  â†’ run. Sidecar .sync.started; >3600s = dead holder, break lock
      â””â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
         â”‚              â”‚
   Phase 0a: scan      Phase 0b: backfill
   find_input_pdfs()   docs already in DB with
   (one pass, .pdf     status == pending
    case-insensitive)  (from a crashed prior run)
         â”‚
         â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 1 â€” STABILITY POLL                          â”‚  ingestion.is_file_stable()
   â”‚ stat() size Ã— (count, interval) â†’ all equal?    â”‚  default 2 polls Ã— 2s
   â”‚ no â†’ skip file this run (still being copied)     â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                        â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 2 â€” INGEST + SHA-256 DEDUP                 â”‚  ingestion.ingest_file()
   â”‚ hash bytes â†’ if row exists: return it (no new rowâ”‚
   â”‚           â†’ else copy to stored/<sha>.pdf        â”‚
   â”‚              doc_type=UNKNOWN, status=pending    â”‚
   â”‚              attempt_count=0                     â”‚
   â”‚ split-child name? <sha16>_p<i>.pdf â†’ link       â”‚
   â”‚              parent_document_id (by SHA prefix)  â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                        â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 3 â€” VLM EXTRACTION  (Prefect task, retry)   â”‚  services/extraction.py
   â”‚ guard: attempt_count >= 3 â†’ status=failed,       â”‚  â† terminal, no raise
   â”‚        return (Prefect would burn retries)       â”‚
   â”‚ attempt_count += 1                               â”‚
   â”‚                                                   â”‚
   â”‚ base64 PDF (WHOLE file, all pages) â”€â”€â–¶ OpenRouter â”‚  NO rasterization
   â”‚   response_model = _VLMPageExtraction (strict)    â”‚  max_retries=0
   â”‚ fail-closed: no OPENROUTER_API_KEY â†’ RuntimeError â”‚
   â”‚                                                   â”‚
   â”‚ Stored raw JSON â†’ documents.raw_extraction_json  â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
           â”‚ type == COMBINED?        â”‚ otherwise
           â–¼ YES                      â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 4 â€” LAYER-1   â”‚   â”‚ Persist:                          â”‚
   â”‚ MULTI-DOC SPLIT    â”‚   â”‚  status = valid                   â”‚
   â”‚ (splitting.py)     â”‚   â”‚  doc_type â† PO/DN/SI (SKIPâ†’UNK)  â”‚
   â”‚                    â”‚   â”‚  po_no_raw = po_reference|doc_no  â”‚
   â”‚ pypdf page ground  â”‚   â”‚  po_no_normalized = normalize_()  â”‚
   â”‚  truth validation: â”‚   â”‚  si_no/invoice_no (SI)            â”‚
   â”‚  page_count match? â”‚   â”‚  dn_no (DN)                       â”‚
   â”‚  ranges 1..N?      â”‚   â”‚  po_reference_ambiguous flag      â”‚
   â”‚  no overlap?       â”‚   â”‚                                   â”‚
   â”‚  every page claimedâ”‚   â”‚ Per line item:                     â”‚
   â”‚   (SKIP counts)?   â”‚   â”‚  qty  = parse_quantity_scaled()   â”‚
   â”‚  â†“ cut non-SKIP    â”‚   â”‚        (babel strict, en_IN, â‰¤2dp,â”‚
   â”‚  into input/ as    â”‚   â”‚         >0, finite, <1e13) Ã—1000 â”‚
   â”‚  <sha16>_p1.pdfâ€¦   â”‚   â”‚  price = same scaler (unused for  â”‚
   â”‚                    â”‚   â”‚          matching, packet only)    â”‚
   â”‚ FAIL any gate â†’    â”‚   â”‚  line_item_no verbatim ("1-1" ok) â”‚
   â”‚  parent failed +   â”‚   â”‚  dn_no per-row (nullable)         â”‚
   â”‚  quarantined, NOT  â”‚   â”‚ rows with qty/desc None â†’ skippedâ”‚
   â”‚  raised            â”‚   â”‚ delete old lines first (retry-safe)â”‚
   â”‚ OK â†’ parent row    â”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
   â”‚  STERILE (no po,  â”‚
   â”‚  no set, no lines) â”‚        Failure â†’ status=failed, re-raise
   â”‚  parent input copy â”‚        (Prefect retries: [2,5,15]s, N=config)
   â”‚  â†’ combined/      â”‚
   â”‚  split_completed_atâ”‚
   â”‚  children re-run   â”‚
   â”‚  next sync         â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
             â”‚
             â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 4b â€” SUCCESS IS READ FROM THE DB ROW,        â”‚
   â”‚ NOT from the task return value.                   â”‚
   â”‚ If persisted status == failed:                    â”‚
   â”‚   errors += 1                                     â”‚
   â”‚   quarantine_document() â†’ quarantine/_documents/  â”‚
   â”‚     + QUARANTINE.txt, and DELETE the input/ copy  â”‚
   â”‚     (else it is re-hashed and re-errored nightly) â”‚
   â”‚   if its PO Set exists â†’ that set is quarantined   â”‚
   â”‚     too: "a member document failed to read"       â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                        â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 5 â€” GROUPING  (only PO mints a set!)         â”‚  services/grouping.py
   â”‚ normalize_po_no(raw): drop "PO"/"REF"/"ORDER"      â”‚  grouping.py:43
   â”‚   labels, drop ",Rev" tail, flatten+upper          â”‚
   â”‚ get_or_create_po_set(create = type=="PO")          â”‚
   â”‚   PO   â†’ find open set by key, else MINT            â”‚
   â”‚   DN/SI/UNKNOWN â†’ attach to open set, NEVER mint   â”‚
   â”‚   no open set â†’ stays UNATTACHED, visible          â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                        â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 6 â€” RESOLVE UNATTACHED DOCS  (3 strategies,  â”‚  resolve_unattached_documents()
   â”‚ strongest evidence first; never mints)            â”‚  :226
   â”‚  1. same dn_no as an already-attached doc         â”‚
   â”‚  2. PO's per-line dn_no reference points at it     â”‚  _anchor_from_po_line_ref
   â”‚     (anchored on PO only â€” must be ONE set)        â”‚
   â”‚  3. sibling filename prefix (4 dash-segments)      â”‚
   â”‚  ambiguous across sets â†’ leave unattached          â”‚
   â”‚ po_reference_ambiguous docs â†’ NEVER auto-attach    â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                        â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 7 â€” RECONCILE  reconcile_po_set()            â”‚  services/reconciliation.py
   â”‚  GATE 0  already merged â†’ return, IMMUTABLE       â”‚  :177
   â”‚  GATE 1  need â‰¥1 PO doc?  else pending            â”‚
   â”‚          need â‰¥1 DN doc?  else pending            â”‚
   â”‚          need â‰¥1 SI doc?  else pending            â”‚
   â”‚          (missing side = unfulfilled demand, WAIT) â”‚
   â”‚  GATE 2  >1 PO doc & single_po_document â†’         â”‚
   â”‚          quarantine "multiple_po_documents"       â”‚
   â”‚  GATE 3  any doc.po_no_normalized != set key â†’     â”‚
   â”‚          quarantine "po_reference_mismatch"       â”‚
   â”‚  GATE 4  any qty <= 0 â†’ quarantine                â”‚
   â”‚          "non_positive_quantity"                  â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                        â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 8 â€” 3-WAY QUANTITY COMPARISON                 â”‚  services/matching.py
   â”‚                                                   â”‚
   â”‚  group_by_line_no(PO, DN) and (PO, SI) INDEPENDENTLYâ”‚
   â”‚   normalize_line_no: "01"â†’"1", " 001 "â†’"1"        â”‚
   â”‚   "1-1" / "1a" survive (string compare)            â”‚
   â”‚  PO line with NO number â†’ QUARANTINE               â”‚
   â”‚                  "po_line_missing_line_item_no"    â”‚
   â”‚  Vendor row with no number â†’ rapidfuzz             â”‚
   â”‚   token_sort_ratio vs PO desc, â‰¥85 (config)        â”‚
   â”‚   FALLBACK ONLY, never overrides a real number      â”‚
   â”‚  Sum each group (1 PO line over 3 DN rows = OK)    â”‚
   â”‚                                                   â”‚
   â”‚  compare_aggregates():                             â”‚
   â”‚   orphan vendor line (no PO counterpart) â†’        â”‚
   â”‚     flag type=IDENTIFICATION (priority 1)          â”‚
   â”‚   qty != po_qty â†’ flag type=QUANTITY (priority 2)  â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                        â–¼
        â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
        â”‚ any IDENTIFICATION flagâ”‚â”€â”€YESâ”€â”€â–º QUARANTINED "unmatched_vendor_line"
        â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                    NO
        â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
        â”‚ any QUANTITY flag where vendor_qty > 0? â”‚  (both sides reported, differ)
        â””â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”˜
        YES  â”‚                                 â”‚ NO (vendor reported nothing)
             â–¼                                 â–¼
        â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”                  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
        â”‚ MISMATCHED  â”‚                  â”‚  PENDING    â”‚
        â”‚ needs human  â”‚                  â”‚ "partial_   â”‚
        â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”˜                  â”‚ fulfillment"â”‚
               â”‚                         â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
        (same three verdicts flow onward)
                        â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 9 â€” CUSTOMS GATE  customs.is_blocked(ps)     â”‚  services/customs.py:12
   â”‚ has_customs_toggle ON AND NOT (CUSTOMS+SHIPPING)  â”‚
   â”‚        â†’ BLOCKED_CUSTOMS, stop                   â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                        â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 10 â€” AUTO-MERGE  merge_po_set()             â”‚  services/merge.py:171
   â”‚ refuses if mismatched/quarantined/blocked        â”‚
   â”‚ order docs by cfg.merge.legal_order               â”‚
   â”‚   SI â†’ DN â†’ PO â†’ SHIPPING â†’ CUSTOMS              â”‚
   â”‚   (types not in list append first-seen)           â”‚
   â”‚ refuses if total line items == 0 (W-22)           â”‚
   â”‚ name  = SI doc's OWN si_no, else PO number        â”‚
   â”‚        (flag "invoice_no_missing" if fallback)    â”‚
   â”‚ _resolve_output_path: existing file owned by      â”‚
   â”‚        another set â†’ MergeNamingError â†’ QUARANTINEâ”‚
   â”‚ pypdf PdfWriter concatenates every page          â”‚
   â”‚ set status=merged, merged_output_path, merged_at  â”‚
   â”‚ IMMUTABLE once merged (first completion wins)     â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                        â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 11 â€” INPUT CLEARING  delete_input_files()    â”‚  ingestion.py:103
   â”‚ ONLY for merged sets: delete by filename, then    â”‚
   â”‚   sweep leftovers by re-hashing                    â”‚
   â”‚ stored/<sha>.pdf copies are NEVER deleted         â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                        â–¼
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ STEP 12 â€” RE-CONCILE SWEEP                        â”‚  sync.py:351
   â”‚ every non-merged set, to catch sets whose docs    â”‚
   â”‚ were all present before this run started          â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                        â–¼
        summary {processed, extracted, errors,
                 touched_po_sets, reconciled_count}
```

---

## 4. Reconcile decision tree (condensed)

```
PO/DN/SI all present?
  NO  â†’ pending   (missing_po | missing_dn | missing_si)
  YES
   â”œ >1 PO doc            â†’ quarantined  multiple_po_documents
   â”œ PO ref drift         â†’ quarantined  po_reference_mismatch
   â”œ qty â‰¤ 0              â†’ quarantined  non_positive_quantity
   â”œ PO line w/o number   â†’ quarantined  po_line_missing_line_item_no
   â”œ vendor orphan line   â†’ quarantined  unmatched_vendor_line
   â”œ qty differs, both reported >0 â†’ MISMATCHED
   â”œ qty short, vendor silent    â†’ pending  partial_fulfillment
   â”” all equal
       â”œ customs on & missing CUSTOMS/SHIPPING â†’ blocked_customs
       â”” merge: naming fail â†’ quarantined  packet_naming_failed
              else â†’ MERGED â†’ output/<si_no|po_no>.pdf
```

### PO Set status transitions

| From | To | Trigger |
|---|---|---|
| â€” | `pending` | PO Set minted, or a side is missing, or partial fulfilment |
| `pending` | `mismatched` | quantities differ and both sides reported > 0 |
| any | `quarantined` | any of 6 quarantine reasons, or a member document failed permanently |
| any | `blocked_customs` | customs toggle on and CUSTOMS+SHIPPING not both attached |
| `pending` | `merged` | all quantities equal + customs gate satisfied + packet nameable + written |
| `merged` | `merged` | immutable â€” no further transition is possible |

---

## 5. Complete feature table

| # | Area | Feature | File / line | Trigger |
|---|---|---|---|---|
| 1 | Ingest | PDF discovery, single pass, case-insensitive suffix (avoids double-send to VLM on Windows) | `ingestion.py:87` | every sync |
| 2 | Ingest | File-stability poll before reading (size Ã— N at interval) | `ingestion.py:16` | every file |
| 3 | Ingest | SHA-256 content dedup â€” one row per unique byte content | `ingestion.py:28` | every file |
| 4 | Ingest | Permanent copy to `stored/<sha>.pdf`; self-heals a missing stored copy | `ingestion.py:47` | new docs |
| 5 | Ingest | Split-child parent linkage via 16-char SHA filename prefix | `ingestion.py:53` | child files |
| 6 | Ingest | Input cleared only after merge (by name + by hash sweep) | `ingestion.py:103` | post-merge |
| 7 | Extract | Whole PDF sent as one base64 call (no rasterization) | `extraction.py:246` | per doc |
| 8 | Extract | Strict Pydantic `response_model` via instructor | `extraction.py:238` | per call |
| 9 | Extract | Fail-closed secrets: `OPENROUTER_API_KEY` from env/.env only, never logged | `extraction.py:220` | per call |
| 10 | Extract | Model id from `config.yaml`, never hardcoded | `extraction.py:222` | per call |
| 11 | Extract | 6-step prompt: header scan, classify, line items, quantity, multi-page, split | `extraction.py:46` | per call |
| 12 | Extract | Line-item-no resolution: embedded "Line Item - N" marker beats the side docket counter | `extraction.py:59` | per call |
| 13 | Extract | Quantity = digits only; UOM/currency stripped, never extracted | `extraction.py:104` | per call |
| 14 | Extract | Attempt cap at 3 â€” terminal `failed`, requires operator reset | `extraction.py:429` | per doc |
| 15 | Extract | Raw VLM JSON persisted for audit + optional dev dump | `extraction.py:459` | per doc |
| 16 | Extract | Prefect retry envelope (N and backoff from config) | `flows/sync.py:129` | per failure |
| 17 | Extract | Line items replaced (not appended) on retry â€” idempotent | `extraction.py:543` | per doc |
| 18 | Split | Layer-1 multi-doc split of COMBINED PDFs | `splitting.py:58` | COMBINED |
| 19 | Split | 4 hard validation gates vs pypdf page ground truth (count, range, overlap, coverage) | `splitting.py:82` | COMBINED |
| 20 | Split | Deterministic child names `<sha16>_p<i>.pdf` â€” re-run never duplicates | `splitting.py:130` | COMBINED |
| 21 | Split | Sterile split parent (no PO, no set, no lines) â€” nothing double-counts | `extraction.py:358` | COMBINED |
| 22 | Split | Two loop guards: child re-reading COMBINED, already-split parent | `extraction.py:317` | COMBINED |
| 23 | Split | SplitError quarantines without raising into the retry envelope | `extraction.py:376` | COMBINED |
| 24 | Group | PO-number normalization (label words, `PO` prefix only when leading, `,Rev` tail) | `grouping.py:43` | per doc |
| 25 | Group | Only PO documents may mint a PO Set (DN/SI never create orphan sets) | `flows/sync.py:21` | per doc |
| 26 | Group | Attach-only sweep for DN/SI/UNKNOWN waiting on an anchor | `grouping.py:122` | per sync |
| 27 | Group | 3-strategy unattached resolution (DN no. â†’ PO per-line ref â†’ filename prefix) | `grouping.py:226` | per sync |
| 28 | Group | Ambiguity refused, never first-wins guessing | `grouping.py:218` | per sync |
| 29 | Group | Multi-PO-number documents never auto-attach | `grouping.py:149` | per sync |
| 30 | Num | All qty/price stored as exact integers Ã—1000 (no floats) | `models.py:121` | per line |
| 31 | Num | Strict babel parsing: â‰¤2 significant decimals, >0, finite, <1e13 | `sanitizer.py:33` | per line |
| 32 | Num | Trailing zeros normalized (`12.45000000` â†’ 12.45) â€” precision vs formatting | `sanitizer.py:64` | per line |
| 33 | Match | Group-and-sum per line (one PO line over many vendor rows reconciles) | `matching.py:63` | per set |
| 34 | Match | Line-no normalization (`01`â†’`1`), alphanumeric forms survive | `matching.py:41` | per set |
| 35 | Match | rapidfuzz description fallback, only for numberless rows, threshold from config | `matching.py:107` | per set |
| 36 | Match | DN and SI pools compared independently against PO | `reconciliation.py:44` | per set |
| 37 | Match | Orphan vendor line = identity failure (priority 1) not a qty diff | `matching.py:126` | per set |
| 38 | Recon | 3-side presence gate â€” missing side pends, never merges | `reconciliation.py:199` | per set |
| 39 | Recon | Multi-PO-document guard (configurable) | `reconciliation.py:231` | per set |
| 40 | Recon | PO-reference drift detection | `reconciliation.py:259` | per set |
| 41 | Recon | Non-positive / unreadable quantity guard | `reconciliation.py:329` | per set |
| 42 | Recon | Partial fulfilment vs real disagreement discrimination | `reconciliation.py:380` | per set |
| 43 | Recon | Plain-language `reconcile_reason` persisted for the dashboard (11 codes) | `reconciliation.py:76` | per set |
| 44 | Recon | Same comparison function drives engine and UI â€” verdict matches screen | `reconciliation.py:25` | â€” |
| 45 | Recon | Phase 1 (touched sets) + Phase 2 (full open-set sweep) | `flows/sync.py:336` | per sync |
| 46 | Customs | Toggle gate: requires BOTH CUSTOMS and SHIPPING when on | `customs.py:12` | per set |
| 47 | Customs | Toggle recomputes `customs_doc_count` and status transitions | `customs.py:34` | operator |
| 48 | Merge | Configurable packet order (default SIâ†’DNâ†’POâ†’SHIPPINGâ†’CUSTOMS) | `merge.py:62` | per merge |
| 49 | Merge | Filename from SI's own number only; PO number fallback is flagged | `merge.py:101` | per merge |
| 50 | Merge | Refuses to overwrite another set's packet â†’ quarantine | `merge.py:128` | per merge |
| 51 | Merge | Refuses zero-line-item auto-merge (no numeric evidence) | `merge.py:223` | per merge |
| 52 | Merge | Immutability once merged (first completion wins) | `merge.py:195` | per merge |
| 53 | Merge | Missing stored PDF aborts the merge rather than writing a partial packet | `merge.py:163` | per merge |
| 54 | Force | Force Merge â€” bypasses reconcile + customs, requires justification â‰¥20 chars, audits customs count | `merge.py:260` | operator |
| 55 | Quarantine | Copy (not move) into `quarantine/<PO>/` â€” stored copies survive | `quarantine.py:168` | on failure |
| 56 | Quarantine | Self-describing `QUARANTINE.txt` with per-line PO/DN/SI table | `quarantine.py:77` | on failure |
| 57 | Quarantine | Windows-safe folder names (reserved device names, MAX_PATH) | `quarantine.py:43` | on failure |
| 58 | Quarantine | Content fingerprint folder (SQLite reuses ids of deleted rows) | `quarantine.py:28` | on failure |
| 59 | Quarantine | Failed-document quarantine + input copy removal (stops nightly error loop) | `quarantine.py:220` | on failure |
| 60 | Quarantine | Delete = DB rows only; files kept; audit row written first | `quarantine.py:297` | operator |
| 61 | Locking | Inter-process sync file lock; 409 / `skipped` when held | `sync_lock.py:84` | always |
| 62 | Locking | Stale-lock watchdog (sidecar > 3600 s = dead holder) | `sync_lock.py:63` | always |
| 63 | Locking | Per-PO-Set action lock in DB; second action â†’ HTTP 409 | `locking.py:35` | per action |
| 64 | Locking | Action-scoped release (never clears another action's lock) | `locking.py:67` | per action |
| 65 | Locking | Auto-release of stale locks on every read view | `po_sets.py:112` | on view |
| 66 | Audit | `audit_log` with action, JSON detail, justification, source; `ON DELETE SET NULL` | `models.py:161` | on action |
| 67 | API | FastAPI app, config validated at startup (fails fast, no degraded run) | `main.py:58` | boot |
| 68 | API | All handlers sync `def` â†’ threadpool (no blocking I/O in `async`) | `po_sets.py:3` | â€” |
| 69 | API | `GET /health` reports input folder, DB, Prefect pool | `main.py:81` | ops |
| 70 | API | `POST /sync` accepts no config path (no LAN path redirect) | `routes/sync.py:38` | operator |
| 71 | API | Upload: bounded read, PDF magic sniff, page count, filename sanitizing, extension allowlist | `routes/dashboard.py:398` | operator |
| 72 | API | Upload of a file owned by another PO Set â†’ explicit 409 naming the owner | `routes/dashboard.py:463` | operator |
| 73 | API | Idempotent re-upload reported honestly, not as a silent success | `routes/dashboard.py:519` | operator |
| 74 | UI | Dashboard: 5 KPI cards, 6 status tabs, instant client-side search | `templates/dashboard.html` | â€” |
| 75 | UI | HTMX live table refresh (2 s), buttons auto-disabled while locked | `_dashboard_table.html` | â€” |
| 76 | UI | PO Set detail: 3-way per-line matrix (PO / Î£DN / Î£SI) + âœ…/âŒ verdict | `routes/dashboard.py:306` | â€” |
| 77 | UI | Slide-over PDF preview drawer + merged-PDF download | `routes/dashboard.py:359` | â€” |
| 78 | UI | Unclassified holding area: UNKNOWN + permanently-failed docs, counted separately | `routes/dashboard.py:648` | â€” |
| 79 | UI | Hand-reclassify a doc's type + PO number (allowlist: PO/DN/SI/CUSTOMS/SHIPPING only) | `routes/dashboard.py:685` | operator |
| 80 | UI | Quarantine page with delete + justification modal | `templates/quarantine.html` | operator |
| 81 | UI | Read-only audit log page | `routes/dashboard.py:548` | â€” |
| 82 | UI | Isolated manual PDF merger (no DB, no PO association, user picks order + filename) | `routes/manual_merger.py` | operator |
| 83 | Ops | Prefect midnight cron deployment registered by script | `scripts/deploy_prefect.py` | deploy |
| 84 | Ops | Rotating file logger (10 MB Ã— 5) | `main.py:39` | boot |
| 85 | Ops | SQLite WAL + `foreign_keys=ON` + engine cache per DB path | `database.py:26` | â€” |
| 86 | Ops | Fail-fast config validation; all env-specific values in `config.yaml`; dirs auto-created | `core/config.py:145` | boot |
| 87 | Ops | Alembic migrations (12 revisions) | `alembic/versions/` | deploy |
| 88 | Tooling | Test/lint/audit stack declared in `pyproject.toml` — pytest, pytest-asyncio, hypothesis, ruff, `pip-audit`, `ty`; coverage measured with `--cov-fail-under=0` | `pyproject.toml` | CI |

---

## 6. HTTP surface

| Method | Path | Purpose | Locked |
|---|---|---|---|
| GET | `/` | 302 â†’ `/dashboard` | â€” |
| GET | `/health` | service health + config paths | â€” |
| GET | `/dashboard` | full dashboard (HTMX header â†’ table fragment) | â€” |
| GET | `/dashboard/table` | HTMX table fragment (2 s poll) | â€” |
| GET | `/po_sets` | JSON list with `is_locked` / `htmx_disabled` | â€” |
| GET | `/po_sets/{id}` | JSON detail with lock state | â€” |
| GET | `/po_sets/{id}/detail` | HTMX fragment (2 s poll) | â€” |
| GET | `/po_sets/{id}/view` | full detail page: 3-way matrix + flags | â€” |
| GET | `/po_sets/{id}/merged_pdf` | download merged packet | â€” |
| GET | `/documents/{id}/preview` | stream stored PDF | â€” |
| POST | `/sync` | trigger sync â€” **409** if already running | file lock |
| GET | `/sync/status` | `{"running": bool}` for HTMX button disable | â€” |
| POST | `/po_sets/{id}/force_merge` | operator override, writes audit | âœ… 409 |
| POST | `/po_sets/{id}/toggle_customs` | flip customs gate | âœ… 409 |
| POST | `/po_sets/{id}/redo_extract` | reset attempt count, re-run VLM, re-reconcile | âœ… 409 |
| POST | `/po_sets/{id}/redo_match` | re-reconcile only (no VLM cost) | âœ… 409 |
| POST | `/po_sets/{id}/merge` | re-evaluate gates + merge if eligible | âœ… 409 |
| DELETE | `/po_sets/{id}/quarantine` | delete quarantined rows, keep files, audit | âœ… 409 |
| POST | `/po_sets/{id}/upload` | upload CUSTOMS/SHIPPING PDF | âœ… 409 |
| GET | `/unclassified` | UNKNOWN + permanently-failed holding area | â€” |
| POST | `/unclassified/{id}/reclassify` | hand-assign type + PO number | â€” |
| GET | `/quarantine` | quarantined sets page | â€” |
| GET | `/quarantine/table` | HTMX fragment (3 s poll) | â€” |
| GET | `/audit` | read-only audit log | â€” |
| GET | `/manual/merger` | isolated manual merger UI | â€” |
| POST | `/manual/merge` | merge uploads in chosen order, no DB | â€” |

---

## 7. Configuration surface (`config.yaml`)

| Section | Key | Default | Effect |
|---|---|---|---|
| `paths` | `input_folder` | `./data/input` | scanned for PDFs; cleared only after merge |
| | `output_folder` | `./data/output` | merged packets |
| | `quarantine_folder` | `./data/quarantine` | failed sets + `_documents/` |
| | `stored_documents_folder` | `./data/stored` | `<sha256>.pdf` â€” never deleted |
| | `database_path` | `./data/aam_merger.db` | SQLite WAL (host-local only) |
| | `combined_folder` | `./data/combined` | parked split parents |
| | `log_folder` | `./data/logs` | `aam_merger.log` |
| `server` | `host` / `port` | `0.0.0.0` / `8000` | LAN bind |
| `vlm` | `model` | `openai/gpt-6-luna` | OpenRouter model id |
| | `request_timeout_seconds` | `60` | API timeout |
| | `api_key_env_var` | `OPENROUTER_API_KEY` | env var name, fail-closed |
| `extraction` | `max_retries` | `3` | Prefect task retries |
| | `retry_backoff_seconds` | `[2, 5, 15]` | backoff schedule |
| `matching` | `fuzzy_description_threshold` | `85` | description fallback only |
| | `locale` | `en_IN` | babel quantity parsing (validated) |
| `merge` | `legal_order` | `["SI","DN","PO","SHIPPING","CUSTOMS"]` | packet page order (validated: no dupes, no unknowns) |
| `ingestion` | `stability_poll_interval_seconds` | `2` | size poll gap |
| | `stability_poll_count` | `2` | size samples |
| `prefect` | `work_pool_name` | `aam-merger-process-pool` | Prefect pool |
| | `max_concurrent_extraction_tasks` | `3` | headroom (tasks run sequentially in practice) |
| `concurrency` | `po_set_lock_timeout_seconds` | `300` | stale lock threshold |
| `reconciliation` | `single_po_document` | `true` | >1 PO in a set â†’ quarantine |
| `logging` | `level` / `max_file_size_mb` / `backup_count` | `INFO` / `10` / `5` | rotating handler |

---

## 8. Explicitly absent by design

Removed from the data model and the matcher, with reasons recorded in
`models.py:1` and `matching.py:19`:

- `line_items.part_no` â€” dropped in migration `c1a2b3d4e5f6`
- `line_items.line_type` â€” dropped in the same migration
- UOM / unit column â€” never extracted, never stored, never reconciled
- SKU rescue in matching (`enable_sku_rescue`) â€” removed as dead config
- `sanity_description_threshold`, `fuzzy_margin` â€” removed as dead config
- Price as a matching input â€” price is stored for the packet only
- Positional / row-order matching
- ERP step-10 line-number mapping
- Description-conflict checking (description is only a fallback key)

**Quantities are the sole reconciliation signal.**

---
---

# PART II â€” PROGRAM LOGIC, CONDITION BY CONDITION

> Every branch, guard and default below is transcribed from the `.py` source.
> Nothing here is inferred from tests or documentation.

---

## 9. Configuration (`core/config.py`)

### 9.1 `load_config(path=None)` â€” resolution and fail-fast

| Step | Condition | Behaviour |
|---|---|---|
| 1 | `path` given | use it |
| 1 | else `os.getenv("AAM_CONFIG_PATH")` set | use that |
| 1 | else | use literal `"config.yaml"` |
| 2 | resolved path **does not exist** | raise `FileNotFoundError` with instructions to copy the example. **Never** silently falls back to `config.example.yaml` |
| 3 | file exists | `yaml.safe_load(read_text("utf-8")) or {}` â€” an empty file becomes `{}`, not `None` |
| 4 | â€” | `AppConfig.model_validate(data)` â€” any schema violation raises `ValidationError` here, at load time |
| 5 | `os.getenv(cfg.vlm.api_key_env_var)` empty | log WARNING only; **does not raise**. Extraction is what fails closed, at call time |

### 9.2 `AppConfig.validate_paths` (field validator, mode=after)

For each of `input_folder`, `output_folder`, `quarantine_folder`,
`stored_documents_folder`, `combined_folder`, `log_folder`:

- if the `Path` does not exist â†’ `mkdir(parents=True, exist_ok=True)` and log `Initialized required path: %s`

Then, separately: `database_path.parent` â†’ `mkdir(parents=True, exist_ok=True)`.

**Effect:** every configured directory is guaranteed to exist before any code runs.

### 9.3 Field-level validation constraints

| Model | Field | Constraint | Failure message |
|---|---|---|---|
| `ExtractionConfig` | `max_retries` | `ge=1, le=5` | pydantic |
| `MatchingConfig` | `fuzzy_description_threshold` | `ge=0, le=100` | pydantic |
| `MatchingConfig` | `locale` | `babel.Locale.parse(v)` must succeed | `Unsupported babel locale: {v!r}` |
| `MergeConfig` | `legal_order` | must be non-empty | `merge.legal_order must not be empty` |
| | | every element âˆˆ `{PO, DN, SI, CUSTOMS, SHIPPING, UNKNOWN}` | `Unknown doc types in merge.legal_order: [...]` |
| | | no duplicates | `Duplicate doc types in merge.legal_order: [...]` |
| `PrefectConfig` | `max_concurrent_extraction_tasks` | `ge=1, le=10` | pydantic |
| `LoggingConfig` | `level` | `Literal["DEBUG","INFO","WARNING","ERROR"]` | pydantic |
| `AppConfig` | whole model | `extra="ignore"`, `env_file=".env"` | unknown YAML keys are silently ignored |

`VLMConfig` has **no `provider` field** â€” it was removed; nothing read it.

---

## 10. Database engine (`core/database.py`)

```
get_engine(cfg):
  db_path = Path(cfg.paths.database_path).resolve()
  key     = db_path.as_posix()          # cross-platform cache key
  if key in _engine_cache: return cached
  db_path.parent.mkdir(parents=True, exist_ok=True)
  url    = f"sqlite:///{key}"
  engine = create_engine(url, connect_args={"check_same_thread": False})
  on every new connection:
      PRAGMA journal_mode=WAL;      # concurrent read while writing
      PRAGMA synchronous=NORMAL;    # durability/throughput trade
      PRAGMA foreign_keys=ON;       # FKs enforced (audit SET NULL depends on this)
  _engine_cache[key] = engine
```

**Conditions:** one engine per resolved DB path, process-wide. `check_same_thread=False`
is required because FastAPI `def` handlers run in a threadpool.

---

## 11. Sync file lock (`services/sync_lock.py`)

Constants: `SYNC_STALE_SECONDS = 3600`, lock name `.sync.lock`, sidecar `.sync.started`.

**Lock location** = `Path(cfg.paths.database_path).parent.resolve()` â€” the DB
directory, which is host-local by design.

### 11.1 `get_sync_lock(cfg_path=None, ensure_dirs=True)`

- `ensure_dirs=True` â†’ `db_dir.mkdir(parents=True, exist_ok=True)`
- returns `FileLock(lock_file, timeout=0, thread_local=False)`
- **`thread_local=False` is load-bearing**: the same lock instance is acquired in
  the request thread and released in the `_run_sync` daemon thread. With the
  default thread-local state, cross-thread `release()` is a silent no-op.

### 11.2 `_break_if_stale(lock)`

| Condition | Action |
|---|---|
| sidecar `stat()` raises `OSError` | **return** (no sidecar â‡’ never broken â€” fail-closed) |
| `age = time.time() - sidecar.mtime` â‰¤ 3600 | return |
| `age > 3600` | log WARNING, `unlink(lock_file)`, `unlink(sidecar)` (both `suppress(OSError)`) |

**Documented residual race:** if the original holder is genuinely still alive past
3600 s, both proceed. Accepted trade-off for a single-host LAN tool.

### 11.3 `acquire_sync_lock(cfg_path=None)`

1. build lock
2. `_break_if_stale(lock)`
3. `lock.acquire(timeout=0)`
4. `Timeout` â†’ **return `None`** (caller â†’ HTTP 409 or `{"status":"skipped"}`)
5. success â†’ `suppress(OSError)` write `str(time.time())` into the sidecar

Sidecar is written **only after** successful acquisition, so a crash between
acquire and sidecar-write leaves a lock with **no** sidecar â€” which is never stale-broken.

### 11.4 `release_sync_lock(lock)`

- `suppress(Exception)` â†’ `lock.release()`
- `suppress(OSError)` â†’ `unlink(sidecar)`

### 11.5 `_is_sync_running(cfg_path=None)` â€” read-only probe

| Step | Condition | Result |
|---|---|---|
| 1 | lock file's parent dir does not exist | `False` (no holder can exist) |
| 2 | `_break_if_stale(lock)` | â€” |
| 3 | `lock.is_locked` | `True` |
| 4 | `acquire(timeout=0)` raises `Timeout` | `True` |
| 5 | `acquire` raises any other exception | `False` |
| 6 | acquire succeeded | `suppress` release, `False` |

---

## 12. Per-PO-Set DB lock (`services/locking.py`)

### 12.1 `is_locked(ps, cfg)`

| Condition | Result |
|---|---|
| `ps.locked_by_action is None` | `False` |
| `ps.locked_at is None` â†’ fall back to `ps.updated_at` | â€” |
| still `None` | `True` (unknown age â‡’ assume locked) |
| `locked_at.tzinfo is None` | `replace(tzinfo=UTC)` (naiveâ†’UTC normalisation) |
| `(now - locked_at).total_seconds() <= cfg.concurrency.po_set_lock_timeout_seconds` | `True` |
| otherwise | `False` (stale) |

### 12.2 `acquire_lock(ps, action, session, cfg)` â€” atomic

```sql
UPDATE po_sets
   SET locked_by_action = :action, locked_at = :now
 WHERE id = :ps.id
   AND ( locked_by_action IS NULL
      OR locked_at        IS NULL
      OR locked_at       <= :now - timeout )
```

- `rowcount > 0` â†’ `session.refresh(ps)`, return `True`
- `rowcount == 0` â†’ `session.refresh(ps)`, return `False` (caller raises 409)

`synchronize_session=False` â€” the UPDATE is the authority, the ORM object is
refreshed afterwards rather than tracked.

### 12.3 `release_lock(ps, session, action=None)`

- `action is None` â†’ `WHERE id = :id` (unconditional clear)
- `action` given â†’ `WHERE id = :id AND locked_by_action = :action` â€” **action-scoped**,
  so one action can never clear another in-flight action's lock
- sets `locked_by_action = NULL, locked_at = NULL`

---

## 13. Ingestion (`services/ingestion.py`)

### 13.1 `find_input_pdfs(input_folder)`

```
folder = Path(input_folder)
if not folder.exists(): return []
return sorted(f for f in folder.iterdir() if f.is_file() and f.suffix.lower() == ".pdf")
```

**Single pass, one pass only.** The docstring records why: `pathlib.glob` is
case-insensitive on Windows, so `glob("*.pdf") + glob("*.PDF")` returns every
file twice â€” and because extraction runs after ingestion, every document was
sent to the VLM a second time on every sync, doubling API cost. Reproduces only
on Windows, so Linux development hides it.

### 13.2 `is_file_stable(p, interval, count)`

| Step | Condition | Result |
|---|---|---|
| 1 | `not p.exists()` | `False` |
| 2 | loop `count` times: | â€” |
| | file vanished mid-poll | `False` |
| | else append `p.stat().st_size` | â€” |
| | | `time.sleep(interval)` |
| 3 | `len(set(sizes)) == 1 and sizes[0] >= 0` | `True` |
| 3 | otherwise | `False` (file is still growing â€” skip this run) |

### 13.3 `ingest_file(src, cfg)`

```
data = src.read_bytes()
h    = sha256(data).hexdigest()

EXISTING = Document where sha256_hash == h (first)
  if EXISTING:
      stored = Path(EXISTING.stored_path)
      if not stored.exists():
          try:  stored.parent.mkdir(...); stored.write_bytes(data)
          except Exception: pass          # self-heal is best-effort
      return EXISTING                      # NO new row, NO re-extraction marker

stored = stored_documents_folder / f"{h}{src.suffix}"
stored.parent.mkdir(parents=True, exist_ok=True)
stored.write_bytes(data)                   # permanent copy

# --- split-child linkage (best effort, can never fail an ingest) ---
try:
    sha16, _ = parse_child_filename(src.name)     # raises ValueError if not a child name
    parent = Document where is_split_parent IS TRUE
                      and sha256_hash LIKE sha16 + '%'
             order by id ASC, first
    parent_id = parent.id if parent else None
except ValueError:  parent_id = None               # ordinary filename
except Exception:   parent_id = None               # linkage must never fail an ingest

Document(sha256_hash=h, original_filename=src.name, stored_path=str(stored),
         doc_type=UNKNOWN, extraction_status=pending, parent_document_id=parent_id)
commit; refresh; return
```

Conditions worth noting:
- `sha256_hash` is **unique** â€” a duplicate file can never create a second row
- a re-ingested duplicate returns the **existing** row, so extraction is retried
  only if that row's status is still `pending`
- the original file is **left in `input/`**; input is cleared only after merge

### 13.4 `delete_input_files(po_set, input_folder)`

| Guard | Condition |
|---|---|
| 1 | `po_set.status == merged` **AND** `po_set.merged_output_path` â€” else return `[]` |
| 2 | `in_dir.exists()` â€” else return `[]` |
| 3 | build `valid_hashes` and `valid_names` from documents where `extraction_status == valid` |
| 4 | for each name: `in_dir/name` exists â†’ `unlink(missing_ok=True)`, append to `deleted` |
| 5 | for each remaining input PDF: re-hash its bytes; if hash âˆˆ `valid_hashes` â†’ `unlink` |
| â€” | every `unlink`/read is wrapped in `try/except: pass` |

**`stored_path` copies are never deleted.** Step 5 catches renamed duplicates that
step 4's filename match would miss.

---

## 14. Extraction (`services/extraction.py`)

### 14.1 `is_manual_only(doc_type)`

`True` for `"CUSTOMS"` and `"SHIPPING"` â€” these are hand-attached and **never sent
to the VLM**, because they are not quantity evidence.

### 14.2 `_parse_scaled_int(val, locale)`

| Condition | Result |
|---|---|
| `val is None` or `str(val).strip() == ""` | `0` |
| `parse_quantity_scaled` raises `ValueError` | `0` |
| any other exception | `0` |

`0` is not a silent zero â€” it routes the PO Set to `non_positive_quantity`
quarantine at `reconciliation.py:329`.

### 14.3 `_call_vlm(stored_path, doc_type, cfg)` â€” secret resolution order

1. `load_dotenv(override=False)`
2. if `cfg.paths.database_path` set â†’ `load_dotenv(db_dir/".env", override=False)`
3. `api_key_env = cfg.vlm.api_key_env_var or "OPENROUTER_API_KEY"`
4. `api_key = os.getenv(api_key_env) or os.getenv("OPENROUTER_API_KEY")`
5. if still empty â†’ loop `[Path(".env"), repo_root/".env"]` with `dotenv_values`,
   try `api_key_env` then `OPENROUTER_API_KEY`, break on first hit
6. still empty â†’ **`raise RuntimeError(f"{api_key_env} not configured â€¦ (fail-closed)")`**

Then:

| Condition | Behaviour |
|---|---|
| `cfg.vlm.model` falsy | `RuntimeError("vlm.model not configured in config.yaml (fail-closed)")` |
| `stored_path` file missing | `FileNotFoundError(f"stored_path not found: {stored_path}")` |

**The call:**
- `pdf_bytes = pdf_path.read_bytes()`; `b64 = base64.b64encode(pdf_bytes).decode()`
- `instructor.from_openai(OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key, timeout=timeout))`
- `client.chat.completions.create(model=model, response_model=_VLMPageExtraction, messages=[system, user], max_retries=0)`
  - `max_retries=0` â€” retries are owned by the Prefect task envelope
  - user content is `[{"type":"text", "text":_PAGE_PROMPT}, {"type":"file", "file":{filename, file_data:"data:application/pdf;base64,â€¦"}}]`
  - **native PDF, no rasterization**

**Returned dict** reads only fields the schema declares â€” anything else raises
`AttributeError` and fails the whole extraction:

```
document_type, has_po_section, has_dn_section, has_si_section, page_count,
components[{doc_type, document_type, page_start, page_end}],
document_number, po_no_raw (= po_reference or document_number), po_reference,
po_reference_ambiguous, vendor_name,
line_items[{line_item_no, description, quantity, unit_price, dn_no}]
```

### 14.4 `_VLMPageExtraction` schema

| Field | Type | Default |
|---|---|---|
| `document_type` | `Literal["PO","SI","DN","COMBINED","SKIP","UNKNOWN"]` | required |
| `has_po_section` / `has_dn_section` / `has_si_section` | `bool` | `False` |
| `page_count` | `int â‰¥ 0` | `0` |
| `components` | `list[_VLMComponent]` | `[]` |
| `document_number` | `str \| None` | `None` |
| `po_reference` | `str \| None` | `None` |
| `po_reference_ambiguous` | `bool` | `False` |
| `vendor_name` | `str \| None` | `None` |
| `line_items` | `list[_VLMLineItem]` | `[]` |

`_VLMComponent.doc_type` has `alias="document_type"` with
`model_config = {"populate_by_name": True}`, `page_start`/`page_end` both `ge=1`.

### 14.5 `extract_document(doc_id, cfg)` â€” full branch order

```
doc = Document.get(doc_id)
if doc is None: raise ValueError(f"document {doc_id} not found")

dtype = doc.doc_type
if is_manual_only(dtype): RETURN doc unchanged          # CUSTOMS/SHIPPING skip the VLM

if (doc.extraction_attempt_count or 0) >= 3:             # ATTEMPT CAP
    doc.extraction_status = failed
    commit; refresh
    log ERROR "â€¦attempt cap %d reached, no further attemptsâ€¦"
    RETURN doc                                          # returns, does NOT raise

doc.extraction_attempt_count += 1
try:
    result = _call_vlm(doc.stored_path, dtype, cfg)
    doc.raw_extraction_json = json.dumps(result, â€¦)      # except â†’ None

    # dev-only file dump â€” written ONLY if a raw dir already exists,
    # or DEBUG=1 / AAM_SAVE_RAW=1 and ./data exists
    if (raw_dir.exists() or alt_raw_dir.exists() or data/raw_extractions.exists()):
        write f"{sha256[:8]}_{safe_stem}.json"
    elif Path("data").exists() and (os.getenv("DEBUG")=="1" or os.getenv("AAM_SAVE_RAW")=="1"):
        write data/raw_extractions/f"{sha256[:8]}_{safe_stem}.json"
    # every inner step is try/except: pass â€” the dump can never fail an extraction

    if result["document_type"] == "COMBINED":
        return _handle_combined(doc, s, result, cfg)     # â† separate branch

    doc.extraction_status = valid

    vtype = result["document_type"]
    if vtype in ("PO","DN","SI","COMBINED","SKIP","UNKNOWN"):
        with contextlib.suppress(Exception):
            doc.doc_type = DocType("UNKNOWN" if vtype == "SKIP" else vtype)
        # runs REGARDLESS of po_no_raw â€” otherwise a DN/SI with a missed PO
        # reference would stay UNKNOWN forever

    if result.get("po_no_raw"):
        doc.po_no_raw = result["po_no_raw"]
        doc.po_no_normalized = normalize_po_no(result["po_no_raw"])

    if result.get("po_reference_ambiguous"):
        doc.po_reference_ambiguous = True

    if document_type == "SI" and document_number:
        doc.si_no = document_number; doc.invoice_no = document_number
    if document_type == "DN" and document_number:
        doc.dn_no = document_number
    if document_type == "PO" and document_number and not doc.po_no_raw:
        doc.po_no_raw = document_number
        doc.po_no_normalized = normalize_po_no(document_number)

    # line items â€” replaced wholesale for retry idempotency
    for li in list(doc.line_items): s.delete(li)
    s.flush()
    locale = cfg.matching.locale or "en_IN"
    for li in result.get("line_items") or []:
        if li.quantity is None or li.description is None: CONTINUE   # dropped
        s.add(LineItem(
            document_id   = doc.id,
            line_item_no  = str(li.line_item_no) if li.line_item_no else None,  # verbatim
            description   = str(li.description),
            quantity      = _parse_scaled_int(li.quantity, locale),
            unit_price    = _parse_scaled_int(li.unit_price, locale),
            dn_no         = str(li.dn_no) if li.dn_no else None,
        ))
    commit; refresh; return doc

except Exception as e:
    doc.extraction_status = failed
    commit; refresh
    log WARNING "VLM extraction failed for doc %s (attempt %s): %s"
    raise e                     # â† re-raised so the Prefect retry envelope runs
```

### 14.6 `_handle_combined(doc, s, result, cfg)` â€” three-way branch

**Loop guard 1 â€” child re-reads as COMBINED** (`doc.parent_document_id is not None`):
1. `doc.extraction_status = failed`; commit; refresh
2. `quarantine_document(doc.parent_document_id, cfg, reason="child_recombined: â€¦")` inside `try/except` log-warning
3. return `doc`

A COMBINED child is otherwise invisible, because Layer 2 names no document types â€”
so the **parent** is what gets quarantined.

**Loop guard 2 â€” already split** (`doc.is_split_parent` **and** `doc.split_completed_at`):
1. `doc_type = COMBINED`; `extraction_status = valid`; commit; refresh
2. return â€” **idempotent, never duplicates children**

**Normal parent â€” sterilise, then cut:**

```
doc_type=COMBINED; extraction_status=valid; is_split_parent=True
po_no_raw=None; po_no_normalized=None; po_reference_ambiguous=False
dn_no=None; si_no=None; invoice_no=None; po_set_id=None
for li in list(doc.line_items): s.delete(li)
s.flush()
```

Then `split_combined(doc.stored_path, result, cfg)`:

| Outcome | Behaviour |
|---|---|
| `SplitError` | `extraction_status=failed`; `split_completed_at=None`; commit; refresh; `quarantine_document(doc.id, cfg, reason=f"split_failed:{e.reason}: {e}")` inside `try/except`; log ERROR; return `doc` â€” **does not raise**, so Prefect does not burn retries on a deterministic verdict |
| success | move the parent's **input copy** into `combined_folder` (`shutil.move`, or `unlink` if the destination already exists) inside `try/except`; `split_completed_at = now(UTC)`; commit; refresh; return |

The parent is never re-scanned because only `input_folder` is scanned. Children
wait in `input/` for the next sync.

---

## 15. Layer-1 split (`services/splitting.py`)

`REASONS = ("page_count_mismatch", "page_range_invalid", "page_range_overlap", "pages_unaccounted")`

`SplitError(reason, message)` raises `ValueError(f"unknown split reason: {reason!r}")`
if constructed with a reason outside that tuple.

### 15.1 `parse_child_filename(name)`

```
m = ^([0-9a-fA-F]{16})_p(\d+)\.pdf$   (IGNORECASE)
no match  â†’ ValueError
num < 1   â†’ ValueError
return (sha16.lower(), num)
```

Strict by design: an employee hand-dropping a lookalike name must not attach to
someone else's parent.

### 15.2 `split_combined(parent_path, response, cfg)`

```
parent missing                â†’ SplitError("page_range_invalid", f"parent not found: {parent}")
data      = parent.read_bytes()
real_pages = len(PdfReader(str(parent)).pages)

response["page_count"] != real_pages
    â†’ SplitError("page_count_mismatch", f"VLM claimed page_count={â€¦}, pypdf ground truth={â€¦}")

components = response["components"]
not components
    â†’ SplitError("pages_unaccounted", "no components claimed; all pages unaccounted")

# per-range shape
for c in components:
    try:    start, end = int(c["page_start"]), int(c["page_end"])
    except: â†’ SplitError("page_range_invalid", f"non-integer range in {c!r}: {e}")
    if start < 1 or end > real_pages or start > end:
            â†’ SplitError("page_range_invalid", f"range {start}-{end} outside 1..{real_pages}: {c!r}")

# overlap
for every page in every range: if already seen
    â†’ SplitError("page_range_overlap", f"page {p} claimed twice: {c!r}")

# coverage
missing = [p for p in 1..real_pages if p not in seen]
if missing â†’ SplitError("pages_unaccounted", f"pages unclaimed: {missing}")

# cut
input_dir.mkdir(parents=True, exist_ok=True)
sha16 = sha256(data).hexdigest()[:16]
kept  = [c for c in components if str(c["doc_type"]).upper() != "SKIP"]
for i, c in enumerate(kept, 1):
    writer = PdfWriter()
    for p in range(int(page_start) - 1, int(page_end)):   # 0-based slice
        writer.add_page(reader.pages[p])
    write input_dir / f"{sha16}_p{i}.pdf"
return list of child paths
```

**Conditions:** numbering is dense over *kept* components, so a `SKIP` in the
middle leaves no gap on disk. Names are deterministic, so re-running overwrites
the same files and never duplicates.

---

## 16. Quantity sanitizer (`services/sanitizer.py`)

`MAX_DECIMAL_PLACES = 2`

| Order | Condition | Result |
|---|---|---|
| 1 | `raw is None` or `str(raw).strip()` empty | `ValueError("Quantity string is empty.")` |
| 2 | `parse_decimal(cleaned, locale, strict=True)` raises `NumberFormatError`/`ValueError` | `ValueError(f"Invalid numeric format: {raw!r}")` |
| 3 | `not val.is_finite()` | `ValueError("Quantity is not a finite number: â€¦")` â€” babel's strict parse still accepts `INF`/`NaN`; `NaN <= 0` is `False` and its exponent is a string, which would raise `TypeError` and escape the clean quarantine path |
| 4 | `val <= 0` | `ValueError("Quantity must be strictly positive: â€¦")` |
| 5 | `val.adjusted() > 12` | `ValueError("Quantity is implausibly large: â€¦")` â€” babel accepts `1e999999999`; `int(val*1000)` would try to materialise a billion-digit integer. Ceiling is 1e12 actual units |
| 6 | `d.normalize()` then significant decimals > 2 | `ValueError(f"Quantity has {n} significant decimals (max 2): â€¦")` |
| 7 | â€” | `return int(val * 1000)` |

**Trailing zeros are normalised, not rejected:** `12.45000000` â†’ 12.45 (2 dp, OK);
`5.000` â†’ 5 (0 dp, OK); `1.234` â†’ 3 dp â†’ rejected, because that is almost always a
European thousands separator meaning 1234, which `Ã—1000` would mis-scale.

**Only `ValueError` is ever raised** â€” no other exception type escapes.

---

## 17. Grouping (`services/grouping.py`)

### 17.1 `normalize_po_no(raw)`

| Step | Condition | Behaviour |
|---|---|---|
| 1 | `raw is None` | `""` |
| 2 | `not isinstance(raw, str)` | **`TypeError(f"PO number must be a string, got {type(raw).__name__}")`** â€” a number/Decimal is not a PO reference; stringifying it would create a plausible-looking but wrong key |
| 3 | `s = raw.strip()`; empty | `""` |
| 4 | `"," in s` | `s = s.rsplit(",", 1)[0]` â€” strips the SAP revision tail: `"PO, Rev # 161538,0"` â†’ `"PO, Rev # 161538"` |
| 5 | tokenise | `re.split(r"[^A-Za-z0-9]+", s)`, drop empties |
| 6 | **first** token | if upper âˆˆ `_LEADING_LABEL_WORDS {"PO","P.O.","P/0"}` or âˆˆ `_LABEL_WORDS` â†’ **drop**; else strip the first matching `_LEADING_PREFIXES` entry, longest-first `("PURCHASEORDER","PURCHASEORDERS","PONO","PO.NO","PO")`, only when `len(up) > len(prefix)` |
| 7 | **later** tokens | drop only if upper âˆˆ `_LABEL_WORDS` |
| 8 | â€” | `"".join(kept).upper()` |

**Two load-bearing properties:** different spellings of one PO collapse to one key
(`161538`, `PO 161538`, `PO, Rev # 161538,0` â†’ `161538`); genuinely different POs
never collide.

**Why `PO` is dropped only in leading position:** the PO prints `D7264-PO-186000-013-01`
while the DN and SI print `D7264-PO186000-013-01-`. Dropping `PO` anywhere but the
front breaks one spelling or the other. Bare `P` is deliberately **absent** from
the prefix list because `P106420232` is a real McDermott code, not a label.

`_LABEL_WORDS = {NO, NO., NUM, NUMBER, NBR, REV, REVISION, REF, REFERENCE, PURCHASE,
ORDER, ORD, BUYER, BUYERS, ORDERS, DOC, DOCUMENT, REFNO, PURCHASEORDER,
PURCHASEORDERS, AND, THE, OF}`

### 17.2 `get_or_create_po_set(po_no, cfg, create=True)`

```
norm = normalize_po_no(po_no)
ps = POSet where po_no_normalized == norm AND status != merged
     order by id ASC, first
if ps is not None:  return ps          # attach to the open set
if not create:      return None        # BLOCKER-5: DN/SI/UNKNOWN never mint
ps = POSet(po_no_normalized=norm, status=pending); commit; refresh; return
```

`status != merged` is what makes `grouping_post_merge_starts_new_set` work: a
merged set is closed, so a later document with the same PO key opens a **new** set.

### 17.3 `attach_unattached_to_open_sets(cfg)` â€” the attach-only sweep

Candidate set: `po_set_id IS NULL` **AND** `extraction_status == valid` **AND**
`po_no_normalized IS NOT NULL` **AND** `is_split_parent == False`.

For each candidate, in order:

| Order | Condition | Action |
|---|---|---|
| 1 | `doc.po_reference_ambiguous` is truthy | **skip, no attach.** More than one PO number is printed; attaching to any one would silently strand the others |
| 2 | an open set exists for `doc.po_no_normalized` (id ASC, first) | attach, add to `touched` |
| 3 | no open set | **skip â€” waits indefinitely.** A key with no anchor keeps waiting for more files (human decision 2026-09-05) |

**Never mints.**

### 17.4 `_anchor_from_po_line_ref(session, dn_no)`

```
if not dn_no: return (None, None)

po_doc_ids = distinct document_id from LineItem where LineItem.dn_no == dn_no
if not po_doc_ids: return (None, None)          # no PO names this DN number

anchor_rows = Document where id IN po_doc_ids
                      AND doc_type == PO
                      AND po_set_id IS NOT NULL
anchor_sets = {r.po_set_id}
if len(anchor_sets) != 1: return (None, None)   # AMBIGUITY REFUSED, not first-wins

return (the one set id, that PO's po_no_normalized)
```

**Why PO is the anchor:** only PO documents mint sets, so a PO in the database is
attached by construction. Anchoring on a sibling DN instead would be circular â€” a
DN is frequently unattached for exactly the reason this path exists.

**Why ambiguity is refused, not resolved:** a DN spanning two POs has no single
correct set, and guessing would attach a document to a transaction it may not
belong to.

### 17.5 `resolve_unattached_documents(cfg)` â€” three strategies

Candidate set: `po_set_id IS NULL` **AND** `extraction_status == valid` **AND**
`is_split_parent == False`.

For each candidate, first match wins:

| # | Strategy | Condition | Refusal condition |
|---|---|---|---|
| 1 | **DN number cross-reference** | another `Document` (id â‰  self) with `dn_no == doc.dn_no` and `po_set_id IS NOT NULL` | anchors resolving to **â‰  1** distinct set â†’ leave unattached (anchors across *distinct* sets are genuine ambiguity) |
| 2 | **PO per-line DN reference** | `_anchor_from_po_line_ref(session, doc.dn_no)` | returns `(None, None)` when no PO names it, or when PO anchors span two sets |
| 3 | **Sibling filename prefix** | `fn.replace("_","-").split("-pages-")[0].split("-")` has **â‰¥ 4** parts â†’ `prefix = "-".join(parts[:4])`; siblings = `Document where original_filename LIKE prefix + '%' AND id â‰  self AND po_set_id IS NOT NULL` | siblings resolving to **â‰  1** distinct set â†’ leave unattached |

On a match:
```
ps = POSet.get(matched_ps_id)
if ps and ps.status != merged:
    doc.po_set_id = matched_ps_id
    if not doc.po_no_normalized and matched_po_norm:
        doc.po_no_normalized = matched_po_norm     # inherit the PO key
    touched.add(matched_ps_id)
```

Order is by **evidential strength**: a number printed on the document beats a
number printed on a *sibling* document, which beats a filename resemblance.

---

## 18. Matching â€” the reconciliation rule (`services/matching.py`)

### 18.1 `_norm(s)`

```
if not s: return ""
s = s.lower()
s = re.sub(r"(\d)([A-Za-z])",  r"\1 \2", s)     # "10kg" â†’ "10 kg"
s = re.sub(r"([A-Za-z])(\d)",  r"\1 \2", s)
return s.strip()
```

### 18.2 `normalize_line_no(s)` â€” the comparison key

| Condition | Result |
|---|---|
| `s is None` | `""` |
| `t = str(s).strip()` empty | `""` |
| `t` does not match `^0*(\d.*)$` | `t` unchanged â€” alphanumeric forms (`1a`, `1-1`) survive intact |
| matches | inner `^(\d+)(.*)$` on the digit run â†’ `num = digits.lstrip("0") or "0"`; return `num + suffix` |

Examples: `"01"â†’"1"`, `" 001 "â†’"1"`, `"1-1"â†’"1-1"`, `"10"â†’"10"`, `"00"â†’"0"`.
Comparison is **as a string**, never as an integer, so form is preserved.

### 18.3 `group_by_line_no(po_lines, vendor_lines, desc_threshold=85)`

Returns `(po_totals, vendor_totals, orphans, failure_reason)`.

**PO side** â€” one pass, and it can abort the whole comparison:
```
for ln in po_lines:
    key = normalize_line_no(ln.line_item_no)
    if not key:
        return {}, {}, [], "po_line_missing_line_item_no"   # â† ABORT
    po_totals[key] = po_totals.get(key, 0) + int(ln.quantity or 0)
    po_desc.setdefault(key, _norm(ln.description or ""))
```

**Vendor side** â€” three-way branch per row:
```
for ln in vendor_lines:
    key = normalize_line_no(ln.line_item_no)
    if not key and desc_threshold:
        key = _best_desc_key(ln, po_desc, desc_threshold)   # FALLBACK ONLY
    if not key:
        orphans.append({**ln, "why": "no_line_number_and_no_description_match"})
    elif key not in po_totals:
        orphans.append({**ln, "why": "no_po_line_with_this_number"})
    else:
        vendor_totals[key] = vendor_totals.get(key, 0) + int(ln.quantity or 0)
```

The fallback is entered **only** when the row has no usable number, and it can
never override a real line number.

### 18.4 `_best_desc_key(vendor_line, po_desc, threshold)`

```
v_desc = _norm(vendor_line.description)
if not v_desc: return ""
best_key, best_score = "", 0.0
for key, p_desc in po_desc.items():
    if not p_desc: continue
    score = fuzz.token_sort_ratio(v_desc, p_desc)      # rapidfuzz
    if score > best_score: best_key, best_score = key, score
return best_key if best_score >= threshold else ""
```

Returns `""` when nothing clears the threshold â€” the caller then quarantines
rather than guessing.

### 18.5 `compare_aggregates(po_totals, vendor_totals, orphans)`

| Condition | Result |
|---|---|
| `orphans` non-empty | return **one entry per orphan** â€” `{line: o.line_item_no or o.description[:40], po_qty: None, vendor_qty: o.quantity, reason: o.why}`. Quantity differences are **not** reported at all, because that would misdescribe an identity problem as an arithmetic one |
| otherwise | for every PO key: `v = vendor_totals.get(key, 0)`; if `v != po_qty` â†’ `{line: key, po_qty, vendor_qty: v, reason: "quantity_mismatch"}` |

**A line the vendor never mentioned is `v = 0`, which differs from `po_qty > 0` â€”
so a short delivery is a mismatch at this layer.** Deciding whether that means
"awaiting delivery" or "failed" is the caller's job, because only the reconciler
knows the set's history.

**Never compares price, SKU, UOM, position, or description conflicts.**

---

## 19. Reconciliation (`services/reconciliation.py`)

### 19.1 `compare_po_set_lines(po_lines, dn_lines, si_lines, desc_threshold=85)`

```
po_totals, dn_totals, dn_orphans, po_fail = group_by_line_no(po_lines, dn_lines, thr)
_,          si_totals, si_orphans, _      = group_by_line_no(po_lines, si_lines, thr)

flags = []
for (label, orphans, totals) in (("DN", dn_orphans, dn_totals),
                                 ("SI", si_orphans, si_totals)):
    for d in compare_aggregates(po_totals, totals, orphans):
        flags.append({
            priority : 1 if d.po_qty is None else 2,      # identity before quantity
            type     : "identification" if d.po_qty is None else "quantity",
            pool     : label,
            line_item_no   : d.line,
            po_quantity    : d.po_qty,
            vendor_quantity: d.vendor_qty,
            reason   : d.reason,
        })
flags.sort(by priority)
```

The two pools are computed **independently** â€” the DN verdict and the SI verdict
are separate facts, and either can fail alone.

### 19.2 `REASON_TEXT` â€” the closed vocabulary

| Code | Dashboard sentence |
|---|---|
| `non_positive_quantity` | A quantity is zero, negative, or unreadable |
| `missing_po_document` | No purchase order document in this set yet |
| `missing_dn_document` | No delivery note in this set yet |
| `missing_si_document` | No invoice in this set yet |
| `partial_fulfillment` | Waiting on more deliveries or invoices |
| `unmatched_vendor_line` | A delivery or invoice line could not be matched to any PO line |
| `po_line_missing_line_item_no` | A PO line has no line number, so it cannot be compared |
| `multiple_po_documents` | This PO Set holds more than one PO document |
| `packet_naming_failed` | The merged packet could not be named unambiguously |
| `po_reference_mismatch` | A document references a different PO number |
| `quantity_mismatch` | PO, delivery, and invoice quantities do not agree |

### 19.3 `explain(reason, flags)`

```
qty  = [f for f in flags if f.type == "quantity"]
base = REASON_TEXT.get(reason or "", "")

if not base:
    # No explicit code. A `mismatched` set carries no reason code, and
    # defaulting it to "Awaiting further processing" told the reviewer to wait
    # on a set that will never resolve itself.
    base = REASON_TEXT["quantity_mismatch"] if qty else "Awaiting further processing"

if not qty: return base

parts = []
for f in qty:
    if f.po_quantity is None or f.vendor_quantity is None: continue
    verb = "delivered" if f.pool == "DN" else "invoiced"
    parts.append(f"{verb} {f.vendor_quantity/1000:g} of {f.po_quantity/1000:g}")
return base + ": " + "; ".join(parts) if parts else base
```

### 19.4 `reconcile_po_set(po_set_id, cfg)` and `_persist_reason`

`_persist_reason` never raises â€” a reconcile is not failed because a note could
not be saved:

| `result["status"]` | `reconcile_reason` written |
|---|---|
| `"merged"` | `"Fully reconciled â€” packet merged"`, plus the first `type=="naming"` flag's `message` when present |
| anything else | `explain(result["reason"], result["flags"])` |

### 19.5 `_reconcile_po_set_inner` â€” the full gate chain

```
GATE 0  ps is None                                    â†’ ValueError
GATE 0  ps.status == merged                           â†’ return {status:"merged", â€¦}   IMMUTABLE, no work done
        docs = list(ps.documents)
        po_docs / dn_docs / si_docs partitioned by doc_type

GATE 1  not po_docs â†’ status=pending, commit,
                      return {status:"pending", reason:"missing_po_document"}
GATE 1  not dn_docs â†’ status=pending, commit,
                      return {status:"pending", reason:"missing_dn_document"}
GATE 1  not si_docs â†’ status=pending, commit,
                      return {status:"pending", reason:"missing_si_document"}
        (each returns flags: [] â€” a missing side is unfulfilled demand, not evidence)

GATE 2  cfg.reconciliation.single_po_document is truthy AND len(po_docs) > 1
        names = sorted join of original_filename (or "?")
        detail = f"This PO Set holds {n} PO documents ({names}); only one PO is expected per PO Set"
        status=quarantined; commit
        quarantine_copy(ps.id, cfg, reason="multiple_po_documents", detail=detail)
        return {status:"quarantined", reason:"multiple_po_documents", detail,
                flags:[{priority:1, type:"identification", message:detail}]}

GATE 3  for d in docs where d.doc_type âˆˆ {PO,DN,SI} and d.po_no_normalized
            and d.po_no_normalized != ps.po_no_normalized:
            log WARNING with BOTH values (W-16: re-extraction overwrites
              doc.po_no_normalized from the VLM while po_set_id stays, so
              VLM PO-reference drift can false-positive here)
        status=quarantined; commit
        msg = f"PO reference mismatch: doc {filename} ({d.key}) != PO Set ({ps.key})"
        quarantine_copy(ps.id, cfg, reason="po_reference_mismatch", detail=msg)
        return {status:"quarantined", â€¦, flags:[identification msg]}

        build po_lines / dn_lines / si_lines from each doc's line_items
        all_lines = po_lines + dn_lines + si_lines

GATE 4  any(line.quantity <= 0 for line in all_lines)
        status=quarantined; commit
        quarantine_copy(ps.id, cfg, reason="non_positive_quantity")
        return {status:"quarantined", reason:"non_positive_quantity", flags: []}

        thr = cfg.matching.fuzzy_description_threshold
        comparison = compare_po_set_lines(po_lines, dn_lines, si_lines, thr)
        po_fail = comparison["po_fail"];  flags = comparison["flags"]

GATE 5  po_fail truthy
        status=quarantined; commit
        quarantine_copy(ps.id, cfg, reason=po_fail)
        return {status:"quarantined", reason:po_fail,
                flags:[{priority:1, type:"identification", message:REASON_TEXT[po_fail]}]}

GATE 6  flags non-empty:
          6a  any(f.type == "identification")
                status=quarantined; commit
                quarantine_copy(ps.id, cfg, reason="unmatched_vendor_line", flags=flags)
                return {status:"quarantined", reason:"unmatched_vendor_line", flags}
              (an orphan is unresolvable identity, not a shortfall â€” guessing
               which PO line it belongs to is refused)

          6b  qty_flags      = [f for f in flags if f.type == "quantity"]
              disagreement   = any((f.vendor_quantity or 0) > 0 for f in qty_flags)

              if not disagreement:
                  status=pending; commit
                  return {status:"pending", reason:"partial_fulfillment", flags}
                  (the vendor reported NOTHING for those lines)

              status=mismatched; commit
              return {status:"mismatched", flags}
              (both sides reported with different numbers â€” a real disagreement)

GATE 7  from app.services.customs import is_blocked
        ps.has_customs_toggle AND is_blocked(ps)
            status=blocked_customs; commit
            return {status:"blocked_customs", flags}

GATE 8  AUTO-MERGE
        prior_status = ps.status
        ps.status = pending; commit            # forward progress only
        merge_info = {}
        try:  merged_path = merge_po_set(po_set_id, cfg, info=merge_info)
        except MergeNamingError as e:
                status=quarantined; commit
                quarantine_copy(ps.id, cfg, reason="packet_naming_failed", detail=str(e))
                return {status:"quarantined", reason:"packet_naming_failed",
                        detail:str(e), flags:[identification msg]}

        s.refresh(ps)
        if merged_path is None:
                if ps.status != prior_status:      # W-8 restore
                    ps.status = prior_status; commit; refresh
                log WARNING "Auto-merge refused for PO Set %s â€” kept %s"

        if merged_path is not None and merge_info.get("invoice_no_missing"):
                flags.append({priority:3, type:"naming",
                    message: f"No invoice number was extracted; packet named "
                             f"'{merge_info["output_name"]}' from the PO number"})

        return {status: ps.status.value, po_set_id, merged_output_path, flags}
```

**Note on the `pending` swap before merge:** the set's status is temporarily
cleared so `merge_po_set`'s own `blocked` check (`mismatched / quarantined /
blocked_customs`) does not reject a set that has just proven itself. If the merge
then refuses, the prior status is restored (W-8).

---

## 20. Customs gate (`services/customs.py`)

### 20.1 `is_blocked(po_set)`

```
if not po_set.has_customs_toggle:  return False

doc_types = {normalised d.doc_type for d in po_set.documents}
has_customs  = DocType.CUSTOMS.value  in doc_types
has_shipping = DocType.SHIPPING.value in doc_types
return not (has_customs and has_shipping)
```

**Exactly both, and nothing else substitutes.** `SHIPPING` alone does not clear
the gate; `CUSTOMS` alone does not clear it.

This function is the **single source of truth** â€” `reconciliation` and `merge`
both import it, so the two paths cannot drift.

### 20.2 `toggle_customs(po_set_id, cfg)` â€” state machine

```
ps = POSet.get(po_set_id);  if None â†’ ValueError

ps.has_customs_toggle = not ps.has_customs_toggle        # FLIP

types_present = distinct types among ps.documents that are CUSTOMS or SHIPPING
ps.customs_doc_count = len(types_present)                 # 0, 1 or 2

if ps.has_customs_toggle:                                  # toggled ON
    if is_blocked(ps):            ps.status = blocked_customs
    elif ps.status == blocked_customs:  ps.status = pending
else:                                                     # toggled OFF
    if ps.status == blocked_customs:  ps.status = pending

commit; refresh; refresh(attribute_names=["documents"]); return ps
```

The toggle is available on **any** status â€” including `merged` and
`quarantined` â€” it is not gated by state.

---

## 21. Merge (`services/merge.py`)

### 21.1 `_si_number(po_set)` â€” strict SI only

Iterate documents; skip any whose type â‰  `SI`; return the first truthy
`si_no or invoice_no`; else `None`.

A number printed on a DN or PO is **never** used to name the packet â€” a packet
named from the wrong document is worse than an unnamed one.

### 21.2 `_any_invoice_number(po_set)` â€” Force Merge only

Same, but without the SI filter. Force Merge is the operator's explicit override
and must still produce a file, so it may look wider than the auto path.

### 21.3 `_ordered_docs(po_set, cfg)` â€” packet ordering

```
DEFAULT_LEGAL_ORDER = ["SI", "DN", "PO", "SHIPPING", "CUSTOMS"]
order = cfg.merge.legal_order if cfg and cfg.merge and cfg.merge.legal_order
        else DEFAULT_LEGAL_ORDER

groups = {doc_type: [docs]}   # first-seen order preserved inside each group
ordered = []
for t in order:  ordered.extend(groups.pop(t, []))
for d in docs:                    # types absent from `order` append first-seen
    t = d.doc_type
    if t in groups and t not in seen: seen.add(t); ordered.extend(groups.pop(t, []))
ordered.extend(leftovers)         # belt-and-braces
```

A new manual type (e.g. an AWB variant) can never silently vanish from a packet.

### 21.4 `_packet_name(po_set)` â†’ `(stem, invoice_missing)`

| Condition | Result |
|---|---|
| `_si_number(po_set)` truthy and `_safe_stem(...)` non-empty | `(safe_stem(si_no), False)` |
| else `po_no_normalized` non-empty | `(safe_stem(po_no), True)` |
| else | `(None, True)` |

`_safe_stem(value)` keeps only `isalnum()` plus `-`, `_`, `.`.

A PO number alone is safe as a filename: only one *open* set exists per PO key
at a time, and a genuine duplicate is still caught by `_resolve_output_path`.

### 21.5 `_resolve_output_path(safe, po_set_id, output_folder, current_path)`

| Condition | Result |
|---|---|
| `current_path` set and `Path(current_path).resolve() == out.resolve()` | return `out` â€” re-merging this set onto its own file is fine |
| `out.exists()` | **`raise MergeNamingError(f"output filename '{out.name}' already exists and belongs to another PO Set; refusing to overwrite (PO Set {id})")`** |
| otherwise | return `out` |

### 21.6 `_write_merged(ordered, out, allow_missing=False)`

```
out.parent.mkdir(parents=True, exist_ok=True)
writer = PdfWriter();  missing = []
for doc in ordered:
    p = Path(doc.stored_path)
    if not p.exists():  missing.append(f"Doc {id} ({name}): {p}");  continue
    for pg in PdfReader(str(p)).pages:  writer.add_page(pg)

if missing and not allow_missing:
    raise FileNotFoundError(f"Missing stored PDF(s) during merge: {'; '.join(missing)}")

writer.write(str(out));  return out
```

**A missing stored PDF aborts the whole merge** â€” it never writes a partial
packet. (Auto-merge passes `allow_missing=False`; so does `force_merge`.)

### 21.7 `merge_po_set(po_set_id, cfg, info=None)` â€” auto path

| Step | Condition | Result |
|---|---|---|
| 1 | `ps is None` | `ValueError(f"POSet {po_set_id} not found")` |
| 2 | `ps.status == merged` | if `merged_output_path` â†’ return that `Path`; else return `None` |
| 3 | `ps.status âˆˆ {mismatched, quarantined, blocked_customs}` | return `None` |
| 4 | `is_blocked(ps)` | return `None` (customs gate, same function as reconcile) |
| 5 | `not _ordered_docs(...)` | return `None` |
| 6 | `total_lines == 0` (sum of `len(d.line_items)`) | log WARNING "zero line-item evidence"; return `None` â€” W-22: a packet with no numeric reconciliation behind it must never go out |
| 7 | `stem, invoice_missing = _packet_name(ps)`; `not stem` | `MergeNamingError(f"PO Set {id} cannot be named: no invoice number and no PO number")` |
| 8 | `_resolve_output_path(...)` | `MergeNamingError` propagates on collision |
| 9 | `info` given | fill `info["output_name"]`, `info["invoice_no_missing"]` |
| 10 | `_write_merged(ordered, out, allow_missing=False)` raises `FileNotFoundError` | log WARNING; return `None` |
| 11 | success | `merged_output_path`, `merged_at = now(UTC)`, `status = merged`; commit; refresh; return `Path` |

### 21.8 `force_merge(po_set_id, cfg, justification=None)` â€” override

| Step | Condition | Result |
|---|---|---|
| 1 | `ps is None` | `ValueError` |
| 2 | `note = validate_justification(justification)` | may raise `ValueError` on a short note |
| 3 | `status == merged` **and** `merged_output_path` | return the existing `Path` â€” **still immutable** |
| 4 | `ordered = _ordered_docs(ps, cfg)` or, if empty, `list(ps.documents)` | â€” |
| 5 | `ordered` still empty | `MergeNamingError(f"PO Set {id} has no documents to merge â€” nothing to write")` |
| 6 | `name_source = _any_invoice_number(ps) or ps.po_no_normalized`; `safe = _safe_stem(...)`; not safe | `MergeNamingError(f"PO Set {id} cannot be named: no invoice number and no PO number")` |
| 7 | `_resolve_output_path(...)` | `MergeNamingError` propagates |
| 8 | `_write_merged(ordered, out, allow_missing=False)` | raises on a missing file |
| 9 | success | set `merged_output_path`, `merged_at`, `status = merged` |
| 10 | `customs_count = count of docs with type âˆˆ {CUSTOMS, SHIPPING}` | â€” |
| 11 | audit | `AuditLog(po_set_id=ps.id, action=force_merge, detail=json({"customs_doc_count":n,"output_name":name}), source="system", justification=note)` |
| 12 | â€” | commit; refresh; return `Path` |

Force Merge bypasses **reconciliation and customs**. It does not bypass
immutability, naming safety, or the missing-file refusal.

---

## 22. Quarantine (`services/quarantine.py`)

`_RESERVED_NAMES = {CON, PRN, AUX, NUL} âˆª {COM1..COM9} âˆª {LPT1..LPT9}`, `_MAX_FOLDER_LEN = 80`

### 22.1 `_set_fingerprint(po_set)`

`sha1("|".join(sorted(each document's sha256_hash)))[:8]`

Derived from **document hashes, not the id** â€” SQLite reuses the id of a deleted
row, so a new set could otherwise inherit an old set's folder and overwrite its
`QUARANTINE.txt`. Re-quarantining the same set gives the same fingerprint, so the
folder stays stable across runs.

### 22.2 `_safe_po_folder(po_no, po_set_id, fingerprint)`

```
raw = keep only chars where ch.isalnum() or ch in "-_."
raw = raw.strip(".-")
if not raw or set(raw) <= {"."}:
    tag = fingerprint or (str(po_set_id) if po_set_id is not None else None)
    return (f"UNIDENTIFIED_PO_{tag}"[:80] if tag else "UNIDENTIFIED_PO")
if raw.upper() in _RESERVED_NAMES:  raw = f"_{raw}"
return raw[:80]
```

Keeps the PO number readable (hyphens/underscores/dots survive) because this is
a folder a person opens. In production `po_no_normalized` is already
alphanumeric, so the filtering is a no-op there and a guard for values written
directly to the database.

### 22.3 `_write_quarantine_report(folder, po_no, status, reason, detail, flags, po_line_qty)`

Builds `QUARANTINE.txt`:

1. Header: `QUARANTINED PO SET`, PO number, status, reason (or `(none recorded)`), detection timestamp in UTC
2. `Detail      : {detail}` when a detail string is present
3. Table built from `po_line_qty` (every PO line, so the reviewer sees the whole set) then overlaid by flags:
   - `type == "identification"` â†’ `row["note"] = f.reason or "unmatched"`, and `row[pool.lower()] = f.vendor_quantity` when pool âˆˆ {DN, SI}
   - `pool âˆˆ {DN, SI}` â†’ `row[pool.lower()] = f.vendor_quantity`
4. Columns `Line | PO | DN | SI | Note`; rows sorted by `(len(key), key)`; quantities rendered by `_format_qty` = `f"{int(value)/1000:g}"` (`None` â†’ `"-"`)
5. Footer: *"These are the exact numbers the engine compared. Reconciliation requires PO == sum(DN) and PO == sum(SI) for every line."*
6. Footer: *"To resolve: correct the source documents and re-run, or force-merge with a justification. Force-merge ships the packet as-is."*

When there are no line numbers at all: `"(no line-item numbers were recorded for this set)"`.

### 22.4 `quarantine_copy(po_set, cfg, reason, detail, flags)`

Accepts a `POSet` object **or** an `int` id. Either way:

```
folder = quarantine_folder / _safe_po_folder(ps.po_no_normalized, ps.id, _set_fingerprint(ps))
folder.mkdir(parents=True, exist_ok=True)
for doc in ps.documents:
    if Path(doc.stored_path).exists():
        shutil.copy(src, folder / src.name)      # COPY, never move
_write_quarantine_report(folder, po_no, status, reason, detail, flags, _po_line_quantities(ps))
return folder
```

`shutil.copy` â€” both the `stored/` copy and the quarantine copy remain, so the
document row stays valid and the operator can still attach and retry.

`_po_line_quantities(ps)` sums `quantity` per `normalize_line_no(line_item_no)`
across documents **of type PO only**.

### 22.5 `quarantine_document(doc_id, cfg, reason)`

```
doc = Document.get(doc_id);  if None â†’ ValueError
capture stored/name/original/id/attempts, then s.expunge(doc)
folder = quarantine_folder / "_documents" / name.replace(".pdf", "")
folder.mkdir(parents=True, exist_ok=True)
if stored.exists(): shutil.copy(stored, folder / name)
write QUARANTINE.txt:
    File        : {original_filename}
    Document id : {id}
    Attempts    : {n}/3 (cap reached, no further automatic attempts)
    Reason      : {reason}
    + retry instructions (enter PO number on Unclassified â†’ attach â†’ Redo/Re-extract)
```

**Only the input-folder copy is deleted** (by the caller in `sync.py`), because
that is what makes the next run re-encounter the file. Without this a dead PDF is
re-hashed, de-duplicated to the same failed row, and re-counted as an error on
every run, forever.

### 22.6 `validate_justification(text)` â€” `MIN_JUSTIFICATION_CHARS = 20`

| Input | Result |
|---|---|
| `None` | `None` â€” optional stays optional so existing callers and the UI keep working |
| `str(text).strip()` empty | `None` |
| length < 20 | **`raise ValueError(f"Justification must be at least 20 characters (got {n})")`** |
| otherwise | the cleaned string |

### 22.7 `delete_quarantined(po_set_id, cfg, justification=None)`

```
ps = POSet.get(po_set_id);  if None â†’ ValueError
note = validate_justification(justification)

GUARD: if ps.status != quarantined:
    raise ValueError(f"POSet {po_set_id} is not quarantined (status={status})")

po_no  = ps.po_no_normalized
docs   = Document where po_set_id == po_set_id
doc_ids = [d.id for d in docs]
if doc_ids: delete LineItem where document_id IN doc_ids

detail = json.dumps({"po_no_normalized": po_no, "document_count": len(doc_ids)})
audit  = AuditLog(po_set_id=po_set_id, action=quarantine_delete, detail=detail,
                  source="system", justification=note)
add(audit); flush()          # â† audit FIRST, with the real id, while the FK is valid
delete Document where po_set_id == po_set_id
delete(ps)                    # ON DELETE SET NULL fires; the audit row survives
commit; refresh(audit); return audit
```

**Files on disk are never deleted** â€” only `po_sets`, `documents` and `line_items` rows.

### 22.8 `manual_merge(files, order, output_path=None)` â€” isolated

| Condition | Result |
|---|---|
| `not files` | `ValueError("No files provided for manual merge")` |
| `order is None` | `order = range(len(files))` |
| `len(order) != len(files)` | `ValueError(f"order length {len(order)} != files length {len(files)}")` |
| `set(order) != set(range(len(files)))` | `ValueError(f"order must be permutation of 0..{n-1}, got {order}")` |
| `output_path is None` | `tempfile.mkstemp(suffix=".pdf")` â€” **isolated from the pipeline `output_folder`** |
| `output_path` given | `parent.mkdir(parents=True, exist_ok=True)` |
| any `files[i]` missing | `FileNotFoundError(f"File not found: {p}")` |

No DB, no PO association. Always writes the file, even with zero pages.

---

## 23. Sync orchestration order (`flows/sync.py`)

### 23.1 `sync_flow(cfg_path, held_lock)`

```
own_lock = None
if held_lock is None:
    own_lock = acquire_sync_lock(cfg_path)
    if own_lock is None:
        return {status:"skipped", reason:"sync_already_running", processed:0,
                extracted:0, errors:0, touched_po_sets:0, reconciled_count:0}
try:    return _sync_flow_locked(cfg_path)
finally: if own_lock is not None: release_sync_lock(own_lock)
```

A route-triggered run passes the lock it already holds, so the flow does not
self-deadlock.

### 23.2 `extract_task(doc_id, cfg_path)` â€” Prefect task

Decorator defaults: `retries=3, retry_delay_seconds=[2, 5, 15]`.
`_extract_task_for(cfg)` re-declares them with `.with_options(retries=cfg.extraction.max_retries, retry_backoff_seconds=cfg.extraction.retry_backoff_seconds)` at every call site, so config is the live authority.

### 23.3 `_persisted_extraction_status(eng, doc_id)`

Re-reads `document.extraction_status` from a fresh session.

**Why this exists:** Prefect places a task in COMPLETED whenever it returns any
Python object, and `extract_document` returns *normally* on the attempt-cap path â€”
a terminal failure. Trusting the task return is how a permanently lost document
came to be reported as `errors: 0`. The document row is the single source of truth.

### 23.4 `_quarantine_broken_document(doc_id, cfg, eng, input_file, touched_po_set_ids)`

Every step is individually guarded; a tidying failure is cosmetic, not a loss.

| Step | Action |
|---|---|
| 1 | `quarantine_document(doc_id, cfg, reason="Extraction failed permanently (attempt cap reached); document content could not be read.")` â€” `except` â†’ log warning |
| 2 | `Path(input_file).unlink(missing_ok=True)` â€” `except` â†’ log warning. This is what stops the nightly re-hash loop |
| 3 | read `po_set_id`; if not `None`: set `status=quarantined` and `reconcile_reason="Quarantined: a document in this set failed to read, so the set could not be verified."` (unless already `merged` or `quarantined`); commit; add to `touched`; `quarantine_copy(po_set_id, cfg, reason=â€¦, detail="A member document failed extraction permanently.")`; log ERROR |

An unconfirmable set must not sit in an apparently-normal `pending` state waiting
on a document that is never going to arrive.

### 23.5 `_sync_flow_locked` â€” exact execution order

```
cfg = load_config; eng = get_engine; Base.metadata.create_all(eng)
input_folder.mkdir(parents=True, exist_ok=True)

processed = 0; errors = 0; touched_po_set_ids = set()

â”€â”€ PHASE 0a: the file loop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
for f in find_input_pdfs(input_folder):
  try:
    if not is_file_stable(f, cfg.ingestion.stability_poll_interval_seconds,
                          cfg.ingestion.stability_poll_count):  continue
    doc = ingest_file(f, cfg);  processed += 1

    try:  _extract_task_for(cfg)(doc.id, cfg_path)
    except Exception:  errors += 1                       # task itself blew up
    else:
        if _persisted_extraction_status(eng, doc.id) == failed:
            errors += 1
            _quarantine_broken_document(doc.id, cfg, eng, f, touched_po_set_ids)

    try:                                     # GROUPING
        with Session(eng) as s2:
            d2 = s2.get(Document, doc.id)
            if d2 and d2.po_no_normalized:
                ps = get_or_create_po_set(d2.po_no_raw or d2.po_no_normalized, cfg,
                                          create = (doc_type(d2) in _ANCHOR_TYPES))
                if ps is None:  continue              # attach-only, no anchor yet
                if d2.po_set_id is None:  d2.po_set_id = ps.id;  s2.commit()
                touched_po_set_ids.add(ps.id)
    except Exception:  errors += 1
  except Exception:                                 # whole-file wrapper
    errors += 1; continue

â”€â”€ PHASE 0b: DB backfill of `pending` rows from a crashed prior run â”€â”€â”€
for doc in Document where extraction_status == pending:   [same 3 blocks]

â”€â”€ PHASE 0c: attach sweeps â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
touched.update(resolve_unattached_documents(cfg))
touched.update(attach_unattached_to_open_sets(cfg))

â”€â”€ PHASE 1: reconcile every touched set â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
for ps_id in touched_po_set_ids:
    try:
        res = reconcile_po_set(ps_id, cfg);  reconciled_count += 1
        if res.status == merged:
            delete_input_files(POSet.get(ps_id), input_folder)
    except Exception:  errors += 1                  # one bad set never blocks others

â”€â”€ PHASE 2: sweep every non-merged set (catches stale sets) â”€â”€â”€â”€â”€â”€â”€â”€â”€
for (ps_id,) in POSet where status != merged:
    if ps_id in touched_po_set_ids:  continue        # already done in phase 1
    try: same as phase 1
    except Exception:  errors += 1

return {processed, extracted: max(0, processed - errors), errors,
        touched_po_sets: len(touched_po_set_ids), reconciled_count}
```

**Why `max(0, â€¦)`:** `processed` counts files successfully ingested while
`errors` counts failures at *any* stage including ingestion itself. The two are
not a partition of the same set, so the subtraction is clamped â€” an operator must
never be shown a negative number of extractions.

**Why the `continue` on `ps is None`:** an attach-only doc with no anchor yet must
not be reconciled, because it belongs to no set.

---

## 24. Route conditions

### 24.1 `POST /sync` (`routes/sync.py`)

```
lock = acquire_sync_lock()
if lock is None:  raise HTTPException(409, "Sync already running")
threading.Thread(target=_run_sync, args=(lock,), daemon=True).start()
return {status:"sync started", pool: cfg.prefect.work_pool_name}
```

- daemon thread so `TestClient` does not block on `BackgroundTasks`
- the lock is held for the whole flow run, so the 409 window is exact â€” no
  artificial sleep
- **`_run_sync(lock, cfg_path=None)`** â†’ `sync_flow(cfg_path=cfg_path, held_lock=lock)`,
  `except` â†’ `logger.exception`, `finally: release_sync_lock(lock)`
- **no config path is accepted from the caller** â€” a request-supplied path would
  let any LAN host redirect the whole pipeline (output folder, database, log
  folder) and create directories as the service account

### 24.2 `_acquire_lock(po_set_id, action, cfg)` / `_release_lock`

```
ps = POSet.get(po_set_id);  if None â†’ HTTPException(404, f"POSet {po_set_id} not found")
if not acquire_lock(ps, action, s, cfg):
    raise HTTPException(409, f"action already in progress on this PO Set: {ps.locked_by_action}")
```

Release always passes the action string, so it is action-scoped.

Every action route uses `try/except HTTPException: raise / except Exception: â†’ 422 / finally: _release_lock(..., action)`.

### 24.3 The seven action routes

| Route | Lock action | Success body | Error mapping |
|---|---|---|---|
| `POST /po_sets/{id}/force_merge` | `force_merge` | `{status:"merged", po_set_id, detail:{merged_path}}` | any `Exception` â†’ **422** `f"Force merge failed: {e}"`, `logger.exception` |
| `POST /po_sets/{id}/toggle_customs` | `toggle_customs` | `{status:"toggled", po_set_id, has_customs_toggle}` | â†’ **422** `str(e)` |
| `POST /po_sets/{id}/redo_extract` | `redo_extract` | `{status:"redo_extract_complete", po_set_id, extractions:[â€¦], reconciliation: rec_res}` | per-doc `except` captured into `extractions[].error`; the route itself does not raise |
| `POST /po_sets/{id}/redo_match` | `redo_match` | `{status:"redo_match_complete", po_set_id, reconciliation: rec_res}` | â€” |
| `POST /po_sets/{id}/merge` | `merge` | `{status, po_set_id, merged_output_path, reason, detail, flags}` â€” always 200 with the verdict, never 422 for "not eligible" | â†’ **422** `f"Merge failed: {e}"` |
| `DELETE /po_sets/{id}/quarantine` | `quarantine_delete` | `{status:"deleted", audit_id}` | `"not quarantined"` in message â†’ **409**, else **422** |
| `POST /po_sets/{id}/upload` | `manual_upload` | 302 â†’ `/po_sets/{id}/view` | see 24.5 |

`redo_extract` in detail:
```
docs = Document where po_set_id == id
for d in docs:
    if not is_manual_only(d.doc_type):        # CUSTOMS/SHIPPING are skipped
        d.extraction_attempt_count = 0         # explicit operator intent
        d.extraction_status = pending
        docs_to_extract.append((d.id, dtype))
commit
for doc_id, _ in docs_to_extract:
    try:  extractions.append({doc_id, status: extract_document(...).extraction_status})
    except Exception as e:  extractions.append({doc_id, error: str(e)})
rec_res = reconcile_po_set(po_set_id, cfg)
```

`merge_now` is **not** Force Merge. It re-runs every gate, so an ineligible set
simply comes back with its status and reason and nothing is written.

### 24.4 `GET /po_sets` and `GET /po_sets/{id}`

Both auto-release stale locks before responding, so a permanently-stale lock can
never leave the dashboard disabled:
```
for ps in all_sets:  if ps.locked_by_action is not None and not is_locked(ps, cfg): release_lock(ps, s)
```

`_po_to_dict` returns `id, po_no_normalized, status, locked_by_action, is_locked, htmx_disabled ("disabled" if locked else ""), has_customs_toggle, updated_at`.

### 24.5 `POST /po_sets/{id}/upload` â€” the full validation ladder

Ordered exactly as written:

| # | Check | Failure |
|---|---|---|
| 1 | `doc_type âˆˆ {CUSTOMS, SHIPPING}` | **422** `f"doc_type must be CUSTOMS or SHIPPING, got {doc_type}"` |
| 2 | PO Set exists | **404** |
| 3 | `acquire_lock(ps, "manual_upload", s, cfg)` | **409** with `ps.locked_by_action` |
| 4 | `data = file.file.read(MAX_UPLOAD_BYTES + 1)` â€” **cap enforced on the stream, not after** | â€” |
| 5 | `not data` | **422** `empty file` |
| 6 | `len(data) > MAX_UPLOAD_BYTES` | **422** `f"file exceeds {MB}MB upload cap"` |
| 7 | `safe_name = Path(file.filename).name` (basename only) | â€” |
| 8 | `Path(safe_name).suffix.lower() != ".pdf"` | **422** `only .pdf uploads accepted` |
| 9 | `sha = sha256(data)`; `existing = Document where sha256_hash == sha` | â€” |
| 10 | `existing is not None and existing.po_set_id == po_set_id` | `already_attached = True` |
| 11 | `existing is not None and existing.po_set_id not in (None, po_set_id)` | **409** naming the owning PO number, with the explanation that documents are deduplicated by content so the same file cannot be attached twice |
| 12 | `existing is None` â†’ `not data.startswith(b"%PDF")` | **422** `not a PDF file` |
| 13 | `len(PdfReader(io.BytesIO(data)).pages) < 1` | **422** `PDF has no pages` |
| 13 | `PdfReader` raises | **422** `f"unreadable PDF: {e}"` |
| 14 | write `stored_documents_folder / f"{sha}.pdf"`; insert `Document(doc_type, extraction_status=valid, po_set_id)`; flush | â€” |
| 15 | recompute `customs_doc_count` = 1 if CUSTOMS present + 1 if SHIPPING present; `ps.customs_doc_count = cnt`; commit | â€” |
| 16 | `already_attached` | **302** to `/po_sets/{id}/view?notice={quoted "already attached; nothing was changed"}` |
| 17 | otherwise | **302** to `/po_sets/{id}/view` |
| â€” | any other `Exception` | **422** `str(e)` |
| â€” | `finally` | `release_lock(ps, s, "manual_upload")` |

`MAX_UPLOAD_BYTES = 100 * 1024 * 1024` (`core/limits.py`) â€” centralised so tests
can monkeypatch one symbol.

**Note on the ordering of 12/13:** the PDF content sniff runs only when the file
is genuinely new. A duplicate hash never re-sniffs and never rewrites.

### 24.6 `GET /po_sets/{id}/view` â€” the detail matrix

```
if doc_ids:
    items = LineItem where document_id IN doc_ids
    enriched = [{line_item_no, description, quantity, unit_price, doc_type} per item]

    po_lines = [l for l in enriched if l.doc_type == "PO"]
    dn_lines = [... "DN"];   si_lines = [... "SI"]
    thr = cfg.matching.fuzzy_description_threshold
    comparison = compare_po_set_lines(po_lines, dn_lines, si_lines, thr)   # â† THE ENGINE'S FUNCTION

    if comparison["po_fail"]:
        flags.append({priority:1, badge:"badge-quarantined",
                      type:"Identification Mismatch", message:REASON_TEXT[po_fail]})

    for f in comparison["flags"]:
        if f.type == "identification":
            flags.append({priority:1, badge:"badge-quarantined",
                          type:"Identification Mismatch",
                          message: f"{f.pool} line has no matching PO line"})
        else:
            verb = "delivered" if f.pool == "DN" else "invoiced"
            flags.append({priority:2, badge:"badge-mismatched", type:"Quantity Mismatch",
                          message: f"Line #{f.line_item_no}: {verb} {f.vendor_quantity/1000:g} of {f.po_quantity/1000:g}"})

    for p in po_lines:                     # per-PO-line 3-way matrix
        key    = normalize_line_no(p.line_item_no)
        agg_dn = comparison["dn_totals"].get(key, 0) / 1000
        agg_si = comparison["si_totals"].get(key, 0) / 1000
        po_q   = p.quantity / 1000
        reconciled = (dn_lines and agg_dn*1000 == p.quantity) and \
                     (si_lines and agg_si*1000 == p.quantity)
        if reconciled:  row_class, badge, verdict = "row-match", "badge-merged", "âœ… Match"
        else:           row_class, badge = "row-mismatch", "badge-mismatched"
                        verdict = f"âŒ Mismatch (PO: {po_q:g}, DN: {agg_dn:g}, SI: {agg_si:g})"
        matrix_rows.append({line_item_no: key or "â€”", description, po_qty, dn_agg_qty,
                            si_agg_qty, po_price, row_class, badge, verdict})
    flags.sort(by priority)

has_merged_file = bool(ps.merged_output_path and Path(ps.merged_output_path).exists())
```

**This is a preview only â€” nothing on this page writes state.** And because it
calls `compare_po_set_lines`, the same function the engine calls, the verdict on
screen *is* the verdict that set the status.

Keys are normalised, so a PO printing `01` lines up with a DN printing `1`.

### 24.7 `GET /unclassified`

Candidate set: `doc_type == UNKNOWN` **OR** `extraction_status == failed`.
`failed_count = sum(1 for d in docs if d.extraction_status == failed)`.

A permanently failed document is **not** waiting for classification â€” it is a
loss. It stays in this view (its `doc_type` was never advanced off `UNKNOWN`) and
must be countable separately, or the holding area reports a clean sheet while
holding files that will never be read. Failed rows never attach, because the
sweeps only take `valid`.

### 24.8 `POST /unclassified/{doc_id}/reclassify`

| # | Check | Failure |
|---|---|---|
| 1 | `DocType(doc_type)` | **422** `f"Invalid doc_type: {doc_type}"` |
| 2 | `new_doc_type âˆˆ _HAND_ASSIGNABLE_DOC_TYPES` = `{PO, DN, SI, CUSTOMS, SHIPPING}` | **422** `"â€¦cannot be hand-tagged; assign the type of an individual document instead"` |
| 3 | document exists | **404** |
| â€” | set `doc.doc_type = new_doc_type` | always |
| â€” | `po_no` non-blank â†’ set `po_no_raw`, `po_no_normalized = normalize_po_no(raw)`, `ps = get_or_create_po_set(raw, cfg)` (create=True), `doc.po_set_id = ps.id` | â€” |
| â€” | commit; then `HX-Request == "true"` â†’ 200 HTML confirmation row; else **302** â†’ `/unclassified` | â€” |

**Why an allowlist and not a denylist:** so that no Layer-2 code has to name a
type it must never produce. `COMBINED` is decided by the Layer-1 split and has no
single-document meaning for an operator to assert. The rule stays true
automatically as types are added.

### 24.9 Dashboard filter (`_po_sets_with_doc_count`)

```
if status_filter:
    if status_filter not in _ALLOWED_STATUSES:  return []      # unknown â†’ empty, not 500
    pools = [ps for ps in all if ps.status == status_filter]  # in-Python, not SQL
else:  pools = all

doc_counts = {po_set_id: count} from a GROUP BY over Document
out = [{id, po_no_normalized, status, status_val, doc_count,
        has_merged_file: bool(merged_output_path and Path(...).exists()),
        updated_at, reconcile_reason, locked_by_action, is_locked} for ps in pools]
out.sort(by (updated_at or id), descending)
```

`_ALLOWED_STATUSES = {s.value for s in POSetStatus}` â€” the enum is the source of
truth, so a status added to the model appears in the filter automatically.

`GET /dashboard` releases stale locks, then branches on
`request.headers.get("HX-Request") == "true"` â†’ `_dashboard_table.html` fragment
vs. the full `dashboard.html` page with `sync_running` and `stats`.

`_get_stats` returns `total, merged, merged_pct, mismatched, blocked_customs,
quarantined, pending, unclassified`, where
`unclassified = count(Document where doc_type == UNKNOWN)`.

### 24.10 PDF streaming

| Route | 404 conditions |
|---|---|
| `GET /documents/{doc_id}/preview` | document missing **or** `stored_path` falsy; **or** the file is not on disk (`"Stored PDF file missing from disk"`) |
| `GET /po_sets/{id}/merged_pdf` | PO Set missing **or** `merged_output_path` falsy; **or** the file is not on disk (`"Merged output PDF missing from disk"`) |

---

## 25. Client-side logic (`static/js/app.js`)

Registered on `alpine:init`.

### 25.1 `Alpine.store('toasts')`

`add(message, type='info', duration=4000)` pushes `{id, message, type}` where
`id = Date.now() + Math.random().toString(36).substring(2, 6)`, and schedules
`remove(id)` after `duration` when `duration > 0`.
`success` = 3500 ms, `error` = 6000 ms, `warning` = 5000 ms, `info` = 4000 ms.

### 25.2 `Alpine.store('pdfDrawer')`

`open(url, title='Document Preview')` sets `url`/`title`/`isOpen`;
`close()` clears `url` and sets `isOpen=false`. Rendered as a single global
`<iframe>` inside a `template x-if`, so the PDF is only loaded when open.

### 25.3 `Alpine.data('tableSearch')`

`filterRows()` lowercases and trims the query, then for every
`#po-table-body tr.po-row` shows the row when the query is empty **or** matches
`row.dataset.po`, `row.dataset.vendor` or `row.dataset.status` by substring.
Purely client-side â€” no request, no server round trip.

### 25.4 `htmx:responseError` â†’ toast mapping

| `xhr.status` | Toast |
|---|---|
| `409` | warning â€” *"Action blocked: PO Set is currently locked by a background operation. Please wait a moment."* |
| `422` | error â€” *"Validation error: " + responseText* |
| `404` | error â€” *"Not found: The requested record does not exist."* |
| anything else | error â€” *"Server error (N). Please check server logs."* |

### 25.5 `htmx:afterRequest` â†’ per-endpoint success messages (2xx only)

Matched on `detail.pathInfo.requestPath`:

| Path ends with | Toast |
|---|---|
| `/toggle_customs` | info â€” *"Customs requirement setting updated."* |
| `/redo_extract` | success â€” *"Document extraction queued."* |
| `/redo_match` | success â€” *"Line-item reconciliation re-matched."* |
| `/\/merge$/` | **parses the JSON body**: `status === 'merged'` â†’ success *"Merged: {merged_output_path}"*; otherwise â†’ warning *"Not merged â€” still {status} ({reason with underscores â†’ spaces}). Fix that, then press Merge Now again."* Non-JSON body falls through to a generic message |
| `/force_merge` | success â€” *"Force merge completed and audit log recorded."* |
| `/sync` | success â€” *"Sync scan triggered."* |

### 25.6 `htmx:sendError`

Network failure â†’ error toast *"Network connection failed. Server might be restarting."*

### 25.7 `keydown` Escape

Closes the PDF drawer **and** removes `.open` from every `.modal-backdrop`.

---

## 26. Template render conditions

### 26.1 Dashboard status badge (`_dashboard_table.html`)

| `status_str` | Badge |
|---|---|
| `merged` | `âœ… Merged & Ready` (badge-merged) |
| `mismatched` | `âš ï¸ Needs Review` (badge-mismatched) |
| `blocked_customs` | `ðŸ”’ Awaiting Customs` (badge-blocked) |
| `quarantined` | `ðŸš¨ Quarantined` (badge-quarantined) |
| `pending` | `â³ Ingesting / Pending` (badge-pending) |
| anything else | raw string in badge-gray |

Plus a second `ðŸ”’ Locked ({locked_by_action})` badge when `is_locked`.
The **PDF download button only renders when `status_str == 'merged' AND has_merged_file`** â€” a merged status without a file on disk gets no dead button.

### 26.2 Age column

Computed **server-side** from a `now_ts` epoch passed into the template, so
relative ages render without a client clock and without layout shift on refresh:
`< 90 s` â†’ `just now`; `< 3600` â†’ `Nm ago`; `< 86400` â†’ `Nh ago`; else `Nd ago`.
No `updated_at` or no `now_ts` â†’ `â€”`.

### 26.3 Why column

`reconcile_reason` when set; otherwise `muted` **"Not evaluated yet"** â€” never
blank, so a reviewer can tell "not yet run" from "ran and found nothing".

### 26.4 Empty states

| Template | Empty state |
|---|---|
| `_dashboard_table.html` | `ðŸ“­ No PO Sets found`; when filtered, names the status |
| `_quarantine_table.html` | `ðŸ›¡ï¸ Quarantine is empty` |
| `unclassified.html` | `ðŸŽ‰ All caught up!` |
| `audit.html` | `ðŸ“ Audit log is clean` |
| `po_set_detail.html` matrix | `ðŸ“‹ No extracted line items yet` + "Click Re-Extract with AI" |
| `po_set_detail.html` documents | "No documents attached to this PO Set." |

### 26.5 PO Set detail â€” conditionally rendered sections

| Condition | Section |
|---|---|
| `notice` present | orange-bordered notice card â€” *an upload that changed nothing must still say so, or it is indistinguishable from a successful one* |
| `has_customs_toggle` | Customs Clearance + Shipping (AWB) rows, each `âœ… Attached` / `âš  Missing` from `customs_doc_count` and the documents' types |
| `status_str == 'merged' and has_merged_file` | `ðŸ“¥ Download Merged PDF` |
| `is_locked` | **every** action button gets the `disabled` attribute |
| `status_str == 'quarantined'` | the red "Delete Quarantined PO Set" card + its confirmation modal |
| always | the Force Merge confirmation modal (opens via class, not a route) |

The upload `<select>` offers **only** CUSTOMS and SHIPPING, matching the route's
validator exactly. The unclassified `<select>` offers only the five hand-assignable
types, matching `_HAND_ASSIGNABLE_DOC_TYPES` exactly.

The unclassified row has a live Alpine getter:
`get norm() { return this.poInput.replace(/[^A-Za-z0-9]/g, '').toUpperCase() }` â€”
a **client-side preview** of the server's normalization, so the operator sees the
grouping key before saving. It is a preview only; the server recomputes it.

### 26.6 Audit template

Renders `force_merge` â†’ âš¡ badge-merged, `quarantine_delete` â†’ ðŸš¨
badge-quarantined, anything else â†’ raw value in badge-pending. The PO Set id is
a link when non-null and `â€”` when the `ON DELETE SET NULL` fired.

---

## 27. Complete condition index

| # | Condition | Consequence | Location |
|---|---|---|---|
| 1 | config file missing | `FileNotFoundError` â€” refuse to start | `config.py:172` |
| 2 | `OPENROUTER_API_KEY` absent | `RuntimeError` fail-closed | `extraction.py:220` |
| 3 | `cfg.vlm.model` absent | `RuntimeError` fail-closed | `extraction.py:222` |
| 4 | stored file missing at VLM time | `FileNotFoundError` | `extraction.py:228` |
| 5 | `attempt_count >= 3` | terminal `failed`, **returns, does not raise** | `extraction.py:429` |
| 6 | doc type is CUSTOMS/SHIPPING | return unchanged, VLM never called | `extraction.py:426` |
| 7 | VLM returns `COMBINED` | `_handle_combined` â€” never a normal persist | `extraction.py:501` |
| 8 | child re-reads as COMBINED | child failed, **parent** quarantined `child_recombined` | `extraction.py:317` |
| 9 | parent already split | idempotent return, no duplicate children | `extraction.py:349` |
| 10 | any of 4 split gates fails | parent failed + quarantined `split_failed:<reason>`, no raise | `splitting.py:82` |
| 11 | file not stable | skip this run, no ingest, no VLM | `sync.py:237` |
| 12 | duplicate SHA-256 | return existing row, no new document | `ingestion.py:36` |
| 13 | PO Set already merged | a later same-key document opens a **new** set | `grouping.py:104` |
| 14 | non-PO document, `create=False` | never mints; stays unattached & visible | `grouping.py:110` |
| 15 | `po_reference_ambiguous` | never auto-attached, by any strategy | `grouping.py:149` |
| 16 | PO line with no usable line no. | comparison aborts, set quarantines | `matching.py:83` |
| 17 | vendor row with no number, nothing â‰¥85 | orphan â†’ quarantine | `matching.py:98` |
| 18 | vendor group not in PO totals | orphan â†’ quarantine | `matching.py:100` |
| 19 | orphans present | quantity diffs **not** reported; identity only | `matching.py:137` |
| 20 | set already merged | reconcile returns immediately, immutable | `reconciliation.py:178` |
| 21 | any side missing | `pending` with an explicit reason, never merge | `reconciliation.py:199` |
| 22 | `single_po_document` and >1 PO | quarantine `multiple_po_documents` | `reconciliation.py:231` |
| 23 | doc PO key â‰  set key | quarantine `po_reference_mismatch` | `reconciliation.py:259` |
| 24 | any line qty â‰¤ 0 | quarantine `non_positive_quantity` | `reconciliation.py:329` |
| 25 | any identification flag | quarantine `unmatched_vendor_line` | `reconciliation.py:366` |
| 26 | quantity flags, all vendor qty 0 | `pending` `partial_fulfillment` | `reconciliation.py:382` |
| 27 | quantity flags, any vendor qty > 0 | `mismatched` | `reconciliation.py:391` |
| 28 | customs on and not (CUSTOMS+SHIPPING) | `blocked_customs` | `reconciliation.py:398` |
| 29 | `MergeNamingError` | quarantine `packet_naming_failed` | `reconciliation.py:414` |
| 30 | merge refused | prior status restored (W-8) | `reconciliation.py:435` |
| 31 | merged with no invoice number | naming flag priority 3 recorded | `reconciliation.py:444` |
| 32 | customs off | `is_blocked` â†’ `False`, gate transparent | `customs.py:17` |
| 33 | set already merged (auto) | return the existing packet path | `merge.py:195` |
| 34 | status in blocked trio | `None` â€” no merge | `merge.py:200` |
| 35 | `is_blocked(ps)` | `None` â€” no merge | `merge.py:213` |
| 36 | total line items == 0 | `None` â€” W-22, no numeric evidence | `merge.py:223` |
| 37 | no SI number and no PO number | `MergeNamingError` | `merge.py:233` |
| 38 | output filename owned by another set | `MergeNamingError` â†’ quarantine | `merge.py:141` |
| 39 | a stored PDF vanished at merge time | `FileNotFoundError` â†’ no partial packet | `merge.py:163` |
| 40 | set already merged (force) | return the existing packet â€” still immutable | `merge.py:279` |
| 41 | force merge, no documents | `MergeNamingError` | `merge.py:288` |
| 42 | justification supplied but < 20 chars | `ValueError` | `quarantine.py:289` |
| 43 | delete a non-quarantined set | `ValueError` â†’ HTTP **409** | `quarantine.py:313` |
| 44 | PO number normalises to a Windows device name | `_` prefix | `quarantine.py:65` |
| 45 | PO number blank | `UNIDENTIFIED_PO_{fingerprint}` | `quarantine.py:62` |
| 46 | PO Set locked by an active action | HTTP **409** | `locking.py:35` â†’ `po_sets.py:70` |
| 47 | lock older than timeout | auto-released on every read view | `po_sets.py:112` |
| 48 | sync lock held | HTTP **409** / `{"status":"skipped"}` | `sync_lock.py:96` |
| 49 | sync sidecar older than 3600 s | lock + sidecar unlinked (dead holder) | `sync_lock.py:63` |
| 50 | upload non-PDF extension | **422** | `dashboard.py:444` |
| 51 | upload over 100 MB | **422** | `dashboard.py:434` |
| 52 | upload missing `%PDF` magic | **422** | `dashboard.py:481` |
| 53 | upload with 0 pages | **422** | `dashboard.py:488` |
| 54 | upload whose hash belongs to another set | **409** naming the owner | `dashboard.py:463` |
| 55 | re-upload the same file to the same set | 302 with a "nothing was changed" notice | `dashboard.py:519` |
| 56 | reclassify to a type outside the allowlist | **422** | `dashboard.py:707` |
| 57 | unknown `status` query param | empty list, not an error | `dashboard.py:58` |
| 58 | HTMX request to `/dashboard` | fragment only, no page shell | `dashboard.py:173` |
| 59 | unknown status / bad locale / bad legal_order | fail fast at startup | `config.py` validators |


---
---

# PART III â€” MATCHING & RECONCILIATION, EXECUTED

> **Sourcing rule for Part III.** Every code statement is quoted from
> `src/app/`. Every numeric output is observed by importing those modules and
> running them. Every input *string* is either (a) quoted verbatim from a
> program file, or (b) constructed by me for the purpose of a specific probe
> and labelled as such. **Nothing here is taken from `tests/` or from any
> database.**
>
> The document strings used below all come from **the program's own prompt
> few-shots** in `src/app/services/extraction.py:83-87`:
>
> | Short name | String | Source line |
> |---|---|---|
> | `NUT` | `NUT, HEX 9/16 IN-12 UNC GRADE B YELLOW ZINC PLATED` | `extraction.py:83` |
> | `WASHER` | `WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS` | `extraction.py:84` |
> | `LOCK` | `WASHER, LOCK, 3/8" - MFG: FLY` | `extraction.py:85` |
> | `VALVE` | `GATE VALVE 2IN CL150 - Line Item - 10` | `extraction.py:86` |
> | `SHORT` | `WASHER, FLAT SAE 1/4 IN` | `extraction.py:87` |
>
> and from the **docstring examples** in `src/app/services/grouping.py:5,33,34,39,234`.
> Line-item numbers and quantities in the scenarios are mine, chosen to drive
> a specific branch; the descriptions are the program's own.

---

## 28. `normalize_line_no` â€” observed

Quoted logic (`app/services/matching.py:41`):

```python
if s is None:
    return ""
t = str(s).strip()
if not t:
    return ""
m = re.match(r"^0*(\d.*)$", t, re.DOTALL)
if not m:
    return t  # <- non-numeric-leading returns verbatim
m2 = re.match(r"^(\d+)(.*)$", m.group(1), re.DOTALL)
if m2 is None:
    return t
num = m2.group(1).lstrip("0") or "0"
return num + m2.group(2)
```

Observed:

| Input | Output | Path taken |
|---|---|---|
| `None` | `''` | line 1 |
| `''` | `''` | line 2 |
| `'  '` | `''` | strip, then empty, line 2 |
| `'01'` | `'1'` | `0*` eats the zero |
| `'001'` | `'1'` | |
| `' 001 '` | `'1'` | strip + `0*` |
| `' 12 '` | `'12'` | strip only |
| `'10'` | `'10'` | `lstrip("0")` is a no-op |
| `'0'` / `'00'` / `'000'` | `'0'` | `lstrip("0") or "0"` fallback |
| `'1-1'` | `'1-1'` | suffix preserved |
| `'007-1'` | `'7-1'` | zeros stripped, hyphen kept |
| `'1a'` | `'1a'` | suffix preserved |
| `'A3'` | `'A3'` | no leading digit, line 5, verbatim |
| `'-1'` | `'-1'` | no leading digit, verbatim |
| `'1.1'` | `'1.1'` | verbatim |

The line-5 early return is what preserves `1a`, `A3` and `-1`: a value that does
not begin with a digit is never touched, so it can never be silently rewritten
into a different key.

---

## 29. `normalize_po_no` â€” observed

Quoted logic (`app/services/grouping.py:43-87`) reduces to:

1. `None` -> `""`; non-`str` -> **`TypeError`**
2. strip
3. `if "," in s: s = s.rsplit(",", 1)[0]`
4. `re.split(r"[^A-Za-z0-9]+", s)`, drop empties
5. **first** token: drop if in `_LEADING_LABEL_WORDS` or `_LABEL_WORDS`; else
   strip the first matching `_LEADING_PREFIXES` entry (longest first) when
   `len(up) > len(prefix)`
6. **later** tokens: drop only if in `_LABEL_WORDS`
7. `"".join(kept).upper()`

Observed, using the docstring inputs at `grouping.py:33-34` and `:234`:

| Input | Output |
|---|---|
| `'161538'` | `161538` |
| `'PO 161538'` | `161538` |
| `'PO, Rev # 161538,0'` | `161538` |
| `'PO-210851'` | `210851` |
| `'PO161538'` | `161538` |
| `'210851'` | `210851` |
| `'8300023893'` | `8300023893` |
| `'3049PO123'` | `3049PO123` |
| `'P106420232'` | `P106420232` |
| `'SIV-DTS-25-477'` | `SIVDTS25477` |
| `'D7264-PO-186000-013-01'` | `D7264PO18600001301` |
| `'D7264-PO186000-013-01-'` | `D7264PO18600001301` |

**The load-bearing pair, and it is the program's own stated reason.** The comment
at `grouping.py:33-35` says:

> "PO" is a label ONLY in leading position. Mid-string it is part of a
> structured code: the PO prints D7264-PO-186000-013-01 while the DN and SI
> print D7264-PO186000-013-01-, so dropping it anywhere but the front breaks
> one spelling or the other.

Observed: both spellings produce `D7264PO18600001301`. The split on
`[^A-Za-z0-9]+` yields `['D7264','PO186000','013','01']`; `PO186000` is not in
`_LABEL_WORDS`, so it survives whole. **The claim in the comment is verified by
execution.**

**`'3049PO123'` -> `3049PO123`, unchanged.** Token 0 is not a label word, and
none of the `_LEADING_PREFIXES` match its *start* (`PURCHASEORDER`, `PONO`,
`PO.NO`, `PO` all fail `startswith` on `"3049..."`). A mid-string `PO` inside a
leading token is preserved.

**`'P106420232'` -> unchanged.** Quoted from `grouping.py:38-40`:

> Longest first, so "PO161538" loses "PO" rather than just "P". Bare "P" is
> deliberately absent: P106420232 is a real McDermott code, not a label.

Verified: the prefix list is `("PURCHASEORDER","PURCHASEORDERS","PONO","PO.NO","PO")`
-- no bare `P` -- so `"P106420232"` is untouched.

**`'SIV-DTS-25-477'` -> `SIVDTS25477`.** This is the sibling-filename shape named
at `grouping.py:234` (`e.g. SIV-DTS-25-477`). It is a *document* number, not a PO
number; `grouping.py:289-291` only ever takes the first four dash-segments as a
`LIKE` prefix, never normalises a whole filename.

---

## 30. `_norm` â€” observed

Quoted logic (`app/services/matching.py:31-38`):

```python
if not s:
    return ""
s = s.lower()
s = re.sub(r"(\d)([A-Za-z])", r"\1 \2", s)  # digit -> letter
s = re.sub(r"([A-Za-z])(\d)", r"\1 \2", s)  # letter -> digit
return s.strip()
```

Observed on the program's own few-shot strings, plus constructed cases:

| Input | Output |
|---|---|
| `WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS` | `washer, flat sae 1/4 in yellow zinc plated cs` |
| `WASHER, LOCK, 3/8" - MFG: FLY` | `washer, lock, 3/8" - mfg: fly` |
| `GATE VALVE 2IN CL150` | `gate valve 2 in cl 150` |
| `10KG` *(constructed)* | `10 kg` |
| `M3` *(constructed)* | `m 3` |
| `1/4IN` *(constructed)* | `1/4 in` |
| `2IN CL150` *(constructed)* | `2 in cl 150` |
| `AB12CD` *(constructed)* | `ab 12 cd` |

**`GATE VALVE 2IN CL150` -> `gate valve 2 in cl 150`.** Both regexes fire:
`2I` -> `2 I`, then `L1` -> `L 1`. A vendor writing `2IN CL150` and a PO writing
`2 IN CL150` therefore produce the same token stream -- shown scoring exactly
100.00 in section 32.

**`_norm` does not strip punctuation.** Commas, periods, hyphens, quotes and
slashes all survive. Only case and digit/letter adjacency change, so
punctuation-heavy vendor descriptions are compared *with* that punctuation.

---

## 31. `parse_quantity_scaled` â€” observed

Quoted logic (`app/services/sanitizer.py:33-74`), `MAX_DECIMAL_PLACES = 2`,
`locale="en_IN"`:

```python
if raw is None or not str(raw).strip():
    raise ValueError("Quantity string is empty.")
val = parse_decimal(
    cleaned, locale=locale, strict=True
)  # NumberFormatError/ValueError -> ValueError
if not val.is_finite():
    raise ValueError(f"Quantity is not a finite number: {raw!r}")
if val <= 0:
    raise ValueError(f"Quantity must be strictly positive: {raw!r}")
if val.adjusted() > 12:
    raise ValueError(f"Quantity is implausibly large: {raw!r}")
normalised = Decimal(str(val)).normalize()
exponent = normalised.as_tuple().exponent
decimals = -exponent if exponent < 0 else 0
if decimals > MAX_DECIMAL_PLACES:
    raise ValueError(f"Quantity has {decimals} significant decimals (max 2): {raw!r}")
return int(val * 1000)
```

**All inputs below are constructed by me** for this probe; no document string is
implied.

### Accepted

| Input | Returned | Printed |
|---|---|---|
| `1` | `1000` | 1 |
| `50` | `50000` | 50 |
| `12.5` | `12500` | 12.5 |
| `12.45000000` | `12450` | 12.45 |
| `5.000` | `5000` | 5 |
| `1234` | `1234000` | 1234 |
| `1,000` | `1000000` | 1000 |
| `1,00,000` | `100000000` | 100000 |
| `12,34,567.89` | `1234567890` | 1234567.89 |
| `1,20,000.50` | `120000500` | 120000.5 |
| `1234567.89` | `1234567890` | |
| `1.5e3` | `1500000` | 1500 |

### Rejected -- every one raises exactly `ValueError`

| Input | Message | The comment that explains it |
|---|---|---|
| `''` / `None` | `Quantity string is empty.` | |
| `1 000` | `Invalid numeric format: '1 000'` | `sanitizer.py:13-14` -- *"Inner spaces / bad grouping fail loudlyâ€¦ A space is never guessed: it could be a separator or a typo."* |
| `1,200,000` | `Invalid numeric format: '1,200,000'` | Western grouping under `en_IN`; the accepted `1,20,000.50` above shows the locale takes Indian grouping. Refused, not reinterpreted. |
| `1.234` | `Quantity has 3 significant decimals (max 2): '1.234'` | `sanitizer.py:10-12` -- *"a third significant decimal means the printed string is something else - most often a European thousands separator ("1.234" meaning 1234), which would otherwise be mis-scaled by 1000x."* |
| `1.2345` | `Quantity has 4 significant decimals (max 2): '1.2345'` | same |
| `0` | `Quantity must be strictly positive: '0'` | |
| `-5` | `Quantity must be strictly positive: '-5'` | |
| `INF` | `Quantity is not a finite number: 'INF'` | `sanitizer.py:48-50` -- *"babel's strict parse still accepts INF / NaN / -INFâ€¦ `val <= 0` is False for NaN and the exponent tuple for Infinity/NaN is a string, which would raise TypeError below and escape the clean quarantine path."* |
| `NaN` | `Quantity is not a finite number: 'NaN'` | same |
| `1e999999999` | `Quantity is implausibly large: '1e999999999'` | `sanitizer.py:56-62` -- *"int(val * 1000) on that would try to materialise a billion-digit integer - a memory bomb from a single mis-read field. The ceiling is 1e12 actual units."* |

**The 2-dp rule is asymmetric on purpose.** `12.45000000` is *accepted* because
`normalize()` runs **before** the precision check, so the eight printed zeros
collapse and the real value (2 dp) passes. `1.234` is *rejected* because its three
decimals survive normalisation. The comment at `sanitizer.py:7-8` gives the
reason: *"Trailing zeros are NOT precisionâ€¦ Vendors emit these constantly
(Excel/OCR round-trips), and rejecting them would quarantine documents that are
perfectly legible."*

**Nothing but `ValueError` escapes.** `extraction._parse_scaled_int`
(`extraction.py:166-185`) maps `ValueError` to `0` and catches every other
exception to `0`, so the sanitizer can never propagate an unexpected type into
the extraction path.

**And `0` is not silent.** `reconciliation.py:329` quarantines the whole set
when any line is `<= 0`, so a mis-parse surfaces as `non_positive_quantity`
rather than as a quiet zero that reconciles against another quiet zero.

---

## 32. The description fallback, scored

Quoted logic (`app/services/matching.py:107-123`):

```python
v_desc = _norm(vendor_line.get("description") or "")
if not v_desc:
    return ""
best_key, best_score = "", 0.0
for key, p_desc in po_desc.items():
    if not p_desc:
        continue
    score = fuzz.token_sort_ratio(v_desc, p_desc)
    if score > best_score:  # <- strictly greater: first key wins a tie
        best_key, best_score = key, score
return best_key if best_score >= threshold else ""
```

Entered from `group_by_line_no:93` only under `if not key and desc_threshold:` --
only for a vendor row with **no** usable line number, and only when the threshold
is truthy.

### 32.1 Measured scores, program-file strings

| Vendor description | PO description | Score | >= 85? | Note |
|---|---|---|---|---|
| `GATE VALVE 2 IN CL150` *(constructed spacing)* | `GATE VALVE 2IN CL150` | **100.00** | pass | `_norm` erased the difference |
| `GATE VALVE 2IN CL150-10` *(constructed)* | `GATE VALVE 2IN CL150` | **93.62** | pass | |
| `10- GATE VALVE 2IN CL150` *(constructed)* | `GATE VALVE 2IN CL150` | **91.67** | pass | |
| `GATE VALVE 2IN CL150 - Line Item - 10` -- **the program's own string, `extraction.py:86`** | `GATE VALVE 2IN CL150` | **72.13** | **fail** | |
| `GATE VALVE 2IN CL150 Line Item - 10` *(constructed)* | `GATE VALVE 2IN CL150` | 74.58 | **fail** | |
| `LINE ITEM - 10 GATE VALVE 2IN CL150` *(constructed)* | `GATE VALVE 2IN CL150` | 74.58 | **fail** | |
| `WASHER, FLAT SAE 1/4 IN` -- `extraction.py:87` | `WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS` -- `:84` | 67.65 | fail | same product, one truncated |
| `NUT, HEX 9/16 IN-12 UNC GRADE B YELLOW ZINC PLATED` -- `extraction.py:83` | `WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS` -- `:84` | 56.84 | fail | two different products |
| `WASHER, LOCK, 3/8" - MFG: FLY` -- `extraction.py:85` | `WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS` -- `:84` | 40.54 | fail | two different products |

### 32.2 Measured: the printed `Line Item - N` marker defeats the fallback

The program's own re-indexed-DN few-shot (`extraction.py:86`) is:

```
'Re-indexed DN (side column lies, use the embedded marker): {"document_type":"DN",
 "document_number":"GDN-RHO-25-513","po_reference":"8300023893","line_items":[
 {"line_item_no":"10","description":"GATE VALVE 2IN CL150 - Line Item - 10",...}]}'
```

and the prompt instructs (`extraction.py:64`):

> description: COMPLETE, do not truncate. Keep the 'Line Item - N' text inside it
> if it was printed there.

**Measured consequence:** that description scores **72.13** against the bare
product name -- 12.87 points below the 85 threshold. Across marker placements:

| Placement | Score |
|---|---|
| bare `GATE VALVE 2IN CL150` | 100.00 |
| `GATE VALVE 2IN CL150-10` | 93.62 |
| `10- GATE VALVE 2IN CL150` | 91.67 |
| `GATE VALVE 2IN CL150 - Line Item - 10` | **72.13** |
| `GATE VALVE 2IN CL150 Line Item - 10` | 74.58 |
| `LINE ITEM - 10 GATE VALVE 2IN CL150` | 74.58 |

`token_sort_ratio` sorts tokens and compares the whole joined string, so the three
extra tokens `line`, `item`, `10` dilute the score below the threshold. **A
numberless vendor row whose description carries the printed marker in word form
cannot be rescued by description** -- it becomes an orphan and quarantines the set
via `unmatched_vendor_line`.

The direction is safe (a false quarantine, never a wrong merge) and consistent
with the design: the prompt tells the model to put the marker into `line_item_no`
(`extraction.py:60-64`, resolution order `(a)` embedded marker first, `(b)`
printed side column second), so the fallback never has to carry it. The
description path is a genuine fallback, not a substitute.

### 32.3 Measured: `_best_desc_key` returns `""` rather than guessing

Against one PO line keyed `"1"`, description `WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS`:

| Call | Result |
|---|---|
| identical description, threshold 85 | `'1'` |
| empty vendor description | `''` |
| `ZZZ QQQ` (score 7.69) | `''` |

### 32.4 Measured: best-key selection across three PO lines

PO lines `10` = `GATE VALVE 2IN CL150`, `12` = `NUT, HEX ...`, `1` = `WASHER, FLAT SAE ...`:

| Numberless vendor description | best | score | all three |
|---|---|---|---|
| `GATE VALVE 2 IN CL150` | `10` | **100.00** | 10: 100.0, 12: 33.3, 1: 35.8 |
| `GATE VALVE 2IN CL150 - Line Item - 10` | `10` | 72.1 | 10: 72.1, 12: 38.2, 1: 40.5 -> **all < 85, so `""` -> orphan** |
| `WASHER, FLAT SAE 1/4 IN` | `1` | 67.6 | 10: 48.9, 12: 30.1, 1: 67.6 -> `""` -> orphan |
| `WASHER, LOCK, 3/8" - MFG: FLY` | `1` | 40.5 | 10: 31.4, 12: 20.3, 1: 40.5 -> `""` -> orphan |
| `UNRELATED SPARE PART` *(constructed)* | -- | 33.8 | 10: 33.3, 12: 31.4, 1: 33.8 -> `""` -> orphan |

Third row: `WASHER, FLAT SAE 1/4 IN` (`:87`) against
`WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS` (`:84`) is the **same product** as
the program presents it, and it scores 67.65 -- a failure. A truncated
description loses the fallback.

### 32.5 Measured: `desc_threshold=0` disables the path

```
PO line 1 = WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS, qty 10000
vendor row: line_item_no = None, description identical, qty 10000

  threshold 0  -> po={'1': 10000}  vendor={}                orphans=['no_line_number_and_no_description_match']
  threshold 85 -> po={'1': 10000}  vendor={'1': 10000}     orphans=[]
```

The guard `if not key and desc_threshold:` skips `_best_desc_key` entirely on a
falsy threshold -- it does not accept a zero-score match. `config.py:50-53` states
this is the only matching knob that is read.

---

## 33. `group_by_line_no` -- observed

### 33.1 Summing, not pairwise matching

```
PO:  line 1  100000
DN:  line 1   40000 ,  line 1  60000
SI:  line 1   70000 ,  line 1  30000

po_totals {'1': 100000}   dn_totals {'1': 100000}   si_totals {'1': 100000}   po_fail None   flags []
```

Described at `matching.py:9-11` and `:72-74`: *"Rows are summed rather than
matched one-to-one, so a single PO line delivered across several vendor rows
reconciles correctly."*

### 33.2 Leading zeros reconcile across documents

```
PO '01' 5000 , DN '1' 5000 , SI '001' 5000
po_totals {'1': 5000}  dn_totals {'1': 5000}  si_totals {'1': 5000}  flags []
```

### 33.3 Duplicate PO line numbers are summed on the PO side

```
PO: line 1 1000 , line 1 2000      -> po_totals {'1': 3000}
DN: line 1 3000 ;  SI: line 1 3000 -> no flags
```

`po_totals[key] = po_totals.get(key, 0) + qty` at `matching.py:85`. A PO that
prints one number twice sums, exactly as the vendor side does.

### 33.4 The PO-side abort discards everything

```
PO:  line 1 1000 ,  line None 2000
DN:  line 1 1000 ,  line 9    50

observed return: po_totals={}  dn_totals={}  orphans=[]  po_fail='po_line_missing_line_item_no'
```

Quoted at `matching.py:82-84`:

```python
if not key:
    # A PO line we cannot address is unresolvable by definition.
    return {}, {}, [], "po_line_missing_line_item_no"
```

The valid line `1` **and** the orphan `9` are both thrown away. The function
returns empty values, so `compare_po_set_lines` (`reconciliation.py:44-45`) builds
both pools from empty lists. `reconciliation.py:346` catches `po_fail` **before**
the flags branch and quarantines. `matching.py:17` states the intent: *"Anything
that fails quarantines the whole set. There is no partial pass."*

### 33.5 The three-way vendor branch

`matching.py:91-102`:

| Condition | Outcome |
|---|---|
| key present and in `po_totals` | `vendor_totals[key] += quantity` |
| no key **and** `desc_threshold` truthy | `_best_desc_key(...)`, then re-test |
| still no key | orphan, `why="no_line_number_and_no_description_match"` |
| key not in `po_totals` | orphan, `why="no_po_line_with_this_number"` |

---

## 34. `compare_aggregates` -- observed

### 34.1 Orphans suppress quantity reporting

`matching.py:137-146` returns orphans **in place of** diffs:

```
po_totals={'1':1000,'2':2000}  vendor_totals={'1':1000}  orphans=[line 9]
-> [{'line':'9','po_qty':None,'vendor_qty':50,'reason':'no_po_line_with_this_number'}]
```

versus no orphan:

```
po_totals={'1':1000,'2':2000}  vendor_totals={'1':1000}  orphans=[]
-> [{'line':'2','po_qty':2000,'vendor_qty':0,'reason':'quantity_mismatch'}]
```

Quoted at `matching.py:133-135`: *"Orphans are reported in place of quantity
differences: a line that resolves to nothing is an identity failure, and
reporting a quantity difference for it would misdescribe the problem to the
reviewer."*

The orphan's display key is `o.get("line_item_no") or o.get("description","")[:40]`
(`matching.py:140`), so a numberless orphan is shown by 40 characters of its
description.

### 34.2 An unmentioned PO line is `0`, and `0` is a mismatch

`matching.py:150-151`: `v_qty = vendor_totals.get(key, 0)`, then
`if v_qty != po_qty`. Since every PO quantity is `> 0` by the time this runs
(gated at `reconciliation.py:329`), a line the vendor never mentioned always
mismatches.

---

## 35. `compare_po_set_lines` -- twelve observed scenarios

Line-item numbers and quantities are mine. Descriptions are the program's own
few-shots. Every table is observed output of the real function.

### 35.1 Best case -- split delivery, split invoice

```
PO  1 = 100000
DN  1 =  40000 + 60000
SI  1 =  70000 + 30000
po {'1': 100000} | dn {'1': 100000} | si {'1': 100000} | po_fail None
(no flags)
```

### 35.2 Leading zeros across all three sides

```
PO '01' 5000 , DN '1' 5000 , SI '001' 5000
po {'1': 5000} | dn {'1': 5000} | si {'1': 5000} | po_fail None
(no flags)
```

### 35.3 Short delivery -- two flags, one per pool

```
PO 1 = 100000 ;  DN 1 = 60000 ;  SI 1 = 60000
p2 quantity DN line='1' po=100000 ven=60000 quantity_mismatch
p2 quantity SI line='1' po=100000 ven=60000 quantity_mismatch
```

Both pools are compared independently (`reconciliation.py:44-45`), so a shortfall
is reported twice -- once as *delivered*, once as *invoiced*.

### 35.4 Over-delivery

```
PO 1 = 100000 ;  DN 1 = 120000 ;  SI 1 = 100000
p2 quantity DN line='1' po=100000 ven=120000 quantity_mismatch
```

One flag, because the SI pool agreed. The line number resolves fine, so it is a
quantity flag and not an orphan.

### 35.5 DN satisfied, SI short

```
PO 1 = 100000 ;  DN 1 = 100000 ;  SI 1 = 80000
p2 quantity SI line='1' po=100000 ven=80000 quantity_mismatch
```

A single-sided invoice shortfall still fails the set.

### 35.6 Vendor orphan line

```
PO 1 = 1000 ;  DN 1 = 1000 + 9 = 500 ;  SI 1 = 1000
po {'1': 1000} | dn {'1': 1000} | si {'1': 1000} | po_fail None
p1 identification DN line='9' po=None ven=500 no_po_line_with_this_number
```

`priority 1`. The SI pool produced no flag, because it had no orphans and its
totals matched.

### 35.7 PO line with no number

```
PO 1 = 1000 , None = 2000
po {} | dn {} | si {} | po_fail 'po_line_missing_line_item_no'
```

### 35.8 Duplicate PO numbers

```
PO 1=1000 , 1=2000 ;  DN 1=3000 ;  SI 1=3000
po {'1': 3000} | dn {'1': 3000} | si {'1': 3000} | po_fail None
(no flags)
```

### 35.9 Numberless vendor row rescued by description

```
PO 1 = 10000 (WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS)
DN None = 10000 (identical description)
SI 1 = 10000
po {'1': 10000} | dn {'1': 10000} | si {'1': 10000} | po_fail None
(no flags)
```

Score 100.00 clears 85, the row is keyed to line `1`, and the set reconciles.

### 35.10 Partial delivery -- undelivered PO lines

```
PO 1=1000, 2=2000, 3=3000 ;  DN 1=1000 ;  SI 1=1000
po {'1':1000,'2':2000,'3':3000} | dn {'1':1000} | si {'1':1000} | po_fail None
p2 quantity DN line='2' po=2000 ven=0
p2 quantity DN line='3' po=3000 ven=0
p2 quantity SI line='2' po=2000 ven=0
p2 quantity SI line='3' po=3000 ven=0
```

**Four flags, all with `vendor_quantity == 0`.** This is the one arithmetic shape
that becomes `pending` rather than `mismatched` -- see section 37.

### 35.11 Docket restart, shape 1: the restarted number collides

```
PO:  line 10 = GATE VALVE 2IN CL150   2000
     line 12 = NUT, HEX ...          50000
DN:  line  1 = GATE VALVE 2IN CL150   2000     <- side column restarted at 1
     line  1 = NUT, HEX ...          50000
SI:  line 10 = ... 2000 ,  line 12 = ... 50000

po {'10': 2000, '12': 50000} | dn {'1': 52000} | si {'10': 2000, '12': 50000}
p2 quantity DN line='1'  po=2000  ven=52000
p2 quantity DN line='12' po=50000 ven=0
```

Both vendor rows collapse into key `1`, inflating it to 52000 while PO line `10`
is only 2000, and PO line `12` goes to zero. The SI side is clean, so both flags
are DN-only.

### 35.12 Docket restart, shape 2: the restarted number is an orphan

Same two vendor rows, but the PO is numbered `1` and `2`:

```
PO:  line 1 = GATE VALVE 2IN CL150 2000 ,  line 2 = NUT, HEX ... 50000
DN:  line 1 = GATE VALVE 2IN CL150 2000 ,  line 1 = NUT, HEX ... 50000
SI:  line 1 = ... 2000 , line 2 = ... 50000

po {'1': 2000, '2': 50000} | dn {'1': 52000} | si {'1': 2000, '2': 50000}
p2 quantity DN line='1' po=2000  ven=52000
p2 quantity DN line='2' po=50000 ven=0
```

**The docket-restart failure takes two shapes depending on the PO's own
numbering** -- inflated total in one, orphan-adjacent in the other -- **and both
land on `mismatched` with the same two flag kinds.** There is no branch that can
turn either into a merge.

This is the hazard the prompt names at `extraction.py:60-62`:

> line_item_no -- MOST IMPORTANT. The vendor's own side column (Sl No / Item No / #)
> is often just a running docket counter and repeats '1' on every page or every
> docket. It is NOT the PO line.

---

## 36. `explain()` -- observed strings

Quoted logic (`reconciliation.py:91-118`) and observed output:

| `reason` | flags | Observed |
|---|---|---|
| `None` | `[]` | `Awaiting further processing` |
| `"unknown_code"` | `[]` | `Awaiting further processing` |
| `"missing_dn_document"` | `[]` | `No delivery note in this set yet` |
| `"partial_fulfillment"` | `[]` | `Waiting on more deliveries or invoices` |
| `"partial_fulfillment"` | qty flag, both values `None` | `Waiting on more deliveries or invoices` |
| `None` | DN qty flag 100000 -> 60000 | `PO, delivery, and invoice quantities do not agree: delivered 60 of 100` |
| `"quantity_mismatch"` | SI qty flag 100000 -> 80000 | `PO, delivery, and invoice quantities do not agree: invoiced 80 of 100` |

Two decisions encoded in the code, both stated in its own comments.

**The verb comes from the pool** (`reconciliation.py:115`):
`verb = "delivered" if f.get("pool") == "DN" else "invoiced"`, and the numbers
are `f.vendor_quantity / 1000 :g` -- 60, not 60000.

**A `mismatched` set carries no reason code**, and `reconciliation.py:96-104` says
why:

> A `mismatched` set carries no reason code, and defaulting it to "Awaiting
> further processing" told the reviewer to wait on a set that will never resolve
> itself and needs a human -- the same failure class as showing a quarantine as a
> wait.

Hence the ladder: unknown or absent code with quantity flags present becomes
`quantity_mismatch`; unknown or absent code with no quantity flags stays
`Awaiting further processing`. The second row of the table confirms the fallback
fires only when quantity flags exist. `explain` never raises and never returns an
empty string.

---

## 37. From comparison to status -- the deciding condition

The comparison result alone decides nothing. The exact lines are
`reconciliation.py:363-393`:

```python
if flags:
    if any(f.get("type") == "identification" for f in flags):
        ps.status = POSetStatus.quarantined
        quarantine_copy(ps.id, cfg, reason="unmatched_vendor_line", flags=flags)
        return {"status": "quarantined", ...}

    qty_flags = [f for f in flags if f.get("type") == "quantity"]
    has_real_disagreement = any((f.get("vendor_quantity") or 0) > 0 for f in qty_flags)

    if not has_real_disagreement:
        ps.status = POSetStatus.pending
        return {"status": "pending", "reason": "partial_fulfillment", ...}

    ps.status = POSetStatus.mismatched
    return {"status": "mismatched", ...}
```

**The single discriminator is `vendor_quantity > 0`.** Read off the observed
scenarios:

| Scenario | Flags | `any(vendor_qty > 0)` | Status | Reason code |
|---|---|---|---|---|
| 35.1 split delivery | none | -- | proceeds to customs + merge | -- |
| 35.2 leading zeros | none | -- | proceeds | -- |
| 35.3 short 60/60 | 2 quantity, vendor 60000 | `True` | **mismatched** | -- |
| 35.4 over 120 | 1 quantity, vendor 120000 | `True` | **mismatched** | -- |
| 35.5 SI short 80 | 1 quantity, vendor 80000 | `True` | **mismatched** | -- |
| 35.6 orphan | 1 **identification** | -- | **quarantined** | `unmatched_vendor_line` |
| 35.7 no number | -- | -- | **quarantined** | `po_line_missing_line_item_no` |
| 35.8 duplicate numbers | none | -- | proceeds | -- |
| 35.9 desc fallback | none | -- | proceeds | -- |
| 35.10 undelivered lines | 4 quantity, **all vendor 0** | `False` | **pending** | `partial_fulfillment` |
| 35.11 docket restart | 2 quantity, vendors 52000 and 0 | `True` | **mismatched** | -- |
| 35.12 docket restart | 2 quantity, vendors 52000 and 0 | `True` | **mismatched** | -- |

So `pending` arises in exactly one arithmetic shape: **every** flagged line has
`vendor_quantity == 0` -- the vendor reported nothing at all for those lines.

The comment at `reconciliation.py:377-379` states the reasoning:

> Partial delivery is only genuine when the vendor reported NOTHING for those
> lines. A line both sides reported with different quantities is a real
> disagreement, not an outstanding delivery.

**The limit this leaves, stated from the scenarios:** in 35.11 and 35.12 the
flagged line `2` has `vendor_quantity == 0` while a *sibling* line is inflated.
The engine cannot tell "line 2 is genuinely short" from "line 2's number was
never resolved because the docket counter restarted" -- both present as `0` on
that line. It resolves the ambiguity conservatively, toward `mismatched`,
because `reconciliation.py:363-365` explains an identity failure as
*"unresolvable identity, not a shortfall: quarantine rather than guess which PO
line it belongs to."*

---

## 38. What matching ignores, per the program

Each row is a negative claim verified by searching `src/app/`: the symbol is
either absent, or present but never read in a comparison.

| Signal | Verified state in `src/app/` |
|---|---|
| `unit_price` | stored (`models.py:122`) and written to the packet, but `matching.py` never references it. `extraction.py:115-118`: *"Prices take no part in matching or merging. Kept only because the column is NOT NULL and the merged packet carries it; the quantity is the sole reconciliation signal."* |
| `part_no` / SKU | no such column in `models.py`; `matching.py` has no reference. `models.py:1-6` states it was blank or unreliable across every real vendor sample reviewed. |
| UOM | not a field on `_VLMLineItem` (`extraction.py:91-118`). `extraction.py:73-75`: *"UOM itself is NOT extracted anywhere in this system. Never return it."* |
| row order / position | `matching.py` iterates lists but indexes everything by `normalize_line_no`; no positional comparison exists. |
| description conflicts | `_best_desc_key` (`matching.py:107`) uses the description only to *find* a key for a numberless row. Two numbered rows are never compared to each other. |
| ERP step-10 mapping | the `Line Item - N` marker is resolved by the VLM into `line_item_no` at extraction time (`extraction.py:60-64`); no mapping table exists in the program. |
| `fuzzy_margin`, `enable_sku_rescue`, `sanity_description_threshold` | removed from `MatchingConfig`. `config.py:53-59`: they *"were read by nothing... worse than inert -- an `enable_sku_rescue: true` next to a matcher that has no SKU rescue invites someone to 'fix' a feature that no longer exists."* |

**Two consequences worth stating plainly, both following from the table.**

*UOM.* Because the unit is discarded before storage, `10 KG` and `10 PCS` on the
same line number become the same `10000`. The program assumes one line number
means one unit of measure. The failure direction is a false quarantine, not a
wrong merge.

*Description conflicts.* Two PO lines sharing a number with contradictory text
are summed into one baseline, because `po_totals[key] += qty` (`matching.py:85`)
keys purely on the number. If the vendor split them differently, the totals will
not agree and the set quarantines. It cannot merge silently, because the summed
baseline must still equal the vendor's summed total.

---

## 39. Matching, condensed

1. **The only signal is the quantity.** Everything else is a key or a label.
2. **`line_item_no` is the key**, normalised for whitespace and leading zeros;
   hyphens, letters and non-digit-leading values survive. Compared as a string.
3. **Both sides are grouped and summed**, never matched row-to-row. One PO line
   across many vendor rows reconciles; one number printed twice on the PO sums.
4. **A PO line with no usable number fails the whole set immediately** --
   short-circuit at `matching.py:83`, discarding every partial result.
5. **A numberless vendor row may fall back to description similarity >= 85**, and
   only then; it never overrides a real line number; a falsy threshold disables
   the path entirely.
6. **A vendor group with no PO counterpart is an orphan** and quarantines.
7. **Orphans suppress quantity reporting**, so a reviewer is never shown an
   arithmetic difference for a line that does not resolve.
8. **A line the vendor never mentioned totals `0`**, which differs from any
   positive PO quantity and is therefore a quantity flag.
9. **DN and SI are compared independently.** Either can fail alone; a shortfall
   is reported twice, once per pool, with different verbs.
10. **Any identification failure quarantines. Any quantity disagreement where the
    vendor reported something is `mismatched`. Quantity flags where the vendor
    reported nothing are `pending`.**
11. **The verdict the dashboard shows is produced by the same function the engine
    calls** -- `compare_po_set_lines`, imported by
    `reconciliation._reconcile_po_set_inner` (`reconciliation.py:44`) and by
    `dashboard.po_set_detail_view` (`routes/dashboard.py:268`).
12. **No tolerance anywhere.** Exact integer equality on x1000 values.
