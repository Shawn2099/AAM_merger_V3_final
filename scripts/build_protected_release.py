"""Build a source-protected AAM Merger release.

The application package (``src/app``) is compiled with Nuitka into a native
extension module and the Python sources are **not** copied into the release.
Deployment keeps the normal CPython 3.11 virtual environment: Uvicorn, Prefect
and NSSM still run the interpreter, they simply import ``app`` from the
compiled module.

Why a module/package rather than a standalone executable:
    * Prefect's process work pool starts Python subprocesses; a frozen
      executable would need to be re-executed as the Prefect engine.
    * The existing NSSM services and update procedure keep working unchanged.
    * Third-party packages stay as ordinary wheels, so licensing and security
      updates are not baked into the build.

Usage (on the build machine, with Python 3.11 and Nuitka installed):

    py -3.11 -m pip install "nuitka>=2.7" ordered-set zstandard
    py -3.11 scripts/build_protected_release.py --zip

The target machine needs Python 3.11 x64 and the normal dependencies; it does
not need Nuitka, a C compiler, or the source tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD_DIR = ROOT / "build" / "nuitka"
DEFAULT_DIST_DIR = ROOT / "dist"
APP_SOURCE = ROOT / "src" / "app"

#: Directories copied verbatim. None of them contain application Python logic:
#: templates/static are rendered assets, alembic/ is the schema history.
RUNTIME_DIRS = ("templates", "static", "alembic")

#: Documentation and configuration an operator needs at the site.
RUNTIME_FILES = (
    "alembic.ini",
    "config.example.yaml",
    ".env.example",
    "DEPLOY.md",
    "docs/DELIVERY.md",
    "docs/SOURCE_PROTECTION.md",
)

#: Convenience wrappers kept at the repository root.
ROOT_LAUNCHERS = (
    "deploy.bat",
    "update_app.bat",
    "restart_services.bat",
    "status.bat",
    "backup_now.bat",
    "Install.cmd",
)

#: Operational scripts needed on the target. Development/UI/soak scripts are
#: deliberately excluded: they are not part of the product and only add noise
#: (and more readable code) to the handover.
SCRIPT_FILES = (
    "setup_test_rig.ps1",
    "deploy_production.ps1",
    "install_nssm_services.ps1",
    "uninstall_nssm_services.ps1",
    "manage_services.ps1",
    "migrate_db.py",
    "deploy_prefect.py",
    "backup_db.ps1",
    "backup_db.py",
    "verify_backup.ps1",
    "_verify_backup.py",
    "development_snapshot.ps1",
    "verify_deployment.ps1",
    "reset_data.ps1",
    "reset_path_guard.ps1",
    "update_production.ps1",
    "requeue_line_mapping.py",
    "review_merged_sets.py",
)

_PYD_RE = re.compile(r"^app\.cp\d+-win_amd64\.pyd$|^app\.cpython-\d+-.*\.(pyd|so)$")


class BuildError(RuntimeError):
    """A release cannot be produced; the message is operator-facing."""


def _project_version(root: Path = ROOT) -> str:
    import tomllib

    with (root / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit(root: Path = ROOT) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _safe_remove(path: Path, *, root: Path = ROOT) -> None:
    """Remove a build output, refusing the project root or a drive root."""
    resolved = path.resolve()
    if resolved == root.resolve() or resolved == Path(resolved.anchor):
        raise BuildError(f"refusing to remove unexpected output path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _copy_file(src: Path, dst: Path) -> bool:
    if not src.is_file():
        print(f"  [skip] {src} (missing)")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _copy_tree(src: Path, dst: Path) -> bool:
    if not src.is_dir():
        print(f"  [skip] {src} (missing)")
        return False
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache"),
    )
    return True


def _find_compiled_package(build_dir: Path) -> Path:
    candidates = [p for p in build_dir.rglob("app*.pyd") if _PYD_RE.match(p.name)]
    if not candidates:
        candidates = [p for p in build_dir.rglob("app*.so") if _PYD_RE.match(p.name)]
    if len(candidates) != 1:
        found = ", ".join(str(p.relative_to(ROOT)) for p in candidates) or "none"
        raise BuildError(
            f"expected exactly one compiled app package in {build_dir}, found: {found}"
        )
    return candidates[0]


def compile_package(python_exe: str, build_dir: Path, jobs: int | None) -> Path:
    """Compile ``src/app`` into a native package and return the .pyd path."""
    probe = subprocess.run(
        [python_exe, "-c", "import nuitka,sys;print(sys.version_info[:2])"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise BuildError(
            f"Nuitka is not installed for {python_exe}. Install it first:\n"
            f'  "{python_exe}" -m pip install "nuitka>=2.7" ordered-set zstandard'
        )
    major, minor = (int(x) for x in probe.stdout.strip().strip("()").split(","))
    if (major, minor) != (3, 11):
        raise BuildError(
            f"the production runtime is CPython 3.11; this interpreter is "
            f"{major}.{minor}. A compiled package is ABI-specific. Build with py -3.11."
        )

    build_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        python_exe,
        "-m",
        "nuitka",
        "--mode=package",
        f"--output-dir={build_dir}",
        "--no-pyi-file",
        # Do NOT strip docstrings. Pydantic puts the model class docstring into
        # the JSON schema sent to the VLM; --python-flag=no_docstrings changed
        # that schema and failed test_extraction_coercion. Native compilation is
        # the protection here, not removing human-readable text.
        # LTO is the 2-core trap: minutes of linking for no runtime benefit.
        "--lto=no",
        f"--jobs={jobs or (os.cpu_count() or 2)}",
        "--mingw64",
        "--assume-yes-for-downloads",
        "--remove-output",
        str(APP_SOURCE),
    ]
    print("[build] " + " ".join(cmd))
    completed = subprocess.run(cmd, cwd=ROOT)
    if completed.returncode != 0:
        raise BuildError(f"Nuitka failed with exit code {completed.returncode}")
    return _find_compiled_package(build_dir)


def _rewrite_pyproject(root: Path, target: Path) -> None:
    """Ship project metadata without asking uv to install a missing package.

    ``uv sync`` installs the project itself by default. In a protected release
    ``src/app`` is a compiled module, not a package directory, so hatchling
    would fail. ``[tool.uv] package = false`` makes uv install only the
    dependencies. pip fallback uses the generated requirements.txt.
    """
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    if "[tool.uv]" not in text:
        raise BuildError("pyproject.toml has no [tool.uv] section to adapt")
    # The native module is ABI-specific. Pin the release to the interpreter it
    # was compiled for so `uv sync` cannot silently choose 3.12/3.13. The
    # source checkout keeps `>=3.11` because it runs on the 3.12 dev box.
    text = text.replace('requires-python = ">=3.11"', 'requires-python = ">=3.11,<3.12"', 1)
    if ">=3.11,<3.12" not in text:
        raise BuildError("could not pin requires-python to >=3.11,<3.12 in the release")
    replacement = (
        "[tool.uv]\nmanaged = true\n"
        "# Protected release: src/app is a compiled .pyd, not an installable package.\n"
        "package = false"
    )
    text = text.replace("[tool.uv]\nmanaged = true", replacement, 1)
    if "package = false" not in text:
        raise BuildError("could not set [tool.uv] package = false in the release pyproject")
    target.write_text(text, encoding="utf-8")


def _write_requirements(target: Path, root: Path = ROOT) -> None:
    """Export the locked runtime dependency set for the pip fallback path."""
    uv = shutil.which("uv")
    if not uv:
        print("  [warn] uv not on PATH; requirements.txt not generated")
        return
    cmd = [
        uv,
        "export",
        "--locked",
        "--no-dev",
        "--no-emit-project",
        "--format",
        "requirements.txt",
        "--output-file",
        str(target),
    ]
    completed = subprocess.run(cmd, cwd=root)
    if completed.returncode != 0:
        raise BuildError("uv export failed; the lockfile may be stale (run `uv lock`)")


def _write_notice(target: Path, version: str) -> None:
    year = datetime.now(UTC).year
    target.write_text(
        f"""AAM Merger V3 {version} — Proprietary and Confidential
