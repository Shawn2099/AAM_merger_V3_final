"""Deploy sync_flow with 11:00 AM daily cron schedule (FR-4.2).

Run once during initial deployment or updates:
    uv run python scripts/deploy_prefect.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Add src to pythonpath so app can be imported
sys.path.insert(0, str(ROOT / "src"))

from app.core.config import load_config  # noqa: E402
from app.flows.sync import sync_flow  # noqa: E402

CRON = "0 11 * * *"


def _schedule(cfg):
    """Cron at the site's local 11:00 AM, IST when nothing is configured.

    Always return a CronSchedule object: `to_deployment(cron=...)` takes a
    string, while `to_deployment(schedule=...)` takes a schedule object. The
    old string branch made the deployment register at UTC midnight and, worse,
    the object branch was passed to `cron=` and failed validation outright.

    The office is in India; an unset key must not silently run at 05:30 local
    (UTC midnight). A site that genuinely wants another zone sets it.
    """
    from prefect.client.schemas.schedules import CronSchedule

    timezone = getattr(cfg.prefect, "schedule_timezone", None) or "Asia/Kolkata"
    return CronSchedule(cron=CRON, timezone=timezone)


def deploy() -> None:
    """Register the deployment against a DURABLE Prefect API.

    Prefect 3 starts an ephemeral local server when `PREFECT_API_URL` is unset,
    writes the deployment into it, prints success, and stops the server on exit
    — so nothing is registered and the nightly job never runs. Refuse instead
    of lying to the operator. `AAM_ALLOW_EPHEMERAL_PREFECT=1` is an explicit
    opt-out for a throwaway demo.
    """
    if not os.getenv("PREFECT_API_URL") and os.getenv("AAM_ALLOW_EPHEMERAL_PREFECT") != "1":
        raise SystemExit(
            "[ERROR] PREFECT_API_URL is not set. Prefect would start a temporary "
            "server, register the deployment there, and stop it on exit — the "
            "nightly sync would silently not exist. Set PREFECT_API_URL (e.g. "
            "http://127.0.0.1:4200/api) or pass -WithPrefectServer to the installer. "
            "Set AAM_ALLOW_EPHEMERAL_PREFECT=1 only for a throwaway demo."
        )
    # Prefect records the cwd as the deployment's source path; make it the
    # project root regardless of where the operator ran this script from.
    os.chdir(ROOT)
    cfg_path = str(ROOT / "config.yaml")
    cfg = load_config(cfg_path)
    pool_name = cfg.prefect.work_pool_name
    # `schedule.nightly_enabled: false` registers the deployment WITHOUT a cron, so
    # re-running this script after switching the sweep off really removes it.
    nightly = bool(getattr(getattr(cfg, "schedule", None), "nightly_enabled", True))
    schedule = _schedule(cfg) if nightly else None
    tz_note = (schedule.timezone or "UTC") if schedule is not None else "none"

    deployment = sync_flow.to_deployment(
        name="aam-midnight-sync",
        schedule=schedule,
        work_pool_name=pool_name,
        description="Daily 11:00 AM automated ingestion scan for AAM Merger V3",
        parameters={"cfg_path": cfg_path},
        job_variables={"working_dir": str(ROOT), "env": {"AAM_CONFIG_PATH": cfg_path}},
    )
    deployment_id = deployment.apply()
    print(
        f"[OK] Prefect deployment 'aam-midnight-sync' registered successfully "
        f"(ID: {deployment_id}, Pool: {pool_name}, "
        + (f"Schedule: {CRON} {tz_note})" if nightly else "daily sweep OFF: no schedule)")
    )


if __name__ == "__main__":
    deploy()
