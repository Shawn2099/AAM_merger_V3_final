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

## 7. Live Win11 deployment (completed 2026-11-04)

The canonical deployment path `C:\Users\malliath\Desktop\deployment` was
upgraded in place. The old services were stopped, the live DB/config/input were
backed up, and the old code was replaced with `main` `4490b04`:

| Check | Result |
|---|---|
| Live DB migrated | `e8f9a0b1c2d3` → `f1a2b3c4d5e6` |
| Documents / PO sets / line items | 104 / 29 / 173 preserved |
| Input folder | 46 files preserved |
| Key files vs `main` `4490b04` | hashes match (`cli.py`, `verify_deployment.ps1`, `pyproject.toml`, validation doc) |
| `.git` in deployment | removed (plain production copy) |
| `pymupdf` in runtime | absent; `pypdfium2==5.14.0` + Pillow installed |
| Services | all three `Running`, `Automatic` |
| `verify_deployment.ps1` | **17 passed, 0 failed, 0 warnings** |
| `/health` on localhost / `10.10.151.53` / hostname | 200 each |
| LAN login | 303 → dashboard, `aam_session` cookie set |
| Forced first-login change | 303 → `/account/password` |
| Scheduled tasks | `AAMMerger-DailyBackup` next 13:00; `AAMMerger-DevelopmentSnapshot` next 15:00 |
| Firewall | `AAM Merger V3 Web (8000)` enabled |

The first admin is `admin` with `must_change_password=1`. Its temporary
password is in `C:\Users\malliath\Desktop\deployment\data\initial_admin_password.txt`
(ACL restricted to the installing user and SYSTEM). Change it at first login,
then delete that file.

The live DB/config/input backup is
`C:\Users\malliath\Desktop\aam_legacy_backup_20261104_143240`. The old code
archive was removed after the new deployment passed verification; the old
release worktree was also removed. The separate `aam_backup` product's files
are untouched; its three stale services were removed (one remained marked for
deletion until the next reboot).

## 8. Remaining gate

1. Reboot Windows 11 and confirm the three `Automatic` services return (the
   service configuration is installed, but the reboot itself was not run here).
2. Change the `admin` password and delete
   `data\initial_admin_password.txt`.
3. Run one real sync with the real OpenRouter key and one restore into a
   scratch folder. The live DB migration, backup and restore drill on its copy
   already passed.

## 9. Test suite after the fixes

- `pytest tests/test_auth.py`: 25 passed.
- `pytest tests/test_windows_scripts.py`: 27 passed, 1 skipped.
- `ruff check` on the changed Python files: clean.
- Full non-UI suite: exit 0; PR #13 Windows and UI release gates passed.
- Clean-copy `setup_test_rig.ps1 -SkipServices`: exit 0.
