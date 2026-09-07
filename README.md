# AAM_MERGER-FINAL — AAM_merger_V3_final

AI-assisted PO/DN/SI reconciliation → merged PDF per PO. See `AGENTS.md` for agent rules, `AAM_merger_V3_SPEC.md` for build spec.

**Repo:** https://github.com/Shawn2099/AAM_merger_V3_final  
**Status:** V3 pipeline implemented — 119 tests green, 71 PO Sets merged in local data. SPEC §1 binding.

## Branches
- `main` — deployable to Win Server 2016
- `dev` — integration
- `feat/*`, `fix/*`, `test/*` — work branches

## Quick start (cross-platform)
- `cp config.example.yaml config.yaml` → edit `config.yaml` paths via `pathlib`; secrets only via `OPENROUTER_API_KEY` in `.env`
- `UV_CACHE_DIR=/tmp/uv-cache uv sync --all-groups`
- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff check . && UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check .`
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
- `UV_CACHE_DIR=/tmp/uv-cache uv run alembic upgrade head`
- `make backup` (WAL checkpoint + timestamped copy to `data/backup/`)

## Stack
FastAPI + Uvicorn (NSSM) + Pydantic v2 + instructor + Prefect 3 + SQLAlchemy sync + SQLite WAL + rapidfuzz + pypdf + pytest. See AGENTS.md §2.
