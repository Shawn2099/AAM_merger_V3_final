# SPEC PF-11: operator-visible system status

Status: **design for review, not built.** Author: Claude (architect). Builder: worker (DeepSeek). Reviewer: Claude.

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
- New in-memory dataclass `PollerStatus` on `SyncPoller` (`src/app/services/poller.py`): `last_tick_at`, `last_run_started_at`,
  `last_run_finished_at`, `last_result` (the dict the sync returned: new/duplicate/rejected/errors/unstable counts, `vlm_halted`),
  `backoff_until`, `queued_since`. Updated inside `tick()` and the run wrapper only; read through `SyncPoller.status()`, which
  returns a plain dict copy under a small `threading.Lock`.
- Manual and nightly runs also update `last_run_*`: record the result in a module-level `last_sync_result` holder set at the end of
  the sync runner (the code that already builds the result dict). Poller and manual share it.
- State is lost on restart (acceptable: after restart the pill says "Watching" with "No run since start"). Do not persist.
- New read-only route `GET /status` (in `api/routes/sync.py`) returns `{state, label, detail, last_check, last_run}` built by ONE
  pure function `build_status(poller_status, sync_running, cfg, now)` in `services/poller.py`, so it is unit-testable without HTTP.
  `GET /sync/status` keeps its current contract (pinned by tests).
- The pill is rendered by `base.html` and refreshed by polling `GET /status` every 10 s (reuse an existing poll if the page has
  one; do not add a second timer on pages that already poll faster).

## Tests the builder must write (test first)
1. `tests/test_system_status.py`: pure `build_status` table test, one case per row above, plus precedence (halted beats running).
2. A failure inside `tick()` does not break `status()`.
3. `GET /status` returns 200 JSON with the keys above and no path-like strings (no backslash, no `/data/`).
4. `tests/ui/test_ui_global.py`: the pill is present on the dashboard and a set page, has `data-testid="system-status"`, its text
   matches the API; with the harness (polling off) it says "Manual".
5. `GET /sync/status` contract unchanged (existing tests stay green).

## Rules
Allowed: `src/app/services/poller.py`, `src/app/api/routes/sync.py`, `src/app/flows/sync.py` (only to publish the result),
`templates/base.html`, `static/js/app.js`, new tests. Forbidden: reconciliation, grouping, merge, models, config keys.
No new dependency. The wording above is the source of truth; keep sentences plain (operators are not engineers).

## Review checklist (Claude)
Thread safety of the status copy; no exception text or paths leak; precedence matches the table; no extra DB queries per poll;
the UI test reads the API, not hard-coded text.
