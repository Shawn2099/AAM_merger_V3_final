# Handover Hardening — 2026-11-02

Branch: `feat/handover-hardening` (based on `main`; `main`'s runtime tree is
byte-identical to `deployed/aam-2026-10-27` — only tests/docs differ).

This is the change record for the handover work. It is not a substitute for
`AAM_merger_V3_PRODUCT.md`, which remains the product source of truth.

## 1. What changed

### Authentication and authorisation

- **Named accounts** in a new `users` table. Passwords are Argon2id
  (`argon2-cffi`), never stored or logged in clear text.
- **Opaque server-side sessions** in `auth_sessions`. The cookie carries a
  random 256-bit token; only its SHA-256 is stored. Logout, password change,
  disabling a user and "sign out everywhere" take effect immediately. The same
  table works with multiple uvicorn workers and later Postgres; no signing
  secret is required.
- **Default deny.** Every route except `/health`, `/login`, `/favicon.ico` and
  `/static/*` requires a session. FastAPI's `/docs`, `/redoc` and
  `/openapi.json` are disabled.
- **Two roles.** `operator` does daily work; `admin` also changes Settings and
  may remove/archive a set. The Settings and destructive routes enforce this on
  the server, not only in the template.
- **CSRF.** A per-session synchronizer token. HTMX sends `X-CSRF-Token`
  automatically (`app.js`); native forms carry a hidden `csrf_token`.
- **Login throttling** per username and per client IP, stored in
  `auth_events`. Five failures in 15 minutes (default) returns 429 with
  `Retry-After`. Unknown-user and wrong-password responses are identical.
- **Audit identity.** Operator actions write `audit_log.source =
  operator:<username>`; machine writers keep `system`/`sync`. With auth off
  (tests/lab) the historical `operator` value is preserved.
- **Bootstrap.** `python -m app.auth.cli create-user --username <name> --role
  admin|operator`. An empty install starts fail-closed and says so on the login
  page. Optional unattended bootstrap:
  `AAM_BOOTSTRAP_ADMIN_USERNAME` / `AAM_BOOTSTRAP_ADMIN_PASSWORD` with
  `auth.bootstrap_from_env: true`; the account must change its password and the
  variables are ignored once any user exists.
- **Security headers**: CSP, `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`; HSTS only over HTTPS.
- **Body caps** on `/login`, `/settings` and `/account/password` in addition to
  the existing upload caps.

### Parallel extraction

`prefect.max_concurrent_extraction_tasks` is now **enforced** (default 3, the
SPEC value for the 2-core host). One sync:

1. ingests every stable file on the flow thread (one SQLite writer, deterministic
   order);
2. reads documents with a bounded `ThreadPoolExecutor` (`aam-extract`), one job
   per `doc_id`, at most the configured number of VLM calls in flight;
3. groups every ingested row in input order, then quarantines failed documents —
   so a quarantine snapshot is complete and reproducible;
4. reconciles and merges exactly as before.

Safety properties:

- The database claim remains an atomic `pending → processing` compare-and-swap;
  a document is never read twice by two workers.
- The VLM call no longer holds a SQLite session/connection (previously held for
  up to 180 s per document). Workers claim, close, call, reopen, persist.
- `database is locked` / pool timeouts are classified as infrastructure, not a
  document verdict: the attempt is refunded and the document stays `pending`.
- The provider breaker is thread-safe. A fatal provider fault (401/402/403 or
  missing key/config) or three distinct HTTP-400 documents stops new launches,
  lets in-flight calls finish, and refunds the run once. Attempt counts go to
  zero, not one.
- PDF rasterisation has its own `vlm.rasterize_concurrency` cap (default 2) so
  three network waits do not become three PyMuPDF renders.
- Progress is monotonic under out-of-order completion (`completed`, `in_flight`,
  percent); a completion can no longer move the bar backwards.
- `Base.metadata.create_all` is cached per process/database, removing hundreds
  of schema writes from a nightly batch.

### Schema and migration

