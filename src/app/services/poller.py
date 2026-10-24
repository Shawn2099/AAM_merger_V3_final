"""Input-folder poller (D5): start a sync when there is something new to read.

A lightweight thread inside the web service. Every `schedule.poll_interval_seconds` it
lists the input folder (no API calls, no hashing) and starts a sync when

* a PDF appeared or changed since the last sync started, or
* a set is only waiting for its settle time and that time has now passed, or
* the previous sync was queued behind another one, or failed on some files (retried a
  few times, then left for the nightly sweep).

Nothing is skipped: while the poller thread is running a sync it is not ticking, and
the first tick afterwards sees whatever arrived meanwhile (one pass reads every waiting
file, oldest first). If a manual Scan or the nightly run holds the sync lock, the tick is
queued and retried every few seconds until the lock is free.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

#: Passes retried after a sync reported errors, before leaving it to the nightly sweep.
MAX_ERROR_RETRIES = 3
#: While queued behind another sync, check the lock this often (seconds).
QUEUED_RECHECK_SECONDS = 5
#: After a sync raised (for example the Prefect server is down) wait this long before trying again.
FAILURE_BACKOFF_SECONDS = 300
#: After the provider rejected the request itself (HTTP 400 breaker), stay quiet this long.
#: Documents stay pending, so nothing is lost; the next pass tries again with whatever the
#: config now says. A manual Scan can be run at any time.
HALT_BACKOFF_SECONDS = 3600
#: Log a warning when a run has been queued behind another sync for this many seconds.
QUEUED_WARN_SECONDS = 120


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


class InputWatcher:
    """Remembers which input files (by path, mtime and size) a sync has already been given."""

    def __init__(self) -> None:
        self._seen: dict[str, tuple[int, int]] = {}

    def snapshot(self, cfg) -> dict[str, tuple[int, int]]:
        from app.services.ingestion import input_pdfs

        snap: dict[str, tuple[int, int]] = {}
        for f in input_pdfs(cfg):
            sig = _signature(f)
            if sig is not None:
                snap[str(f)] = sig
        return snap

    def has_new_files(self, snapshot: dict[str, tuple[int, int]]) -> bool:
        """A non-empty file whose signature is not one we already handed to a sync.

        Zero-byte files are ignored on purpose (decided: skipped for now).
        """
        return any(sig[1] > 0 and self._seen.get(path) != sig for path, sig in snapshot.items())

    def commit(self, snapshot: dict[str, tuple[int, int]]) -> None:
        """Record what a sync was started with (taken BEFORE it ran, so a file that
        grew during the run still counts as new afterwards)."""
        self._seen = dict(snapshot)


def settling_sets_ready(cfg, now=None) -> bool:
    """True when a set waiting only for its settle time can now be merged."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import Document, POSet, POSetStatus
    from app.services.reconciliation import SETTLING_PREFIX

    settle = cfg.schedule.merge_settle_minutes
    if settle <= 0:
        return False
    now = now or datetime.now(UTC)
    with Session(get_engine(cfg)) as s:
        rows = (
            s.query(POSet.id, func.max(Document.created_at))
            .join(Document, Document.po_set_id == POSet.id)
            .filter(
                POSet.status == POSetStatus.pending,
                POSet.archived_at.is_(None),
                POSet.reconcile_reason.like(f"{SETTLING_PREFIX}%"),
                Document.archived_at.is_(None),
            )
            .group_by(POSet.id)
            .all()
        )
    for _set_id, newest in rows:
        if newest is None:
            return True
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=UTC)
        if newest + timedelta(minutes=settle) <= now:
            return True
    return False


def run_poller_sync(lock) -> dict:
    """Run one sync while holding `lock`, with the settle rule switched on."""
    from app.core.config import load_config
    from app.services.sync_lock import release_sync_lock

    token = None
    try:
        # Everything that can fail sits inside the try, so the lock (and its sidecar) is
        # always released and the dashboard never shows a sync that is not running.
        from app.flows.sync import sync_flow
        from app.services.reconciliation import SETTLE_MINUTES

        token = SETTLE_MINUTES.set(load_config().schedule.merge_settle_minutes)
        return sync_flow(cfg_path=None, held_lock=lock) or {}
    finally:
        if token is not None:
            from app.services.reconciliation import SETTLE_MINUTES

            SETTLE_MINUTES.reset(token)
        release_sync_lock(lock)


