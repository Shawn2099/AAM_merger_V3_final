# AAM_MERGER-FINAL — AAM_merger_V3_final

AI-assisted PO/DN/SI reconciliation → one merged PDF per PO. FastAPI + Prefect
+ SQLite WAL, built for a single Windows Server 2016 host on a LAN.

**Start here:** [`AAM_merger_V3_PRODUCT.md`](./AAM_merger_V3_PRODUCT.md) — what
the product is, the reconciliation rule, and its accepted limitations.

> The older `AAM_merger_V3_SPEC.md` and `AAM_merger_V3_business_logic.md` are
> kept for the vendor research and requirement history they contain. Their
> matching/reconciliation sections describe a richer matcher that was
> **deliberately retired on 2026-09-29** and are **superseded** by the PRODUCT
> doc. Do not implement against them.

## What it does

`Input → SHA-256 dedup → VLM extraction → group by PO number → reconcile by
line number → auto-merge → output`, with a web dashboard for the human
reviewer.

The reconciliation rule is short and is the whole product: group both sides by
line number, sum, and require `PO == DN aggregate` **and** `PO == SI aggregate`
exactly, in scaled integers. One unresolvable line fails the whole set.

## Quick start

```bash
cp config.example.yaml config.yaml    # then edit paths; set OPENROUTER_API_KEY in .env
uv sync --all-groups
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Checks:

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
```

Note: use **forward slashes** in `config.yaml` paths. A double-quoted
`C:\AAM\input` is a YAML escape error and the app will refuse to start.

## Stack

FastAPI · Uvicorn (NSSM on Windows, never Gunicorn) · Pydantic v2 ·
instructor + OpenRouter · Prefect 3 · SQLAlchemy 2 sync · SQLite WAL ·
rapidfuzz · pypdf · Jinja2 + HTMX + Alpine.js · pytest.

## Where things live

| Path | What |
|---|---|
| `src/app/services/matching.py` | the rule: group, sum, compare |
| `src/app/services/reconciliation.py` | `compare_po_set_lines` (one entry point, used by the engine *and* the dashboard) and `reconcile_po_set` |
| `src/app/services/extraction.py` | the VLM schema — the whole contract with the model |
| `src/app/models/models.py` | the four tables |
| `AAM_merger_V3_PRODUCT.md` | the contract. Read before changing anything. |

## Known limitations

The product's accepted limitations are listed in PRODUCT §8, not hidden here.
The most important: **a wrong item can merge.** Two rows on the same line
number with the same quantity but different descriptions are summed, and there
is a real vendor set where that produces a merge that should not happen. The
CA's human review is load-bearing, not a formality.
