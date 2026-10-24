# Delivery Runbook — one PC in the client's office

This is the short version for handing the MVP to the client. The full technical
detail is in [`DEPLOY.md`](../DEPLOY.md); this page is what to follow on the day.

**Target setup:** one Windows PC (the "server") runs the web app, the database,
the file folders and the nightly sync. Other people in the office open the
dashboard in a browser at `http://<server-ip>:8000/dashboard`. They all see the
same data — there is no per-user login.

---

## 1. What the server PC needs

| Item | Requirement |
|---|---|
| OS | Windows 10/11 or Windows Server 2016+ |
| CPU / RAM | 2 cores, 4 GB free (2-core is the design target) |
| Disk | Local disk only. **Never put the database on an SMB share** — SQLite WAL needs shared memory |
| Network | Static LAN IP (or a DHCP reservation). Internet access for OpenRouter |
| Rights | Administrator for the install (services, firewall, backup task) |
| Python | Python 3.11+ **or** `uv` on `PATH` (the installer prefers `uv`) |
| Key | An OpenRouter API key with its own quota, in `.env` — never in `config.yaml` |

---

## 2. Put the code and the key in place

Copy the repository to the server (for example `C:\AAM_Merger`) and open
**PowerShell as Administrator** in that folder.

```powershell
# One-time: the secret. Use the tester key, not a personal one.
notepad .env
#   OPENROUTER_API_KEY=sk-or-v1-...
```

`config.yaml` is created for you from `config.example.yaml`. The default paths
are `./data/...`, relative to the project folder — that is correct for a
single-PC install. Edit only if you are moving the data to another local disk,
and keep the SQLite file on a local drive.

If the project folder moves or is renamed, rerun `scripts/deploy_prefect.py`:
the Prefect deployment's paths are fixed at deploy time.

---

## 3. Install

> **Fresh database required.** The PO grouping key changed to digits-only
> (PRODUCT section 8, entry M) and old keys are not migrated. Do not upgrade a
> database created before this change in place; install with a new, empty one
> (`uv run python scripts/migrate_db.py`).

Allow the scripts to run in this PowerShell session and unblock the downloaded files first:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
Get-ChildItem -Recurse *.ps1 | Unblock-File
```

```powershell
.\scripts\setup_test_rig.ps1 -OpenFirewall -ScheduleBackup -WithPrefectServer
```

What it does, in order:

1. syncs the Python environment (`uv sync --no-dev`, or `pip install -e .`); this installs PyMuPDF
   (`pymupdf==1.28.2`, a binary wheel used to rasterise PDF pages for the VLM; it needs the
   Microsoft Visual C++ 2015-2022 x64 redistributable, which a fresh Windows Server 2016 may lack);
2. creates `config.yaml` if missing and the data folders;
3. applies the database schema (`python scripts/migrate_db.py` — handles both a
   fresh database and the existing `create_all`-era database, which has no
   `alembic_version`; a bare `alembic upgrade head` fails on the latter);
   After upgrading an existing rig, run `python scripts/review_merged_sets.py` (read-only) to list
   already-merged packets that need manual review.
4. adds the inbound firewall rule for TCP 8000 (`-OpenFirewall`);
5. registers the daily 01:00 database backup (`-ScheduleBackup`, no pruning,
   into `data\development\snapshots\db`) and the 01:30 copy-only development
   snapshot task for logs and PDFs;
6. installs and starts three Windows services (NSSM log rotation is disabled;
   `PREFECT_HOME` is pinned to `data\prefect`):
   `AAMMerger-PrefectServer`, `AAMMerger-Worker`, `AAMMerger-Web`;
7. waits for the Prefect API, creates the `aam-merger-process-pool` work pool,
   registers the **midnight sync** deployment, and restarts the worker;
8. performs an HTTP health check.

It prints the dashboard URL using this machine's LAN IP. Services start on boot.

**Re-running the installer is safe** — it updates the environment, services and
scheduled sync without touching data. That is also the upgrade path (section 8).

> Assembling the pieces by hand? Then you must do step 7 yourself:
> `python -m prefect work-pool create aam-merger-process-pool --type process`
> before `python scripts/deploy_prefect.py`. Without a work pool the nightly
> sync is never registered.

---

## 4. Verify before you hand it over

1. On the server: `http://127.0.0.1:8000/health` returns `{"status":"ok",...}`.
2. On **another PC** in the office: open `http://<server-ip>:8000/dashboard`.
   If that fails, the firewall rule is missing — re-run with `-OpenFirewall`.
