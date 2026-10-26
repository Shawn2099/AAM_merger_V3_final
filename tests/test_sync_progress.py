"""Unit tests for SyncProgressTracker service and /sync/status integration."""

import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import app
from app.services.sync_progress import SyncProgressTracker, progress_tracker


def test_progress_tracker_idle_state():
    tracker = SyncProgressTracker()
    snap = tracker.snapshot()
    assert snap["running"] is False
    assert snap["phase"] == "idle"
    assert snap["total"] == 0
    assert snap["current"] == 0
    assert snap["percent"] == 0


def test_progress_tracker_lifecycle_and_eta():
    tracker = SyncProgressTracker()
    tracker.start(total_files=100, phase="scanning")
    snap1 = tracker.snapshot()
    assert snap1["running"] is True
    assert snap1["phase"] == "scanning"
    assert snap1["total"] == 100
    assert snap1["current"] == 0
    assert snap1["percent"] == 0

    # Advance to file 25
    tracker.update_file(index=25, filename="DOC_025.pdf", extracted=24, errors=1)
    snap2 = tracker.snapshot()
    assert snap2["running"] is True
    assert snap2["phase"] == "extracting"
    assert snap2["current"] == 25
    assert snap2["percent"] == 25
    assert snap2["current_file"] == "DOC_025.pdf"
    assert snap2["extracted"] == 24
    assert snap2["errors"] == 1

    # Transition to reconciling
    tracker.set_phase("reconciling", "Grouping and reconciling…")
    snap3 = tracker.snapshot()
    assert snap3["phase"] == "reconciling"
    assert "reconciling" in snap3["message"].lower()

    # Finish
    tracker.finish({"extracted": 95, "errors": 5, "reconciled_count": 12})
    snap4 = tracker.snapshot()
    assert snap4["running"] is False
    assert snap4["phase"] == "completed"
    assert snap4["percent"] == 100
    assert snap4["extracted"] == 95
    assert snap4["errors"] == 5
    assert snap4["reconciled"] == 12


def test_progress_tracker_thread_safety():
    tracker = SyncProgressTracker()
    tracker.start(total_files=1000)

    def worker(i: int):
        tracker.update_file(index=i, filename=f"file_{i}.pdf", extracted=i, errors=0)
        snap = tracker.snapshot()
        assert snap["running"] is True

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(1, 50)))

    tracker.finish()
    assert tracker.snapshot()["running"] is False


def test_sync_status_endpoint_contract(monkeypatch):
    client = TestClient(app)
    # 1. Idle state
    monkeypatch.setattr("app.api.routes.sync._is_sync_running", lambda: False)
    r_idle = client.get("/sync/status")
    assert r_idle.status_code == 200
    data_idle = r_idle.json()
    assert data_idle["running"] is False
    assert data_idle["detail"] == "idle"

    # 2. Running state with progress
    progress_tracker.start(total_files=50, phase="extracting")
    progress_tracker.update_file(index=15, filename="INV_99.pdf", extracted=14, errors=1)
    monkeypatch.setattr("app.api.routes.sync._is_sync_running", lambda: True)

    try:
        r_run = client.get("/sync/status")
        assert r_run.status_code == 200
        data_run = r_run.json()
        assert data_run["running"] is True
        assert data_run["detail"] == "Sync already running"
        assert data_run["total"] == 50
        assert data_run["current"] == 15
        assert data_run["percent"] == 30
        assert data_run["current_file"] == "INV_99.pdf"
        assert data_run["extracted"] == 14
        assert data_run["errors"] == 1
    finally:
        progress_tracker.reset()
