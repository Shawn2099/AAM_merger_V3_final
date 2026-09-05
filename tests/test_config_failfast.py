"""Config fail-fast (SPEC 13.1, BLOCKER-9, W-13).

A missing config.yaml must raise, never silently fall back to the committed
example. A missing VLM API key must log a warning, never pass silently.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest


def test_missing_config_raises(tmp_path, monkeypatch):
    """No config.yaml + only a decoy example present → FileNotFoundError."""
    from app.core.config import load_config

    shutil.copy(Path("config.example.yaml"), tmp_path / "config.example.yaml")
    monkeypatch.setenv("AAM_CONFIG_PATH", str(tmp_path / "nope.yaml"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        load_config()


def test_missing_api_key_warns(tmp_path, monkeypatch, caplog):
    """Unset API key env var → warning logged (not silent pass)."""
    from app.core.config import load_config

    cfg_path = tmp_path / "cfg.yaml"
    shutil.copy(Path("config.example.yaml"), cfg_path)
    monkeypatch.setenv("AAM_CONFIG_PATH", str(cfg_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        load_config()
    assert any("OPENROUTER_API_KEY" in r.message for r in caplog.records)
