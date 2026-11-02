"""Verify that a protected release is complete and behaves like the source app.

Checks, in order:

1. Release layout: one compiled ``app*.pyd`` in ``src/``, no ``src/app/`` and
   no application ``.py`` files.
2. Compiled import: the module loader is Nuitka's and ``inspect.getsource``
   cannot recover the application source.
3. Character/contract equivalence: the VLM prompts, the Pydantic JSON schema
   sent to the model, and ``REASON_TEXT`` are byte-for-byte identical between
   the source checkout and the compiled package.
4. HTTP smoke: ``/health``, ``/dashboard`` and ``/openapi.json`` answer 200,
   and Jinja resolves templates from the release, not from the source tree.

Run from the repository root after building:

    .\\.venv\\Scripts\\python.exe scripts\\verify_protected_release.py

The source Python can be the 3.12 development venv; the compiled Python must
be the CPython 3.11 environment used for the release (``.venv311`` here).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = ROOT / "dist"
DEFAULT_SOURCE_PY = ROOT / ".venv" / "Scripts" / "python.exe"
DEFAULT_COMPILED_PY = ROOT / ".venv311" / "Scripts" / "python.exe"

_EQUIVALENCE_PROBE = r"""
import hashlib
import inspect
import json

import app
from app.services.extraction import _PAGE_PROMPT, _SYSTEM_PROMPT, _VLMPageExtraction
from app.services.reconciliation import REASON_TEXT


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


schema = json.dumps(
    _VLMPageExtraction.model_json_schema(), sort_keys=True, indent=1, ensure_ascii=False
)
try:
    inspect.getsource(__import__("app.services.reconciliation", fromlist=["_"]))
    getsource = "available"
except OSError:
    getsource = "unavailable"

print(json.dumps({
    "loader": type(app.__loader__).__name__,
    "getsource": getsource,
    "system_prompt_sha256": digest(_SYSTEM_PROMPT),
    "page_prompt_sha256": digest(_PAGE_PROMPT),
    "page_prompt_length": len(_PAGE_PROMPT),
    "schema_sha256": digest(schema),
    "schema": schema,
    "reason_text": REASON_TEXT,
}, sort_keys=True, ensure_ascii=False))
"""

_SMOKE_PROBE = r"""
import json

from fastapi.testclient import TestClient

import app
import app.api.templating as templating
from app.main import app as fastapi_app

