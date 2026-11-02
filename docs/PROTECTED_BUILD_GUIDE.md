# Protected Build Guide — How to Rebuild and Verify the Compiled Release

> **Audience:** any developer who picks up `feat/source-protection`.
> **Goal:** turn the source checkout into a runnable release that contains no
> application Python source, without changing application behaviour.
> **Status:** this is the workflow that produced the protected build on
> 2026-11-02 and passed the full suite (6,994 tests, 0 failures). Each assembly
> writes a fresh random build ID to `BUILD_INFO.json`.

Read [`SOURCE_PROTECTION.md`](./SOURCE_PROTECTION.md) first for why Nuitka was
chosen and what remains unprotected.

## 1. TL;DR

```powershell
# build environment (once)
py -3.11 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install "nuitka>=2.7" ordered-set zstandard

# compile + assemble + zip
.\.venv-build\Scripts\python.exe scripts\build_protected_release.py --zip

# verification environment (once; no project install)
uv venv --python 3.11 .venv311
uv pip install --python .\.venv311\Scripts\python.exe `
  -r dist\aam-merger-protected-0.1.0\requirements.txt
uv pip install --python .\.venv311\Scripts\python.exe `
  pytest pytest-asyncio pytest-cov hypothesis pytest-html `
  pytest-playwright playwright axe-playwright-python `
  pytest-playwright-visual-snapshot pillow

# prove compiled == source for prompts/schema/strings, then smoke it
.\.venv\Scripts\python.exe scripts\verify_protected_release.py

# full suite against the compiled package
$env:PYTHONPATH = (Resolve-Path dist\aam-merger-protected-0.1.0\src).Path
.\.venv311\Scripts\python.exe -m pytest --no-cov -q
```

Output: `dist/aam-merger-protected-<version>/` and `.zip`.

## 2. Toolchain that worked

| Item | Value |
|---|---|
| Build OS | Windows 11 x64 (development machine) |
| Build interpreter | CPython **3.11.9 x64** (`py -3.11`) |
| Compiler | Nuitka **4.3.4**; MinGW64 **15.2.0** auto-downloaded |
| C compiler install | none needed — Nuitka downloads MinGW64 with `--mingw64` |
| Runtime target | CPython **3.11 x64** on Windows Server 2016 |
| Prefect | 3.8.3 |
| Build command | `--mode=package --no-pyi-file --lto=no --jobs=4 --mingw64 --assume-yes-for-downloads` |
| Cold build | roughly 10 minutes; ccache makes later builds much faster |
| Output | one `app.cp311-win_amd64.pyd` (~4.8 MB) |

The pyd has only four DLL imports: `KERNEL32.dll`, `msvcrt.dll`, `SHELL32.dll`
and `python311.dll`. No MinGW runtime DLL has to be shipped. Verified with
`objdump -p` and by importing it with a stripped-down `PATH`.

## 3. What worked, and why

1. **Nuitka package mode, not a standalone executable.**
   `--mode=package` compiles every module under `src/app` into one extension
   module. Third-party packages stay normal wheels. Uvicorn, Prefect, NSSM and
   Alembic keep running under the normal Python interpreter.
2. **Keep the pyd at `src/app.cp311-win_amd64.pyd`.**
   Nuitka's virtual `__file__` then resolves `src/app/api/templating.py` et al.
   The existing `Path(__file__).resolve().parents[3]` logic finds
   `templates/`, `static/` and `.env` without code changes.
3. **Never ship `src/app/`.**
   A real package directory shadows the extension module and silently ships the
   source again. The build script fails if it sees one.
4. **`PYTHONPATH=<root>\src` for every process.**
   NSSM services already set this; `deploy_prefect.py` inserts it itself and
   repeats it in the Prefect job environment.
5. **Prefect `MODULE_PATH` entrypoint.**
   `app.flows.sync.sync_flow` imports from the compiled package. Prefect's
   default `FILE_PATH` entrypoint names `app/flows/sync.py`, which does not
   exist in a protected release.
