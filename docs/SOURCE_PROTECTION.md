# Source Protection for On-Premise Handover

> **Status:** implemented on `feat/source-protection` (2026-11-02).
> **Decision:** compile `src/app` with Nuitka package mode and ship no
> application `.py` / `.pyc` files. This is a deterrent and a trade-secret
> measure, not a technical guarantee. The client owns the machine.
>
> **Rebuild/verify:** [`PROTECTED_BUILD_GUIDE.md`](./PROTECTED_BUILD_GUIDE.md)
> has the exact toolchain, commands, traps and the full-suite procedure.

## 1. What this actually protects against

| Scenario | Before (source zip) | After (compiled release) |
|---|---|---|
| Office user copies the folder | Full Python source | A native `.pyd`; no Python source to open |
| Client IT browses the install directory | Reads every rule | Finds the compiled module, templates, migrations and scripts |
| Casual decompilation with `uncompyle6`/`pycdc` | Minutes | No Python bytecode exists in the app package |
| Determined reverse engineering | Trivial | Native-code analysis (Ghidra/IDA), high cost and time |
| Runtime memory dump on the client host | Trivial | Possible for a skilled admin; cannot be prevented locally |
| Leak after handover | Untraceable | `BUILD_INFO.json` identifies the build; contract/NDA is the remedy |

**What remains readable:** Jinja templates, static JS/CSS, Alembic migration
files, operational PowerShell/Python scripts, `config.yaml`, the SQLite
schema, and the OpenAPI route list. None of those contain the reconciliation
engine. Do not put secrets in them.

**Impossible to guarantee:** a client administrator can always inspect a
running process, dump memory, patch a check, or rebuild a client. The purpose
of this change is to remove the *trivial* copy path and to support the legal
position that the code is a protected trade secret.

## 2. Options considered

| Option | Verdict | Why |
|---|---|---|
| **Nuitka package mode** (selected) | **Yes** | Compiles the whole `app` package to one native extension. Keeps the normal CPython 3.11 venv, Uvicorn, Prefect and NSSM unchanged. Free (AGPLv3 + runtime exception). No source or bytecode shipped. |
| PyArmor | No (for now) | Obfuscates bytecode, but the free tier cannot build `dashboard.py`; Basic is a paid licence. Public static unpackers exist for current versions, and `--restrict` breaks `python -m uvicorn`/Prefect entrypoints without per-module exceptions. Raises the bar less than native compilation. |
| PyInstaller | No | Ships `.pyc` inside the bundle; `pyinstxtractor` + a decompiler recovers it. Also awkward with Prefect's process worker. |
| Cython | No | Native, but per-module build with MSVC and known Pydantic/FastAPI annotation breakage. Nuitka does the same job with less build glue. |
| Docker image on the client host | No | The client admin can `docker save` and read every layer. WS2016 also has no current Python container base. |
| AST rename/minify | No | Output is still valid Python; breaks Pydantic annotations. Security value is near zero. |
| Hosted engine / SaaS | **Tier 1 long-term** | The only option that truly keeps the code off the client machine. Not implemented here because it changes the LAN-only deployment and data-flow agreement. Recommended for the extraction/reconciliation engine if the client accepts it. |
| Contract, NDA, audit clause, per-customer build ID | **Must do** | The enforceable layer. Technical protection is evidence of "reasonable measures" for trade-secret law, not a substitute for the agreement. |

Research sources are listed in §9.

## 3. What the build does

`scripts/build_protected_release.py`:

1. Runs Nuitka on `src/app`:
   `--mode=package --no-pyi-file --lto=no --mingw64`.
   Output: `src/app.cp311-win_amd64.pyd` (about 4.8 MB here).
   Docstrings are deliberately **not** stripped: Pydantic places the model
   class docstring in the JSON schema sent to the VLM, and removing it changes
   the extraction contract. Native compilation is the protection, not text
   removal.
2. Copies only that `.pyd` into `release/src/` — **not** `src/app/`.
   A real `app/` directory would shadow the extension module and re-ship the
   source.
3. Copies the runtime assets: `templates/`, `static/`, `alembic/`,
   `alembic.ini`, `config.example.yaml`, `.env.example`, `DEPLOY.md`, the
   operator docs/scripts, launchers and `PROPRIETARY_NOTICE.txt`. The internal
   README/specification/plan files are **not** copied: they describe the
   architecture and the product rules, and they link to files the client does
   not receive.
4. Rewrites `pyproject.toml` with `[tool.uv] package = false`, so `uv sync`
   installs dependencies only (there is no installable `src/app` package).
5. Exports the locked runtime set to `requirements.txt` for the pip fallback.
6. Writes `BUILD_INFO.json` with a random build ID, git commit, Python
   version and SHA-256 of the `.pyd`.
