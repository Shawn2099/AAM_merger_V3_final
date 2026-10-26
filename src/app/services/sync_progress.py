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
            self._state.current_index = index
            self._state.current_file = filename
            if extracted is not None:
                self._state.extracted_count = extracted
            if errors is not None:
                self._state.error_count = errors
            self._state.message = f"AI extracting {filename} ({index}/{self._state.total_files})"

    def finish(self, summary: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._state.running = False
            self._state.phase = "completed"
            self._state.completed_at = time.time()
            if summary:
                self._state.extracted_count = summary.get("extracted", self._state.extracted_count)
                self._state.error_count = summary.get("errors", self._state.error_count)
                self._state.reconciled_count = summary.get("reconciled_count", 0)
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
            elif s.total_files > 0 and s.current_index > 0:
                percent = min(100, int((s.current_index / s.total_files) * 100))
                # Average seconds per file so far for remaining estimate
                avg_time = elapsed / s.current_index
                remaining_files = max(0, s.total_files - s.current_index)
                eta_seconds = int(avg_time * remaining_files)

            return {
                "running": s.running,
                "phase": s.phase,
                "total": s.total_files,
                "current": s.current_index,
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