6. **Pin Python 3.11 in the release.**
   `.python-version` = `3.11` and `requires-python = ">=3.11,<3.12"` stop
   `uv sync` from creating a 3.13 venv for a cp311 module. The PowerShell
   installers also check the venv and refuse a mismatched minor version.
7. **`[tool.uv] package = false` in the release `pyproject.toml`.**
   `uv sync` installs dependencies only; there is no `src/app` package for
   hatchling to build. A locked `requirements.txt` covers the no-uv fallback.
8. **Keep docstrings.**
   `--python-flag=no_docstrings` changes Pydantic's JSON schema: the model
   class docstring becomes the schema `description` sent to the VLM. That was
   caught by `test_extraction_coercion.py`; the flag is deliberately not used.
9. **Verify prompts/schema text, not just imports.**
   `scripts/verify_protected_release.py` compares the SHA-256 of
   `_SYSTEM_PROMPT`, `_PAGE_PROMPT`, the Pydantic schema and `REASON_TEXT`
   between the source and compiled environments. This is the direct answer to
   "did any characters change?".

## 4. What did not work / traps

| Trap | Symptom | Fix |
|---|---|---|
| `--python-flag=no_docstrings` | Pydantic schema hash changes; VLM prompt loses model description | Do not use that flag. Rebuild. |
| `uv sync` without a Python pin | Creates a 3.13 venv; `ImportError: DLL load failed while importing app` | Ship `.python-version` 3.11 + `requires-python <3.12`; installers enforce it. |
| Prefect default FILE_PATH | Deployment entrypoint points at missing `app/flows/sync.py` | `entrypoint_type=EntrypointType.MODULE_PATH`; pass `PYTHONPATH`. |
| `src/app/` left beside the pyd | Source is imported instead of the pyd; protection silently gone | Build check; deploy check; never copy a source tree over a release. |
| `inspect.getsource` | `OSError: could not get source code` | Expected. Do not add runtime code that reads its own source. Source-text tests skip under Nuitka. |
| Fake `os.name` tests | Pytest can crash formatting the failure (`PosixPath` on Windows) | The POSIX-only test is skipped in the compiled run; run the suite alone. |
| LTO on 2 cores | Link stage can take tens of minutes | Keep `--lto=no`; compile with `--jobs=2` on the 2-core host. |
| Standalone/onefile exe | Prefect process workers re-execute the engine; awkward and slow | Use package mode with the normal venv. |
| Unsigned `.pyd` | Antivirus quarantine/AV false positive | Authenticode-sign the pyd and installer; submit a sample to the client AV. |
| Shipping README/specs | Architecture and product rules exposed | The build whitelist deliberately excludes them. |
| Concurrent pytest runs | One timing-sensitive test failed when a second suite ran simultaneously | Run the compiled suite alone. |

## 5. What the build script actually ships

`scripts/build_protected_release.py` copies a **whitelist**, not the repository:

- `src/app.cp311-win_amd64.pyd`
- `templates/`, `static/`, `alembic/`, `alembic.ini`
- `config.example.yaml`, `.env.example`, `.python-version`
- `DEPLOY.md`, `docs/DELIVERY.md`, `docs/SOURCE_PROTECTION.md`,
  `docs/PROTECTED_BUILD_GUIDE.md`
- operator scripts and launchers listed in `SCRIPT_FILES` / `ROOT_LAUNCHERS`
- `pyproject.toml` (rewritten: `package=false`, Python 3.11 pin), `uv.lock`,
  `requirements.txt`
- `BUILD_INFO.json` (build id, git commit, Python, pyd SHA-256)
- `PROPRIETARY_NOTICE.txt`

It deliberately does **not** copy: `src/app/`, tests, README, PRODUCT/SPEC/PLAN
docs, `pattern_lab/`, `prompt_improvement/`, reports, data, `.git`, `.venv`.

