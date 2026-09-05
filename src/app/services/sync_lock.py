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
is older than SYNC_STALE_SECONDS (default 3600s — a full 100-PDF + VLM run
budget on this host) is treated as a dead holder's residue: the files are
unlinked and the next sync proceeds. Residual race: if the original holder is
still alive past the threshold, both proceed — accepted, documented tradeoff
for a single-host LAN tool (alternative is blocking all syncs until restart).
"""

from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path

from filelock import FileLock, Timeout

from app.core.config import load_config

logger = logging.getLogger(__name__)

#: Stale-break threshold (human decision 2026-09-05).
SYNC_STALE_SECONDS = 3600

_LOCK_NAME = ".sync.lock"
_SIDECAR_NAME = ".sync.started"


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


def _break_if_stale(lock: FileLock) -> None:
    """Unlink lock + sidecar when the sidecar proves the holder died > threshold ago."""
    sc = _sidecar_for(Path(lock.lock_file))
    try:
        age = time.time() - sc.stat().st_mtime
    except OSError:
        return
    if age <= SYNC_STALE_SECONDS:
        return
    logger.warning(
        "Breaking stale sync lock (age %.0fs > %ds) at %s",
        age,
        SYNC_STALE_SECONDS,
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
    _break_if_stale(lock)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return None
    with contextlib.suppress(OSError):
        _sidecar_for(Path(lock.lock_file)).write_text(str(time.time()))
    return lock


def release_sync_lock(lock: FileLock) -> None:
    """Release a lock obtained via acquire_sync_lock (any thread) + sidecar."""
    with contextlib.suppress(Exception):
        lock.release()
    with contextlib.suppress(OSError):
        _sidecar_for(Path(lock.lock_file)).unlink(missing_ok=True)


def _is_sync_running(cfg_path: str | None = None) -> bool:
    """True while any thread/process holds the sync lock (FR-CONC-3).

    Read-only: performs no directory creation. A missing lock dir/file means
    no holder can exist → False.
    """
    lock = get_sync_lock(cfg_path, ensure_dirs=False)
    if not Path(lock.lock_file).parent.exists():
        return False
    _break_if_stale(lock)
    if lock.is_locked:
        return True
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return True
    except Exception:
        return False
    with contextlib.suppress(Exception):
        lock.release()
    return False
