"""Audit 2026-10-23 — P2-7 (backup naming/integrity/retention, reset allow-list).

The backup script is imported the way the existing backup test imports it
(file path, not a package) so these tests run even though it is not part of
`app`.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = ROOT / "scripts" / "backup_db.py"
RESET_GUARD = ROOT / "scripts" / "reset_path_guard.ps1"
RESET_SCRIPT = ROOT / "scripts" / "reset_data.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


def _load_backup_module():
    spec = importlib.util.spec_from_file_location("backup_db_audit", BACKUP_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def project(tmp_path):
    """A fake project root with a config.yaml and a small WAL-mode database."""
    (tmp_path / "data").mkdir()
    db = tmp_path / "data" / "aam_merger.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("create table t (id integer primary key, v text)")
    con.execute("insert into t (v) values ('one')")
    con.commit()
    con.close()
    (tmp_path / "config.yaml").write_text(
        "paths:\n  database_path: ./data/aam_merger.db\n", encoding="utf-8"
    )
    return tmp_path


# ---------------------------------------------------------------------------
# backup_db.py
# ---------------------------------------------------------------------------


def test_two_backups_from_the_same_moment_do_not_collide(project):
    module = _load_backup_module()

    first = module.run_backup(retention_days=0, project_root=project)
    second = module.run_backup(retention_days=0, project_root=project)

    assert first != second
    assert first.exists() and second.exists()
    for path in (first, second):
        ok, detail = module._integrity_check(path)
        assert ok, detail


def test_the_counter_steps_around_an_existing_timestamp(project):
    module = _load_backup_module()
    backup_dir = project / "data" / "backup"
    backup_dir.mkdir()
    moment = datetime(2026, 10, 23, 1, 2, 3, 456789, tzinfo=UTC)

    first = module._next_backup_path(backup_dir, moment)
    first.write_bytes(b"placeholder")
    second = module._next_backup_path(backup_dir, moment)

    assert second != first
    assert second.name == f"{first.stem}_1.db"


def test_a_failed_integrity_check_leaves_no_accepted_backup(project, monkeypatch):
    module = _load_backup_module()
    monkeypatch.setattr(
        module, "_integrity_check", lambda path: (False, "database disk image is malformed")
    )

    with pytest.raises(RuntimeError, match="integrity_check"):
        module.run_backup(retention_days=0, project_root=project)

    backup_dir = project / "data" / "backup"
    assert list(backup_dir.glob("*.db")) == []
    assert list(backup_dir.glob("*.partial")) == []


def test_retention_prunes_only_verified_old_backups(project):
    module = _load_backup_module()
    backup_dir = project / "data" / "backup"
    backup_dir.mkdir()
    old = time.time() - 40 * 86400

    verified = backup_dir / "aam_merger_20200101_000000_000000.db"
    shutil.copy(project / "data" / "aam_merger.db", verified)
    corrupt = backup_dir / "aam_merger_20200102_000000_000000.db"
    corrupt.write_bytes(b"this is not a sqlite database")
    os.utime(verified, (old, old))
    os.utime(corrupt, (old, old))

    fresh = module.run_backup(retention_days=30, project_root=project)

    assert not verified.exists(), "a verified backup past retention must be pruned"
    assert corrupt.exists(), "a corrupt backup must be kept for a human to inspect"
    assert fresh.exists(), "the backup just written must never be pruned"


# ---------------------------------------------------------------------------
# reset_data.ps1 path allow-list
# ---------------------------------------------------------------------------


def _powershell(script: str) -> str:
    if POWERSHELL is None:
        pytest.skip("PowerShell is required for the reset guard tests")
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("relative", "allowed"),
    [
        ("data/input", True),
        ("data/input/nested/deep", True),
        ("data/backup/old", False),
        ("data/backup", False),
        ("data", False),
        ("logs", False),
        ("..", False),
    ],
)
def test_reset_allow_list_only_accepts_paths_under_the_data_root(tmp_path, relative, allowed):
    data_root = tmp_path / "data"
    backup_dir = data_root / "backup"
    candidate = tmp_path / relative

    output = _powershell(
        f". '{RESET_GUARD}'; "
        f"Test-ResetPathAllowed -Path '{candidate}' "
        f"-DataRoot '{data_root}' -BackupDir '{backup_dir}'"
    )

    assert output.lower() == ("true" if allowed else "false")


def test_reset_script_parses_and_uses_the_allow_list():
    check = (
        "$parseErrors = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{RESET_SCRIPT}', "
        "[ref]$null, [ref]$parseErrors); "
        "if ($parseErrors) { $parseErrors | ForEach-Object { Write-Output $_.Message }; exit 1 }"
    )
    assert _powershell(check) == ""

    source = RESET_SCRIPT.read_text(encoding="utf-8")
    assert "reset_path_guard.ps1" in source
    assert "Test-ResetPathAllowed" in source


def _junction(link: Path, target: Path) -> None:
    """Directory junctions need no administrator rights (unlike symlinks)."""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_reset_script_refuses_a_configured_path_outside_the_data_root(tmp_path):
    """The real script, against a real config: nothing outside data/ is touched."""
    if POWERSHELL is None:
        pytest.skip("PowerShell is required for the reset guard tests")

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("do not touch", encoding="utf-8")
    inside_input = outside / "input"
    inside_input.mkdir()
    input_sentinel = inside_input / "evidence.pdf"
    input_sentinel.write_text("do not delete", encoding="utf-8")

    # The script needs the repo's interpreter, package and helper scripts.
    for name in (".venv", "src", "scripts"):
        _junction(project / name, ROOT / name)

    (project / "config.yaml").write_text(
        "paths:\n"
        f"  input_folder: '{outside / 'input'}'\n"
        f"  output_folder: '{outside / 'output'}'\n"
        f"  quarantine_folder: '{outside / 'quarantine'}'\n"
        f"  stored_documents_folder: '{outside / 'stored'}'\n"
        f"  database_path: '{outside / 'aam_merger.db'}'\n"
        f"  combined_folder: '{outside / 'combined'}'\n"
        f"  log_folder: '{outside / 'logs'}'\n",
        encoding="utf-8",
    )
    (project / "data").mkdir()

    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project / "scripts" / "reset_data.ps1"),
            "-ProjectDir",
            str(project),
            "-Force",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )

    assert result.returncode != 0, result.stdout
    assert "outside the project data root" in (result.stdout + result.stderr)
    assert sentinel.exists(), "a path outside data/ was touched"
    assert input_sentinel.exists(), "a configured folder outside data/ was cleared"