3. Drop one complete real set (one PO, one DN, one SI) into `data\input`, press
   **Scan Input Folder**, and confirm it reaches **Merged & Ready** and the
   packet appears in `data\output`.
4. `.\scripts\manage_services.ps1 -Action status` shows all three services
   `Running`.
5. `.\scripts\backup_db.ps1 -RetentionDays 0` creates a file in
   `data\development\snapshots\db`, and the `AAMMerger-DevelopmentSnapshot`
   task has copied logs/files into `data\development\snapshots`.
6. **Pin the host names.** In `config.yaml` set `server.allowed_hosts` to the
   server's name and static IP, for example `["aam-server", "10.0.0.5"]`. The
   shipped default `["*"]` accepts any Host header, which leaves the dashboard
   open to a malicious web page reaching the LAN server from a user's browser.
   Restart `AAMMerger-Web`, then re-check step 2 from another PC.
7. **Confirm the schedule zone.** `prefect.schedule_timezone` must be the
   office's IANA zone (default `Asia/Kolkata`); re-run the installer if it
   changed.
8. **Hand over the daily check.** Nothing alerts anyone if the midnight sync
   does not run. Every morning the operator opens the dashboard and confirms
   that new documents from yesterday have been read; if the inbox is waiting
   and nothing moved overnight, check the three services
   (`manage_services.ps1 -Action status`) before anything else.

---

## 5. Let the office submit documents

The product reads PDFs from the server's `data\input` folder. Users on other
PCs cannot see that folder unless you share it:

```powershell
# Example only — choose the security group that matches the office.
New-SmbShare -Name "AAM-Input" -Path "C:\AAM_Merger\data\input" -FullAccess "Domain Users"
```