Copyright (c) {year} AAM. All rights reserved.

This software and its compiled components are proprietary. They are licensed,
not sold. You may not copy, distribute, sublicense, reverse engineer,
decompile, disassemble, or attempt to derive the source code except to the
extent expressly permitted by the written licence agreement.

The software contains trade secrets and confidential information. Access is
limited to authorised users at the licensed site. Report suspected copying or
disclosure to the supplier immediately.

Third-party open-source components remain under their own licences; see the
installed Python packages for their licence texts.
""",
        encoding="utf-8",
    )


def assemble(
    pyd_path: Path,
    output: Path,
    *,
    version: str | None = None,
    write_requirements: bool = True,
    zip_output: bool = False,
    root: Path = ROOT,
) -> Path:
    """Assemble the runnable release tree around a compiled ``app`` package."""
    pyd_path = pyd_path.resolve()
    if not pyd_path.is_file() or not _PYD_RE.match(pyd_path.name):
        raise BuildError(f"not a compiled app package: {pyd_path}")
    version = version or _project_version(root)

    _safe_remove(output, root=root)
    output.mkdir(parents=True, exist_ok=True)
    print(f"[assemble] {output}")

    # The compiled package must be the only thing on src/. A directory named
    # app/ would shadow the extension module and ship the source again.
    (output / "src").mkdir()
    shutil.copy2(pyd_path, output / "src" / pyd_path.name)

    for name in RUNTIME_DIRS:
        _copy_tree(root / name, output / name)
    for name in RUNTIME_FILES:
        _copy_file(root / name, output / name)
    for name in ROOT_LAUNCHERS:
        _copy_file(root / name, output / name)
    (output / "scripts").mkdir(exist_ok=True)
    for name in SCRIPT_FILES:
        _copy_file(root / "scripts" / name, output / "scripts" / name)

    _rewrite_pyproject(root, output / "pyproject.toml")
    # uv reads this before it reads requires-python; both say 3.11 so a host
    # with several interpreters cannot create a 3.12 venv for a cp311 module.
    (output / ".python-version").write_text("3.11\n", encoding="utf-8")
    _copy_file(root / "uv.lock", output / "uv.lock")
    if write_requirements:
        _write_requirements(output / "requirements.txt", root=root)

    _write_notice(output / "PROPRIETARY_NOTICE.txt", version)

    info = {
        "product": "AAM Merger V3",
        "version": version,
        "build_id": uuid.uuid4().hex[:12],
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "build_python": ".".join(str(part) for part in sys.version_info[:3]),
        "build_python_executable": sys.executable,
        "git_commit": _git_commit(root),
        "compiled_module": pyd_path.name,
        "compiled_module_sha256": _sha256(pyd_path),
        "protection": "Nuitka package mode (native extension, no .py sources shipped)",
    }
    (output / "BUILD_INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    # Fail the build rather than hand over a tree that still contains the app source.
    app_py = sorted(p for p in (output / "src").rglob("*.py"))
    if app_py:
        raise BuildError(f"application Python source leaked into src/: {app_py}")
    if (output / "src" / "app").exists():
        raise BuildError("release contains src/app/; it would shadow the compiled package")

    if zip_output:
        archive = shutil.make_archive(
            str(output), "zip", root_dir=output.parent, base_dir=output.name
        )
        print(f"[zip] {archive}")
    print(f"[ok] {output}")
    return output


def verify_release_layout(output: Path) -> list[str]:
    """Return human-readable problems with an assembled release (empty = ok)."""
    problems: list[str] = []
    pyds = list((output / "src").glob("app*.pyd")) + list((output / "src").glob("app*.so"))
    if len(pyds) != 1:
        problems.append(f"expected one compiled app module in src/, found {len(pyds)}")
    if (output / "src" / "app").exists():
        problems.append("src/app/ directory is present and would shadow the compiled module")
    for required in (
        "templates",
        "static",
        "alembic",
        "scripts",
        "pyproject.toml",
        ".python-version",
    ):
        if not (output / required).exists():
            problems.append(f"missing {required}")
    return problems


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="CPython 3.11 used to build")
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="release directory (default: dist/aam-merger-protected-<version>)",
    )
    parser.add_argument("--jobs", type=int, default=None, help="parallel C compiler jobs")
    parser.add_argument("--pyd", type=Path, default=None, help="reuse an existing .pyd")
    parser.add_argument(
        "--skip-build", action="store_true", help="assemble the newest existing .pyd"
    )
    parser.add_argument("--zip", action="store_true", help="also create a .zip next to the folder")
    parser.add_argument(
        "--no-requirements", action="store_true", help="do not run uv export (offline builds)"
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    version = _project_version()
    output = args.output or (DEFAULT_DIST_DIR / f"aam-merger-protected-{version}")

    try:
        if args.pyd:
            pyd = args.pyd
        elif args.skip_build:
            pyd = _find_compiled_package(args.build_dir)
        else:
            pyd = compile_package(args.python, args.build_dir, args.jobs)
        assemble(
            pyd,
            output,
            version=version,
            write_requirements=not args.no_requirements,
            zip_output=args.zip,
        )
        problems = verify_release_layout(output)
        if problems:
            raise BuildError("release verification failed: " + "; ".join(problems))
    except BuildError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
