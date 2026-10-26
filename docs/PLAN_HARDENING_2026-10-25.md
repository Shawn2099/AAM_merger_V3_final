# PLAN: hardening batch H1 (WS5 items + customs-toggle audit)

Status: **rev 1 DRAFT (Claude, 2026-10-25), awaiting Opus review.** Owner: "include 7 also, first draft a plan, then an Opus
review, and make sure they are proper fixes and compatible with this code base; if so apply all". Scope freeze (D17) holds:
every item fixes or hardens existing behaviour; none adds a feature. Each item is its own commit, test first, named files only.
Nothing here touches the files DeepSeek is editing (`dashboard.py`, `templates/**`, `static/js/app.js`, `scripts/ui_test_env.py`,
`tests/ui/**`, `tests/test_dashboard.py`).

Findings from reading the code that CHANGE the WS5 text in DEPLOYMENT_PLAN (to be corrected there once approved):
- `config.py` uses `extra="forbid"` for every model. Removing a key makes an existing `config.yaml` that still names it FAIL to
  start. WS5.3 as written ("remove with a one-time ignored-key warning") is not possible without changing the strictness.
- `server.port` IS read by `scripts/reset_data.ps1` (`c.server.port`). `server.host` and `prefect.max_concurrent_extraction_tasks`
  are read by nothing (`flows/sync.py:289` says so).
- The poller does NOT re-hash per minute. `InputWatcher` compares (path, mtime, size) each tick and starts a sync only for a new
  signature. The re-reading happens inside a sync: `ingest_file` reads and hashes every PDF still in the input folder (an
  unmerged set's input copies stay until it merges). WS5.4 as written overstates the cost.
- `BodySizeLimitMiddleware` already exists (`main.py:117`), mounted only for `/manual/merge`. The pipeline upload route
  `/po_sets/{id}/upload` has only an in-route read cap (`MAX_UPLOAD_BYTES + 1`), after Starlette has spooled the body.

## H1-1. Origin guard compares the port (WS5.2)
Problem: `OriginGuardMiddleware` compares host names only (`main.py:87-93`). Another local web service on the same PC (a different
port) can post to this app with an Origin whose host equals the request host.
Change (`main.py`): also compare the port, normalised (explicit port, else 80 for http / 443 for https from the Origin's
scheme; the request side takes the port from `URL(scope).port`, else the scheme default). The SCHEME is deliberately NOT compared:
the service has no TLS and runs without `--proxy-headers`, so behind any HTTPS reverse proxy a scheme comparison would refuse
every legitimate request, and a scheme mismatch on the same host:port is not an attack the port check misses.
Tests (`tests/test_origin_guard.py` or the existing file for the guard): same host+port accepted; same host different port
refused 403; Origin without a port against Host `x:80`; Referer-only; `null`; missing headers still accepted; IPv6 literal host.
Compatibility check: the UI test server and `TestClient` send `Host: testserver` with no Origin, so they are unaffected;
HTMX in the browser sends the page's own origin.

## H1-2. Unused config keys: document, do not remove (WS5.3, changed)
Change: comments only in `config.example.yaml` and a note in `docs/DELIVERY.md` and PRODUCT section 8:
`server.host`: not read (the installer's `-HostAddress` binds); `server.port`: read only by `reset_data.ps1` to check the port is
free, the service binds the installer's `-Port`; `prefect.max_concurrent_extraction_tasks`: not read (extraction is sequential
inside the sync). Optionally a startup warning when one of the unread keys is set to a non-default value (an operator who set
`max_concurrent_extraction_tasks: 8` expecting parallelism). No model change, so no existing config can break.
Test: the example config still loads; if the warning is built, one test for it.

## H1-3. Re-hashing of input files: measure first, no code now (WS5.4, changed)
Reason: the idle poller is already cheap. A sync re-reads the PDFs still in input; a cache of (path, size, mtime) -> sha would have
to be process-wide to survive between syncs (the midnight Prefect run is a different process, so it starts cold), and it would skip
`ingest_file`'s validation (header, completeness) and the "restore the stored copy if missing" step. The benefit is unproven.
Plan: add a measurement to the soak test (WS6): sync time with 0, 50 and 200 pending input files of realistic size. Build the cache
only if that shows a problem. Record the decision (D21) so it is not reopened by the old text.

## H1-4. Request-size limit on the pipeline upload (WS5.1)
Change (`main.py` only; `dashboard.py` is not touched): mount a second `BodySizeLimitMiddleware` for the upload route with
`max_bytes = MAX_UPLOAD_BYTES + 2 MiB` (same overhead the manual merger uses) and a way to match `/po_sets/<id>/upload`:
the class matches by `startswith` today, so add an optional `path_suffix` (POST only) rather than prefix-matching all of
`/po_sets/`. The in-route cap stays as the second line of defence. The existing 413 page (`error_response`) is used.
Behaviour to preserve: the middleware raises `BodyTooLarge` from `receive`; for a route whose body is parsed by FastAPI
(`File(...)`/`Form(...)` parameters, before the handler runs) it must still yield a clean 413 and not a 500.
Tests: Content-Length over the cap -> 413 before any parse; chunked body over the cap -> 413 (monkeypatch `MAX_UPLOAD_BYTES` low);
a normal upload still works; browser (Accept: text/html) gets the themed page; nothing is written to the stored folder on 413;
the manual-merger middleware tests stay green.

## H1-5. Customs toggle writes an audit row (was "item 7")
Problem: switching `Customs Check` decides whether a packet must contain customs pages and is not audited
(`test_known_limitation_customs_toggle_writes_no_audit_row`).
Change (`services/customs.py::toggle_customs`): add an `AuditLog` row in the SAME transaction/commit as the flip: action
`manual_status_change`, source `operator`, detail `{"note": "Customs check switched ON|OFF", "customs_check": true|false}`
(the audit page renders `note`). No new enum value, no migration. The service is also called directly by unit tests
(`tests/test_customs.py`), so the row is written there too; those tests do not count audit rows.
Tests: flip ON then OFF writes two rows with the right notes; the row appears on `/audit`; an archived set still refuses
(no row); the old pinned test is replaced by `test_customs_toggle_writes_an_audit_row` (flipped on purpose); the doc lines in
`docs/CUSTOMS_OPERATIONS.md` and PRODUCT section 8 are updated.

## Order and acceptance
H1-5, H1-4, H1-1, H1-2 (H1-3 is a note in WS6 plus D21). Per item: test first, then the change; `ruff check`, `ruff format --check`
on the touched files, `ty check src`, the neighbouring tests, then the full suite once at the end. GitNexus impact before editing
`OriginGuardMiddleware`, `BodySizeLimitMiddleware` and `toggle_customs`. Opus reviews the diff before the commit.
