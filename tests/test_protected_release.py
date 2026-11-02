"""Contract tests for the source-protected release and its Prefect entrypoint.

The real compile is intentionally not part of the unit suite: it needs Nuitka,
a C compiler, and several minutes. ``scripts/build_protected_release.py`` is
covered here at the assembly boundary; the compiled artifact is exercised by
the release verification run described in docs/SOURCE_PROTECTION.md.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

from prefect.types.entrypoint import EntrypointType

ROOT = Path(__file__).resolve().parents[1]


def _load_script(module_name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    pyd = root / "src" / "app.cp311-win_amd64.pyd"
    pyd.write_bytes(b"MZ-fake-compiled-package")
    for directory in ("templates", "static", "alembic", "scripts"):
        (root / directory).mkdir()
    (root / "templates" / "dashboard.html").write_text("<html>dashboard</html>", encoding="utf-8")
    (root / "alembic" / "env.py").write_text("# schema history, not app source\n", encoding="utf-8")
    (root / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (root / "config.example.yaml").write_text("server:\n  port: 8000\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "9.9.9"\n'
        'requires-python = ">=3.11"\n[tool.uv]\nmanaged = true\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("", encoding="utf-8")
    return root, pyd


def test_protected_release_ships_only_the_compiled_app(tmp_path):
    builder = _load_script("build_protected_release", "scripts/build_protected_release.py")
    root, pyd = _fake_project(tmp_path)
    output = tmp_path / "dist" / "release"

    builder.assemble(pyd, output, root=root, write_requirements=False)

    assert (output / "src" / pyd.name).is_file()
    assert not (output / "src" / "app").exists()
    assert list((output / "src").rglob("*.py")) == []
    assert builder.verify_release_layout(output) == []


def test_protected_release_records_a_verifiable_build_id(tmp_path):
    builder = _load_script("build_protected_release", "scripts/build_protected_release.py")
    root, pyd = _fake_project(tmp_path)
    output = tmp_path / "dist" / "release"

    builder.assemble(pyd, output, root=root, write_requirements=False)

    info = json.loads((output / "BUILD_INFO.json").read_text(encoding="utf-8"))
    assert info["version"] == "9.9.9"
    assert info["compiled_module"] == pyd.name
    assert info["compiled_module_sha256"] == hashlib.sha256(pyd.read_bytes()).hexdigest()
    assert len(info["build_id"]) == 12


def test_protected_release_never_installs_the_missing_source_package(tmp_path):
    builder = _load_script("build_protected_release", "scripts/build_protected_release.py")
    root, pyd = _fake_project(tmp_path)
    output = tmp_path / "dist" / "release"

    builder.assemble(pyd, output, root=root, write_requirements=False)

    pyproject = (output / "pyproject.toml").read_text(encoding="utf-8")
    assert "package = false" in pyproject
    assert 'requires-python = ">=3.11,<3.12"' in pyproject
    assert (output / ".python-version").read_text(encoding="utf-8").strip() == "3.11"
    assert (output / "PROPRIETARY_NOTICE.txt").is_file()


def test_layout_check_flags_a_src_app_directory(tmp_path):
    builder = _load_script("build_protected_release", "scripts/build_protected_release.py")
    root, pyd = _fake_project(tmp_path)
    output = tmp_path / "dist" / "release"
    builder.assemble(pyd, output, root=root, write_requirements=False)

    (output / "src" / "app").mkdir()

    assert any("shadow" in problem for problem in builder.verify_release_layout(output))


def test_prefect_deployment_uses_a_module_entrypoint(monkeypatch):
    deploy_prefect = _load_script("deploy_prefect_under_test", "scripts/deploy_prefect.py")
    captured: dict = {}

    class FakeDeployment:
        def apply(self):
            return "deployment-id"

    class FakeFlow:
        def to_deployment(self, **kwargs):
            captured.update(kwargs)
            return FakeDeployment()

    monkeypatch.setattr(deploy_prefect, "sync_flow", FakeFlow())
    monkeypatch.setattr(
        deploy_prefect,
        "load_config",
        lambda _path: SimpleNamespace(
            prefect=SimpleNamespace(
                work_pool_name="aam-merger-process-pool", schedule_timezone=None
            ),
            schedule=SimpleNamespace(nightly_enabled=True),
        ),
    )
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")

    previous_cwd = os.getcwd()
    try:
        deploy_prefect.deploy()
    finally:
        os.chdir(previous_cwd)

    assert captured["entrypoint_type"] is EntrypointType.MODULE_PATH
    assert captured["job_variables"]["working_dir"] == str(ROOT)
    assert captured["job_variables"]["env"]["PYTHONPATH"] == str(ROOT / "src")
