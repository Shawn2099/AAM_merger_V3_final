# AAM_merger_V3 — Technical Specification (SPEC.md)

**Status:** Draft v1, derived directly from `AAM_merger_V3_business_logic.md` (Final) plus decisions confirmed in the spec-drafting session below. No requirement in this document may be implemented in a way that contradicts the business logic doc — if a conflict is found, STOP and ask (see Section 1).

**Build scope:** Input folder → identification → extraction → grouping → matching → reconciliation → automatic merge → output folder. Distribution (Phase 2) is explicitly excluded from this document.

---

## 1. Agent Operating Rules (binding on any coding agent implementing this spec)

These rules sit above every other section and are non-negotiable regardless of how far into implementation the agent is.

1. **Ambiguity → ask, don't infer.** If any requirement in this document is unclear, contradicts another section, or doesn't cover the situation the agent has hit, the agent stops and asks the human — it does not pick a plausible interpretation and proceed. A wrong guess that compiles and runs is worse than a blocked task, because this system's failure mode (a bad merge going to the CA) is silent and costly.
2. **Stuck or looping → ask, don't keep retrying blind.** If the agent has attempted the same fix more than twice without resolving it, or is oscillating between two broken states, it stops and reports: what it tried, what happened each time, and what it currently believes the blocker is. It asks the human to investigate or decide, rather than attempting a third or fourth variation on its own.
3. **Verify, don't guess.** Before declaring any piece of functionality done, the agent runs it against real or realistic inputs and inspects the actual output — it does not reason about what the code "should" do and stop there. This applies especially to: extraction against real sample PDFs (see business doc Section 17 — real vendor samples exist and should be used), reconciliation math on constructed edge cases, and concurrency behavior under simulated simultaneous actions. A claim of "this should work" without an observed result is not acceptable as a completion signal.
4. **This spec is the source of truth over the agent's own training-data defaults.** Where this spec names a specific library, pattern, or version, that instruction wins over whatever the agent would otherwise default to from general knowledge — general "best practice" from training data may be stale or may not account for the Windows Server 2016 / dual-core constraint.

---

## 2. Purpose & Scope

AI-assisted document reconciliation system for a CA firm. Automates matching of Purchase Orders (PO), Delivery Notes (DN), and Sales Invoices (SI), producing one merged PDF per PO once quantities reconcile exactly. The CA is the final human reviewer; the system never claims final authority.

**In scope (this build):** ingestion, dedup, storage, classification, extraction, grouping, matching, aggregation, reconciliation, customs gate, merge, force merge, quarantine (including manual deletion), manual PDF merger, full web dashboard, audit logging.

**Out of scope (Phase 2, parked, not designed against):** post-merge distribution (company/buyer lookup, email drafting), authentication/roles, multi-server deployment.

Full business rules live in `AAM_merger_V3_business_logic.md` — this document translates those rules into buildable, testable, EARS-format requirements plus the technical architecture. Where this document restates a business rule, the business doc is definitive if there's ever a wording mismatch.

---

## 3. Roles (unchanged from business doc)

- **Employee** — copies files into input folder, triggers Sync, uses dashboard (all actions, no permission gating in v1).
- **CA** — reviews merged output outside the system. Not a system role/login.
- **System** — no user identity concept in v1. All audit entries attributed to "system," not a named person.

---

## 4. Glossary

Identical to business doc Section 3 (PO, DN, SI, PO Set, po_no, line_item_no, Aggregate DN/SI Qty, Combined document, Manual-upload document, Reconciliation, Merge, Quarantine). Do not redefine these terms differently anywhere else in code, UI copy, or comments.

---

## 5. System Architecture

### 5.1 Deployment topology & hardware-driven decisions

