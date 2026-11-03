r"""Test bootstrap — the suite must never write into a real data folder.

Two problems this file removes, both of which only show up when nobody is
watching:

1. **The suite wrote into the repository's data folder.** The paths in
   `config.example.yaml` are relative (`./data/output`), so a test that
   loads it without overriding them writes merged packets — and stored
   documents — into the folder the product uses. On a developer machine
   that is clutter; on the production host it is the live output folder.
   Every configuration the suite loads is now redirected into one
   temporary directory, so a test run leaves nothing behind.

2. **The suite could not start on a fresh clone.** The app refuses to run
   without a `config.yaml` (SPEC 13.1) and `load_config()` reads the
   working directory, so a clone without one failed 54 tests with
   "Config not found". A developer's own `config.yaml` still wins; if
   there is none, the session writes a temporary one.

The redirect is applied by replacing `app.core.config.load_config` at
import time, before pytest imports the test modules — so the test modules
that do `from app.core.config import load_config` pick up the redirect
rather than the original.

**Only placeholder paths are redirected.** A path that points into the
repository's own data folder is a placeholder from `config.example.yaml`
and is moved into the session directory. A path that points anywhere else —
a test's own `tmp_path`, a deployment's `C:\AAM` — is left exactly as it
is, so an explicitly provided configuration keeps the paths its owner
chose.

A test that needs a specific path still sets it: `cfg.paths.output_folder
= tmp_path / "output"` overrides the redirect, exactly as before.
"""

from __future__ import annotations

import atexit
import contextlib
import gc
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import dotenv
import pytest
import yaml

from app.core import config as config_module

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: One temporary directory for the whole session. Every path the suite
#: touches lives under it, and it is removed when the session ends.
_SESSION_DIR = Path(tempfile.mkdtemp(prefix="aam-merger-tests-"))

#: The suite must never pick up the developer's own secrets. A real
#: `.env` at the repository root holds a live API key, and the config
#: loader reads it — so a test that asserts "no key -> fail closed" would
#: find the real key and pass straight through the guard it is testing.
#: Tests that need a key set one themselves.
#:
#: The guard blocks the repository's own `.env` and nothing else: a `.env`
#: a test writes in its tmp_path is still loaded, so the loader can be
#: tested for what it does.
_real_load_dotenv = dotenv.load_dotenv


def _guarded_load_dotenv(dotenv_path=None, *args, **kwargs):
    """Load `.env` files the suite wrote; never the repository's own."""
    candidate = Path(dotenv_path) if dotenv_path else Path.cwd() / ".env"
    try:
        resolved = candidate.resolve()
    except OSError:  # pragma: no cover - an unresolvable path is not ours
        return False
    if resolved == _REPO_ROOT or _REPO_ROOT in resolved.parents:
        return False
    return _real_load_dotenv(dotenv_path, *args, **kwargs)


dotenv.load_dotenv = _guarded_load_dotenv

#: The loader as it was before this file replaced it.
_original_load_config = config_module.load_config


def _session_paths() -> dict[str, str]:
    return {
        "input_folder": str(_SESSION_DIR / "input"),
        "output_folder": str(_SESSION_DIR / "output"),
        "quarantine_folder": str(_SESSION_DIR / "quarantine"),
        "stored_documents_folder": str(_SESSION_DIR / "stored"),
        "combined_folder": str(_SESSION_DIR / "combined"),
        "archive_folder": str(_SESSION_DIR / "development" / "archived_sets"),
        "log_folder": str(_SESSION_DIR / "logs"),
        "database_path": str(_SESSION_DIR / "aam_merger.db"),
    }


def _is_placeholder(path) -> bool:
    r"""True when a path points into the repository's own data folder.

    Those are the relative placeholders of `config.example.yaml`
    (`./data/output` and friends). They are unsafe: the suite would write
    into the folder the product uses. A path anywhere else — a test's
    `tmp_path`, a deployment's `C:\AAM` — belongs to whoever set it and is
    left alone.
    """
    try:
        resolved = Path(path).resolve()
    except Exception:
        return False
    return resolved == _REPO_ROOT or _REPO_ROOT in resolved.parents


def _redirect_paths(cfg):
    """Move placeholder paths into the session directory."""
    for name, value in _session_paths().items():
        current = getattr(cfg.paths, name, None)
        if current is not None and _is_placeholder(current):
            setattr(cfg.paths, name, value)

    # The manual merger's staging folder is not under `paths:`; redirect it too
    # so a test run can never sweep or write into the repository's data folder.
    temp = getattr(cfg.manual_merge, "temp_folder", None)
    if temp is not None and _is_placeholder(temp):
        cfg.manual_merge.temp_folder = _SESSION_DIR / "manual_merge_tmp"
    return cfg


def _load_config(path=None):
    """The real loader, with every path redirected into the session dir.

    The no-argument load is the one the app performs at import/lifespan. It must
    never take the developer's ``config.yaml`` (and must never start the real
    poller). Tests that pass an explicit config keep that config's values.
    """
    cfg = _redirect_paths(_original_load_config(path))
    if path is None:
        cfg.schedule.poll_enabled = False
        # A developer/production config may name real LAN hosts. The test client
        # speaks to ``testserver`` and must not be refused by TrustedHostMiddleware.
        cfg.server.allowed_hosts = ["*"]
    # The suite's thousands of existing route tests predate authentication and
    # exercise business logic, not login. Auth has its own dedicated tests that
    # build the app with `auth_enabled=True`. Never let a test run inherit a live
    # login requirement from config.example.yaml.
    cfg.auth.enabled = False
    return cfg


