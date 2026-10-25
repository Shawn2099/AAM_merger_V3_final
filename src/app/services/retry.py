"""Retry helper - mirrors the Prefect task retry envelope for non-Prefect callers.

The nightly sync wraps extract_document in a Prefect task with configurable
retries and backoff (flows/sync.py::_extract_task_for). Manual operator actions
that call extract_document directly would silently use different retry semantics
- one transient VLM blip = permanent failure for that attempt. This module
provides the same bounded retry behaviour without introducing a Prefect task
dependency in the route layer.

Usage:
    from app.services.retry import with_backoff_retry

    result = with_backoff_retry(
        lambda: extract_document(doc_id, cfg),
        max_retries=cfg.extraction.max_retries,
        backoff_seconds=list(cfg.extraction.retry_backoff_seconds),
        label=f"redo_extract doc={doc_id}",
    )
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def with_backoff_retry(
    fn: Callable[[], T],
    max_retries: int,
    backoff_seconds: list[float],
    *,
    label: str = "",
) -> T:
    """Call fn(), retrying on exception up to max_retries times with configurable backoff.

    Mirrors the retry semantics of the Prefect extract_task (flows/sync.py
    _extract_task_for) so that manual redo_extract has identical retry
    behaviour to the nightly sync. A successful return (including a
    no-exception terminal path like the attempt-cap in extract_document) is
    never retried - only an exception triggers a retry.

    Args:
        fn: Callable to invoke. Use a lambda with a default-argument capture
            (``lambda d=doc_id: f(d, cfg)``) to avoid late-binding in loops.
        max_retries: Maximum number of additional attempts after the first
            (mirrors Prefect task ``retries`` parameter).
        backoff_seconds: List of delay values. Index is attempt number (0-based).
            If the list is shorter than max_retries, the last value is repeated.
        label: Human-readable label for log messages only.

    Returns:
        Return value of fn() on the first successful call.

    Raises:
        The last exception raised by fn() if all attempts are exhausted.
    """
    last_exc: BaseException | None = None
    total_attempts = max_retries + 1

    for attempt in range(total_attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = float(
                    backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
                    if backoff_seconds
                    else 1.0
                )
                logger.warning(
                    "%s attempt %d/%d failed (%s: %s); retrying in %.0fs",
                    label or "with_backoff_retry",
                    attempt + 1,
                    total_attempts,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                time.sleep(delay)
            else:
                logger.warning(
                    "%s all %d attempts failed; last error: %s: %s",
                    label or "with_backoff_retry",
                    total_attempts,
                    type(exc).__name__,
                    exc,
                )

    assert last_exc is not None  # total_attempts >= 1
    raise last_exc
