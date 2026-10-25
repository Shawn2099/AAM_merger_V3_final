"""Inter-process Sync guard (SPEC FR-4.3, FR-CONC-3).

Single FileLock in the database directory (local host per SPEC 5.1) serializes
manual POST /sync against the midnight Prefect cron, which invokes sync_flow
directly in a worker process (OS-level flock works across processes).

Threading: the SAME lock instance is acquired in the request thread and
released in the _run_sync daemon thread, so locks are constructed with
thread_local=False. With filelock's default thread-local state, cross-thread
release() is a silent no-op (per-thread lock_file_fd) and the OS lock would
only clear via GC (see filelock how-to: "Use locks with multiple threads").

Stale watchdog: a sidecar file records acquisition time. A lock whose sidecar
is older than the stale threshold is treated as a dead holder's residue: the
files are unlinked and the next sync proceeds. The threshold is
_stale_seconds(): the SYNC_STALE_SECONDS floor (1200s) or
extraction.stale_processing_seconds + 60, whichever is larger, so it is raised
with config. The holder refreshes the sidecar mtime via heartbeat_sync_lock()
per document and per reconcile set, so a live sync never looks stale; the
threshold must exceed the worst gap between heartbeats (one document's
extraction worst case, bounded by config validation, or one set's reconcile
sweep). Residual race: if the
original holder is still alive but stopped heartbeating past the threshold,
both proceed — accepted, documented tradeoff for a single-host LAN tool
(alternative is blocking all syncs until restart).
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path

from filelock import FileLock, Timeout

from app.core.config import load_config

logger = logging.getLogger(__name__)

#: Stale-break floor in seconds. Originally 3600 (human decision 2026-09-05);
#: set to 1200 on 2026-10-23 and raised with config by _stale_seconds(), since
#: heartbeat_sync_lock() refreshes the sidecar per document and per set.
SYNC_STALE_SECONDS = 1200

_LOCK_NAME = ".sync.lock"
_SIDECAR_NAME = ".sync.started"


def _stale_seconds(cfg_path: str | None = None) -> int:
    """Effective stale threshold: the floor, raised to cover one extraction worst case."""
    try:
        cfg = load_config(cfg_path) if cfg_path else load_config()
        return max(SYNC_STALE_SECONDS, int(cfg.extraction.stale_processing_seconds) + 60)
    except Exception:
        return SYNC_STALE_SECONDS


def _lock_file(cfg_path: str | None = None, ensure_dirs: bool = True) -> Path:
    cfg = load_config(cfg_path) if cfg_path else load_config()
    db_dir = Path(cfg.paths.database_path).parent.resolve()
    if ensure_dirs:
        db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / _LOCK_NAME


def _sidecar_for(lock_file: Path) -> Path:
    return lock_file.parent / _SIDECAR_NAME


def get_sync_lock(cfg_path: str | None = None, ensure_dirs: bool = True) -> FileLock:
    """Build the shared sync FileLock. ensure_dirs=False performs no
    filesystem writes (for read-only status polling)."""
    return FileLock(
        str(_lock_file(cfg_path, ensure_dirs=ensure_dirs)),
        timeout=0,
        thread_local=False,
    )


def _break_if_stale(lock: FileLock, cfg_path: str | None = None) -> None:
    """Unlink lock + sidecar when the sidecar proves the holder died > threshold ago."""
    sc = _sidecar_for(Path(lock.lock_file))
    try:
        age = time.time() - sc.stat().st_mtime
    except OSError:
        return
    threshold = _stale_seconds(cfg_path)
    if age <= threshold:
        return
    logger.warning(
        "Breaking stale sync lock (age %.0fs > %ds) at %s",
        age,
        threshold,
        lock.lock_file,
    )
    with contextlib.suppress(OSError):
        Path(lock.lock_file).unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        sc.unlink(missing_ok=True)


def acquire_sync_lock(cfg_path: str | None = None) -> FileLock | None:
    """Try-acquire the sync lock; None when another sync holds it (→ 409/skip).

    Writes the sidecar timestamp only after a successful acquisition, so a
    crash between acquire and sidecar write can only leave a lock file with
    NO sidecar — which is never stale-broken (fail-closed direction).
    """
    lock = get_sync_lock(cfg_path)
    _break_if_stale(lock, cfg_path)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return None
    with contextlib.suppress(OSError):
        _sidecar_for(Path(lock.lock_file)).write_text(str(time.time()))
    return lock


def release_sync_lock(lock: FileLock) -> None:
    """Release a lock obtained via acquire_sync_lock (any thread) + sidecar."""
    # Remove the sidecar BEFORE releasing: a waiter that acquires in the gap writes its own
    # sidecar, and unlinking afterwards would delete it and make a running sync look idle.
    with contextlib.suppress(OSError):
        _sidecar_for(Path(lock.lock_file)).unlink(missing_ok=True)
    with contextlib.suppress(Exception):
        lock.release()


def heartbeat_sync_lock(cfg_path: str | None = None) -> None:
    """Refresh the sidecar mtime of a live sync so it is not seen as stale.

    Only touches an existing sidecar: never creates it and never touches the
    lock file. Never raises, and is cheap enough to call once per document.
    """
    with contextlib.suppress(Exception):
        sc = _sidecar_for(_lock_file(cfg_path, ensure_dirs=False))
        if sc.exists():
            os.utime(sc, None)


def _is_sync_running(cfg_path: str | None = None) -> bool:
    """True while any thread/process holds the sync lock (FR-CONC-3).

    Checks the sidecar file only — never acquires the lock. Acquiring the lock
    for a status check introduces a TOCTOU window: concurrent dashboard polls
    would briefly see each other's acquisitions as 'running', causing the Sync
    button to flicker incorrectly.

    Sidecar contract (above): written immediately after acquisition, deleted
    immediately after release. Its presence + recency is sufficient to confirm
    an active holder without touching the lock itself.
    """
    try:
        lock_file = _lock_file(cfg_path, ensure_dirs=False)
    except Exception:
        return False
    sc = _sidecar_for(lock_file)
    try:
        age = time.time() - sc.stat().st_mtime
        return age <= _stale_seconds(cfg_path)
    except OSError:
        # Sidecar absent → no active holder
        return False