7. Fails if any `.py` appears under `release/src/` or if `src/app/` exists.
8. Optionally creates the handover zip.

The compiled package is ABI-specific: build with **CPython 3.11 x64**, the
same runtime the production host uses. Rebuild for every Python upgrade.

## 4. Build (on the build machine)

```powershell
py -3.11 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install "nuitka>=2.7" ordered-set zstandard
.\.venv-build\Scripts\python.exe scripts\build_protected_release.py --zip
```

Nuitka downloads its own MinGW64 on first use (`--assume-yes-for-downloads`);
no Visual Studio installation is required. A cold build of this package took
about 10 minutes on the development machine; subsequent builds use ccache.
Keep `BUILD_INFO.json` with the release archive.

Output: `dist/aam-merger-protected-<version>/` and
`dist/aam-merger-protected-<version>.zip`.

The full conversion/verification workflow, including the traps found while
building this branch, is in
[`PROTECTED_BUILD_GUIDE.md`](./PROTECTED_BUILD_GUIDE.md).

Re-assemble without recompiling Nuitka (useful while checking what ships):

```powershell
.\.venv-build\Scripts\python.exe scripts\build_protected_release.py --skip-build
# or point at an archived artifact
.\.venv-build\Scripts\python.exe scripts\build_protected_release.py --pyd build\nuitka\app.cp311-win_amd64.pyd
```

## 5. Deploy / update on the client host

The compiled release is a normal project tree with one difference: `src/`
contains the `.pyd`, not `app/`.

```powershell
# stop services, copy the new files, then:
.\scripts\setup_test_rig.ps1 -OpenFirewall -ScheduleBackup -WithPrefectServer
```

`setup_test_rig.ps1` already sets `PYTHONPATH=<root>\src` for NSSM services,
so `python -m uvicorn app.main:app` and the Prefect worker import the compiled
module. The installer detects the `.pyd` and uses `requirements.txt` on the
pip fallback path; `uv sync` uses `package = false` from the release
`pyproject.toml`.

The release also pins **Python 3.11** in `.python-version` and
`requires-python = ">=3.11,<3.12"`. Without that pin, `uv sync` chose the host's
newest Python (3.13 in testing) and the cp311 `.pyd` failed to import.
`setup_test_rig.ps1`, `deploy_production.ps1` and `update_production.ps1` now
check the venv's actual minor version and refuse to start with a clear message
if it is not 3.11. If the host has no Python 3.11 and no `uv`, install Python
3.11 x64 first; `uv` can download it automatically.

`scripts/deploy_prefect.py` now registers the flow with
`entrypoint_type=EntrypointType.MODULE_PATH` (`app.flows.sync.sync_flow`)
instead of Prefect's default file path. A compiled package has no
`app/flows/sync.py`, so the file path would fail. The job environment also
repeats `PYTHONPATH` for the worker's child interpreter.

**Do not** copy a source `src/app/` directory over a protected release. It
will shadow the `.pyd` and silently ship the source again. The build script
checks for this; deployment checks only that `PYTHONPATH` points at `src/`.

### 5.1 Rollback

The source of truth remains the git repository on the supplier side; the
protected tree under `dist/` is generated and is git-ignored. Keep the previous
release folder on the client host. To roll back:

```powershell
.\scripts\manage_services.ps1 -Action stop
# swap the release folder back (or restore the previous files)
.\scripts\manage_services.ps1 -Action start
```

Do not unzip a source checkout as a rollback: it changes the runtime layout and
reintroduces the source-code exposure this change removes.

## 6. Verification performed

| Check | Command | Result |
|---|---|---|
| Baseline source suite | `.venv\Scripts\python -m pytest --no-cov -q` | Green (0 failures) |
| Release assembly contract tests | `.venv\Scripts\python -m pytest --no-cov -q tests/test_protected_release.py` | 5 passed |
| Compiled == source | `.venv\Scripts\python scripts\verify_protected_release.py` | Prompts, Pydantic schema, `REASON_TEXT` identical; loader `nuitka_module_loader`; `/health`, `/dashboard`, `/openapi.json` 200 |
| Compiled import | `PYTHONPATH=<release>/src python -c "import app"` | `app.__file__` = `<release>/src/app/__init__.py` (Nuitka virtual path); no source file exists; loader is `nuitka_module_loader` |
| Template resolution | `import app.api.templating` | `TEMPLATES_DIR` resolved to `<release>/templates`, exists |
| Alembic migrations | `python scripts\migrate_db.py` in the release | Reached head `e8f9a0b1c2d3` against the compiled models |
| Web smoke (TestClient) | `/health`, `/dashboard`, `/openapi.json` | 200 / 200 (13,673 bytes) / 200 (31 routes) |
| Web smoke (real Uvicorn) | `python -m uvicorn app.main:app --port 8011` | `/health` 200, `/dashboard` 200, 13,673 bytes |
| Dependency install path | `uv sync --no-dev` in a fresh protected tree | exit 0; venv selected Python 3.11.17 from `.python-version`; compiled app imported and `/health` returned 200 |
| Prefect entrypoint (build only) | `sync_flow.to_deployment(..., entrypoint_type=MODULE_PATH)` | `app.flows.sync.sync_flow`, compiled module imported |
| Prefect deployment (real API) | Local `prefect server start`, create pool, run `scripts/deploy_prefect.py` from the release | Registered `aam-midnight-sync`; entrypoint `app.flows.sync.sync_flow`; schedule `0 11 * * *` Asia/Kolkata; job env includes `PYTHONPATH=<release>/src` |
| Full suite against compiled app | `.venv311\Scripts\python -m pytest --no-cov -q` with `PYTHONPATH=<release>/src` | **6,702 passed, 292 skipped, 0 failed** (JUnit, 688 s) |