| Constraint | Decision | Rationale |
|---|---|---|
| Windows Server 2016, no containers assumed | Native Windows process, not Docker | Avoids WSL2/Hyper-V container dependency on a 2016 host; simplest reliable path |
| 2 CPU cores | Async I/O concurrency model for the web layer; explicit Prefect concurrency limits (not autoscaling) | CPU-bound parallelism doesn't scale past 2 cores — async avoids thread contention for I/O-bound work (VLM API calls, DB, file I/O) |
| 128 GB RAM | Not architected around — treated as headroom, not a design driver | No component in this system needs >2-4 GB working set at "modest volume." Do not add memory-hungry caching layers to "use" the RAM; that's solving a problem that doesn't exist |
| SQLite in WAL mode (locked decision) | Single-writer discipline: all writes go through one connection pool sized to expected concurrent writers (see 10) | WAL allows concurrent readers + one writer; 2 cores means write contention is inherently low, so this is not a bottleneck at this scale |
| Prefect server already running | Used via Prefect's Python client + a `process`-type work pool on the same host | No Kubernetes/Docker work pool — matches the single-Windows-host reality |
| Process supervision on Windows | NSSM (Non-Sucking Service Manager) wraps both the web app process and the Prefect worker process as Windows Services, auto-restart on crash | `gunicorn` does not run on Windows (Unix-only, fork-based) — this is the standard, non-deprecated Windows equivalent |
| "Launch from any computer on the LAN" (confirmed this session) | The app and its SQLite database run on ONE designated Windows Server 2016 host. Uvicorn binds to `0.0.0.0` on that host; other LAN machines access it via `http://<host-ip>:<port>` in a browser — no local install anywhere else. DB and all `stored_path`/quarantine/input/output folders stay local to that host. | Keeps this a normal single-host web app. Confirmed explicitly to avoid the alternative (DB on a network share), which SQLite's own documentation states is unsafe in WAL mode — WAL requires shared memory between processes, which SMB/UNC network filesystems cannot provide, risking silent corruption, not just slowness. |
| Realistic volume (confirmed this session): ~10 PO Sets/day, effectively single user | No load-driven scaling needed anywhere in this system | Directly simplifies §9 concurrency locking (still kept as a cheap safeguard, but not solving a real contention problem) and §10 NFR sizing |

### 5.2 Approved library table (pin exact versions in `requirements.txt`/lockfile at implementation time — check each for newer patch releases and any deprecation notices before pinning, per Section 1 rule 4)

| Purpose | Library | Minimum version (as of this spec) | Notes |
|---|---|---|---|
| Web framework | FastAPI | 0.124.x | ASGI, native Pydantic v2 integration, auto OpenAPI docs |
| ASGI server | Uvicorn | latest 0.3x | Run standalone, wrapped by NSSM — not Gunicorn (Windows-incompatible) |
| Data validation / schemas | Pydantic | v2.9+ | Required for `instructor`-based structured extraction and all API models |
| Structured LLM output enforcement | `instructor` | latest | Already locked decision — wraps OpenRouter client calls, enforces Pydantic schema on VLM output |
| Orchestration | Prefect | 3.x (latest 3.x) | Already running — use `process` work pool, not Docker/K8s pools |
| ORM / DB layer | SQLAlchemy | 2.x (sync engine, not `asyncio` extension) | See 5.3 for why sync, not async, DB access |
| DB migrations | Alembic | latest | Never hand-edit schema; every schema change is a migration |
| Fuzzy string matching | `rapidfuzz` | latest 3.x | Already locked decision (Section 8 of business doc) — MIT licensed, C++-backed, not the abandoned `fuzzywuzzy` |
| PDF concatenation | `pypdf` | latest | Pure-Python, actively maintained, replaces the deprecated `PyPDF2` name |
| Config/secrets management | `pydantic-settings` | latest | Env var + `.env` loading with validation — not manual `os.environ` parsing |
| Windows service wrapper | NSSM | latest stable | Not a Python package — external tool, documented separately in deployment runbook |
| Frontend rendering | Jinja2 (server-rendered) + HTMX + Alpine.js | latest | See 5.3 — deliberately not a React/SPA build for this scale |
| Testing | `pytest` + `pytest-asyncio` | latest | Standard, not a custom test runner |

**Ban list (explicit, do not implement even if it "would work"):** bare `except:` clauses; synchronous/blocking I/O calls inside `async def` route handlers; manual SQL string concatenation (use SQLAlchemy parameterized queries only); global mutable state for request/session handling; any custom retry/backoff loop where Prefect's built-in retry policy covers the case; any hand-rolled scheduler where Prefect's deployment scheduling covers the case; `PyPDF2` (deprecated, superseded by `pypdf`).

### 5.3 Key architecture decisions & rationale

- **Sync SQLAlchemy, not async/`aiosqlite`.** At this scale (modest volume, single low-core box), async SQLite drivers add complexity and known rough edges without a throughput benefit. FastAPI runs `def` (sync) route handlers in a threadpool automatically, so sync DB calls don't block the event loop for other requests. This avoids reinventing async-SQLite reliability work that isn't needed here.
- **Jinja2 + HTMX + Alpine.js, not a React SPA.** Full dashboard is in scope (per your decision), but a build-tooled SPA (Node toolchain, bundler, separate deploy artifact) is unjustified complexity for an internal LAN tool with a handful of concurrent users and no offline/mobile requirement. Server-rendered pages with HTMX for partial updates (e.g. refreshing the PO Set list after Sync, without a full page reload) meets every UI requirement in Section 8 with a single Python process and no separate frontend build/deploy step — directly serving the "no custom logic, no reinventing the wheel" instruction.
- **Prefect flow boundaries:** one flow per Sync run (ingestion → dedup → store → classify → extract → group → match → reconcile → merge, for every file discovered in that run). Each document's classify/extract step is a Prefect task, retried per Section 7.5's policy via Prefect's native `retries`/`retry_delay_seconds`, not custom retry code.
- **Windows Task Scheduler is NOT used for the midnight run.** Prefect's own deployment-level cron schedule handles it — avoids a second, redundant scheduling mechanism outside Prefect's visibility.

