# SQLite Concurrency Evaluation — 2026-11-09

## Question

Can the app carry **3 concurrent VLM extraction calls** and **200 PO Sets/day** on
SQLite, or is PostgreSQL required?

## Answer

**For the application database, SQLite is sufficient. PostgreSQL is not
justified by the app’s 200-Set/day load.** The live “database is locked” errors
are **Prefect’s own SQLite metadata database** — 178,368 occurrences in the
live Prefect server log — not the application DB, which is 323 KB and has no
lock errors. One genuine application-DB failure mode was reproduced and fixed:
a read→write lock upgrade under a competing writer.

PostgreSQL is the right fix for **Prefect’s metadata** (or remove Prefect), and
for a later cloud/container move. It is not needed for the app DB at this
load.

## Evidence

### The application DB is tiny; the dev/test Prefect DB is not

| Database | Size | Rows |
|---|---:|---|
| Application `data/aam_merger.db` | **0.48 MB** | 197 documents, 477 line items, 56 PO Sets, 11 audit rows |
| Dev/test Prefect `~/.prefect/prefect.db` | **2.18 GB** | 36,000 flow runs, 183,471 task runs, 309,785 logs |
| Live Prefect `deployment\data\prefect\prefect.db` | **4.7 MB** + 4.95 MB WAL | live metadata |

Prefect’s DB breakdown:

- `sync_flow` — 16,320 runs
- `recovery_flow` — 16,033 runs
- `aam-backup` — 3,640 runs
- `task_run_state` — 434 MB
- `task_run` — 113 MB
- `log` — 100 MB
- WAL — 121 MB

That is test/dev churn. The suite did not isolate `PREFECT_HOME`, so every test
run wrote to the developer’s real Prefect DB. The app DB is roughly 4,500×
smaller.

### Application logs contain no lock errors

Searched `data/logs/aam_merger.log`, `dev-web.err`, and
`real_sync_20261024.out`:

- `database is locked` — **0**
- `database table is locked` — **0**
- `OperationalError` — **0**
- `SQLITE_BUSY` — **0**

The existing code mentions were defensive, simulated in tests, or audit
hypotheses. `HANDOFF_PLAN.md` WS6-02 says “possible … not reproduced”; the
robustness audit found no single-document input that triggers it.

### Live deployment evidence (2026-11-09)

The live services run from `C:\Users\malliath\Desktop\deployment`.

| Evidence | Finding |
|---|---|
| `data\logs\nssm-prefect-server-stderr.log` | **718 MB**, **178,368** occurrences of `database is locked` (last written 2026-11-07) |
| Failing Prefect operation | `prefect.server.models.deployments.mark_deployments_ready` → `UPDATE deployment … RETURNING` |
| Live Prefect DB | `data\prefect\prefect.db` **4.7 MB** + 4.95 MB WAL |
| Live app DB | `data\aam_merger.db` **323 KB** |
| Live app log | **0** `database is locked` / `OperationalError` hits |

**The live lock errors are Prefect’s own metadata DB, not the application
database.** The failing query is Prefect’s work-queue/deployment polling
(`mark_deployments_ready`). The Prefect DB is small; this is write contention
between Prefect’s own async sessions, not a size problem.

The live config still says `max_concurrent_extraction_tasks` is “not enforced:
extraction is sequential” — the parallel-extraction feature is **not deployed**
yet. The live Prefect errors predate it. Moving the app DB to Postgres would
not fix them.

### The real application-DB failure mode was reproduced

With one session holding a read transaction and another holding the write lock,
the old claim pattern (`SELECT` then `UPDATE` in the same transaction) failed
immediately:

```text
s1 read status: pending
s2 holds a write lock (uncommitted)
RESULT: read->write upgrade FAILED: OperationalError
(sqlite3.OperationalError) database is locked
```

`busy_timeout` does not help a deadlock-prone lock upgrade. This is exactly the
pattern the extraction claim and the post-VLM persistence used.

### The fix

`src/app/services/extraction.py` now separates inspection from mutation:

- `_read_document_snapshot()` reads in a short session that is **closed before
  any write**.
- `_write_first()` makes the `UPDATE` the first statement of a fresh
  transaction.
- The claim is a single `UPDATE … RETURNING` (SQLite 3.35+; this host has
  3.53.1), so it takes the write lock and returns the claimed values without a
  second `SELECT`.
- The post-VLM persistence locks and revalidates with
  `UPDATE … WHERE status='processing' AND archived_at IS NULL`, then reads and
  writes inside the same write transaction.
- The error/refund/failure paths are write-first too.
- Stale-snapshot guards prevent overwriting a newer archive, manual
  reclassification, operator attempt reset, or already-valid row.
- `PRAGMA journal_size_limit=67108864` caps the WAL after checkpointing.

### Burst results

Harness: `scripts/sqlite_burst.py`. It runs the real bounded extraction pool
with a fake VLM, a dashboard reader polling every 50 ms, and a competing writer
committing every 20 ms. It fails on any lock/error outcome.

**600 documents (200 PO Sets × 3 documents), 3 workers, 10 ms VLM latency**

| Metric | Result |
|---|---:|
| Duration | 7.109 s |
| Throughput | 84.4 documents/s |
| Max in-flight VLM calls | **3** |
| Outcomes | **600 valid, 0 errors** |
| Attempted exactly once | 600 / 600 |
| Line items | 1,800 |
| Dashboard reads | 113 |
| Competing writer commits | 66 |
| WAL peak | 4.11 MB |
| WAL after checkpoint | 0.00 MB |
| DB size | 0.55 MB |

**60 documents, 3 workers, 1,000 ms VLM latency (20.4 s)**

| Metric | Result |
|---|---:|
| Max in-flight VLM calls | **3** |
| Outcomes | **60 valid, 0 errors** |
| Dashboard reads | 325 |
| Competing writer commits | 638 |
| WAL peak | 3.94 MB |
| WAL after checkpoint | 0.00 MB |

The 600-document run is a harsher write burst than real 60-second VLM calls: it
generates roughly 1,200 writes/second while still producing no lock errors.

### Edge cases pinned

| Test file | Cases |
|---|---|
| `tests/test_sqlite_lock_upgrade.py` | Claim and persistence start with a write; a competing writer makes them wait, not fail; no `SELECT`→`UPDATE` transaction remains |
| `tests/test_parallel_extraction_edges.py` | Archive/reclassify between read and claim; operator attempt reset at cap; archive/manual/status change mid-VLM; two workers racing a stale claim (one VLM call); breaker trip after claim releases only that row; attempt-cap race charges once |
| `tests/test_parallel_extraction.py` | Empty job list; duplicate job IDs; cap; failure isolation; breaker; infrastructure classification; monotonic progress |
| `tests/test_sqlite_burst.py` | 40-document 3-worker burst with dashboard reader and competing writer |
| `tests/test_test_isolation.py` | Prefect state stays in the test session directory |

## Decision

| Need | SQLite today | PostgreSQL |
|---|---|---|
| App: 3 concurrent VLM calls | **Yes** — with the write-first fix | Yes |
| App: 200 Sets/day (≈600 docs) | **Yes** — DB grows <1 GB/year | Yes |
| App: single Windows host | **Yes** — local NTFS | Yes, adds a service |
| Live `database is locked` errors | **No — Prefect’s metadata DB, 178,368 hits** | **Move Prefect metadata here** |
| Cloud/containers/multi-instance | No | **Required** |
| Online backup/PITR/HA | No | Yes |

**Stay on SQLite for the application database.** The write-first fix and the
burst evidence cover the app’s concurrency. Revisit PostgreSQL for the app when
the cloud/container move becomes real, or if the burst harness ever reports an
application-DB error.

**Do not keep Prefect’s metadata on SQLite under the live load.** The live
server log is the proof: 178,368 lock errors on `mark_deployments_ready`. The
options, in order of preference:

1. **Move Prefect metadata to PostgreSQL** (the `prefect` database on the same
   instance; `PREFECT_API_DATABASE_CONNECTION_URL`). This is a targeted fix and
   can be done without moving the app DB.
2. **Prefect Cloud** — removes the local Prefect server and its SQLite DB
   entirely; good fit for the cloud move.
3. **Remove Prefect** — Windows Task Scheduler + the plan’s `sync_runs` history
   table. Fewest moving parts, but loses Prefect’s UI/run history.

Keeping Prefect on SQLite while adding 200 Sets/day and 3 concurrent extraction
workers will make its contention worse, not better.

## Prefect cleanup (two different problems)

### Live server: contention, not size

The live Prefect DB is only 4.7 MB. The 718 MB file is
`data\logs\nssm-prefect-server-stderr.log`, which is repeated
`database is locked` stack traces. Vacuuming the DB will **not** fix the lock
errors; they are write contention in Prefect’s own async sessions
(`mark_deployments_ready`).

1. Move Prefect metadata to PostgreSQL, use Prefect Cloud, or remove Prefect
   (see the decision above).
2. Rotate/truncate the 718 MB Prefect stderr log after a backup. This is not
   audit history; the “never rotate” policy is for the application/audit logs.
3. Keep `PREFECT_HOME` under `data\prefect` on the service.

### Dev/test: 2.18 GB `~/.prefect/prefect.db`

That file is test churn, not the live DB. The test-suite fix prevents future
growth: `tests/conftest.py` sets `PREFECT_HOME` to `<session temp>/prefect`,
and clears inherited `PREFECT_API_URL` /
`PREFECT_API_DATABASE_CONNECTION_URL`. If desired, stop any dev Prefect
process, back up `~/.prefect/prefect.db`, rename it, and let Prefect recreate
it; this loses test run history only.

## Reproduce

```powershell
# Focused regression and edge cases
uv run pytest tests/test_sqlite_lock_upgrade.py tests/test_parallel_extraction_edges.py -q -o addopts=""

# 200 Sets/day equivalent (600 documents)
uv run python scripts/sqlite_burst.py --documents 600 --workers 3 --vlm-latency-ms 10

# Realistic VLM latency
uv run python scripts/sqlite_burst.py --documents 60 --workers 3 --vlm-latency-ms 1000
```

## Why not Postgres or Go now

- **Postgres** solves multi-instance/container/cloud deployment and online
  backup/PITR. It does not solve a 200-Set/day single-host load; the app DB is
  0.48 MB and the burst writes are already far below SQLite's capacity. It adds
  a service and roughly 250–400 MB on a 4 GB host. Use it when the cloud move is
  real, with PostgreSQL 17.11 (the newest major tested on Server 2019).
- **Go** would save roughly 80–100 MB of web-process RSS (measured Python web
  RSS: 112 MB working set). It would rewrite ~14,900 source lines and invalidate
  1,395 tests, while the PDF/VLM/extraction libraries that carry the domain risk
  are Python. It does not fix SQLite locking (Postgres does) and does not reduce
  the VLM/PDFium load. Revisit only for a Go-first team, 100+ concurrent users,
  or a single-binary/edge requirement.

## Remaining limitations

- SQLite has one writer. Keep write transactions short and never hold a
  session across a VLM/network call. The claim-close-call-persist design and
  the write-first rules above are load-bearing.
- The database must stay on local NTFS. WAL on SMB is unsupported.
- **Prefect’s SQLite metadata is not viable for the live load.** The live
  server log contains 178,368 `database is locked` errors. Move Prefect’s
  metadata to PostgreSQL or Prefect Cloud, or remove Prefect. This is separate
  from the application DB, which stays SQLite.
- The 718 MB Prefect stderr log needs rotation/truncation; it is repeated stack
  traces, not audit history.
- The auth `last_seen` touch is a best-effort read→write and catches/rolls
  back. Under contention it may skip a touch, but it cannot fail the request.
  It is rate-limited to once per 60 s per session.
- The 2.18 GB dev Prefect file was not deleted automatically; it needs the
  cleanup above.
