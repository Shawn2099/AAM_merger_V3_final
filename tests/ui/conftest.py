"""Browser UI test harness (opt-in: RUN_E2E=1).

    RUN_E2E=1 uv run python -m pytest tests/ui -o addopts= -p no:cacheprovider

The session builds a throwaway environment with `scripts/ui_test_env.py` (real pipeline
code, canned VLM reply, no network) and serves it on a free port. Read `manifest`
instead of guessing what is on screen. A test that CHANGES data must use a scenario
marked `mutable` in the manifest, and only that one, so tests stay independent.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(config, items):
    skip = pytest.mark.skip(reason="set RUN_E2E=1 (Chromium installed) to run the UI suite")
    for item in items:
        if Path(str(item.fspath)).parent.name == "ui" or "tests/ui" in str(item.fspath).replace(
            "\\", "/"
        ):
            item.add_marker(pytest.mark.e2e)
            if os.getenv("RUN_E2E") != "1":
                item.add_marker(skip)


def _load_env_module():
    spec = importlib.util.spec_from_file_location(
        "ui_test_env", REPO / "scripts" / "ui_test_env.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _start_env(tmp_path_factory, name: str):
    """Build and serve one throwaway harness environment."""
    root = tmp_path_factory.mktemp(name)
    # Blank, not popped: the config loader reads `.env` files itself and
    # `override=False` honours a variable that is present. An empty string is
    # present, so the developer's real key cannot come back (H-01).
    env = {**os.environ, "OPENROUTER_API_KEY": ""}
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "ui_test_env.py"), "--root", str(root)],
        cwd=str(REPO),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    module = _load_env_module()
    proc, base = module.serve(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return SimpleNamespace(
        base=base, root=root, manifest=manifest, db_path=root / "aam_merger.db", proc=proc
    )


@pytest.fixture(scope="session")
def ui_env(tmp_path_factory):
    env = _start_env(tmp_path_factory, "ui_test_env")
    try:
        yield env
    finally:
        _stop(env.proc)


@pytest.fixture(scope="session")
def empty_ui_env(tmp_path_factory):
    """A second throwaway environment with every business table emptied.

    The empty-state copy on the dashboard can only be reached with no sets.
    The seeded session environment always has at least one of every status, so
    this fixture builds the same real environment and then deletes its rows
    (orders, documents, line items, audit) before returning the running server.
    Tests use absolute URLs because the shared browser context's base_url still
    points at the seeded environment.
    """
    env = _start_env(tmp_path_factory, "ui_test_empty_env")
    con = sqlite3.connect(env.db_path)
    try:
        con.execute("PRAGMA foreign_keys = OFF")
        tables = [
            row[0]
            for row in con.execute(
                "select name from sqlite_master where type='table' "
                "and name not like 'sqlite_%' and name != 'alembic_version'"
            )
        ]
        with con:
            for table in tables:
                con.execute(f'delete from "{table}"')
    finally:
        con.close()
    return env


@pytest.fixture(scope="session")
def empty_base_url(empty_ui_env):
    """Absolute base URL for the empty environment (not the pytest-playwright base_url)."""
    return empty_ui_env.base


@pytest.fixture(scope="session")
def scan_env(tmp_path_factory):
    """A second, throwaway environment for the one test that presses Scan (D-12).

    `POST /sync` runs the real flow in the web process: it re-reconciles every
    open set with the poller's settle window OFF (`SETTLE_MINUTES` is only set
    by the poller), so it merges a set that is merely waiting for its settle
    window. A suite that also checks that state cannot share the database with
    the click, and every other seeded state must stay as the build left it.
    """
    env = _start_env(tmp_path_factory, "ui_test_scan_env")
    try:
        yield env
    finally:
        _stop(env.proc)


def _stop(proc) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()
    if proc.stdout is not None:
        proc.stdout.close()


@pytest.fixture(scope="session")
def base_url(ui_env):
    """pytest-playwright resolves page.goto('/dashboard') against this."""
    return ui_env.base


@pytest.fixture(scope="session")
def manifest(ui_env):
    return ui_env.manifest


@pytest.fixture()
def db(ui_env):
    """Read-only DB connection for checking that what the page shows is what is stored."""
    con = sqlite3.connect(f"file:{ui_env.db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    yield con
    con.close()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, base_url):
    """The context every UI test gets.

    `base_url` is named explicitly, not inherited indirectly: pytest-playwright
    builds the session-scoped context args ONCE, and a browser test that runs
    earlier in a combined suite (test_manual_merger_e2e.py) can instantiate them
    while no UI `base_url` fixture is in play. Relying on the plugin's cached
    dict then left the context without a base URL, and every relative
    `page.goto("/dashboard")` in this directory failed with "Cannot navigate to
    invalid URL" only when the whole suite was run.
    """
    return {
        **browser_context_args,
        "base_url": base_url,
        "accept_downloads": True,
        "viewport": {"width": 1440, "height": 900},
    }


@pytest.fixture(autouse=True)
def fail_on_js_errors(page):
    """Any uncaught JavaScript error on a page is a test failure."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    yield
    assert not errors, f"JavaScript errors: {errors}"


def scenario(manifest: dict, name: str) -> dict:
    return manifest["scenarios"][name]