---

## 6. Data Model

### 6.1 `documents` table

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `sha256_hash` | text, unique, indexed | dedup key (Section 4.4 of business doc) |
| `original_filename` | text | as found in input folder |
| `stored_path` | text | internal permanent copy path (Section 4.5) — never deleted automatically |
| `doc_type` | enum: `PO, DN, SI, COMBINED, CUSTOMS, SHIPPING, COMMERCIAL_INVOICE, UNKNOWN` | |
| `po_no_raw` / `po_no_normalized` | text | normalization rule: strip non-alphanumeric, uppercase (Section 7 of business doc) |
| `dn_no`, `si_no`, `invoice_no` | text, nullable | |
| `extraction_status` | enum: `pending, processing, valid, failed` | binary valid/invalid, no confidence score (Section 6.4) |
| `extraction_attempt_count` | int, default 0 | capped at 3 (Section 6.4) |
| `po_set_id` | FK, nullable | null until grouped or if `UNKNOWN` |
| `created_at`, `updated_at` | timestamp | |

### 6.2 `line_items` table

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `document_id` | FK | |
| `line_item_no` | text, nullable | primary matching key |
| `description` | text | |
| `quantity` | integer (scaled ×1000, see 6.4) | |
| `unit_price` | integer (scaled ×1000) | |

Note: `part_no` and UOM are explicitly NOT stored (business doc 6.2, 6.3 — confirmed dropped fields). Do not add these columns even if a future document sample seems to have reliable data for them, without a spec change.

### 6.3 `po_sets` table

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `po_no_normalized` | text, indexed | grouping key |
| `status` | enum: `pending, mismatched, quarantined, blocked_customs, merged` | the five statuses (business doc Section 13) |
| `has_customs_toggle` | boolean, default false | |
| `customs_doc_count` | int, computed or tracked | must reach 2 (`CUSTOMS` + `SHIPPING`) before merge if toggle is on |
| `merged_output_path` | text, nullable | set only on `merged` |
| `merged_at` | timestamp, nullable | set only once, immutable after (business doc: merged is permanently closed) |
| `locked_by_action` | text, nullable | see Section 10 concurrency lock |
| `created_at`, `updated_at` | timestamp | |

### 6.4 Numeric representation

All quantities and prices stored as integers, scaled ×1000 to avoid float drift (business doc Section 10). Every comparison in reconciliation logic operates on these integers — never cast to float for comparison.

### 6.5 `audit_log` table

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `po_set_id` | FK, nullable | nullable because some entries may be system-level, not PO-Set-scoped |
| `action` | enum: `force_merge, quarantine_delete, manual_status_change` (extend only via spec change, not silently) | |
| `detail` | text/JSON | e.g. customs doc count at time of Force Merge |
| `timestamp` | timestamp | |
| `source` | text | "system" — no user identity in v1 (business doc Section 2) |

Force Merge and Quarantine Deletion both write here — confirmed in this session as requiring identical treatment (confirmation modal + permanent audit log entry). No other action in this build requires an audit log entry.

---

## 7. Functional Requirements (EARS notation)

Numbering mirrors business doc sections for traceability.

### 7.1 Ingestion (business doc §4)

- **[FR-4.1]** The system shall provide a manual "Sync" trigger accessible from the dashboard.
- **[FR-4.2]** The system shall run a scheduled ingestion pass at midnight via a Prefect deployment cron schedule.
- **[FR-4.3]** While an ingestion run (manual or scheduled) is in progress, if a new Sync trigger is received, the system shall reject it and display "Sync already running" rather than queuing it.
- **[FR-4.4]** If the midnight run does not execute (e.g. server was down), the system shall not run a catch-up job; the next Sync or midnight run shall process whatever remains in the input folder.
- **[FR-4.5]** Before treating a file as ready for processing, the system shall poll its file size at a minimum 2-second interval and shall only proceed once size is stable across two consecutive polls.
- **[FR-4.6]** For every file processed, the system shall compute its SHA-256 hash. If a document with the same hash already exists, the system shall not create a duplicate document record.
- **[FR-4.7]** The system shall copy every ingested file to an internal `stored_path` independent of the input folder original.
- **[FR-4.8]** The system shall clear a file from the input folder if and only if both: (a) a merged output exists for its PO Set, AND (b) its extracted data is persisted. The internal `stored_path` copy shall never be auto-deleted.
- **[FR-4.9]** If a file cannot be opened or parsed, the system shall route it through the same retry-then-`failed` path as an extraction failure (§7.5) — no separate failure state.

