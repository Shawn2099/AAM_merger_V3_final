"""The midnight sync must run at the office's midnight, not UTC's.

Prefect's cron schedule is UTC unless a timezone is given. The deployment is
built from config so the installer can set it per site; unset keeps Prefect's
default (UTC) rather than silently choosing a zone.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import PrefectConfig

ROOT = Path(__file__).resolve().parents[1]


def _load_deploy_module():
    spec = importlib.util.spec_from_file_location(
        "deploy_prefect_script", ROOT / "scripts" / "deploy_prefect.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_schedule_timezone_is_optional_and_defaults_to_none():
    assert PrefectConfig().schedule_timezone is None


def test_schedule_timezone_rejects_an_unknown_zone():
    with pytest.raises(ValueError):
        PrefectConfig(schedule_timezone="Mars/Olympus")


def test_schedule_carries_the_configured_timezone():
    module = _load_deploy_module()
    cfg = SimpleNamespace(prefect=SimpleNamespace(schedule_timezone="Asia/Kolkata"))

    schedule = module._schedule(cfg)

    assert schedule.cron == "0 0 * * *"
    assert schedule.timezone == "Asia/Kolkata"


def test_schedule_is_a_plain_cron_string_when_no_timezone_is_set():
    module = _load_deploy_module()
    cfg = SimpleNamespace(prefect=SimpleNamespace(schedule_timezone=None))

    assert module._schedule(cfg) == "0 0 * * *"
