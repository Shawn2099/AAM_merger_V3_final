# AGENTS.md — AAM_MERGER-FINAL (AAM_merger_V3_final)

> **Scope:** `Input → Sync → Dedup → Classify → Extract → Group → Match → Reconcile → Merge → Output`
> **Repo:** `https://github.com/Shawn2099/AAM_merger_V3_final`
> **Sources of truth:** [`AAM_merger_V3_SPEC.md`](./AAM_merger_V3_SPEC.md) > [`AAM_merger_V3_business_logic.md`](./AAM_merger_V3_business_logic.md) > this file. If this file ever contradicts SPEC, SPEC wins — STOP and ask.
> **POC reference only:** `~/Desktop/AAM_merger_V2` — lessons-learned, never a code base to branch from.

---

## 1. Binding Agent Operating Rules (SPEC §1 — non-negotiable)

1. **Ambiguity → ask, don't infer.** Unclear, contradictory, or uncovered case → STOP, ask human. A silent bad merge to the CA is worse than a blocked task.
2. **Stuck/looping → ask, don't retry blind.** Same fix failed twice or oscillating states → report what was tried, what happened each time, current hypothesis, and ask.
3. **Verify, don't guess.** No "should work" — run against *real* inputs and inspect output: real vendor PDFs (STS/IRE/Ensign per business doc §17), reconciliation edge cases, 409-lock concurrency. No observed result = not done.
4. **Spec over training defaults.** Where SPEC names a library/pattern/version, SPEC wins over the model's defaults (Win2016 / 2-core constraints).

---

## 2. Stack (SPEC §5.2 — pin exact patches at implementation)

| Purpose | Library | Notes |
|---|---|---|
| Web | FastAPI 0.124.x + Uvicorn 0.3x + Pydantic v2.9+ | ASGI; Uvicorn standalone wrapped by NSSM — **never Gunicorn** (Unix-only) |
| Validation / LLM | `pydantic-settings` + `instructor` + OpenRouter GPT-5.6 Luna | Model id from `config.yaml`, never hardcoded |
| Orchestration | Prefect 3.x `process` pool (`aam-merger-process-pool`) | Already running — midnight cron is Prefect schedule, not Task Scheduler |
| DB | SQLAlchemy 2.x **sync** + Alembic + SQLite WAL | Single-writer; `def` handlers run in threadpool — **no `aiosqlite`** |
| Matching/PDF | `rapidfuzz` 3.x + `pypdf` (not `PyPDF2`) | 85% token-sort threshold from `config.yaml` |
| Testing | `pytest` + `pytest-asyncio` (latest) | Standard per SPEC §5.2 — TDD-first workflow (§7) |
| Config | YAML `config.yaml` validated by `pydantic-settings` | + `config.example.yaml` (committed), real `config.yaml` gitignored; secrets only via `OPENROUTER_API_KEY` env/`.env` |
| Frontend | Jinja2 + HTMX + Alpine.js (server-rendered) | No React SPA / Node build |
| Service | NSSM (external) | Wraps web + Prefect worker as Windows Services |

**Ban list:** bare `except:`, blocking I/O inside `async def`, manual SQL strings, global mutable request state, hand-rolled retry/scheduler where Prefect covers it, `PyPDF2`.

---

## 3. Data Model (SPEC §6 — verified line-by-line; this is a summary, SPEC is primary)

> **Verify-before-lock:** Diffed against SPEC §6.1–6.5 + §9 on 2026-08-15. Enums, field names, and int-scaling below match exactly; AGENTS.md is a router, not a migration source. If any cell here looks off, re-read SPEC §6 before writing Alembic migrations — a "plausible paraphrase" that compiles is the silent-failure mode SPEC §1.1 warns about.