client = TestClient(fastapi_app)
health = client.get("/health")
dashboard = client.get("/dashboard")
openapi = client.get("/openapi.json")
print(json.dumps({
    "loader": type(app.__loader__).__name__,
    "health": health.status_code,
    "dashboard": dashboard.status_code,
    "dashboard_bytes": len(dashboard.content),
    "openapi": openapi.status_code,
    "routes": len(openapi.json().get("paths", {})),
    "templates": str(templating.TEMPLATES_DIR),
    "templates_exist": templating.TEMPLATES_DIR.is_dir(),
}, sort_keys=True))
"""


class VerificationError(RuntimeError):
    """A release check failed; the message is operator-facing."""


def _latest_release() -> Path:
    candidates = sorted(
        (p for p in DEFAULT_DIST.glob("aam-merger-protected-*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise VerificationError("no dist/aam-merger-protected-* directory found; build one first")
    return candidates[0]


def _run_probe(python: Path, code: str, *, cwd: Path, pythonpath: Path) -> dict:
    if not python.is_file():
        raise VerificationError(f"Python interpreter not found: {python}")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(pythonpath)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONHOME", None)
    completed = subprocess.run(
        [str(python), "-c", code],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise VerificationError(
            f"probe failed with {python}:\n{completed.stdout}\n{completed.stderr}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise VerificationError(f"probe produced no output: {python}")
    return json.loads(lines[-1])


def _check_layout(release: Path) -> None:
    if not release.is_dir():
        raise VerificationError(f"release directory missing: {release}")
    pyds = list((release / "src").glob("app*.pyd"))
    if len(pyds) != 1:
        raise VerificationError(f"expected one app*.pyd in {release / 'src'}, found {len(pyds)}")
    if (release / "src" / "app").exists():
        raise VerificationError("release contains src/app/; it would shadow the compiled module")
    leaked = sorted((release / "src").rglob("*.py"))
    if leaked:
        raise VerificationError(f"application Python source in release: {leaked}")
    if not (release / ".python-version").is_file():
        raise VerificationError("release is missing .python-version")
    if (release / ".python-version").read_text(encoding="utf-8").strip() != "3.11":
        raise VerificationError("release .python-version must pin 3.11")
    pyproject = (release / "pyproject.toml").read_text(encoding="utf-8")
    if "package = false" not in pyproject:
        raise VerificationError("release pyproject.toml is missing [tool.uv] package = false")
    if 'requires-python = ">=3.11,<3.12"' not in pyproject:
        raise VerificationError("release pyproject.toml is not pinned to CPython 3.11")
    if not (release / "BUILD_INFO.json").is_file():
        raise VerificationError("release is missing BUILD_INFO.json")


def _check_compiled_probe(probe: dict) -> None:
    if not probe["loader"].lower().startswith("nuitka"):
        raise VerificationError(
            f"release imported a source module ({probe['loader']}), not the Nuitka package"
        )
    if probe["getsource"] != "unavailable":
        raise VerificationError("inspect.getsource still reads application source in the release")


def _check_equivalence(source: dict, compiled: dict) -> None:
    keys = (
        "system_prompt_sha256",
        "page_prompt_sha256",
        "page_prompt_length",
        "schema_sha256",
        "schema",
        "reason_text",
    )
    differences = [key for key in keys if source[key] != compiled[key]]
    if differences:
        raise VerificationError("compiled app differs from source in: " + ", ".join(differences))


def _check_smoke(smoke: dict, release: Path) -> None:
    if smoke["loader"].lower().startswith("nuitka") is False:
        raise VerificationError("smoke test did not import the compiled package")
    for key in ("health", "dashboard", "openapi"):
        if smoke[key] != 200:
            raise VerificationError(f"/{key} returned {smoke[key]}, expected 200")
    if smoke["dashboard_bytes"] < 1000:
        raise VerificationError("dashboard response is suspiciously small")
    if smoke["routes"] < 1:
        raise VerificationError("OpenAPI reports no routes")
    expected_templates = (release / "templates").resolve()
    if Path(smoke["templates"]).resolve() != expected_templates:
        raise VerificationError(
            f"templates resolved to {smoke['templates']}, expected {expected_templates}"
        )
    if not smoke["templates_exist"]:
        raise VerificationError("release templates directory does not exist")


def verify(release: Path, source_python: Path, compiled_python: Path) -> None:
    print(f"[check] release: {release}")
    _check_layout(release)
    print("[ok] layout: only the compiled app package is shipped")

    source_probe = _run_probe(source_python, _EQUIVALENCE_PROBE, cwd=ROOT, pythonpath=ROOT / "src")
    compiled_probe = _run_probe(
        compiled_python, _EQUIVALENCE_PROBE, cwd=ROOT, pythonpath=release / "src"
    )
    _check_compiled_probe(compiled_probe)
    _check_equivalence(source_probe, compiled_probe)
    print("[ok] prompts, Pydantic schema and reason text are identical to source")
    print(f"[ok] compiled loader: {compiled_probe['loader']}; getsource: unavailable")

    with tempfile.TemporaryDirectory(prefix="aam-release-verify-") as tmp:
        tmp_path = Path(tmp)
        config = tmp_path / "config.yaml"
        shutil.copy2(ROOT / "config.example.yaml", config)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(release / "src")
        env["PYTHONIOENCODING"] = "utf-8"
        env["AAM_CONFIG_PATH"] = str(config)
        env.pop("PYTHONHOME", None)
        completed = subprocess.run(
            [str(compiled_python), "-c", _SMOKE_PROBE],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            raise VerificationError(f"smoke probe failed:\n{completed.stdout}\n{completed.stderr}")
        smoke = json.loads([line for line in completed.stdout.splitlines() if line.strip()][-1])
        _check_smoke(smoke, release)
        print(
            "[ok] smoke: /health 200, /dashboard 200 "
            f"({smoke['dashboard_bytes']} bytes), /openapi.json 200 ({smoke['routes']} routes)"
        )
        print("[ok] templates resolve from the release, not the source checkout")

    print("[PASS] protected release is complete and behaves like the source app")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=None, help="release directory")
    parser.add_argument("--source-python", type=Path, default=DEFAULT_SOURCE_PY)
    parser.add_argument("--compiled-python", type=Path, default=DEFAULT_COMPILED_PY)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        verify(
            (args.release or _latest_release()).resolve(),
            args.source_python,
            args.compiled_python,
        )
    except VerificationError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
