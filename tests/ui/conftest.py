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


@pytest.fixture(scope="session")
def ui_env(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui_test_env")
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
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
    try:
        yield SimpleNamespace(
            base=base, root=root, manifest=manifest, db_path=root / "aam_merger.db"
        )
    finally:
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
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
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