class SyncPoller:
    def __init__(
        self,
        load_cfg: Callable | None = None,
        run_sync: Callable | None = None,
        acquire: Callable | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        from app.core.config import load_config
        from app.services.sync_lock import acquire_sync_lock

        self._load_cfg = load_cfg or load_config
        self._run_sync = run_sync or run_poller_sync
        self._acquire = acquire or acquire_sync_lock
        self.watcher = InputWatcher()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._clock = clock or time.monotonic
        self._queued = False
        self._queued_since: float | None = None
        self._backoff_until = 0.0  # no run before this time (failure / provider halt)
        self._after_backoff = False  # a run is owed once the backoff ends
        self._error_retries = MAX_ERROR_RETRIES
        self.runs = 0  # syncs this poller started (tests and the status line)

    # ------------------------------------------------------------------ one tick
    def tick(self) -> str:
        """Returns "disabled", "idle", "queued" or "ran"."""
        now = self._clock()
        try:
            cfg = self._load_cfg()
        except Exception:
            logger.warning("Poller could not read the config; skipping this tick", exc_info=True)
            return "idle"
        if not cfg.schedule.poll_enabled:
            return "disabled"
        if now < self._backoff_until:
            return "idle"
        try:
            snapshot = self.watcher.snapshot(cfg)
            wanted = (
                self._queued
                or self._after_backoff
                or self.watcher.has_new_files(snapshot)
                or settling_sets_ready(cfg)
            )
        except Exception:
            logger.warning("Poller check failed; will try again", exc_info=True)
            return "idle"
        if not wanted:
            return "idle"
        lock = self._acquire()
        if lock is None:
            if not self._queued:
                self._queued_since = now
            self._queued = True  # another sync is running: run again as soon as it ends
            if self._queued_since is not None and now - self._queued_since >= QUEUED_WARN_SECONDS:
                logger.warning(
                    "A sync has held the lock for over %d s; the poller is still waiting for it",
                    QUEUED_WARN_SECONDS,
                )
                self._queued_since = now  # warn again after another interval, not every tick
            return "queued"
        self._queued = False
        self._queued_since = None
        self._after_backoff = False
        self.runs += 1
        try:
            result = self._run_sync(lock) or {}
        except Exception:
            # Typical cause: the Prefect server is down. Do NOT mark the files as handled
            # (they must be retried once it is back) and do not hammer it every minute.
            logger.exception("Poller sync failed; backing off %d s", FAILURE_BACKOFF_SECONDS)
            self._backoff_until = self._clock() + FAILURE_BACKOFF_SECONDS
            self._after_backoff = True
            return "ran"
        if result.get("vlm_halted"):
            logger.error(
                "The provider rejected the request itself (HTTP 400); extraction is paused "
                "for %d s. Fix vlm.model / the request options, or press Scan input folder.",
                HALT_BACKOFF_SECONDS,
            )
            self._backoff_until = self._clock() + HALT_BACKOFF_SECONDS
            self._after_backoff = True
            self._error_retries = MAX_ERROR_RETRIES
            self.watcher.commit(snapshot)
            return "ran"
        needs_retry = bool(result.get("errors", 0) or result.get("unstable", 0))
        if needs_retry and self._error_retries > 0:
            self._error_retries -= 1  # leave the snapshot uncommitted: files count as new
        else:
            self._error_retries = MAX_ERROR_RETRIES
            self.watcher.commit(snapshot)
        return "ran"

    # ------------------------------------------------------------------ thread
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                action = self.tick()
                interval = (
                    self._load_cfg().schedule.poll_interval_seconds if action != "disabled" else 30
                )
            except Exception:
                logger.exception("Poller loop error")
                interval = 30
            if self._queued:
                interval = min(interval, QUEUED_RECHECK_SECONDS)
            self._stop.wait(interval)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="aam-input-poller", daemon=True)
        self._thread.start()
        logger.info("Input poller started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None


def start_if_enabled(cfg) -> SyncPoller | None:
    """Called from the web service's startup; None when polling is switched off."""
    if not cfg.schedule.poll_enabled:
        return None
    poller = SyncPoller()
    poller.start()
    return poller


__all__ = ["InputWatcher", "SyncPoller", "settling_sets_ready", "start_if_enabled"]
