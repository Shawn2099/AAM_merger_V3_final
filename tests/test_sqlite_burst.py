"""Parallel-extraction burst on the real SQLite path.

The full harness is ``scripts/sqlite_burst.py``; this test runs a smaller
version in CI. It is marked slow because it starts real threads, real WAL
traffic and a competing writer.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_burst_module():
    spec = importlib.util.spec_from_file_location(
        "sqlite_burst", ROOT / "scripts" / "sqlite_burst.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.slow
def test_three_worker_burst_has_no_lock_errors(tmp_path):
    burst = _load_burst_module()
    summary = burst.run_burst(
        documents=40,
        workers=3,
        vlm_latency_ms=10,
        dashboard_interval_ms=20,
        writer_interval_ms=10,
        items=3,
        workdir=tmp_path / "burst",
        keep=False,
    )

    assert summary["passed"], summary["failures"]
    # The cap is the hard rule; reaching exactly 3 in a 40-document sample is
    # timing-dependent (the first call can finish before the third starts).
    assert 1 <= summary["max_in_flight"] <= 3
    assert summary["valid"] == 40
    assert summary["attempted_once"] == 40
    assert summary["line_items"] == 120
    assert summary["errors"] == []
    assert summary["wal_after_checkpoint_mb"] <= 64