### 7.2 Classification (business doc §5)

- **[FR-5.1]** The system shall classify every ingested document into exactly one of: `PO, DN, SI, COMBINED, CUSTOMS, SHIPPING, COMMERCIAL_INVOICE, UNKNOWN` before any reconciliation logic runs.
- **[FR-5.2]** `CUSTOMS`, `SHIPPING`, `COMMERCIAL_INVOICE` documents shall never be sent through the VLM and shall never be used in reconciliation matching.
- **[FR-5.3]** If a document is classified `UNKNOWN`, the system shall place it in a separate unclassified holding area, not grouped into any PO Set and not auto-quarantined.

### 7.3 Extraction (business doc §6)

- **[FR-6.1]** The system shall extract each unique (post-dedup) document via a VLM using native PDF input (no rasterization) with a Pydantic schema enforced via `instructor`. Confirmed model: **GPT-5.6 Luna via OpenRouter**. The model identifier string shall be read from `config.yaml` (§16), never hardcoded, so a future model swap (e.g. to Sol as fallback, per prior VLM research) requires only a config change.
- **[FR-6.2]** The system shall extract document-level fields (doc type, `po_no`, `dn_no`, `si_no`, invoice number) and line-item-level fields (`line_item_no`, description, quantity, unit price) only. `part_no` and UOM shall not be extracted or stored.
- **[FR-6.3]** When extracting a PO, the system shall validate the extracted PO number against the corresponding number appearing on the matched DN/SI's own PO field, to guard against decoy PO codes on the source document.
- **[FR-6.4]** The system shall treat a split-across-lines description (header line + detail block) as a single line item, not two.
- **[FR-6.5]** If extraction fails (hard failure — API/network error, or soft failure — schema validation failure), the system shall retry up to 3 times with backoff. After 3 failures, the system shall mark that document `failed` without blocking matching/reconciliation for the rest of that PO Set.
- **[FR-6.6]** The system shall not compute or store any confidence score for extraction. Extraction status shall be binary: `valid` or `failed`.
- **[FR-6.7]** For a `COMBINED` document, the system shall issue a single VLM call prompted to return all identifiable sub-document sections (PO/DN/SI) in one structured response. If the model cannot confidently identify all expected sections, the system shall treat this as an extraction failure for that document (retry, then `failed`) — never a partial pass.
- **[FR-6.8]** The system shall provide two distinct user-triggered actions: "Redo/Re-extract" (re-sends to VLM, costs an API call) and "Redo matching" (re-runs matching against already-stored data, no API call). These shall not be combined into a single action.

### 7.4 Grouping (business doc §7)

- **[FR-7.1]** The system shall normalize every extracted `po_no` by stripping non-alphanumeric characters and uppercasing before use as a grouping key.
- **[FR-7.2]** All documents sharing the same normalized `po_no` shall belong to the same PO Set. A previously unseen `po_no` shall start a new PO Set.

### 7.5 Line-item matching (business doc §8)

- **[FR-8.1]** The system shall treat `line_item_no` as the sole primary matching key when present.
- **[FR-8.2]** When `line_item_no` is absent, the system shall fall back to fuzzy description matching using `rapidfuzz` token-sort similarity, with a threshold of 85% stored as a configurable constant (not hardcoded inline). Description matching shall never override or double-check a present `line_item_no` match.
- **[FR-8.3]** The system shall not use `slno`, positional order, or `part_no` as matching signals under any circumstance.
- **[FR-8.4]** When two documents of the same type reference the same `line_item_no` with consistent descriptions, the system shall sum their quantities (§7.6). When descriptions conflict for the same `line_item_no`, the system shall route the entire PO Set to `quarantined`.
- **[FR-8.5]** If a DN or SI line item cannot be matched to any PO line (no `line_item_no` match and fuzzy match also fails), the system shall route the entire PO Set to `quarantined`.

### 7.6 Aggregation & reconciliation (business doc §9–§11)

- **[FR-9.1]** For every PO line item, the system shall independently compute Aggregate DN Quantity (sum across all DNs) and Aggregate SI Quantity (sum across all SIs). These sums shall never be added together.
- **[FR-10.1]** For every PO line item, the system shall check `PO Quantity == Aggregate DN Quantity` AND, independently, `PO Quantity == Aggregate SI Quantity`, both as exact integer comparisons (no tolerance band). Both checks must independently pass.
- **[FR-10.2]** Reconciliation shall be evaluated line-by-line. One failing line shall fail the entire PO Set — there is no partial-pass state.
- **[FR-10.3]** If any quantity encountered is negative or zero, the system shall route that PO Set to `quarantined` rather than evaluating it as a normal reconciliation case.
- **[FR-11.1]** The system shall perform an exact-match price check as a secondary condition. A price-only mismatch shall not block a merge that quantity has already cleared, but shall still be flagged for reviewer visibility.
- **[FR-11.2]** When multiple flags exist on one line item, the system shall surface them to the reviewer in this priority order: (1) line-item identification, (2) quantity, (3) price.

