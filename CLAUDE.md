# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AAM Merger V3: PO/DN/SI reconciliation that produces one merged PDF per PO. FastAPI + Prefect + SQLAlchemy (sync) + SQLite WAL, with a server-rendered Jinja2/HTMX/Alpine dashboard. It is built for a single Windows Server 2016 / office PC on a LAN, and developed on Linux or Windows.

**Read first:** `docs/CORE_LOGIC.md`, then `AAM_merger_V3_PRODUCT.md`. The PRODUCT doc is the source of truth and wins over `AGENTS.md`. `AAM_merger_V3_SPEC.md` and `AAM_merger_V3_business_logic.md` describe a richer matcher that was retired on 2026-09-29. Do not implement against their matching/reconciliation sections. `SESSION_HANDOFF.md` has the latest state.

## Commands

Uses `uv`. The Makefile sets `UV_CACHE_DIR=/tmp/uv-cache`, which is awkward on Windows, so run `uv` directly there.

```bash
uv sync --all-groups                         # install (make dev)
cp config.example.yaml config.yaml           # then edit paths; set OPENROUTER_API_KEY in .env
uv run python scripts/migrate_db.py          # fresh and legacy create_all-era DBs; use this, not bare alembic
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

uv run ruff check . && uv run ruff format --check .   # make lint (line length 100)
uv run ty check src                          # type check; error-on-warning, must stay clean
uv run pytest -q                             # full suite (make test)
uv run pytest tests/test_merge.py::test_name # single test
make business-logic                          # engine verdicts + the pages an operator reads
```

- Run `make business-logic` after touching reconciliation, grouping, matching or the reporting routes. `tests/test_business_scenarios_ui.py` asserts both the engine verdict and what the dashboard, quarantine and set pages display.
- `config.yaml` paths must use forward slashes. A double-quoted `C:\AAM\input` is a YAML escape error, and the app fails fast on invalid config.
- Prefect (optional locally): `make prefect` / `make prefect-stop`. `scripts/deploy_prefect.py` registers the midnight deployment on the `aam-merger-process-pool` work pool.
- Windows deployment scripts (NSSM services, backup task, reset) are in `scripts/*.ps1`. See `docs/DELIVERY.md` for the install and handover checklist.

## Architecture

Pipeline: `Input -> SHA-256 dedup -> classify -> VLM extract -> group by PO -> reconcile -> merge -> output`. It is driven by `src/app/flows/sync.py`, a Prefect flow with one task per document. Sync is triggered by the midnight Prefect cron or by manual `POST /sync` (`api/routes/sync.py`).

- **Concurrency.** `services/sync_lock.py` is a cross-process FileLock that serializes manual and cron syncs, with a stale-sidecar watchdog. `services/locking.py` holds per-PO-set locks (`locked_by_action`/`locked_at`). A second Force Merge on a locked set returns 409.
- **Anchoring.** Only `PO` documents may mint a new PO set (`_ANCHOR_TYPES` in `flows/sync.py`). DN/SI attach to an existing set or sit visibly unattached; UNKNOWN/COMBINED never attach until an operator reclassifies them (`_ATTACHABLE` in `grouping.py`). A multi-PO doc is flagged `po_reference_ambiguous` and never guessed.
- **Extraction** (`services/extraction.py`). The VLM schema is the whole contract with the model (via `instructor` + OpenRouter; the model id comes from config). `splitting.py` cuts combined multi-document PDFs into `<sha16>_p<i>.pdf` children, and any failed gate quarantines the whole parent. `sanitizer.py` parses quantities with babel in strict mode into integers scaled x1000.
- **Grouping** (`grouping.py`) normalises PO numbers. **Matching** (`matching.py`) applies the rule: group both sides by normalised line number and sum. Description is used only as a fallback key for a PO line with no number.
- **Reconciliation** (`reconciliation.py`). `compare_po_set_lines` is the single entry point, shared by the engine and the dashboard. `reconcile_po_set` sets the status: `pending`, `mismatched`, `quarantined`, `blocked_customs`, `merged`. The rule is that PO must equal the DN aggregate AND the SI aggregate exactly. Quantity is the only signal (no price, SKU or UOM), and one failing line fails the whole set. `customs.py` gates and appends customs/shipping docs. `merge.py` writes the output PDF. `quarantine.py` copies files out and writes audit rows.
- **Data** (`models/models.py`, Alembic in `alembic/`). Four tables: documents, line_items, po_sets, audit_log. `line_items` deliberately has no `part_no`, `line_type` or `uom`. Do not re-add them. The SHA-256 uniqueness is a partial index over non-archived rows only. Files are never auto-deleted; deletes archive to `data/development/archived_sets` and write an audit row.
- **Web.** `main.py` mounts `OriginGuardMiddleware` (CSRF-style Origin/Referer check) and optional `TrustedHostMiddleware`. Routes are in `api/routes/` and templates in `templates/`. HTMX/Alpine are vendored in `static/js` because the target LAN may be offline, so do not switch to CDNs. SQLAlchemy is sync, with no `aiosqlite`.

