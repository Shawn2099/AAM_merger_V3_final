"""Input poller (D5): detection, queueing, retries, and the settle-time merge rule."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session
from test_robustness_harness import (
    HttpVLM,
    extraction,
    line,
    make_cfg,
    run_sync,
    sets,
    write_pdf,
)

from app.core.config import ScheduleConfig, load_config
from app.core.database import get_engine
from app.models import Document, POSetStatus
from app.services.poller import InputWatcher, SyncPoller, settling_sets_ready
from app.services.reconciliation import SETTLE_MINUTES, SETTLING_PREFIX


@pytest.fixture()
def rcfg(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, monkeypatch)
    cfg.schedule.poll_enabled = True
    return cfg


def _pdf(cfg, name, size=300) -> Path:
    return write_pdf(Path(cfg.paths.input_folder) / name, size)


# ---------------------------------------------------------------- config
def test_schedule_defaults_are_conservative_and_validated():
    cfg = ScheduleConfig()
    assert cfg.poll_enabled is False and cfg.nightly_enabled is True
    assert cfg.poll_interval_seconds == 60 and cfg.merge_settle_minutes == 5
    with pytest.raises(ValueError):
        ScheduleConfig(poll_interval_seconds=5)  # a tighter loop than 15 s is refused
    with pytest.raises(ValueError):
        ScheduleConfig(unknown_key=1)


def test_the_example_config_turns_polling_on_with_a_one_minute_interval():
    cfg = load_config("config.example.yaml")
    assert cfg.schedule.poll_enabled is True
    assert cfg.schedule.poll_interval_seconds == 60
    assert cfg.schedule.merge_settle_minutes == 5


# ---------------------------------------------------------------- watcher
def test_a_new_file_is_detected_once_and_a_grown_file_again(rcfg):
    watcher = InputWatcher()
    assert watcher.has_new_files(watcher.snapshot(rcfg)) is False  # empty folder
    path = _pdf(rcfg, "a.pdf")
    snap = watcher.snapshot(rcfg)
    assert watcher.has_new_files(snap) is True
    watcher.commit(snap)
    assert watcher.has_new_files(watcher.snapshot(rcfg)) is False  # already handed to a sync
    with path.open("ab") as fh:
        fh.write(b"\n%padding")
    os.utime(path, None)
    assert watcher.has_new_files(watcher.snapshot(rcfg)) is True  # grew: a copy was in progress


def test_zero_byte_files_are_ignored(rcfg):
    (Path(rcfg.paths.input_folder) / "empty.pdf").write_bytes(b"")
    watcher = InputWatcher()
    assert watcher.has_new_files(watcher.snapshot(rcfg)) is False


def test_subfolder_files_count(rcfg):
    sub = Path(rcfg.paths.input_folder) / "supplier"
    sub.mkdir()
    write_pdf(sub / "x.pdf", 310)
    watcher = InputWatcher()
    assert watcher.has_new_files(watcher.snapshot(rcfg)) is True


# ---------------------------------------------------------------- tick
class _FakeSync:
    def __init__(self, results=None):
        self.calls = 0
        self.results = list(results or [])

    def __call__(self, lock):
        self.calls += 1
        return self.results.pop(0) if self.results else {"errors": 0}


def _poller(cfg, fake, lock="LOCK"):
    return SyncPoller(load_cfg=lambda: cfg, run_sync=fake, acquire=lambda: lock)


def test_a_disabled_poller_does_nothing(rcfg):
    rcfg.schedule.poll_enabled = False
    _pdf(rcfg, "a.pdf")
    fake = _FakeSync()
    assert _poller(rcfg, fake).tick() == "disabled"
    assert fake.calls == 0


def test_idle_until_a_file_arrives_then_one_run_then_idle_again(rcfg):
    fake = _FakeSync()
    poller = _poller(rcfg, fake)
    assert poller.tick() == "idle"
    _pdf(rcfg, "a.pdf")
    assert poller.tick() == "ran" and fake.calls == 1
    assert poller.tick() == "idle" and fake.calls == 1  # the file is not re-run every minute
    _pdf(rcfg, "b.pdf", 320)
    assert poller.tick() == "ran" and fake.calls == 2


def test_a_busy_lock_queues_the_run_and_it_happens_when_the_lock_frees(rcfg):
    fake = _FakeSync()
    holder = {"lock": None}
    poller = SyncPoller(load_cfg=lambda: rcfg, run_sync=fake, acquire=lambda: holder["lock"])
    _pdf(rcfg, "a.pdf")
    assert poller.tick() == "queued" and fake.calls == 0  # nothing is skipped...
    holder["lock"] = "LOCK"
    assert poller.tick() == "ran" and fake.calls == 1  # ...it runs once the lock is free
    assert poller.tick() == "idle"


def test_errors_are_retried_a_few_times_then_left_for_the_nightly_sweep(rcfg):
    fake = _FakeSync(results=[{"errors": 2}] * 10)
    poller = _poller(rcfg, fake)
    _pdf(rcfg, "a.pdf")
    outcomes = [poller.tick() for _ in range(6)]
    assert outcomes[:4] == ["ran", "ran", "ran", "ran"]  # first try + 3 retries
    assert outcomes[4:] == ["idle", "idle"]  # then the snapshot is committed
    assert fake.calls == 4


def test_a_sync_that_raises_is_contained(rcfg):
    def boom(lock):
        raise RuntimeError("provider down")

    poller = SyncPoller(load_cfg=lambda: rcfg, run_sync=boom, acquire=lambda: "LOCK")
    _pdf(rcfg, "a.pdf")
    assert poller.tick() == "ran"  # the thread survives


def test_the_poller_thread_starts_and_stops(rcfg):
    rcfg.schedule.poll_interval_seconds = 15
    poller = SyncPoller(load_cfg=lambda: rcfg, run_sync=_FakeSync(), acquire=lambda: "LOCK")
    poller.start()
    poller.stop(timeout=5)
    assert poller._thread is None


# ---------------------------------------------------------------- settle time (real pipeline)
PO_NO = "4500088888"
W_PO, W_DN, W_SI = 111.0, 222.0, 333.0
STD = [line("1", "10", "BOLT"), line("2", "5", "NUT")]


def _good():
    return {
        W_PO: extraction("PO", PO_NO, None, STD),
        W_DN: extraction("DN", "DN-1", PO_NO, STD),
        W_SI: extraction("SI", "INV-1", PO_NO, STD),
    }


def _drop(cfg):
    inp = Path(cfg.paths.input_folder)
    write_pdf(inp / "PO.pdf", W_PO)
    write_pdf(inp / "DN.pdf", W_DN)
    write_pdf(inp / "SI.pdf", W_SI)


def test_a_poller_sync_waits_for_the_settle_time_and_then_merges(rcfg, monkeypatch):
    HttpVLM(monkeypatch, _good())
    _drop(rcfg)
    token = SETTLE_MINUTES.set(5)
    try:
        run_sync()
    finally:
        SETTLE_MINUTES.reset(token)
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.pending, ps.reconcile_reason
    assert (ps.reconcile_reason or "").startswith(SETTLING_PREFIX)
    assert ps.merged_output_path is None
    # Ten minutes later nothing has arrived: the next pass merges it with no operator action.
    with Session(get_engine(rcfg)) as s:
        s.execute(update(Document).values(created_at=datetime.now(UTC) - timedelta(minutes=10)))
        s.commit()
    assert settling_sets_ready(_cfg5(rcfg)) is True
    token = SETTLE_MINUTES.set(5)
    try:
        run_sync()
    finally:
        SETTLE_MINUTES.reset(token)
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.merged, ps.reconcile_reason


def _cfg5(cfg):
    cfg.schedule.merge_settle_minutes = 5
    return cfg


def test_a_set_still_inside_its_settle_time_is_not_ready(rcfg, monkeypatch):
    HttpVLM(monkeypatch, _good())
    _drop(rcfg)
    token = SETTLE_MINUTES.set(5)
    try:
        run_sync()
    finally:
        SETTLE_MINUTES.reset(token)
    assert settling_sets_ready(_cfg5(rcfg)) is False


def test_without_the_poller_a_set_merges_at_once(rcfg, monkeypatch):
    """The nightly run, manual Scan and Merge Now never wait."""
    HttpVLM(monkeypatch, _good())
    _drop(rcfg)
    run_sync()  # SETTLE_MINUTES defaults to 0
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.merged, ps.reconcile_reason


def test_full_chain_new_files_then_settle_wait_then_merge_without_an_operator(rcfg, monkeypatch):
    """The real poller + the real sync: files arrive, the set waits, then merges by itself."""
    monkeypatch.setattr("app.core.config.load_config", lambda path=None: rcfg)
    HttpVLM(monkeypatch, _good())
    poller = SyncPoller(load_cfg=lambda: rcfg)  # real sync, real file lock
    assert poller.tick() == "idle"
    _drop(rcfg)
    assert poller.tick() == "ran"
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.pending
    assert (ps.reconcile_reason or "").startswith(SETTLING_PREFIX)
    assert poller.tick() == "idle"  # settle time not over: nothing to do, nothing skipped
    with Session(get_engine(rcfg)) as s:
        s.execute(update(Document).values(created_at=datetime.now(UTC) - timedelta(minutes=10)))
        s.commit()
    assert poller.tick() == "ran"  # woken by the settle time, not by a new file
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.merged, ps.reconcile_reason
    assert poller.tick() == "idle"