### 7.7 Customs gate (business doc §12)

- **[FR-12.1]** The system shall allow a PO Set to be manually toggled "Has Customs" at any time regardless of current status.
- **[FR-12.2]** Once toggled, the system shall force the PO Set into `blocked_customs` and hold it there until both a `CUSTOMS` and a `SHIPPING` document have been manually uploaded. These shall be attached as-is, never sent to the VLM.
- **[FR-12.3]** A `COMBINED` document that is otherwise self-reconciled shall still wait on the 2 customs uploads if the toggle is on.
- **[FR-12.4]** `COMMERCIAL_INVOICE` shall be attachable at any time as an optional document and shall never itself be required to clear `blocked_customs`.

### 7.8 Status model (business doc §13)

- **[FR-13.1]** The system shall support exactly five PO Set statuses: `pending, mismatched, quarantined, blocked_customs, merged`. No other status shall be introduced without a spec change.
- **[FR-13.2]** Quarantine shall be a whole-PO-Set state only — never applied per line item.
- **[FR-13.3]** When a PO Set is quarantined, the system shall copy (not move) every document tied to that `po_no` into a physical quarantine folder.
- **[FR-13.4]** A `mismatched` PO Set that is still open (not `merged`) shall automatically re-evaluate on arrival of new or corrected data, and may move to `pending` or directly to `merged` without a manual trigger. The "Redo matching" action (§7.3) shall also be available to force re-evaluation without new data.

### 7.9 Quarantine resolution — manual deletion (confirmed this session, not in original business doc)

- **[FR-13.5]** The dashboard shall provide a "Delete" action on any `quarantined` PO Set.
- **[FR-13.6]** Deletion shall require a confirmation modal (irreversible action) and shall write a permanent `audit_log` entry (`action = quarantine_delete`), matching the treatment of Force Merge.
- **[FR-13.7]** Deletion shall remove only the `po_sets` DB row (and its associated `documents`/`line_items` rows scoped to that PO Set). It shall NOT delete: (a) the internal `stored_path` copies of the documents, (b) the physical quarantine folder copies. Both remain on disk indefinitely as the manual audit trail.
- **[FR-13.8]** After deletion, the human resolves the case using the separate Manual PDF Merger tool (§7.11), working from the quarantine folder copies. There is no automatic re-injection of the deleted PO Set back into the AI pipeline.
- **[FR-13.9]** If a document sharing the same `po_no` as a deleted PO Set later arrives via normal ingestion, the system shall treat it as a brand-new PO Set (same rule as any first-time `po_no` — §7.4), since the DB row no longer exists to conflict with.

### 7.10 Merge (business doc §14)

- **[FR-14.1]** Auto-merge shall fire only when every relevant line item across every associated document has cleanly reconciled — the PO Set must not be `mismatched` or `quarantined`, and must not be `blocked_customs` if the customs toggle is on.
- **[FR-14.2]** A `COMBINED` document shall be treated as self-reconciled and merge-ready only if the AI confidently identified PO, DN, and SI sections within it (§7.3, FR-6.7) — never merely because it's tagged `COMBINED`.
- **[FR-14.3]** The system shall concatenate documents into the output PDF in this fixed order: SI → DN → PO → (AWB → Customs Declaration, if customs applies). This order shall not vary by case.
- **[FR-14.4]** Merge shall be whole-document concatenation only. The system shall never re-sort, re-order, or edit page content of any source document, even when a document's internal line order doesn't follow PO order.
- **[FR-14.5]** The output filename shall be the Invoice/SI number, with no fallback naming logic (SI presence is guaranteed by the fact that merge cannot fire without a reconciled SI).
- **[FR-14.6]** Once a PO Set reaches `merged`, the system shall treat it as permanently closed — never re-opened, re-evaluated, or overwritten. A later document sharing that `po_no` shall start an entirely new PO Set.
- **[FR-14.7]** If both a `COMBINED` document and separate PO/DN/SI documents exist for the same `po_no` and both reach reconciled state, whichever completes first shall become the authoritative merge; the other shall remain visible but non-authoritative — no tie-breaking logic, no auto-quarantine of the collision.

### 7.11 Force Merge (business doc §14.1)

