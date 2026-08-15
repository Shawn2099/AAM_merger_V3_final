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


def deploy() -> None:
    cfg = load_config()
    pool_name = cfg.prefect.work_pool_name

    deployment = sync_flow.to_deployment(
        name="aam-midnight-sync",
        cron="0 0 * * *",  # Midnight every day (SPEC §7.1 FR-4.2)
        work_pool_name=pool_name,
        description="Nightly automated ingestion scan for AAM Merger V3",
    )
    deployment_id = deployment.apply()
    print(
        f"✅ Prefect deployment 'aam-midnight-sync' registered successfully "
        f"(ID: {deployment_id}, Pool: {pool_name}, Schedule: 0 0 * * *)"
    )


if __name__ == "__main__":
    deploy()