config_module.load_config = _load_config


@pytest.fixture(scope="session", autouse=True)
def _no_minimum_file_age():
    """Tests write a file and ingest it at once; the 30 s copy-safety age would hold every one.

    Session scope so module-scoped fixtures that run a sync are covered too.
    tests/test_s2_copy_safety.py monkeypatches the real value back to exercise the rule.
    """
    from app.services import ingestion

    saved = ingestion.MIN_FILE_AGE_SECONDS
    ingestion.MIN_FILE_AGE_SECONDS = 0
    yield
    ingestion.MIN_FILE_AGE_SECONDS = saved


@pytest.fixture(autouse=True)
def _no_minimum_file_age_per_test(monkeypatch):
    """Re-apply the zero age on the module object that is live NOW.

    A test that reloads app modules leaves the session fixture holding a stale module object,
    which made `test_is_file_stable_*` fail once in a full run. monkeypatch undoes this after
    each test; the S2 tests patch the real value on top of it.
    """
    import importlib

    ingestion = importlib.import_module("app.services.ingestion")
    monkeypatch.setattr(ingestion, "MIN_FILE_AGE_SECONDS", 0)


def _ensure_a_config_file() -> None:
    """Install a session config for every no-argument ``load_config()`` call.

    The suite must not depend on a developer's ``config.yaml`` (it may name
    production paths, a real model, or a non-wildcard ``allowed_hosts`` list).
    This session config is built from the committed example, with every path
    under the session directory and the background poller off. Tests that need
    their own config set ``AAM_CONFIG_PATH`` (or pass a path explicitly).
    """
    data = yaml.safe_load((_REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    data["paths"] = _session_paths()
    data.setdefault("schedule", {})["poll_enabled"] = False
    data.setdefault("server", {})["allowed_hosts"] = ["*"]
    data.setdefault("extraction", {})["split_combined"] = False
    manual = data.setdefault("manual_merge", {})
    manual["temp_folder"] = str(_SESSION_DIR / "manual_merge_tmp")
    target = _SESSION_DIR / "config.yaml"
    target.write_text(yaml.safe_dump(data), encoding="utf-8")
    os.environ["AAM_CONFIG_PATH"] = str(target)


def pytest_sessionfinish(session, exitstatus) -> None:
    """Stop the temporary Prefect API server a test run started (I-9).

    Prefect 3 starts a subprocess uvicorn server on demand and registers
    ``SubprocessASGIServer.stop`` with ``atexit``. On Windows with a
    uv-created virtualenv, ``sys.executable`` is a trampoline: ``terminate()``
    kills the trampoline and leaves the real interpreter (the uvicorn child)
    running, so every test run leaked a
    ``python -m uvicorn ... prefect.server.api.server:create_app`` process.

    Kill the whole process tree here, while the trampoline is still alive,
    then let Prefect's own ``stop()`` reset the singleton. Only servers that
    this session actually started are touched.
    """
    if os.name == "nt":
        module = sys.modules.get("prefect.server.api.server")
        server_cls = getattr(module, "SubprocessASGIServer", None) if module else None
        if server_cls is not None:
            seen: set[int] = set()
            for server in list(getattr(server_cls, "_instances", {}).values()):
                process = getattr(server, "server_process", None)
                pid = getattr(process, "pid", None)
                if pid is not None and pid not in seen:
                    seen.add(pid)
                    with contextlib.suppress(Exception):
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True,
                            timeout=15,
                        )
                # The process is already dead. Neutralise the server object so
                # Prefect's own atexit ``stop()`` cannot wait on the dead API
                # (observed at ~2 minutes on Windows). A second ``stop()`` after
                # this is a no-op.
                with contextlib.suppress(Exception):
                    # Prefect registered ``server.stop`` with atexit; if it is
                    # left registered, the interpreter blocks in it after this
                    # hook returns, waiting on the API of the process we killed.
                    atexit.unregister(server.stop)
                    server.server_process = None
                    server.running = False
                    instance_key = getattr(server, "_instance_key", server.port)
                    server_cls._instances.pop(instance_key, None)

    # The session directory holds the test DB, stored PDFs and config. Dispose
    # the cached SQLAlchemy engines first: on Windows an open SQLite handle makes
    # rmtree fail, and ignore_errors would silently leave the directory behind.
    with contextlib.suppress(Exception):
        from app.core import database as database_module

        for engine in list(database_module._engine_cache.values()):
            engine.dispose()
        database_module._engine_cache.clear()
    gc.collect()
    # Windows releases a SQLite handle only after the Session/engine is gone;
    # retry briefly instead of silently leaving the whole session directory.
    for _ in range(10):
        shutil.rmtree(_SESSION_DIR, ignore_errors=True)
        if not _SESSION_DIR.exists():
            return
        time.sleep(0.2)
    # A daemon/Prefect child can still hold a handle when this hook returns.
    # Delete after this interpreter exits, when Windows has released it.
    if os.name == "nt":
        with contextlib.suppress(Exception):
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import shutil, sys, time; time.sleep(2); "
                    "shutil.rmtree(sys.argv[1], ignore_errors=True)",
                    str(_SESSION_DIR),
                ],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )


_ensure_a_config_file()