- **[FR-14.8]** Force Merge shall be available on any PO Set regardless of current status.
- **[FR-14.9]** Force Merge shall require a confirmation modal and shall be fully unconditional — it bypasses all matching logic and the customs gate, merging with whatever `CUSTOMS`/`SHIPPING` files exist at that moment (0, 1, or 2).
- **[FR-14.10]** Every Force Merge shall write a permanent `audit_log` entry (`action = force_merge`), including the customs document count present at time of use.

### 7.12 Manual PDF Merger (business doc §14.2)

- **[FR-14.11]** The system shall provide a fully separate manual merge tool: user uploads arbitrary files, reorders via drag-and-drop, and downloads the merged result. This tool shall create no database row and have no `po_no` association.
- **[FR-14.12]** The manual merger's output destination and filename shall be user-selectable in the UI at merge time — not auto-written to the automated pipeline's output folder or filename convention by default. The user may choose the same output folder if they wish, but the system shall not enforce it.
- **[FR-14.13]** The manual merger shall be visually and navigationally isolated in the UI from the automated pipeline dashboard, so it cannot be confused with "the AI pipeline."

### 7.13 AI/logic boundary (business doc §15)

- **[FR-15.1]** The AI/VLM's role shall be strictly limited to PDF → structured extraction. Every downstream step (grouping, matching, aggregation, comparison, status assignment) shall be deterministic code with no model call and no model-influenced judgment.

---

## 8. UI / Dashboard Requirements

Full web dashboard confirmed in scope. Required views/actions:

| View/Action | Requirement |
|---|---|
| Sync trigger | Button; disabled with "Sync already running" message while a run is in progress (FR-4.3) |
| PO Set list | Filterable/sortable by status (`pending, mismatched, quarantined, blocked_customs, merged`); shows `po_no`, status, document count, last updated |
| PO Set detail | Shows all associated documents, per-line reconciliation flags in priority order (FR-11.2), customs toggle state |
| Customs toggle | Available from PO Set detail regardless of status (FR-12.1) |
| Manual document upload | For `CUSTOMS`/`SHIPPING`/`COMMERCIAL_INVOICE` attachment |
| Redo/Re-extract vs Redo matching | Two distinct buttons, never merged (FR-6.8) |
| Force Merge | Requires confirmation modal; available from any PO Set (FR-14.8/9) |
| Quarantine review | Shows quarantine folder contents reference; provides Delete action with confirmation modal (FR-13.5/6) |
| Unclassified holding area | Separate view for `UNKNOWN` documents (FR-5.3), with manual reclassification action |
| Manual PDF Merger | Separate page/section, visually distinct navigation (FR-14.13); output filename/location selectable at merge time (FR-14.12) |
| Audit log view | Read-only list of `force_merge` and `quarantine_delete` entries |

Rendering approach: Jinja2 server-rendered templates, HTMX for partial refresh (e.g. PO Set list updates after Sync completes) — see §5.3.

---

## 9. Concurrency & Locking Rules

Confirmed this session: no auth in v1, but a simple safeguard against two employees colliding on the same PO Set is required.

- **[FR-CONC-1]** Every state-changing action on a PO Set (Force Merge, Delete, Redo/Re-extract, Redo matching, customs toggle, manual document attach) shall acquire a per-PO-Set lock (`locked_by_action` field, §6.3) for its duration.
- **[FR-CONC-2]** If a second request targets a PO Set that is currently locked, the system shall reject it with a clear "action already in progress on this PO Set" response (HTTP 409) rather than queuing or silently overwriting.
- **[FR-CONC-3]** The dashboard shall visually disable action buttons for a PO Set currently locked by another in-flight action (via HTMX polling or response state), to reduce the chance of a user hitting a 409 in the first place.
- **[FR-CONC-4]** This lock is a stated assumption, not a full authorization system — it prevents accidental double-submission, not malicious concurrent use. If real multi-user collisions turn out to be frequent in practice, revisit with proper auth (out of scope for this build). Given confirmed usage is effectively single-user at ~10 PO Sets/day (§10 NFR-1), this lock is cheap insurance against an edge case (e.g. someone double-clicking Force Merge), not a load-bearing part of the design — do not over-engineer it.

---

## 10. Non-Functional Requirements