| SPEC table | AGENTS.md summary | Exact match? |
|---|---|---|
| §6.1 `documents` | `id` PK; `sha256_hash` text unique indexed (dedup key §4.4); `original_filename` text; `stored_path` text (never auto-deleted §4.5); `doc_type` enum `PO, DN, SI, COMBINED, CUSTOMS, SHIPPING, COMMERCIAL_INVOICE, UNKNOWN`; `po_no_raw`/`po_no_normalized` text (strip non-alnum, uppercase §7); `dn_no`/`si_no`/`invoice_no` text nullable; `extraction_status` enum `pending, processing, valid, failed` (binary, §6.4); `extraction_attempt_count` int default 0 capped at 3 (§6.4); `po_set_id` FK nullable (null until grouped / if UNKNOWN); `created_at`/`updated_at` timestamp | ✅ — previously omitted `original_filename` and `created_at/updated_at` names, now restored |
| §6.2 `line_items` | `id` PK; `document_id` FK; `line_item_no` text nullable (primary match key); `description` text; `quantity` integer scaled ×1000 (§6.4); `unit_price` integer scaled ×1000 (§6.4) | ✅ |
| §6.3 `po_sets` | `id` PK; `po_no_normalized` text indexed (grouping key); `status` enum `pending, mismatched, quarantined, blocked_customs, merged` (§13); `has_customs_toggle` boolean default false; `customs_doc_count` int (must reach 2 `CUSTOMS`+`SHIPPING` if toggle on); `merged_output_path` text nullable (set only on `merged`); `merged_at` timestamp nullable immutable (permanently closed); `locked_by_action` text nullable (SPEC §6.3 cites "see Section 10" — actual lock rules are SPEC §9 FR-CONC-1; AGENTS.md follows SPEC §9); `created_at`/`updated_at` timestamp | ✅ — `locked_by_action` name and 5-status enum verified |
| §6.4 numeric | All quantities *and* prices stored as integers scaled ×1000; every comparison on ints, never float | ✅ — AGENTS.md now states quantities *and* prices |
| §6.5 `audit_log` | `id` PK; `po_set_id` FK nullable (system-level entries allowed); `action` enum `force_merge, quarantine_delete, manual_status_change` (extend only via spec change); `detail` text/JSON (e.g. customs count at Force Merge); `timestamp` timestamp; `source` text `"system"` (no user identity v1) | ✅ — previously omitted `manual_status_change`, now restored |

Compact form for quick grep: `documents(id, sha256_hash unique, original_filename, stored_path, doc_type[PO,DN,SI,COMBINED,CUSTOMS,SHIPPING,COMMERCIAL_INVOICE,UNKNOWN], po_no_raw/normalized, dn_no/si_no/invoice_no, extraction_status[pending,processing,valid,failed], extraction_attempt_count≤3, po_set_id, created_at/updated_at)` • `line_items(id, document_id, line_item_no, description, quantity×1000 int, unit_price×1000 int)` • `po_sets(id, po_no_normalized indexed, status[pending,mismatched,quarantined,blocked_customs,merged], has_customs_toggle, customs_doc_count, merged_output_path, merged_at immutable, locked_by_action)` • `audit_log(id, po_set_id nullable, action[force_merge, quarantine_delete, manual_status_change], detail JSON, timestamp, source="system")`. No `part_no`/`UOM` columns — do not add without spec change. Reconciliation: per-line `PO == AggDN AND PO == AggSI` exact ints, no tolerance; one line fails → whole set fails; negative/zero → quarantined; price check secondary (flag only).

---

## 4. Branching & Git Workflow (Recommended)

**Why `main`/`dev` + feature branches:** single dev + future LAN collaborators; keeps `main` deployable to Win2016 while `dev` integrates.

```
main  ── deployable, protected (PR only)
dev   ── integration (merge feature branches here)
feat/<short>  e.g. feat/ingestion, feat/reconcile
fix/<short>   bugfixes
test/<short>  experiments / sample-PDF validation
```

* Work on `feat/*` branched from `dev`; PR `feat/*` → `dev`; PR `dev` → `main` for deploy.
* Use worktrees when parallel tasks conflict: `using-git-worktrees` skill (`git worktree add`).
* Remote: `git@github.com:Shawn2099/AAM_merger_V3_final.git` (or HTTPS). On first push: `git init` + `git remote add origin` (do not nest inside `AAM_merger_V2`).
* Commits: name files you changed (no `git add -A` on dirty tree); never `--hard`/`rebase`/`push --force` without explicit ask; wait on lock files, never delete them.

> **Git status:** `AAM_MERGER-FINAL` is not yet initialized — `AGENTS.md` + `.mcp.json` + `skills-lock.json` are uncommitted until you say `push`. Single source of truth for git state is this section (§4).

---

## 5. Cross-Platform (Recommended: best-effort)

Dev = Linux, prod = single Win Server 2016 (2 cores, 128GB, `0.0.0.0:<port>` LAN, DB + stored/quarantine/input/output local to host — **never SQLite on SMB share**, WAL requires shared memory).

