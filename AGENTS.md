# AGENTS.md — AAM_MERGER-FINAL (AAM_merger_V3_final)

> **Scope:** `Input → Sync → Dedup → Classify → Extract → Group → Reconcile → Merge → Output`
> **Repo:** `https://github.com/Shawn2099/AAM_merger_V3_final`
>
> **Source of truth:** [`AAM_merger_V3_PRODUCT.md`](./AAM_merger_V3_PRODUCT.md) —
> the product as built and intended. **If this file or the older specs
> contradict it, the PRODUCT doc wins.** `AAM_merger_V3_SPEC.md` and
> `AAM_merger_V3_business_logic.md` are retained for the vendor research and
> requirement history they contain; their matching/reconciliation sections
> describe a richer matcher that was **deliberately retired on 2026-09-29** and
> are superseded. Do not implement against them.
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

## 3. Data Model

> **Authoritative:** src/app/models/models.py. The PRODUCT doc §2-§6 explains
> why each column exists. Summary below for grep only.

| Table | Columns |
|---|---|
| documents | id PK; sha256_hash unique indexed (dedup key); original_filename; stored_path (never auto-deleted); doc_type enum PO, DN, SI, COMBINED, CUSTOMS, SHIPPING, COMMERCIAL_INVOICE, UNKNOWN; 
aw_extraction_json; po_no_raw/po_no_normalized; po_reference_ambiguous (multi-PO doc is left unattached, never guessed); dn_no/si_no/invoice_no nullable; extraction_status enum pending, processing, valid, failed; extraction_attempt_count capped at 3; po_set_id FK nullable; created_at/updated_at |
| line_items | id PK; document_id FK; line_item_no text nullable (**the** matching key); description; quantity int ×1000; unit_price int ×1000; dn_no nullable (per-line DN reference) |
| po_sets | id PK; po_no_normalized indexed (grouping key); status enum pending, mismatched, quarantined, blocked_customs, merged; has_customs_toggle; customs_doc_count; merged_output_path; 
econcile_reason (plain-language, dashboard reads it); merged_at (immutable); locked_by_action; locked_at; created_at/updated_at |
| udit_log | id PK; po_set_id FK nullable ON DELETE SET NULL; ction enum orce_merge, quarantine_delete, manual_status_change; detail JSON; justification (operator note, >= 20 chars when given); 	imestamp; source |

**line_items has NO part_no, NO line_type, NO uom.** These existed
briefly to serve a retired matcher and were dropped in migration
c1a2b3d4e5f6. Do not re-add them. See PRODUCT doc §7-§8 for why, and for what
their absence costs.

**Numeric:** all quantities and prices are integers scaled ×1000. Every
comparison is on those integers, never a float.

**Reconciliation** (PRODUCT doc §2): group both sides by normalised
line_item_no and sum; PO must equal the DN aggregate AND the SI aggregate
exactly; a vendor group with no PO counterpart quarantines; one failing line
fails the whole set. **Quantities are the only signal** — no price, no SKU, no
UOM, no step-10 mapping, no description-conflict check.

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

1. **Read `AAM_merger_V3_PRODUCT.md` first.** No code until the rule in §2 and
   the accepted limitations in §8 are understood.
2. **TDD:** one test per behaviour, Given/When/Then with static expected
   values. Examples that hold today: PO 100 = DN 40+60 and SI 70+30 →
   `merged`; an orphan vendor line → `quarantined`; a set with no SI number →
   auto-merge refuses; delete keeps files and writes an audit row; second
   Force Merge on a locked set → 409.
3. **If you remove a guard, add the limitation to PRODUCT §8 and pin it with a
   test named `test_limitation_*` or `test_known_limitation_*`.** A removed
   guard that leaves no trace is how this document came to be needed.
4. **Verify (PRODUCT §11):** real vendor samples for extraction; synthetic edge
   cases for reconcile; a real concurrent Force Merge for 409.
   `verification-before-completion` must pass. A claim without an observed
   result is not done.
5. **Ask don't guess** on any ambiguity; after 2 failed fix attempts, stop and
   report.

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

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **AAM_merger_V3_final** (1884 symbols, 3582 relationships, 67 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/AAM_merger_V3_final/context` | Codebase overview, check index freshness |
| `gitnexus://repo/AAM_merger_V3_final/clusters` | All functional areas |
| `gitnexus://repo/AAM_merger_V3_final/processes` | All execution flows |
| `gitnexus://repo/AAM_merger_V3_final/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
