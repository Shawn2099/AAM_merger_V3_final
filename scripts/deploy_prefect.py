"""Deploy sync_flow with midnight cron schedule (FR-4.2).

Run once during initial deployment or updates:
    uv run python scripts/deploy_prefect.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add src to pythonpath so app can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from app.core.config import load_config
from app.flows.sync import sync_flow

CRON = "0 0 * * *"


def _schedule(cfg):
    """Cron at the site's local midnight when configured, else Prefect's UTC."""
    timezone = getattr(cfg.prefect, "schedule_timezone", None)
    if not timezone:
        return CRON
    from prefect.client.schemas.schedules import CronSchedule

    return CronSchedule(cron=CRON, timezone=timezone)


def deploy() -> None:
    cfg = load_config()
    pool_name = cfg.prefect.work_pool_name
    schedule = _schedule(cfg)
    tz_note = getattr(schedule, "timezone", None) or "UTC"

    deployment = sync_flow.to_deployment(
        name="aam-midnight-sync",
        cron=schedule,
        work_pool_name=pool_name,
        description="Nightly automated ingestion scan for AAM Merger V3",
    )
    deployment_id = deployment.apply()
    print(
        f"[OK] Prefect deployment 'aam-midnight-sync' registered successfully "
        f"(ID: {deployment_id}, Pool: {pool_name}, Schedule: 0 0 * * * {tz_note})"
    )


if __name__ == "__main__":
    deploy()
