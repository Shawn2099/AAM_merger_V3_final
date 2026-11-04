# Windows 11 deployment acceptance — 2026-11-04

**Decision:** Windows 11 x64 is the deployment target. Windows Server 2016 is
retired as the target; its clean-image/2-core gates are historical.

**Host under test**

| Item | Value |
|---|---|
| OS | Windows 11 Home, 10.0.26300, x64 |
| CPU | Intel i7-7700, 8 logical processors |
| Python | 3.11 (project `.venv`) |
| Branch | `feat/win11-deployment`, from `main` `272b158` |
| Renderer | pypdfium2 5.14.0 + Pillow 13.0.0 |
| Authentication | ON (`auth.enabled: true`) |

The session is an administrator account with UAC filtering (`IsInRole` is
false), so the real NSSM install/reboot cannot run without an elevation prompt.
Everything below was executed without elevation and is reproducible.

---

## 1. Isolated current-code rig

A throwaway root was created at
`%TEMP%\aam_win11_acceptance_20261103_144922`, with a config that pointed every
path at that root and bound Uvicorn to `127.0.0.1:8010`. The live deployment
was not modified.

The database was copied from the live October deployment with SQLite's online
backup API, then migrated by `scripts/migrate_db.py`:

| Check | Result |
|---|---|
| Migration head | `f1a2b3c4d5e6` |
| Pre-migration backup written | yes |
| Documents preserved | 104 → 104 |
| PO sets preserved | 29 → 29 |
| Line items preserved | 173 → 173 |
| Auth tables present | `users`, `auth_sessions`, `auth_events` |

## 2. Real HTTP authentication gate

Uvicorn was started with the current code and the isolated config. The gate
used a browser-like `Accept: text/html` client and real session cookies.

| Check | Result |
|---|---|
| `/health` unauthenticated | 200 |
| `/dashboard` unauthenticated | 303 → `/login?next=%2Fdashboard` |
| Login page | 200 |
| Wrong password | 401, no session cookie |
| Admin login | 303 → `/dashboard`, `aam_session` cookie |
| Admin dashboard | 200, 67,177 bytes |
| CSRF token exposed | yes, 43 characters |
| Admin `/audit` | 200 |
| Admin `/settings` | 200 |
| Admin `/account/password` | 200 |
| `/docs` while signed in | 404 (disabled) |
| CSP / X-Frame-Options | `unsafe-eval` for Alpine; `SAMEORIGIN` |
| Logout without CSRF | 403 |
| Logout with CSRF | 303 → `/login` |
| Dashboard after logout | 303 → login |
| Operator login | 303 |
| Operator dashboard | 200 |
| Operator `/settings` | 403 |
| `auth_events` rows written | login failure, login success ×2, logout |

**Result: 18/18 checks passed.** `tests/test_auth.py` is green (25 tests).

## 3. Bug found and fixed — PowerShell BOM in `--password-stdin`

Windows PowerShell 5.1 prefixes piped native-process stdin with a UTF-8 BOM.
`python -m app.auth.cli create-user --password-stdin` was hashing that BOM as
part of the password, so the operator could never sign in with the password
they typed.

`src/app/auth/cli.py` now strips a leading U+FEFF before validating the line.
`tests/test_auth.py::test_password_stdin_strips_a_powershell_bom` pins the
behaviour.

## 4. Deployment verification fix

`scripts/verify_deployment.ps1` used to call `/dashboard` without a browser
`Accept` header and treat the auth response as a failure. It now reports the
auth gate as healthy:

```text
[PASS] Dashboard /dashboard : Auth gate active (HTTP 401); sign in at /login
```

It also checks the firewall rule the current installer actually creates
(`AAM Merger V3 Web (<port>)`) as well as the old name.

Audit result against the isolated rig:

```text
Audit Results: 15 Passed, 0 Failed, 2 Warnings
```

The two warnings are expected on the throwaway rig: the 13:00 scheduled task
and the firewall rule were not registered for port 8010. Services and Prefect
were already running on the host and passed.

## 5. Backup and restore drill

`scripts/backup_db.ps1` produced a 319,488-byte online backup. A copy restored
into a scratch database returned:

| Check | Result |
|---|---|
| `PRAGMA integrity_check` | `ok` |
| Documents | 104 |
| PO sets | 29 |
| Line items | 173 |
| Users | 2 |

## 6. NSSM installer dry run

`scripts/install_nssm_services.ps1 -ProjectDir . -ConfigPath <isolated config>
-Port 8010 -HostAddress 127.0.0.1 -WithPrefectServer -DryRun` exited 0. It
printed every NSSM command for the three services, including the
`PrefectServer` → `Worker` dependency, the config path, the Prefect API URL and
the log paths. Nothing was changed.

## 7. Still to run with elevation

These are the only remaining deployment gates and need one UAC approval:

1. Install/update the three NSSM services against the chosen current-code
   folder, with the firewall rule and the 13:00 backup / 15:00 snapshot tasks.
2. Stop/start the services and confirm `/health`, `verify_deployment.ps1` and a
   real operator login from the LAN address.
3. Reboot Windows 11 and confirm the services return automatically.
4. Run one real sync (real PDFs, real OpenRouter key) and one restore into a
   scratch folder.
5. Then point the live `AAMMerger-*` services at the current code; until then
   the October deployment remains the live instance.

## 8. Test suite after the fixes

- `pytest tests/test_auth.py`: 25 passed.
- `pytest tests/test_windows_scripts.py`: 27 passed, 1 skipped.
- `ruff check` on the changed Python files: clean.
- Full non-UI suite and the UI gate are re-run before the PR is merged.
