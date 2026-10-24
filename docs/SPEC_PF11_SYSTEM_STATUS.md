# SPEC PF-11: operator-visible system status

Status: **rev 2 (after Opus review 2026-10-25); not built.** Author: Claude (architect). Builder: worker (DeepSeek). Reviewer: Claude.

Why: with the 1-minute poller (D5) nobody presses Scan any more. If the provider halts, the poller backs off for an hour, the
lock is held by a hung sync, or every extraction fails, the dashboard looks idle and fine (finding F-16). The operator needs one
glance to know "is the system alive and what did it last do".

## What the operator sees
A status pill in the top bar (replaces the "Service Online" link to `/health`, finding F-13) and a one-line detail on the
dashboard under the title. Pill states, first match wins:

| State | Pill | Detail line |
|---|---|---|
| provider halted (`vlm_halted`) | red "Paused" | "Paused until 15:10: the AI provider rejected requests. Documents are safe and waiting. Check the API key and model, then press Scan." |
| sync running now | blue "Working" | "Reading documents (started 14:03)" |
| files waiting behind a stuck sync > 2 min | amber "Waiting" | "A sync has been running for 12 min; new files are queued." |
| last sync had errors / all extractions failed | amber "Needs attention" | "Last run 14:05: 3 of 3 documents could not be read." |
| polling off | grey "Manual" | "Automatic checking is off. Press Scan Input Folder to process new files." (add "Nightly run at 00:00" if enabled) |
| polling on, last run ok | green "Watching" | "Checks the input folder every minute. Last check 14:07, last run 14:05: 2 new documents." |

Never show server paths or exception text. Times are the PC's local time (use the app's existing time formatting helper).

## Data source (no DB change, no migration)
Three facts decide the design: the midnight Prefect run is a SEPARATE PROCESS from the web service; the poller lives on
`app.state.poller` and is `None` when polling is off (`main.py:242-243`); the poller's `_backoff_until` / `_queued_since` are
`time.monotonic` values and cannot be shown as clock times.

- **Run result is persisted as a file.** `_sync_flow_locked` (`flows/sync.py` ~867) writes `last_sync.json` (write to a temp file,
  then `os.replace`) next to the sync-lock sidecar, with: `finished_at` (ISO, local tz), `started_at`, `trigger`
  (`poller|manual|nightly`), `processed`, `extracted`, `errors`, `vlm_halted`, `unstable`. `/status` reads it. This makes nightly and
  manual runs visible to the web process. A run that ended `skipped` (lock busy) must NOT overwrite it. Manual `_run_sync`
  (`routes/sync.py:31`) discards the result today; publishing happens inside `_sync_flow_locked`, so it is covered.
- **Running state** comes from the sync-lock sidecar (works across processes, `sync_lock.py:118,144-166`); the sidecar's start time
  gives "running for N min". Do not derive it from `_queued_since` (reset every 120 s, `poller.py:199`).
- **Poller state** (wall-clock `datetime` fields, NOT monotonic): `halted_until`, `last_tick_at`, `last_error_at`. Add them to
  `SyncPoller` next to the monotonic ones and expose `SyncPoller.status()` returning a plain dict copy under a small lock.
  A poller sync that raises (300 s backoff, `poller.py:211`) shows as "Needs attention".
- Time format: `dt.astimezone().strftime("%H:%M")` (same as `reconciliation.py:952`). There is no shared helper; do not invent one.
- **"All failed"** means `processed > 0 and extracted == 0 and errors > 0`.
- `GET /status` takes `request: Request`; `request.app.state.poller` may be `None`; polling on/off is read from the CURRENT config
  (`schedule.poll_enabled`), not from the poller. It is a read-only route built on ONE pure function
  `build_status(poller_status | None, sync_running, last_sync | None, cfg, now)` in `services/poller.py`, unit-testable without HTTP.
  `GET /sync/status` keeps its contract.
- **Timer:** one `setInterval(10000)` in `app.js` on every page that has the pill. The existing 5 s `/sync/status` poll runs only
  when `#sync-btn` exists (`app.js:211-229`); leave it alone.
- Poller fields held in memory are lost on restart: acceptable. `last_sync.json` survives restarts; before any run it is absent and
  the pill says "Watching" with "No run since start".

## Tests the builder must write (test first)
1. `tests/test_system_status.py`: pure `build_status` table test, one case per row above, plus precedence (halted beats running).
2. A failure inside `tick()` does not break `status()`; `GET /status` works with `app.state.poller` = None (polling off) and with a missing or corrupt `last_sync.json`.
3. `GET /status` returns 200 JSON with the keys above and no path-like strings (no backslash, no `/data/`).
4. `tests/ui/test_ui_global.py`: the pill is present on the dashboard and a set page, has `data-testid="system-status"`, its text
   matches the API; with the harness (polling off) it says "Manual".
5. `GET /sync/status` contract unchanged (existing tests stay green).

## Rules
Allowed: `src/app/services/poller.py`, `src/app/api/routes/sync.py`, `src/app/flows/sync.py` (only to write `last_sync.json`), `src/app/services/sync_lock.py` (read the start time only),
`templates/base.html`, `static/js/app.js`, new tests. Forbidden: reconciliation, grouping, merge, models, config keys.
No new dependency. The wording above is the source of truth; keep sentences plain (operators are not engineers).

## Review checklist (Claude)
Thread safety of the status copy; no exception text or paths leak; precedence matches the table; no extra DB queries per poll;
the UI test reads the API, not hard-coded text.
