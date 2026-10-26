"""The daily backup must work from any working directory.

The Windows Scheduled Task that runs `backup_db.ps1` has no working
directory (it defaults to C:\\Windows\\System32). Config paths are relative
(`./data/aam_merger.db`), so resolving them against the process cwd would
either fail or, worse, back up a database that is not the product's.

These tests pin the rule: a relative path in config.yaml is relative to the
project root, never to wherever the process happens to be started.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backup_db.py"


def _load_backup_module():
    spec = importlib.util.spec_from_file_location("backup_db_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def project(tmp_path):
    """A fake project root with a config.yaml, a database and nothing else."""
    (tmp_path / "data").mkdir()
    db = tmp_path / "data" / "aam_merger.db"
    con = sqlite3.connect(db)
    con.execute("create table t (id integer primary key, v text)")
    con.execute("insert into t (v) values ('one')")
    con.commit()
    con.close()
    (tmp_path / "config.yaml").write_text(
        "paths:\n"
        "  input_folder: ./data/input\n"
        "  output_folder: ./data/output\n"
        "  quarantine_folder: ./data/quarantine\n"
        "  stored_documents_folder: ./data/stored\n"
        "  database_path: ./data/aam_merger.db\n"
        "  combined_folder: ./data/combined\n"
        "  log_folder: ./data/logs\n",
        encoding="utf-8",
    )
    return tmp_path


def test_relative_database_path_resolves_against_project_root(
    project, tmp_path_factory, monkeypatch
):
    """Run from a different cwd: the backup still finds the project's DB."""
    module = _load_backup_module()
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(elsewhere)

    target = module.run_backup(retention_days=0, project_root=project)

    assert target.parent == (project / "data" / "backup").resolve()
    con = sqlite3.connect(target)
    try:
        assert con.execute("select v from t").fetchone()[0] == "one"
    finally:
        con.close()


def test_missing_config_is_an_error_not_a_silent_default(project, tmp_path_factory, monkeypatch):
    """A project with no config.yaml must fail loudly, not back up a guessed path."""
    module = _load_backup_module()
    (project / "config.yaml").unlink()
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(elsewhere)

    with pytest.raises(FileNotFoundError):
        module.run_backup(retention_days=0, project_root=project)
