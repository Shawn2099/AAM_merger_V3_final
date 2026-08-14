# AAM_MERGER-FINAL — AAM_merger_V3_final

AI-assisted PO/DN/SI reconciliation → merged PDF per PO. See `AGENTS.md` for agent rules, `AAM_merger_V3_SPEC.md` for build spec.

**Repo:** https://github.com/Shawn2099/AAM_merger_V3_final  
**Status:** scaffolding (no code yet) — SPEC §1 binding.

## Branches
- `main` — deployable to Win Server 2016
- `dev` — integration
- `feat/*`, `fix/*`, `test/*` — work branches

## Quick start (cross-platform)
- `cp config.example.yaml config.yaml` (not yet created) → edit `config.yaml` paths via `pathlib`
- `NPM_CONFIG_CACHE=/tmp/npm-cache npx --yes skills list`
- `GITNEXUS_HOME=/tmp/gitnexus_tmp gitnexus analyze --skip-git .` if index stale

## Stack
FastAPI + Uvicorn (NSSM) + Pydantic v2 + instructor + Prefect 3 + SQLAlchemy sync + SQLite WAL + rapidfuzz + pypdf + pytest. See AGENTS.md §2.
