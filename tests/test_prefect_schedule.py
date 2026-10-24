"""The midnight sync must run at the office's midnight, not UTC's.

Prefect's cron schedule is UTC unless a timezone is given. The deployment is
built from config so the installer can set it per site; unset defaults to the
office's zone (Asia/Kolkata) rather than silently running at 05:30 local.
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


def test_schedule_is_a_cron_schedule_and_defaults_to_ist():
    module = _load_deploy_module()
    cfg = SimpleNamespace(prefect=SimpleNamespace(schedule_timezone=None))

    schedule = module._schedule(cfg)
    assert schedule.cron == "0 0 * * *"
    assert schedule.timezone == "Asia/Kolkata"


def test_to_deployment_accepts_the_schedule_object():
    """The deployment must be constructed with `schedule=`, not `cron=`.

    Passing a CronSchedule to `cron=` fails pydantic validation, so the
    deployment was never registered when a timezone was configured. Build the
    deployment object only (no `.apply()`): this is the regression guard.
    """
    from app.flows.sync import sync_flow

    module = _load_deploy_module()
    cfg = SimpleNamespace(prefect=SimpleNamespace(schedule_timezone="Asia/Kolkata"))

    deployment = sync_flow.to_deployment(
        name="aam-midnight-sync-test",
        schedule=module._schedule(cfg),
        work_pool_name="test-pool",
    )
    assert len(deployment.schedules) == 1
    built = deployment.schedules[0].schedule
    assert built.cron == "0 0 * * *"
    assert built.timezone == "Asia/Kolkata"


def test_deployment_sets_working_dir_and_config_path(monkeypatch):
    """The worker must run in the project root with the project's config.

    A process-pool worker started elsewhere would otherwise resolve
    `config.yaml` and relative paths against its own cwd.
    """
    import os

    from app.flows.sync import sync_flow

    module = _load_deploy_module()
    captured: dict = {}

    class _Deployment:
        def apply(self):
            return "dep-id"

    def fake_to_deployment(self, **kwargs):
        captured.update(kwargs)
        captured["cwd"] = os.getcwd()
        return _Deployment()

    cfg = SimpleNamespace(
        prefect=SimpleNamespace(schedule_timezone="Asia/Kolkata", work_pool_name="pool")
    )
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")
    monkeypatch.setattr(module, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(type(sync_flow), "to_deployment", fake_to_deployment)
    monkeypatch.chdir(ROOT / "tests")

    module.deploy()

    root = Path(module.ROOT)
    assert root == ROOT
    assert captured["cwd"] == str(ROOT)
    jv = captured["job_variables"]
    assert jv["working_dir"] == str(ROOT)
    assert jv["env"]["AAM_CONFIG_PATH"] == str(ROOT / "config.yaml")
    assert captured["parameters"]["cfg_path"] == str(ROOT / "config.yaml")


def test_nightly_switch_off_registers_the_deployment_without_a_schedule(monkeypatch):
    """schedule.nightly_enabled: false removes the cron when deploy_prefect.py is re-run."""
    from app.flows.sync import sync_flow

    module = _load_deploy_module()
    captured: dict = {}

    class _Deployment:
        def apply(self):
            return "dep-id"

    def fake_to_deployment(self, **kwargs):
        captured.update(kwargs)
        return _Deployment()

    cfg = SimpleNamespace(
        prefect=SimpleNamespace(schedule_timezone="Asia/Kolkata", work_pool_name="pool"),
        schedule=SimpleNamespace(nightly_enabled=False),
    )
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")
    monkeypatch.setattr(module, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(type(sync_flow), "to_deployment", fake_to_deployment)
    module.deploy()
    assert captured["schedule"] is None
