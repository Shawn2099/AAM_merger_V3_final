"""Release-gate UI harness.

This package is the pre-release gate for the CA redesign. It sits on top of the
existing ``tests/ui`` harness (throwaway DB, canned VLM, no network) and adds:

* an evidence bundle for every test (screenshot, console, network, DOM, trace);
* axe accessibility checks;
* visual baselines (``--update-snapshots`` on the first run);
* tests for the redesign's own contract (names, tabs, previews, zero states).

Run from the repository root::

    $env:RUN_E2E = "1"
    uv run pytest tests/ui/release -o addopts= -p no:cacheprovider
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import BrowserContext, Page

REPO = Path(__file__).resolve().parents[3]
ARTIFACTS = Path(__file__).parent / "artifacts"


def _ui_test_env_module():
    spec = importlib.util.spec_from_file_location(
        "ui_test_env_release", REPO / "scripts" / "ui_test_env.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _start_release_env(tmp_path_factory, name: str):
    root = tmp_path_factory.mktemp(name)
    env = {**os.environ, "OPENROUTER_API_KEY": ""}
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "ui_test_env.py"), "--root", str(root)],
        cwd=str(REPO),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    module = _ui_test_env_module()
    proc, base = module.serve(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return SimpleNamespace(
        base=base, root=root, manifest=manifest, db_path=root / "aam_merger.db", proc=proc
    )


@pytest.fixture(scope="session")
def ui_env(tmp_path_factory):
    """Release tests get their own database.

    The existing suite mutates its shared scenarios (archive, force merge,
    reclassify). Running the release gate after it in the same environment made
    these checks order-dependent, so this overrides the parent ``ui_env`` and
    builds a second throwaway tree for this directory only.
    """
    env = _start_release_env(tmp_path_factory, "ui_test_release_env")
    try:
        yield env
    finally:
        proc = env.proc
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
        if proc.stdout is not None:
            proc.stdout.close()


@pytest.fixture(scope="session")
def harness_paths(ui_env) -> dict[str, Path]:
    """Configured folders for the release environment (quarantine, archive, stored...)."""
    data = yaml.safe_load((ui_env.root / "config.yaml").read_text(encoding="utf-8"))
    return {key: Path(value) for key, value in data["paths"].items()}


def _slug(nodeid: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid).strip("_")
    return slug[-140:] or "test"


@pytest.fixture(scope="session", autouse=True)
def clean_release_artifacts():
    """Start each release run with an empty evidence folder."""
    shutil.rmtree(ARTIFACTS, ignore_errors=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture()
def artifacts(request) -> Path:
    directory = ARTIFACTS / _slug(request.node.nodeid)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@dataclass
class Evidence:
    """Everything a reviewer needs to reproduce a finding."""

    page: Page
    context: BrowserContext
    directory: Path
    console: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    bad_responses: list[dict] = field(default_factory=list)
    _tracing: bool = field(default=False, repr=False)

    def assert_clean(self, *, allow: tuple[str, ...] = ()) -> None:
        """Fail on uncaught JS errors, console errors, failed or 4xx/5xx calls.

        ``allow`` is a tuple of URL/text fragments that are expected (for
        example the known missing ``/favicon.ico``).
        """

        def permitted(value: str) -> bool:
            return any(fragment in value for fragment in allow)

        errors = [e for e in self.errors if not permitted(e)]
        console = [
            f"{c['text']} {(c.get('location') or {}).get('url', '')}"
            for c in self.console
            if not permitted(f"{c['text']} {(c.get('location') or {}).get('url', '')}")
        ]
        failed = [
            text
            for f in self.failed
            if not permitted(text := f"{f['url']} {f['failure']}")
        ]
        responses = [
            f"{r['status']} {r['url']}"
            for r in self.bad_responses
            if not permitted(r["url"])
        ]
        assert not errors, f"uncaught JavaScript errors: {errors}"
        assert not console, f"console errors: {console}"
        assert not failed, "failed requests:\n" + "\n".join(failed)
        assert not responses, "4xx/5xx responses:\n" + "\n".join(responses)

    def close(self) -> None:
        """Persist the bundle. Never raises: a broken bundle must not hide the test verdict."""
        page = self.page
        try:
            if not page.is_closed():
                page.screenshot(path=self.directory / "final.png", full_page=True)
                (self.directory / "page.html").write_text(page.content(), encoding="utf-8")
        except Exception:  # pragma: no cover - best effort
            pass
        (self.directory / "console.json").write_text(
            json.dumps(self.console, indent=2), encoding="utf-8"
        )
        (self.directory / "network.json").write_text(
            json.dumps(
                {"failed_requests": self.failed, "bad_responses": self.bad_responses},
                indent=2,
            ),
            encoding="utf-8",
        )
        try:
            if self._tracing:
                self.context.tracing.stop(path=self.directory / "trace.zip")
        except Exception:  # pragma: no cover - best effort
            pass


@pytest.fixture()
def evidence(page: Page, context: BrowserContext, artifacts: Path):
    ev = Evidence(page=page, context=context, directory=artifacts)
    page.on(
        "console",
        lambda m: m.type == "error"
        and ev.console.append({"text": m.text, "location": m.location}),
    )
    page.on("pageerror", lambda exc: ev.errors.append(str(exc)))
    page.on(
        "requestfailed",
        lambda r: ev.failed.append({"url": r.url, "failure": str(r.failure)}),
    )
    page.on(
        "response",
        lambda r: r.status >= 400
        and ev.bad_responses.append({"status": r.status, "url": r.url}),
    )
    try:
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        ev._tracing = True
    except Exception:  # pragma: no cover - tracing is optional
        ev._tracing = False
    yield ev
    ev.close()


@pytest.fixture()
def axe() -> Axe:
    return Axe()


# The one expected 404 in the app: no favicon is shipped.
FAVICON = ("favicon.ico",)