The full compiled-app run is the important one: it exercises the same tests
as the source checkout, but every `import app` resolves to the `.pyd`.
Source-text guards and the POSIX `os.name` test are skipped automatically when
the loader is `nuitka_module_loader` (they read `src/app/**/*.py` or fake the
host OS); the functional tests still run. Run this suite alone: one
timing-sensitive test failed when a second pytest process was run concurrently,
and passed in the clean run above.

The first compiled run also caught a real issue: `--python-flag=no_docstrings`
changed the Pydantic schema sent to the VLM. The flag was removed and the
schema hash test passes again.

## 7. Known limitations (accepted)

1. **The client owns the host.** A determined administrator can reverse
   engineer native code, dump process memory, or patch the app. Native
   compilation makes that expensive; it does not make it impossible.
2. **Templates, static assets, Alembic versions and operator scripts stay as
   readable files.** They are not the reconciliation engine. They do reveal
   the UI, the schema history and operational behaviour.
3. **`/openapi.json` exposes the route table**, and the SQLite schema is
   visible in the database file. This was already true before this change.
4. **The OpenRouter key is readable on the host** if it is in `.env` or the
   service environment. Use a client-owned key, a proxy, or the hosted-engine
   option if this matters. Do not treat the local key as secret.
5. **The build is tied to CPython 3.11 x64.** A 3.12 or 32-bit host needs a
   separate build; the `.pyd` will not load.
6. **No code signing yet.** `pyarmor_runtime` is not shipped, but native
   `.pyd` files can still attract antivirus heuristics. Sign the release with
   the organisation's Authenticode certificate and submit a sample to the
   client's AV vendor before handover.
7. **WS2016 reaches extended-support end on 2027-01-12.** Plan the OS/runtime
   upgrade independent of this work.
8. **No per-machine licence check is implemented.** Machine binding was
   deliberately skipped: a false positive would brick a production CA tool.
   The build ID is for attribution, not enforcement.

## 8. Recommended non-code layers

1. **Contract first.** Proprietary licence, NDA, no-reverse-engineering and
   audit-right clauses; mark the build and repository "Confidential".
2. **Client-supplied OpenRouter key**, or route LLM calls through a supplier
   proxy. A key on the client host is not a secret.
3. **Per-customer build ID** (already written to `BUILD_INFO.json`) and a
   build log. If a copy appears, the build identifies the handover.
4. **Code signing** of the `.pyd` and installer, plus an AV allow-list entry
   agreed with the client's IT.
5. **Consider the hosted engine** for the highest-value logic. Keep the thin
   FastAPI/Prefect shell on-site and send normalised line items to an HTTPS
   engine you operate. This is the only option that removes the code from the
   client's machine.

## 9. Research sources

- Nuitka package mode / user manual: <https://nuitka.net/user-documentation/use-cases.html>
- Nuitka runtime licence exception: <https://github.com/Nuitka/Nuitka/blob/develop/LICENSE-RUNTIME.txt>
- Prefect `to_deployment` / entrypoint types: <https://github.com/PrefectHQ/prefect/blob/3.8.3/src/prefect/flows.py>
- PyArmor limits and pricing: <https://pyarmor.readthedocs.io/en/latest/topic/obfuscated-script.html>
- PyArmor static unpacker (why it was not selected): <https://github.com/Lil-House/PyArmor-Static-Unpack-1shot>
- PyInstaller operating mode (bytecode, decompilable): <https://pyinstaller.org/en/stable/operating-mode.html>
- Windows Server 2016 lifecycle: <https://learn.microsoft.com/en-us/lifecycle/products/windows-server-2016>
- Trade-secret definition / reasonable measures: <https://www.law.cornell.edu/uscode/text/18/1839>
