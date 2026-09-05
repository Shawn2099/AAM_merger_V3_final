"""Task 9 TDD - 409 on concurrent Sync + per-PO lock (FR-4.3, FR-CONC-1-4, FR-CONFIG-2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import load_config
from app.core.database import get_engine
from app.models import POSet, POSetStatus
from app.models.base import Base


@pytest.fixture()
def tmp_db(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test_conc.db"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.unclassified_folder = tmp_path / "unclassified"
    for p in [
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.unclassified_folder,
    ]:
        Path(p).mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    return cfg


@pytest.fixture()
def client(tmp_db, monkeypatch):
    # ensure sync routes see same DB via config
    monkeypatch.setenv("AAM_CONFIG_PATH", str(Path("config.example.yaml")))
    # patch load_config to return tmp_db for routes
    import app.api.routes.sync as sync_mod

    # monkeypatch load_config inside routes to return tmp_db
    monkeypatch.setattr("app.api.routes.sync.load_config", lambda path=None: tmp_db)
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: tmp_db)
    monkeypatch.setattr("app.flows.sync.load_config", lambda path=None: tmp_db)
    monkeypatch.setattr("app.services.sync_lock.load_config", lambda path=None: tmp_db)
    # reset global sync lock
    sync_mod._sync_running = False
    from app.main import app

    with TestClient(app) as c:
        yield c
    sync_mod._sync_running = False


def _create_po_set(cfg, po_no="PO9999", status=POSetStatus.pending):
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    from sqlalchemy.orm import Session

    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=status)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        return ps.id


def test_concurrent_sync_409(client):
    # first POST /sync -> 200, second immediate -> 409 "Sync already running"
    r1 = client.post("/sync")
    assert r1.status_code == 200, r1.text
    r2 = client.post("/sync")
    assert r2.status_code == 409, r2.text
    assert "Sync already running" in r2.text


def test_po_lock_409(client, tmp_db):
    # lock PO Set with force_merge, second action -> 409
    po_id = _create_po_set(tmp_db, po_no="PO1001")
    r1 = client.post(f"/po_sets/{po_id}/force_merge")
    if r1.status_code == 200:
        # simulate long-running by re-locking directly with locked_at
        eng = get_engine(tmp_db)
        from sqlalchemy.orm import Session

        with Session(eng) as s:
            ps = s.get(POSet, po_id)
            ps.locked_by_action = "force_merge"
            ps.locked_at = datetime.now(UTC)
            s.commit()
        r2 = client.post(f"/po_sets/{po_id}/force_merge")
        assert r2.status_code == 409, r2.text
        assert "already in progress" in r2.text.lower()
    else:
        assert r1.status_code in (200, 409)
        r2 = client.post(f"/po_sets/{po_id}/force_merge")
        assert r2.status_code == 409


def test_po_lock_timeout_releases(tmp_db, client):
    # FR-CONFIG-2: lock auto-releases after po_set_lock_timeout_seconds (300s)
    po_id = _create_po_set(tmp_db, po_no="PO2002")
    eng = get_engine(tmp_db)
    from sqlalchemy.orm import Session

    with Session(eng) as s:
        ps = s.get(POSet, po_id)
        ps.locked_by_action = "force_merge"
        # set locked_at to 400s ago (beyond 300s timeout)
        ps.locked_at = datetime.now(UTC) - timedelta(seconds=400)
        s.commit()
    # now second action should NOT 409 because lock is stale and auto-released
    r = client.post(f"/po_sets/{po_id}/force_merge")
    # should succeed (200) because stale lock is auto-cleared
    assert r.status_code == 200, r.text


def test_sync_tasks_have_retry_backoff():
    """P2: Prefect extract_task must have retries=3 and retry_delay [2,5,15] (FR-6.5)."""
    from app.flows.sync import extract_task

    retries = getattr(extract_task, "retries", None)
    delay = getattr(extract_task, "retry_delay_seconds", None)
    if retries is None:
        retries = getattr(extract_task, "_retries", None)
    if delay is None:
        delay = getattr(extract_task, "_retry_delay_seconds", None)
    assert retries == 3, f"extract_task retries={retries}"
    assert delay == [2, 5, 15], f"extract_task delay={delay}"


def test_sync_flow_is_flow():
    """P2: sync_flow must be a Prefect flow (one flow per Sync, FR-4.3)."""
    from prefect import Flow

    from app.flows.sync import sync_flow

    # sync_flow should be a Flow instance or have flow decorator metadata
    assert hasattr(sync_flow, "name") or isinstance(sync_flow, Flow)
    # name check
    flow_name = getattr(sync_flow, "name", None) or getattr(sync_flow, "__name__", "")
    assert "sync" in str(flow_name).lower()


def test_sync_lock_shared_across_threads(tmp_db, monkeypatch):
    """FileLock acquired in the request thread must be releasable in the
    _run_sync daemon thread (FR-4.3). filelock is thread-local by default,
    which makes cross-thread release() a silent no-op."""
    import threading

    import app.services.sync_lock as sl_mod

    monkeypatch.setattr(sl_mod, "load_config", lambda path=None: tmp_db)
    lock = sl_mod.get_sync_lock()
    lock.acquire(timeout=0)
    outcome = []

    def worker():
        try:
            lock.release()
            outcome.append("released")
        except Exception as e:
            outcome.append(f"error:{e}")

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert outcome == ["released"]
    assert lock.is_locked is False
    # lock must be re-acquirable after cross-thread release
    lock.acquire(timeout=0)
    lock.release()
    assert lock.is_locked is False


def test_release_is_action_scoped(tmp_db):
    """release_lock must only clear the lock held by the given action
    (FR-CONC-2): request A finishing must not stomp request B's fresh lock."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet, POSetStatus
    from app.models.base import Base
    from app.services.locking import acquire_lock, release_lock

    eng = get_engine(tmp_db)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = POSet(po_no_normalized="STOMP", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        pid = ps.id
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        assert acquire_lock(ps, "action_a", s, tmp_db) is True
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        release_lock(ps, s, "action_b")  # wrong owner must NOT clear
        s.refresh(ps)
        assert ps.locked_by_action == "action_a"
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        release_lock(ps, s, "action_a")
        s.refresh(ps)
        assert ps.locked_by_action is None


def test_route_release_passes_action(tmp_db, monkeypatch):
    """Route finally-blocks must release only their own action: releasing as
    a different action must leave the lock intact (FR-CONC-2 lock-stomp)."""
    from sqlalchemy.orm import Session

    import app.api.routes.po_sets as po_routes
    from app.core.database import get_engine
    from app.models import POSet, POSetStatus
    from app.models.base import Base
    from app.services.locking import acquire_lock

    monkeypatch.setattr(po_routes, "load_config", lambda: tmp_db)
    eng = get_engine(tmp_db)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = POSet(po_no_normalized="STOMP2", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        pid = ps.id
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        assert acquire_lock(ps, "force_merge", s, tmp_db) is True
    # a different action's finally-block must NOT clear this lock
    po_routes._release_lock(pid, tmp_db, "toggle_customs")
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        assert ps.locked_by_action == "force_merge"
    # the owning action releases cleanly
    po_routes._release_lock(pid, tmp_db, "force_merge")
    with Session(eng) as s:
        ps = s.get(POSet, pid)
        assert ps.locked_by_action is None


def test_running_sync_detected(tmp_db, monkeypatch):
    """Held sync lock → _is_sync_running True; released → False (FR-CONC-3)."""
    import app.services.sync_lock as sl

    monkeypatch.setattr(sl, "load_config", lambda path=None: tmp_db)
    assert sl._is_sync_running() is False
    lock = sl.acquire_sync_lock()
    assert lock is not None
    assert sl._is_sync_running() is True
    sl.release_sync_lock(lock)
    assert sl._is_sync_running() is False


def test_stale_sync_lock_breaks(tmp_db, monkeypatch):
    """Sidecar older than SYNC_STALE_SECONDS → lock treated as dead residue."""
    import os
    import time

    import app.services.sync_lock as sl

    monkeypatch.setattr(sl, "load_config", lambda path=None: tmp_db)
    lock = sl.acquire_sync_lock()
    assert lock is not None
    sc = sl._sidecar_for(Path(lock.lock_file))
    assert sc.exists()
    sl.release_sync_lock(lock)  # holder dies, files may remain
    if sc.exists():
        old = time.time() - (sl.SYNC_STALE_SECONDS + 10)
        os.utime(sc, (old, old))
        assert sl._is_sync_running() is False
        assert sl.acquire_sync_lock() is not None
    else:
        assert sl._is_sync_running() is False


def test_sync_flow_skips_when_locked(tmp_db, monkeypatch):
    """Direct sync_flow (cron path) with a held lock → skipped summary (W-7)."""
    import app.services.sync_lock as sl
    from app.flows.sync import sync_flow

    monkeypatch.setattr(sl, "load_config", lambda path=None: tmp_db)
    monkeypatch.setattr("app.flows.sync.load_config", lambda path=None: tmp_db)
    holder = sl.acquire_sync_lock()
    assert holder is not None
    try:
        # .fn runs the raw flow logic without Prefect server round-trips
        run_fn = getattr(sync_flow, "fn", sync_flow)
        res = run_fn()
        assert res.get("status") == "skipped"
        assert res.get("reason") == "sync_already_running"
    finally:
        sl.release_sync_lock(holder)


def test_sync_flow_accepts_held_lock(tmp_db, monkeypatch):
    """Route path: sync_flow with held_lock runs without self-deadlock."""
    import app.services.sync_lock as sl
    from app.flows.sync import sync_flow

    monkeypatch.setattr(sl, "load_config", lambda path=None: tmp_db)
    monkeypatch.setattr("app.flows.sync.load_config", lambda path=None: tmp_db)
    holder = sl.acquire_sync_lock()
    assert holder is not None
    try:
        run_fn = getattr(sync_flow, "fn", sync_flow)
        res = run_fn(held_lock=holder)
        assert res.get("status") != "skipped"
        assert "processed" in res
    finally:
        sl.release_sync_lock(holder)