Users then copy PDFs to `\\<server-name>\AAM-Input`, and anyone can press
**Scan Input Folder**. Share **only `data\input`** — not `data\`, which contains
the database, the quarantine and the merged packets.

Keep the input folder local-disk backed. A mapped SMB path in `config.yaml`
would put the document reads on the network; sharing the folder to clients is
fine, pointing the app at a UNC path is not required and adds failure modes.

---

## 6. Daily operation

- **Nightly sync** runs at 00:00 (Prefect deployment `aam-midnight-sync`).
  `config.example.yaml` sets `prefect.schedule_timezone: "Asia/Kolkata"`, so
  this is **IST midnight**. Change it to the office's IANA zone if the client
  is elsewhere, and re-run the installer to re-register the schedule.
- **Backups** run at 01:00 via the `AAMMerger-DailyBackup` scheduled task with
  `-RetentionDays 0` and `-BackupDir data\development\snapshots\db`. Nothing is
  pruned.
- **Development snapshot** runs at 01:30 via
  `AAMMerger-DevelopmentSnapshot`. It copies logs, raw JSON, `stored`,
  `quarantine`, `output` and `combined` into `data\development\snapshots` with
  `robocopy /E /XO` (copy-only; never deletes).
- **Logs** are in `data\logs\` (`aam_merger.log` plus NSSM stdout/stderr).
  `logging.backup_count: 0` and NSSM `AppRotateFiles 0` keep one growing file.
- **Delete is archive.** A quarantined set's rows and PDFs are kept; the export
  is under `data\development\archived_sets`.
- **Manual scan** is the same button users press: it is safe to run at any time;
  a second request while one is running is refused with 409.

Restore a backup: stop the services, replace `data\aam_merger.db` with a file
from `data\development\snapshots\db`, delete any `-wal`/`-shm` beside it, start
the services. Keep `data\stored`, `data\output`, `data\combined` and
`data\quarantine` with the restored DB — the rows point at those files.

---

## 7. Clearing data between test rounds

> **Do not run this on the pilot PC.** The pilot exists to collect data; a reset
> destroys the DB, the ingested PDFs and the generated packets. Use it only on a
> throwaway test rig, after copying `data\development` to external media.

```powershell
.\scripts\reset_data.ps1          # asks you to type RESET
```

It refuses while port 8000 is in use, backs the database up first, deletes the
database and every ingested/generated file, then recreates the schema. It keeps
`config.yaml`, `.env`, `data\backup` and the logs. It does not preserve
`data\development`; copy that folder first if you need it.

It also refuses to touch any path that is outside `<project>\data`, or that is
the backup folder (or one of its parents): a typo in `config.yaml` cannot make
it empty a system folder, and the snapshot it just wrote is never deleted by
the same run. A production layout under `C:\AAM` is therefore out of scope for
this test-rig script — use `scripts\backup_db.py` and a manual cleanup there.

---

## 8. Updating the code

```powershell
.\scripts\manage_services.ps1 -Action stop
git pull
.\scripts\setup_test_rig.ps1 -OpenFirewall -ScheduleBackup -WithPrefectServer
```

The installer re-applies migrations and re-registers the deployment. If you
skipped `git pull` because the client has no git, copy the new files over and
run the same command.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Other PCs cannot open the dashboard | No inbound rule for TCP 8000. Re-run with `-OpenFirewall`. Check the office network/VLAN. |
| Dashboard opens, buttons do nothing | The browser is loading stale HTML. HTMX/Alpine are served by the app itself now; hard-refresh (Ctrl+F5). |
| "Sync already running" (409) | Someone else pressed scan. Wait for the spinner to finish. Clicking twice does not duplicate work. |
| `OPENROUTER_API_KEY not set` in the log | `.env` missing or not in the project folder. Fix it and restart `AAMMerger-Web`; no restart is needed for extraction runs, but the startup warning is the tell. |
| Nightly sync does not run | `PREFECT_API_URL` and the pool must exist. Check `AAMMerger-PrefectServer` is Running, then re-run the installer (step 7). |
| Documents stay "Not read" | The server has no internet, or the key is out of quota. Check `data\logs\aam_merger.log`. |
| Database errors on a network drive | Move the database back to a local disk. SQLite WAL does not work over SMB. |
| Port 8000 already in use | Stop whatever owns it (`Get-NetTCPConnection -LocalPort 8000`), or re-run the installer with `-Port <free port>`. The bind port comes from the installer parameter; `server.port` in `config.yaml` is not read by the service. |

---

## 10. Handover checklist

- [ ] `.env` holds the **tester** key (not a personal one)
- [ ] Services installed and Running; they survive a reboot
- [ ] Firewall rule present; dashboard opens from a second PC
- [ ] One real PO/DN/SI set merged end-to-end; packet in `data\output`
- [ ] Intake folder shared (or an agreed way to deliver PDFs)
- [ ] Daily backup task registered with `-RetentionDays 0`; one backup exists in `data\development\snapshots\db`
- [ ] `AAMMerger-DevelopmentSnapshot` task registered; one snapshot contains logs/files
- [ ] NSSM `AppRotateFiles 0` on all three services; `logging.backup_count: 0`
- [ ] `data\development\archived_sets` exists and is writable; Delete was tested once
- [ ] No one will run `scripts\reset_data.ps1` during the pilot
- [ ] Dashboard URL written down for users
- [ ] `docs/TESTER_GUIDE.md` given to the testers
- [ ] Known limitations explained (guessed merge risk, quantities only, one PO per set, archive-on-delete)

---

## Security note (accepted for this MVP)

There is **no authentication**. Anyone who can reach port 8000 can see every
document and press every button. That is acceptable only on the client's
private office LAN. Do not expose it to the internet, and keep the firewall rule
scoped to the office subnet rather than "Any".

Two browser-side hardening checks exist and are worth keeping on:

- A state-changing request (`POST`/`PUT`/`PATCH`/`DELETE`) whose `Origin` or
  `Referer` names a different host than the request is refused with `403`.
  Requests that send neither header (curl, integrations) still work.
- `server.allowed_hosts` (default `["*"]`, which accepts any host) can list the
  host's real names/IPs; when it does, an HTTP `Host` header that is not listed
  is refused. Set it when the host address is stable — it closes DNS-rebinding
  reads that the no-auth design would otherwise allow.

They reduce cross-site request forgery and DNS rebinding; they are **not**
authentication and do not make an internet-facing deployment safe.
