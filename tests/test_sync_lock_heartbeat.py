"""Sync lock heartbeat: a live sync refreshes its sidecar so it is never seen as stale (2026-10-23)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import app.services.sync_lock as sl
from app.core.config import load_config


@pytest.fixture()
def lock_cfg(tmp_path, monkeypatch):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "heartbeat.db"
    monkeypatch.setattr(sl, "load_config", lambda path=None: cfg)
    return cfg


def _backdate(path: Path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_heartbeat_revives_stale_sidecar(lock_cfg):
    lock = sl.acquire_sync_lock()
    assert lock is not None
    try:
        sc = sl._sidecar_for(Path(lock.lock_file))
        _backdate(sc, 1300)
        assert sl._is_sync_running() is False  # stale past the 1200s floor
        sl.heartbeat_sync_lock()
        assert sl._is_sync_running() is True
    finally:
        sl.release_sync_lock(lock)


def test_heartbeat_without_sidecar_creates_nothing(lock_cfg):
    lock_file = sl._lock_file(ensure_dirs=False)
    sc = sl._sidecar_for(lock_file)
    assert not sc.exists()
    assert not lock_file.exists()
    sl.heartbeat_sync_lock()
    assert not sc.exists()
    assert not lock_file.exists()
    assert sl._is_sync_running() is False


def test_recent_sidecar_counts_as_running(lock_cfg):
    lock = sl.acquire_sync_lock()
    assert lock is not None
    try:
        _backdate(sl._sidecar_for(Path(lock.lock_file)), 100)
        assert sl._is_sync_running() is True
    finally:
        sl.release_sync_lock(lock)


def test_break_if_stale_spares_heartbeated_sidecar(lock_cfg):
    lock = sl.acquire_sync_lock()
    assert lock is not None
    try:
        sc = sl._sidecar_for(Path(lock.lock_file))
        _backdate(sc, 1300)
        sl.heartbeat_sync_lock()
        sl._break_if_stale(lock)
        assert sc.exists()
        assert sl._is_sync_running() is True
    finally:
        sl.release_sync_lock(lock)


def test_break_if_stale_unlinks_sidecar_past_floor(lock_cfg):
    lock = sl.acquire_sync_lock()
    assert lock is not None
    sc = sl._sidecar_for(Path(lock.lock_file))
    try:
        _backdate(sc, 1300)
        sl._break_if_stale(lock)
        assert not sc.exists()
    finally:
        sl.release_sync_lock(lock)


def test_break_if_stale_keeps_heartbeated_sidecar_past_floor(lock_cfg):
    lock = sl.acquire_sync_lock()
    assert lock is not None
    sc = sl._sidecar_for(Path(lock.lock_file))
    try:
        _backdate(sc, 1300)
        sl.heartbeat_sync_lock()
        sl._break_if_stale(lock)
        assert sc.exists()
    finally:
        sl.release_sync_lock(lock)


def test_stale_threshold_raised_by_extraction_config(lock_cfg):
    # threshold = max(1200, stale_processing_seconds + 60) = 2060 here
    lock_cfg.extraction.stale_processing_seconds = 2000
    lock = sl.acquire_sync_lock()
    assert lock is not None
    sc = sl._sidecar_for(Path(lock.lock_file))
    try:
        _backdate(sc, 1300)
        sl._break_if_stale(lock)
        assert sc.exists()  # 1300 s < 2060 s: not broken
        _backdate(sc, 2100)
        sl._break_if_stale(lock)
        assert not sc.exists()  # 2100 s > 2060 s: broken
    finally:
        sl.release_sync_lock(lock)