* All environment-specific values in `config.yaml` (paths, host/port, model, timeouts, thresholds, `po_set_lock_timeout_seconds`, `max_concurrent_extraction_tasks=3`). App fails fast on invalid config. `max_concurrent_extraction_tasks` is fixed at `3` per SPEC §13.2 (`prefect.max_concurrent_extraction_tasks: 3 # generous headroom for 10 sets/day, not a scaling knob`) — not a range; a 2–3 range would be a spec amendment, not an AGENTS.md drift.
* Code: `pathlib.Path` everywhere, never hardcode `C:\AAM\...`; use `pathlib` + `os.path` join; separators from config; no `\\` literals.
* Keep Windows-only code isolated: NSSM wrapper + deployment runbook; app stays portable (Uvicorn, sync SQLAlchemy, `RotatingFileHandler`, `pydantic-settings` env).
* No dual-OS CI required (accepted); note Windows deltas in runbook. If volume grows beyond ~10 PO Sets/day, tune `config.yaml` only — no code change.

---

## 6. Skills & MCP — How to Use

**Superpowers (project, `obra/superpowers` — 14 skills):** covers SPEC §1.

* Before creative work → `brainstorming` ([.agents/skills/brainstorming/SKILL.md](.agents/skills/brainstorming/SKILL.md))
* Before any feature/bugfix → `test-driven-development` (write failing test first) + `systematic-debugging` on failure + `verification-before-completion` before claiming done
* Planning → `writing-plans` → `executing-plans`; parallel tasks → `dispatching-parallel-agents` / `subagent-driven-development`
* Review gates → `requesting-code-review` / `receiving-code-review`; finishing → `finishing-a-development-branch`

**Self-serve skills:** `find-skills` is global (`~/.agents/skills/find-skills`). Discover with:
```bash
NPM_CONFIG_CACHE=/tmp/npm-cache npx --yes skills find <query>
NPM_CONFIG_CACHE=/tmp/npm-cache npx --yes skills add <owner/repo@skill> -y
```

**GitNexus (graph intelligence):**
* Hooks active (`gitnexus-hook.cjs` on Grep/Bash); MCP via [.mcp.json](.mcp.json) → `{"mcpServers":{"gitnexus":{"command":"gitnexus","args":["mcp"]}}}`
* Before editing a symbol → `impact` (blast radius); before commit → `detect_changes`; exploring → `query` + `context`
* If index stale → `GITNEXUS_HOME=/tmp/gitnexus_tmp gitnexus analyze --skip-git .` (`~/.gitnexus` currently RO by sandbox; remote is `https://github.com/Shawn2099/AAM_merger_V3_final`)
* Guide: [.agents/skills/gitnexus-guide/SKILL.md](.agents/skills/gitnexus-guide/SKILL.md) and siblings (`-exploring`, `-impact-analysis`, `-debugging`, `-refactoring`, `-cli`)

**Already wired non-superpowers:** `python-best-practices`, `modern-python`, `python-error-handling`, `db`/`sqlite-database-expert`, `pdf`/`pypdf`, `playwright-best-practices` (dashboard E2E), `security-and-hardening` (no-auth accepted risk, `audit_log` is accountability).

---

## 7. Development Workflow

1. **Read SPEC + business logic first.** No code until FRs understood.
2. **TDD:** one test per FR minimum (Given/When/Then, static expected values) — e.g. FR-10.1 100=40+60 / 70+30 → reconciled; FR-8.4 conflicting descriptions → `quarantined`; FR-13.7 delete keeps files + audit row; FR-CONC-2 409 on locked set.
3. **Implement EARS FRs in order:** ingestion → classify → extract (single VLM call for COMBINED, retry 3× `[2,5,15]`) → grouping → matching → aggregation → reconciliation → customs → merge → quarantine/manual merger → dashboard + locks.
4. **Verify (SPEC §1.3):** real vendor samples (§17) for extraction; synthetic edge cases for reconcile; simulated concurrent Force Merge for 409. `verification-before-completion` must pass.
5. **Ask don't guess** on any ambiguity; after 2 failed fix attempts, stop and report.

---

## 8. Quick Commands

```bash
# project root
ls -la
cat AAM_merger_V3_SPEC.md AAM_merger_V3_business_logic.md

# skills
NPM_CONFIG_CACHE=/tmp/npm-cache npx --yes skills list
NPM_CONFIG_CACHE=/tmp/npm-cache npx --yes skills find <keyword>

# gitnexus
gitnexus list; GITNEXUS_HOME=/tmp/gitnexus_tmp gitnexus analyze --skip-git .

# config
cp config.example.yaml config.yaml  # then edit paths/model/thresholds; set OPENROUTER_API_KEY in .env
```

---

## 9. Open Items (resolve before claiming done)

Exact patch versions to pin; DB backup cadence (NFR-6); LAN IP/firewall for `server.host`/`port` (config-only, no code change).

---

*This file is the agent's entry point. New agents: read SPEC §1 first, then this file, then `find-skills`/`gitnexus-guide` as needed. When stuck: ask.*