- **[NFR-1] Volume (confirmed this session):** ~10 PO Sets/day, effectively single-user usage. Prefect concurrency limits should be set low (e.g. 2-3 concurrent extraction tasks is generous headroom, not a bottleneck-avoidance measure) — this is not a system that needs to scale, and no component should be engineered as if it does. If actual usage grows meaningfully beyond this, revisit `config.yaml`'s concurrency values (§16) — no code change should be needed for that adjustment alone.
- **[NFR-2] Extraction retry timing:** backoff across the 3 retry attempts (FR-6.5) is an explicit per-attempt list (`extraction.retry_backoff_seconds` in `config.yaml`, §13.2), fed into Prefect's native retry policy — not a formula hardcoded in application code, so timing can be tuned without touching code.
- **[NFR-3] Prefect task concurrency:** capped explicitly via `prefect.max_concurrent_extraction_tasks` in `config.yaml` (§13.2) rather than left unbounded or hardcoded — consistent with the 2-core constraint (§5.1), and easy to raise later without a code change if volume grows.
- **[NFR-4] Logging:** structured logging (not print statements), rotated on a schedule appropriate for a Windows host with no syslog — file-based rotation via Python's standard `logging.handlers.RotatingFileHandler` or equivalent, not a custom rotation script.
- **[NFR-5] Secrets:** the OpenRouter API key shall be loaded via `pydantic-settings` from environment variable or `.env` file, never hardcoded, never logged.
- **[NFR-6] Backup:** the SQLite database file backup cadence is an operational runbook item (outside this spec's code scope) but must be documented in the deployment runbook, since the DB is the single source of truth for extracted data (business doc §4.5).

---

## 11. Security Posture (accepted risk, restated)

No authentication or role system in v1. Anyone with LAN access to the tool has full access to every action, including Force Merge and Quarantine Delete. This is an explicit, accepted risk for an internal tool with modest usage (business doc §19 FAQ), not an oversight. The audit log (§6.5) is the accountability mechanism for the two destructive actions, not access control.

---

## 12. Testing & Acceptance Criteria

Per Section 1 rule 3 (verify, don't guess), every functional requirement above needs a concrete test before being marked complete. Format: Given/When/Then with static expected values — not computed at test time.

Representative examples (full test matrix to be built alongside implementation, one per FR at minimum):

- **FR-10.1:** Given a PO line qty=100, DN1=40+DN2=60, SI1=70+SI2=30 → When reconciliation runs → Then status is NOT `mismatched` for that line (matches business doc §10 worked example exactly).
- **FR-8.4:** Given two DNs both referencing `line_item_no=5` with identical descriptions → When matching runs → Then quantities are summed, no quarantine. Given the same but with conflicting descriptions → Then PO Set status is `quarantined`.
- **FR-13.7:** Given a quarantined PO Set with 3 associated documents → When Delete is confirmed → Then the `po_sets` row and its `documents`/`line_items` rows are gone, but `stored_path` files and quarantine folder files still exist on disk, and one `audit_log` row with `action=quarantine_delete` exists.
- **FR-CONC-2:** Given a PO Set locked by an in-flight Force Merge → When a second Force Merge request hits the same PO Set → Then the second request receives HTTP 409, and only one merge occurs.
- **Real-sample validation (business doc §17):** extraction prompts must be validated against the three real vendor PO Sets already reviewed (STS, IRE, Ensign layouts) before extraction is considered done — not just against synthetic test PDFs.

---

## 13. Configuration Management

Confirmed this session: everything environment-specific — paths, timeouts, thresholds, model name — must be externalized to a config file, not hardcoded, so the same codebase runs unmodified on a dev machine, a test box, or the production WS2016 host.

### 13.1 Mechanism

- Format: **YAML** (`config.yaml`), loaded and validated via **`pydantic-settings`** — already an approved library (§5.2), so this introduces no new dependency. `pydantic-settings` supports a YAML source natively; the config is defined as a Pydantic model, so a malformed or missing value fails fast at startup with a clear validation error rather than surfacing as a confusing runtime bug later.
- **Secrets are the one exception.** The OpenRouter API key shall NEVER live in `config.yaml` (which may end up in version control). It is loaded from an environment variable or a separate untracked `.env` file, per the existing NFR-5 rule. `config.yaml` may reference the env var name it expects, but never the key value itself.
- A `config.example.yaml` (with placeholder/dummy values, safe to commit) shall ship alongside the real `config.yaml` (gitignored) — standard practice so a fresh install has a template to copy.
- The app shall validate the full config at startup and refuse to start with a clear error message if a required path doesn't exist or a value is out of an acceptable range — it shall not fail silently or fall back to an undocumented default for anything safety-relevant (retry counts, thresholds, folder paths).

### 13.2 Required config fields (minimum set — agent may add more if a genuine need surfaces, but must not silently hardcode something that belongs here)

```yaml
paths:
  input_folder: "C:\\AAM\\input"
  output_folder: "C:\\AAM\\output"
  quarantine_folder: "C:\\AAM\\quarantine"
  stored_documents_folder: "C:\\AAM\\stored"
  unclassified_folder: "C:\\AAM\\unclassified"
  database_path: "C:\\AAM\\db\\aam_merger.sqlite3"
  log_folder: "C:\\AAM\\logs"

server:
  host: "0.0.0.0"       # bind on all interfaces so LAN machines can reach it
  port: 8000

vlm:
  provider: "openrouter"
  model: "gpt-5.6-luna"      # swap here, not in code, if model changes
  request_timeout_seconds: 60
  api_key_env_var: "OPENROUTER_API_KEY"   # name only — never the key itself

extraction:
  max_retries: 3
  retry_backoff_seconds: [2, 5, 15]   # explicit per-attempt, not a formula buried in code

matching:
  fuzzy_description_threshold: 85     # token-sort similarity %, business doc §8

ingestion:
  stability_poll_interval_seconds: 2
  stability_poll_count: 2

prefect:
  work_pool_name: "aam-merger-process-pool"
  max_concurrent_extraction_tasks: 3   # NFR-1 — generous headroom for 10 sets/day, not a scaling knob

concurrency:
  po_set_lock_timeout_seconds: 300     # safety valve — auto-release a stuck lock (§9) rather than requiring a manual DB fix

logging:
  level: "INFO"
  max_file_size_mb: 10
  backup_count: 5
```

- **[FR-CONFIG-1]** All folder paths, the server bind host/port, the VLM model identifier and timeout, retry counts/backoff values, the fuzzy match threshold, the ingestion stability poll settings, and the Prefect work pool name/concurrency limit shall be read from `config.yaml` at startup, not hardcoded anywhere in application code.
- **[FR-CONFIG-2]** A PO Set lock (§9, FR-CONC-1) shall auto-release after `po_set_lock_timeout_seconds` if the action that acquired it never completes (e.g. a crashed process) — added here because a hardcoded-forever lock would require a manual DB fix to recover from, which contradicts "verify, don't guess" if nobody notices it happened.

---

## 14. Assumptions Carried Forward (business doc §18) — do not silently re-litigate these

1. Concurrent Sync/midnight overlap → second trigger ignored.
2. Missed midnight run → no catch-up logic.
3. Corrupted/unreadable PDF → same retry-then-failed path.
4. Mid-copy stability check → included, 2s poll interval.
5. Retry triggers → both hard and soft failures count toward the 3-retry limit.
6. No AI confidence scoring anywhere.
7. Combined documents → one extraction call for all sections.
8. Multi-page/long line-item tables → native full-PDF input, no cropping; monitored risk, not a blocker.
9. `UNKNOWN` doc type → separate holding area.
10. Fuzzy threshold → 85% token-sort similarity, configurable constant.
11. Negative/zero/credit-note quantities → out of scope, routes to quarantine.
12. Conflicting duplicate line references → quarantine; consistent descriptions → normal summation.
13. `mismatched` can self-recover automatically.
14. Prefect is the orchestration choice — default, swappable later without redesign.
15. No auth/roles in v1 — explicit accepted risk.
16. Prior repo's YOLO-based extraction path fully discarded — clean rebuild, reference only for lessons learned.

## 15. New Assumptions From This Session

17. Full web dashboard is in scope for this build (not deferred, not view-only).
18. Quarantine resolution path: manual DB deletion (with audit log) + separate Manual PDF Merger tool — no automated quarantine-recovery workflow.
19. Manual merger output location/filename is user-selectable per use, not fixed to pipeline conventions.
20. Per-PO-Set optimistic locking is the concurrency safeguard for the no-auth multi-user LAN scenario — not a full session/auth system.
21. Volume: ~10 PO Sets/day, effectively single-user (confirmed — no longer a placeholder).
22. VLM: GPT-5.6 Luna via OpenRouter is the confirmed model, read from config, not hardcoded.
23. All paths, timeouts, thresholds, and the model identifier are externalized to `config.yaml` (§13) — no environment-specific value is hardcoded in application code.
24. "Launch from any computer" means browser access to one designated host running the app + DB, not a distributed/multi-host app or a network-shared database file. This was confirmed specifically to avoid putting SQLite on a network share, which SQLite's own documentation states is unsafe in WAL mode.

---

## 16. Open Items Requiring Decision Before/During Implementation

- Exact library patch versions to pin — agent must check current PyPI/changelog state at implementation time (Section 1, rule 4) rather than trusting this document's versions indefinitely, since this spec can go stale.
- DB backup cadence and location (NFR-6) — operational decision, not yet made.
- ~~Whether the designated host machine is already chosen/provisioned~~ — **Resolved:** same dual-core/128GB RAM Windows Server 2016 box already running the Prefect server. App, DB, and Prefect worker all co-locate on this one host, consistent with §5.1. Exact LAN IP/hostname and firewall rule for the chosen port are deployment-time details, applied after development — not a blocker, since `server.host`/`server.port` are config.yaml values (§13.2), not hardcoded, so they're set once at deploy time with no code change.


we have our proof of concpet at ~/Desktop/AAM_merger_V2 ,u can refer it only if needed but always follow this doc
