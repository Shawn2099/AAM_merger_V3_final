"""In-memory thread-safe progress tracker for ingestion sync.

Avoids disk writes to satisfy test_concurrency.py assertions while providing
sub-millisecond live progress snapshots to the dashboard.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class SyncProgressState:
    running: bool = False
    phase: str = "idle"  # "idle" | "scanning" | "extracting" | "reconciling" | "completed"
    total_files: int = 0
    current_index: int = 0
    completed: int = 0
    in_flight: int = 0
    current_file: str = ""
    extracted_count: int = 0
    error_count: int = 0
    unstable_count: int = 0
    reconciled_count: int = 0
    started_at: float = 0.0
    completed_at: float = 0.0
    message: str = ""


class SyncProgressTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = SyncProgressState()

    def start(self, total_files: int, phase: str = "scanning") -> None:
        with self._lock:
            self._state = SyncProgressState(
                running=True,
                phase=phase,
                total_files=total_files,
                current_index=0,
                completed=0,
                in_flight=0,
                current_file="",
                extracted_count=0,
                error_count=0,
                started_at=time.time(),
                message="Scanning input folder and checking file stability…",
            )

    def set_phase(self, phase: str, message: str = "") -> None:
        with self._lock:
            if self._state.running:
                self._state.phase = phase
                if message:
                    self._state.message = message

    def update_file(
        self,
        index: int,
        filename: str,
        extracted: int | None = None,
        errors: int | None = None,
    ) -> None:
        with self._lock:
            if not self._state.running:
                return
            self._state.phase = "extracting"
            # Monotonic: with parallel work the caller's index may arrive out of
            # order, and a percentage that jumps backwards looks like a restart.
            self._state.current_index = max(self._state.current_index, index)
            self._state.completed = max(self._state.completed, self._state.current_index)
            self._state.current_file = filename
            if extracted is not None:
                self._state.extracted_count = max(self._state.extracted_count, extracted)
            if errors is not None:
                self._state.error_count = max(self._state.error_count, errors)
            self._state.message = (
                f"AI extracting {filename} ({self._state.current_index}/{self._state.total_files})"
            )

    def task_started(self, filename: str) -> None:
        """One extraction worker picked up a document."""
        with self._lock:
            if not self._state.running:
                return
            self._state.phase = "extracting"
            self._state.in_flight += 1
            self._state.current_file = filename

    def task_finished(
        self, filename: str, *, failed: bool = False, extracted: int | None = None
    ) -> None:
        """One extraction worker finished; counts only move forward."""
        with self._lock:
            if not self._state.running:
                return
            self._state.in_flight = max(0, self._state.in_flight - 1)
            self._state.completed += 1
            self._state.current_index = max(self._state.current_index, self._state.completed)
            self._state.current_file = filename
            if extracted is not None:
                self._state.extracted_count = max(self._state.extracted_count, extracted)
            if failed:
                self._state.error_count += 1
            self._state.message = (
                f"AI extracting {filename} "
                f"({self._state.completed}/{self._state.total_files} complete)"
            )

    def finish(self, summary: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._state.running = False
            self._state.phase = "completed"
            self._state.in_flight = 0
            self._state.completed_at = time.time()
            # Never lower a live count: the summary is authoritative for the
            # final numbers, but a racing worker must not make the bar regress.
            if summary:
                self._state.extracted_count = max(
                    self._state.extracted_count, summary.get("extracted", 0)
                )
                self._state.error_count = max(self._state.error_count, summary.get("errors", 0))
                self._state.reconciled_count = max(
                    self._state.reconciled_count, summary.get("reconciled_count", 0)
                )
            self._state.message = "Sync complete"

    def reset(self) -> None:
        with self._lock:
            self._state = SyncProgressState()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            s = self._state
            now = time.time()
            elapsed = int(now - s.started_at) if s.started_at > 0 else 0

            percent = 0
            eta_seconds = 0
            if s.phase == "completed":
                percent = 100
            elif s.total_files > 0 and s.completed > 0:
                percent = min(100, int((s.completed / s.total_files) * 100))
                # Average seconds per completed document for the remaining work.
                avg_time = elapsed / s.completed
                remaining_files = max(0, s.total_files - s.completed)
                eta_seconds = int(avg_time * remaining_files)

            return {
                "running": s.running,
                "phase": s.phase,
                "total": s.total_files,
                "current": s.completed or s.current_index,
                "completed": s.completed,
                "in_flight": s.in_flight,
                "current_file": s.current_file,
                "percent": percent,
                "extracted": s.extracted_count,
                "errors": s.error_count,
                "reconciled": s.reconciled_count,
                "elapsed_seconds": elapsed,
                "eta_seconds": eta_seconds,
                "message": s.message,
            }


progress_tracker = SyncProgressTracker()
