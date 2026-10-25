"""S3: a service STARTED on a broken config.yaml runs on the newest valid backup."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from app.core import config as config_module

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    # Fresh process state: nothing remembered, nothing reported.
    monkeypatch.setattr(config_module, "_LAST_GOOD", {})
    monkeypatch.setattr(config_module, "_REPORTED_BAD", set())
    monkeypatch.setattr(config_module, "_CONFIG_CACHE", {})
    folder = tmp_path / "install"
    folder.mkdir()
    text = (REPO / "config.example.yaml").read_text(encoding="utf-8")
    (folder / "config.yaml").write_text(text, encoding="utf-8")
    (folder / "config_backups").mkdir()
    return folder


@pytest.fixture
def log_records():
    """Records logged by app.core.config, captured with our own handler.

    `caplog` relies on propagation, which an earlier test in a full run can switch off, and the
    logger can be disabled by a logging reconfiguration; both made these tests pass alone and
    fail in the full suite.
    """
    records: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    log = logging.getLogger(config_module.__name__)
    handler = _Keep(level=logging.DEBUG)
    was_disabled, old_level = log.disabled, log.level
    log.disabled = False
    log.setLevel(logging.DEBUG)
    log.addHandler(handler)
    try:
        yield records
    finally:
        log.removeHandler(handler)
        log.setLevel(old_level)
        log.disabled = was_disabled


def _backup(folder: Path, stamp: str, text: str | None = None) -> Path:
    target = folder / "config_backups" / f"config-{stamp}.yaml"
    if text is None:
        shutil.copy2(folder / "config.yaml", target)
    else:
        target.write_text(text, encoding="utf-8")
    return target


def _break(folder: Path) -> None:
    (folder / "config.yaml").write_text("paths: [this is: not valid\n  vlm: {", encoding="utf-8")


def test_start_on_broken_file_uses_newest_valid_backup(cfg_dir, log_records):
    older = (cfg_dir / "config.yaml").read_text(encoding="utf-8")
    _backup(cfg_dir, "20261001T000000Z", older.replace("poll_enabled: true", "poll_enabled: x"))
    _backup(cfg_dir, "20261002T000000Z")
    _break(cfg_dir)
    cfg = config_module.load_config(cfg_dir / "config.yaml")
    assert cfg.paths.input_folder
    assert str(cfg_dir / "config.yaml").lower() in {k.lower() for k in config_module._LAST_GOOD}
    assert any("20261002T000000Z" in r.getMessage() for r in log_records)


def test_skips_invalid_newest_backup(cfg_dir, log_records):
    _backup(cfg_dir, "20261001T000000Z")
    _backup(cfg_dir, "20261003T000000Z", "paths: nonsense\n")
    _break(cfg_dir)
    cfg = config_module.load_config(cfg_dir / "config.yaml")
    assert cfg is not None
    messages = [r.getMessage() for r in log_records]
    assert any("20261001T000000Z" in m for m in messages)
    assert not any("20261003T000000Z" in m for m in messages)


def test_no_valid_backup_still_raises(cfg_dir):
    _backup(cfg_dir, "20261003T000000Z", "paths: nonsense\n")
    _break(cfg_dir)
    with pytest.raises(Exception):  # noqa: B017 - any fail-fast error is right
        config_module.load_config(cfg_dir / "config.yaml")
    assert config_module._LAST_GOOD == {}


def test_no_backup_folder_still_raises(cfg_dir):
    shutil.rmtree(cfg_dir / "config_backups")
    _break(cfg_dir)
    with pytest.raises(Exception):  # noqa: B017
        config_module.load_config(cfg_dir / "config.yaml")


def test_relative_paths_resolve_against_real_config_dir(cfg_dir):
    _backup(cfg_dir, "20261002T000000Z")
    _break(cfg_dir)
    cfg = config_module.load_config(cfg_dir / "config.yaml")
    expected = (cfg_dir / "data" / "input").resolve()
    assert Path(cfg.paths.input_folder).resolve() == expected
    for value in (cfg.paths.output_folder, cfg.paths.quarantine_folder):
        assert "config_backups" not in str(value)
        assert Path(value).resolve().is_relative_to(cfg_dir.resolve())


def test_error_logged_once_across_repeated_loads(cfg_dir, log_records):
    _backup(cfg_dir, "20261002T000000Z")
    _break(cfg_dir)
    for _ in range(3):
        config_module.load_config(cfg_dir / "config.yaml")
    errors = [r for r in log_records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