- New tables `users`, `auth_sessions`, `auth_events`.
- Alembic revision `f1a2b3c4d5e6` (after `e8f9a0b1c2d3`).
  `scripts/migrate_db.py` detects old create_all-era databases, stamps them,
  upgrades to the new head and now verifies the auth tables exist.
- `scripts/migrate_db.py` still writes a pre-migration backup.

## 2. Deploying this change

1. **Back up first** (`scripts/backup_db.ps1` or `backup_now.bat`). A copy of
   the database is cheap insurance; `migrate_db.py` also writes one.
2. Stop the web and worker services if you can. (The app and migration are
   online-safe, but a stopped service removes the race with a running sync.)
3. Update the code and dependencies: `uv sync --all-groups`.
4. Run `python scripts/migrate_db.py`. It must print
   `[OK] Database at f1a2b3c4d5e6`.
5. Create the first administrator:
   `.\.venv\Scripts\python.exe -m app.auth.cli create-user --username admin --role admin`
   (set `PYTHONPATH=src` if the project is not installed editable).
6. Add an `auth:` block to `config.yaml` if it is missing (copy it from
   `config.example.yaml`). `auth.enabled: true` is the default even when the
   block is absent.
7. Start the services. Check `/health` (public) and `/login`.
8. Sign in, change the bootstrap password if one was used, and force-merge one
   test set so the audit row shows `operator:<username>`.

## 3. Verification performed

- New `tests/test_auth.py` (22 tests): default deny, login/logout, session
  rotation across app instances, cookie flags, CSRF header and form paths,
  roles, throttling, audit attribution, Argon2id, bootstrap, last-admin guard.
- New `tests/test_parallel_extraction.py` (7 tests): concurrency cap, one call
  per document, slow document isolation, breaker drain/refund, failure
  isolation, `database is locked` refund, CAS race, monotonic progress.
- Real Uvicorn smoke (port 8877, temp config): `/health` 200, CLI created an
  admin, login redirected to `/dashboard` 200, an authenticated POST without
  the CSRF header returned 403, and the same POST with `X-CSRF-Token` reached
  the route (404 for a missing set). This exercises the real ASGI server, not
  only `TestClient`.
- NSSM installer `-DryRun`: all service install/environment/restart commands
  printed for the web and worker services, nothing changed.
- Backup/restore drill: created a WAL database, ran `scripts/backup_db.py`,
  `PRAGMA integrity_check` returned `ok`, and the backup restored into a new
  database with both rows intact.
- Existing suites re-run: concurrency/409, extraction, WS6 crash/restart,
  robustness containment, quarantine, migration, best-case pipeline.
- The full existing suite is run with `auth.enabled=false` (tests/conftest.py)
  so thousands of business-logic tests keep their meaning. Final run:
  `pytest -q --no-cov` exited 0 with no failures.
  If the separate `test/suite-hardening-2026-11-03` branch is merged, keep the
  `cfg.auth.enabled = False` line in its session config: authentication has its
  own tests that build the app with `auth_enabled=True`.