## 6. Conversion checklist for a new developer

1. `git switch feat/source-protection` (or branch from it).
2. Make normal source changes and run the source suite first:
   ```powershell
   .\.venv\Scripts\python.exe -m pytest --no-cov -q
   ```
3. Rebuild:
   ```powershell
   .\.venv-build\Scripts\python.exe scripts\build_protected_release.py --zip
   ```
4. Verify the assembled tree:
   ```powershell
   .\.venv\Scripts\python.exe scripts\verify_protected_release.py
   ```
   Expected: `[PASS] protected release is complete and behaves like the source app`.
5. Run the full suite against the compiled package:
   ```powershell
   $env:PYTHONPATH = (Resolve-Path dist\aam-merger-protected-0.1.0\src).Path
   .\.venv311\Scripts\python.exe -m pytest --no-cov -q
   ```
   Expected on this branch: `6,702 passed, 292 skipped` (skips include the
   source-text guards and the POSIX-only test).
6. Optionally verify Prefect registration against a local server (see §9).
7. Sign the pyd/installer, test the client AV, then follow
   [`DEPLOY.md`](../DEPLOY.md) / [`docs/DELIVERY.md`](./DELIVERY.md).

## 7. Adding code, dependencies, assets or a new Python version

- **New module under `src/app/`** — package mode includes it automatically.
  Rebuild; no build-script change.
- **New module imported only by a string** (e.g. `importlib.import_module(name)`)
  — add `--include-module=app.your.module` to `compile_package` or make the
  import literal. The test suite must import the module either way.
- **New dependency** — add it to `pyproject.toml`, run `uv lock`, rebuild. The
  build exports the new lockfile to `requirements.txt`.
- **New static/template/alembic content** — already covered by directory copy.
- **New runtime data file outside those directories** — add it to
  `RUNTIME_DIRS`/`RUNTIME_FILES` in `build_protected_release.py`.
- **Python version upgrade (e.g. 3.12)** — rebuild with that interpreter, update
  `REQUIRES-PYTHON` in `_rewrite_pyproject`, `.python-version`, and the three
  PowerShell guards; a new ABI requires a new `.pyd`. Do not mix versions.
- **New environment-specific path** — put it in `config.yaml`; do not hardcode
  it in compiled code.

## 8. Verification details

### Exact character/contract check

`scripts/verify_protected_release.py` launches one subprocess with the source
`PYTHONPATH` and one with the release `PYTHONPATH`, then compares:

- SHA-256 of `_SYSTEM_PROMPT` and `_PAGE_PROMPT`
- prompt length (including control characters/Unicode)
- SHA-256 of `_VLMPageExtraction.model_json_schema()` (sorted, UTF-8)
- the complete `REASON_TEXT` mapping
- loader type and `inspect.getsource` availability
- HTTP `/health`, `/dashboard`, `/openapi.json`
- template directory resolution

Observed on 2026-11-02:

```text
[ok] layout: only the compiled app package is shipped
[ok] prompts, Pydantic schema and reason text are identical to source
[ok] compiled loader: nuitka_module_loader; getsource: unavailable
[ok] smoke: /health 200, /dashboard 200 (13673 bytes), /openapi.json 200 (31 routes)
[ok] templates resolve from the release, not the source checkout
[PASS] protected release is complete and behaves like the source app
```

This is stronger than "it imports": it proves the exact text the VLM receives,
the schema instructor sends, and the human-readable reason strings did not
change during compilation.

### Full suite

The compiled run must be done in a separate CPython 3.11 environment with
`PYTHONPATH` pointing at the release `src/`, not the repository `src/`. Verify
`app.__loader__` is `nuitka_module_loader`; if it is `SourceFileLoader`, the
test is exercising the source tree, not the release.

Expected extra skips under compilation:

- `test_prompts_are_clean_utf8` (reads `src/app/services/extraction.py` text)
- `test_b_09_every_setting_is_visible_to_it_and_read_somewhere` (reads source)
- `test_product_doc_lists_every_reason_code_the_reconciler_emits` (reads source)
- `test_no_combined_references_in_engine` (scans source files)
- `test_non_windows_ignores_ctime` (fakes `os.name=posix`; the compiled build is Windows-only)

They are source-tree guards, not runtime behaviour. The source suite still runs
them.

## 9. Prefect registration check (optional, real API)

```powershell
$env:PREFECT_HOME = "$env:TEMP\aam_prefect_probe"
.\.venv311\Scripts\python.exe -m prefect server start --host 127.0.0.1 --port 4201
# wait for /api/health
$env:PREFECT_API_URL = "http://127.0.0.1:4201/api"
.\.venv311\Scripts\python.exe -m prefect work-pool create aam-merger-process-pool --type process --overwrite
cd dist\aam-merger-protected-0.1.0
$env:PYTHONPATH = (Resolve-Path src).Path
..\..\.venv311\Scripts\python.exe scripts\deploy_prefect.py
..\..\.venv311\Scripts\python.exe -m prefect deployment inspect "sync_flow/aam-midnight-sync"
```

The deployment must show `'entrypoint': 'app.flows.sync.sync_flow'` and
`PYTHONPATH` in `job_variables`. Stop the server and remove the probe home
afterwards.

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `ModuleNotFoundError: No module named 'app'` | `PYTHONPATH` does not include `<root>\src` |
| `ImportError: DLL load failed while importing app` | venv is not CPython 3.11 x64; recreate with 3.11 |
| `TemplateNotFound` / dashboard 500 | pyd moved out of `src/`, or `templates/` not beside it |
| Everything imports but `/dashboard` is blank | static/templates missing from the release; reassemble |
| Prefect says entrypoint not found | deployment registered with FILE_PATH or worker lacks PYTHONPATH; re-run `deploy_prefect.py` |
| Schema hash test fails | docstrings were stripped/changed; rebuild without `--python-flag=no_docstrings` |
| `uv sync` chooses 3.12/3.13 | `.python-version` or `requires-python` missing from release |
| Source edits do not appear | stale `.pyd`; rerun the build, not just the assembler |
| AV deletes the pyd | sign it and add a client AV exclusion after submission |

## 11. Evidence recorded for this branch

| Check | Result |
|---|---|
| pyd build | `app.cp311-win_amd64.pyd`, 4,831,744 bytes |
| Release zip | `dist/aam-merger-protected-0.1.0.zip`, 93 entries, 0 app `.py` in `src/` |
| Build ID | generated per assembly in `BUILD_INFO.json` (a rebuild creates a new ID) |
| pyd SHA-256 | `96cd597ecafa6b3c9eb67827c81702d6dd28f426ca57d37dc265e6f01d40c5c3` |
| Source vs compiled strings/schema | identical (verification script) |
| Full compiled suite | 6,994 tests, 6,702 passed, 292 skipped, 0 failed (688 s) |
| Alembic | migrated to `e8f9a0b1c2d3` using compiled models |
| Uvicorn smoke | `/health` 200, `/dashboard` 200 (13,673 bytes) |
| Prefect | `aam-midnight-sync` registered; entrypoint `app.flows.sync.sync_flow` |
| uv and pip install paths | both selected/used CPython 3.11 and imported the pyd |

## 12. Related files

- [`SOURCE_PROTECTION.md`](./SOURCE_PROTECTION.md) — threat model, option comparison, limits
- [`DEPLOY.md`](../DEPLOY.md) — installation and service operations
- [`DELIVERY.md`](./DELIVERY.md) — client handover checklist
- `scripts/build_protected_release.py` — compiler + release assembler
- `scripts/verify_protected_release.py` — equivalence and smoke verifier
- `tests/test_protected_release.py` — assembly contract tests
- `tests/test_prefect_schedule.py` — module entrypoint / PYTHONPATH regression