## Conventions that bite

- All quantities and prices are integers scaled x1000. Compare integers, never floats.
- If you remove a guard, document the limitation in PRODUCT section 8 and pin it with a test named `test_limitation_*` or `test_known_limitation_*`.
- Ban list: bare `except:`, blocking I/O inside `async def`, manual SQL strings, `PyPDF2`, Gunicorn, SQLite on an SMB share, and hardcoded Windows paths (use `pathlib` plus config).
- Ambiguity or a case the docs don't cover means stop and ask. After two failed fixes, report instead of retrying. Verify against real vendor PDFs, synthetic edge cases, and a real concurrent Force Merge, not only mocks.
- Commit by naming the files you changed. The tree is usually dirty, so don't use `git add -A`.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **AAM_merger_V3_final** (1773 symbols, 3499 relationships, 71 execution flows).

> Index stale? Run `node .gitnexus/run.cjs analyze --index-only` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? Bootstrap with `npx`, `bunx`, or `pnpm dlx` — e.g. `bunx gitnexus@latest analyze` (npm 11 npx crash; #1939).

## Always Do

- **MUST run impact before editing.** Use `impact({target: "symbolName", direction: "upstream"})` or `node .gitnexus/run.cjs impact "symbolName" --direction upstream --repo .`; report callers, processes, and risk. Never substitute grep for graph analysis.
- **MUST analyze graph changes before committing.** Use `detect_changes({scope: "all"})` (MCP) or `node .gitnexus/run.cjs detect-changes --scope all --repo .` (CLI fallback). `partial: true` or `truncated: true` is not a clean check — a zero means unseen, not unaffected; re-run it. For regression review: `detect_changes({scope: "compare", base_ref: "main"})` or `node .gitnexus/run.cjs detect-changes --scope compare --base-ref "main" --repo .`.
- MUST warn on HIGH/CRITICAL `risk` pre-edit; never use `riskSharedAxes` to waive a HIGH/CRITICAL `risk` warning. Compare File/symbol: MCP File omits axes; Graph-RAG expands File.
- **MUST treat `risk: UNKNOWN` as unresolved, not as low.** An empty caller set is not evidence the symbol is unused — it can also mean the callers are not resolvable by the index (plain-object property access, dynamic dispatch, cross-language calls). `impact` pairs `UNKNOWN` with a `riskNote` saying so. Confirm with a text search before treating the symbol as safe to change or delete; do not proceed on the strength of a zero.
- **MUST use `query({search_query: "concept"})` for concepts/flows, `context({name: "symbolName"})` for a named symbol, or `impact` for blast radius, on read-only callers, dependencies, imports, or execution flow.** Graph first; text search only for empty/`UNKNOWN`/literals.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method before MCP/CLI impact analysis.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis, and never read `UNKNOWN` as an all-clear — it means the walk could not answer, which is the one verdict that requires confirming by other means.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit before MCP/CLI graph change analysis.

## Resources

| Resource | Use for |
| --- | --- |
| `gitnexus://repo/AAM_merger_V3_final/context` | Codebase overview, check index freshness |
| `gitnexus://repo/AAM_merger_V3_final/clusters` | All functional areas |
| `gitnexus://repo/AAM_merger_V3_final/processes` | All execution flows |
| `gitnexus://repo/AAM_merger_V3_final/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
| --- | --- |
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