- `pip-audit` is clean after upgrading `pypdf` to 6.21.4, `filelock` to 4.2.7,
  `aiohttp` to 3.14.5, `certifi` to 2026.10.23, `msgpack` to 1.2.4,
  `multidict` to 7.2.1, `oauthlib` to 4.0.1 and `opentelemetry-api` to 1.45.2
  (the last two are Prefect's transitive dependencies). Re-run `pip-audit`
  before each handover update; the tool database changes over time.

## 4. Remaining owner decisions / on-site work (not code)

These are the items the code cannot prove or decide. They are ordered by
handover risk.

1. **TLS — accepted LAN risk.** The handover target is a private LAN, so the
   plain-HTTP service is the agreed deployment. Authentication protects access,
   not confidentiality; if the service is ever exposed beyond the LAN (or the
   client wants encryption on the LAN), put IIS/nginx TLS in front and set
   `auth.cookie_secure: always`.
2. **PyMuPDF version/licence.** PyMuPDF 2.1 is the current release and its
   `open(stream=..., filetype="pdf")` / `get_pixmap(dpi=...).tobytes("png")`
   calls work unchanged (checked in an isolated environment). Upgrading does
   **not** change the licence: 2.1 is still AGPL-3.0 or Artifex commercial.
   A full renderer selection (PDF Oxide, pypdfium2, pdf.js, Poppler,
   Ghostscript, PDFBox and the rest) is in
   [`PDF_RENDERER_EVALUATION_2026-11-03.md`](./PDF_RENDERER_EVALUATION_2026-11-03.md).
   Decision: **pypdfium2 5.14.0 is the intended permissive replacement**
   (BSD/Apache, 693/695 real PDFs, low memory), pending the implementation and
   WS2016/VLM gates in that document. PDF Oxide 0.3.78 is rejected for now:
   30/695 real files fail to render and user-password PDFs render blank pages
   without raising. The licence choice is therefore: fund the pypdfium2
   migration, buy an Artifex licence, or accept AGPL.
3. **Real install, reboot and restore drill.** A full NSSM install requires an
   elevated shell; this workstation session is not elevated, so the service
   install and reboot were **not** run here. What was run: the NSSM installer
   in `-DryRun` mode (all service commands printed, nothing changed) and a real
   backup → integrity-check → restore drill into a scratch database (rows
   verified). Still run WS9 on the target Server 2016 host: install, reboot,
   one real set, and a restore into a scratch folder.
4. **One installer — decided.** `deploy.bat` and `Install.cmd` now both run
   `scripts/setup_test_rig.ps1` (firewall, 13:00 backup, local Prefect). The
   older `scripts/deploy_production.ps1` is marked legacy and is no longer
   referenced by the handover path.
5. **Backup timing — decided.** Sync 11:00, backup 13:00, development snapshot
   15:00, all inside the 10:00–16:00 working day. The SQLite online backup is
   still verified by its own integrity check; a long sync no longer overlaps
   the backup by 30 minutes.
6. **Firewall scope — decided.** The chosen installer scopes the rule to
   Domain/Private/LocalSubnet. The legacy script's any-profile rule is not used.
7. **Log and data growth.** NSSM rotation is off by design; `logging.backup_count:
   0` keeps one growing log. There is no disk-space alert and no retention for
   output/stored/quarantine/archive. Decide a retention policy before go-live.
8. **Health does not prove the worker.** `/health` is liveness for the web
   process. A dead Prefect worker or an unregistered nightly deployment still
   shows the dashboard as online. Add a worker/deployment check (or an external
   monitor on the Prefect API) if the client needs one.
9. **Audit coverage.** Upload, Scan and re-extract/re-match still write no
   `audit_log` row (PRODUCT §8). Authentication now identifies the actor, but
   those actions are only in application logs.
10. **Installer still does not create the admin.** Creating a login is a manual
    CLI step by design (no password in an installer answer file). If the client
    wants unattended installs, use the bootstrap environment variables.

## 5. SaaS/cloud path (least rework)

The auth design was chosen to survive the move: Argon2id, opaque DB sessions,
CSRF tokens and the throttling counters are portable SQLAlchemy tables. What
still ties the app to one host, and the intended order of change:

| Today | First SaaS step |
|---|---|
| SQLite WAL | Postgres (same sync SQLAlchemy models; add a `database_url` config and dialect branch in `core/database.py`) |
| Local `stored/output/quarantine` folders | Object storage (S3/Azure Blob) behind a small storage interface; keep the local filesystem adapter for the pilot |
| In-process poller and sync lock | Move the schedule to Prefect/queue; one worker per tenant; replace `filelock` with a DB/Redis lock |
| `sync_progress` in the web process | Persist progress to DB/Redis so the web and worker processes agree |
| Single sync lock | Per-tenant/per-workspace lock key |
| No tenant column | Add `tenant_id` as a product change, not a role trick; every query and unique index becomes tenant-scoped |
| TrustedHost/Origin compare the request Host | Add `server.public_origin`; configure trusted proxy headers and `cookie_secure: always` |
| No SSO/MFA | Add OIDC (Authlib) as an additional login method; keep the local account for the pilot |

Do not fake multi-tenancy with the `admin`/`operator` roles. They are
authorisation, not isolation.
