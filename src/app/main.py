"""FastAPI application entry point for AAM Merger V3.

Cross-platform configuration, WAL SQLite engine, static asset mounting,
RotatingFileHandler logging (SPEC NFR-4), and comprehensive route inclusion.

Two hardened middlewares sit in front of every route (audit P1-10):

* `OriginGuardMiddleware` — a state-changing request whose `Origin`/`Referer`
  names a different host than the request is refused. Requests that carry
  neither header (curl, an HTMX client, an integration test) are still
  accepted: the app has no cookie-based session to ride, so the guard is
  against a *browser* being told to act for someone else (CSRF), not a
  general authentication layer.
* `TrustedHostMiddleware` — only mounted when `server.allowed_hosts` is not
  `["*"]` (the default). It closes DNS rebinding when the host has a stable
  name or address.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import URL, Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.manual_merger import router as manual_router
from app.api.routes.po_sets import router as po_sets_router
from app.api.routes.sync import router as sync_router
from app.core.config import load_config
from app.core.http_errors import BodyTooLarge, error_response
from app.core.limits import MAX_MANUAL_MERGE_REQUEST_BYTES

logger = logging.getLogger(__name__)

#: Methods that change state. GET/HEAD/OPTIONS are deliberately absent: the
#: dashboard polls with GET every 2 s and a browser sends no Origin for it.
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _host_from_url(value: str) -> str | None:
    """Return the hostname of an Origin/Referer value, or None if unusable.

    `urlsplit` never raises for a malformed authority; a value such as the
    opaque `null` Origin yields no hostname and is therefore rejected.
    """
    try:
        return urlsplit(value).hostname
    except ValueError:  # pragma: no cover - urlsplit tolerates almost anything
        return None


class OriginGuardMiddleware:
    """Refuse cross-origin POST/PUT/PATCH/DELETE (audit P1-10).

    No authentication is accepted risk (PRODUCT §8); CSRF is not. A malicious
    page can otherwise make the operator's browser force-merge a PO Set or
    upload a PDF. The check is a same-origin comparison of the `Origin` (or
    the `Referer` when the browser sends only that) against the request's
    `Host`. A request with neither header is allowed so the documented
    operator workflows — `curl`, HTMX, `TestClient` — keep working.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("method", "") not in _STATE_CHANGING_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        source = headers.get("origin") or headers.get("referer")
        if source is None:
            # Not a browser navigation or an intentional programmatic call.
            await self.app(scope, receive, send)
            return

        source_host = _host_from_url(source)
        request_host = URL(scope=scope).hostname
        if (
            source_host is None
            or request_host is None
            or source_host.casefold() != request_host.casefold()
        ):
            logger.warning(
                "Refused %s %s: Origin/Referer host %r does not match Host %r",
                scope.get("method"),
                scope.get("path"),
                source_host,
                request_host,
            )
            response = JSONResponse(
                {
                    "detail": (
                        "Cross-origin request refused: the Origin/Referer host "
                        "does not match this server. Reload the dashboard from "
                        "its own address and try again."
                    )
                },
                status_code=403,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class BodySizeLimitMiddleware:
    """Reject an oversized request body before a parser turns it into disk I/O.

    Starlette's multipart parser spools file parts to temp files and has no
    total-body limit, so the per-file caps checked inside a route run too late
    (the 100 MB body is already on disk).  This guard wraps `receive` and
    counts bytes as they arrive, which covers chunked requests too.

    It is only mounted on the manual-merger upload path today; the pipeline's
    upload route keeps its existing per-file cap.
    """

    def __init__(
        self,
        app: ASGIApp,
        paths: tuple[str, ...],
        max_bytes: int,
        display_mb: int | None = None,
    ) -> None:
        self.app = app
        self.paths = paths
        self.max_bytes = max_bytes
        self.display_mb = display_mb

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or not any(
            scope.get("path", "").startswith(path) for path in self.paths
        ):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, send)
            return

        received = 0
        started = False

        async def send_wrapper(message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send_wrapper)
        except BodyTooLarge:
            # If a handler already produced a response, let it stand (the route
            # maps this exception to its own themed page).
            if started:
                raise
            await self._reject(scope, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        limit_mb = self.display_mb or self.max_bytes // (1024 * 1024)
        request = Request(scope)
        response = error_response(
            request,
            413,
            "too_large",
            f"That upload is larger than the {limit_mb} MB limit. "
            "Please remove a file or use smaller ones.",
        )
        await response(scope, request.receive, send)


def _setup_logging() -> None:
    """Deprecated shim: the setup now lives in `app.core.logging_setup`."""
    from app.core.logging_setup import setup_logging

    setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # SPEC §13.1: validate config at startup, outside any try/except —
    # an invalid/missing config must refuse to start, not run degraded.
    cfg = load_config()
    # Schema creation is a startup action, not a per-poll one. Every route used
    # to call `create_all` (the dashboard does it every 2 s); `CREATE TABLE IF
    # NOT EXISTS` is cheap but it is still a writer competing with merges.
    from app.core.database import get_engine
    from app.models.base import Base

    Base.metadata.create_all(get_engine(cfg))
    _setup_logging()

    # A crashed merge can leave `mm-*.pdf` staging files behind. Sweep only
    # this tool's own prefix in its own folder; never touch a real document.
    if cfg.manual_merge.sweep_on_startup:
        from app.services.manual_merge import resolve_temp_folder, sweep_temp_folder

        try:
            folder = resolve_temp_folder(cfg.manual_merge.temp_folder, cfg.paths.database_path)
            sweep_temp_folder(folder, cfg.manual_merge.sweep_after_hours)
        except Exception:
            logger.warning("Manual-merge staging sweep failed at startup", exc_info=True)

    yield


def _manual_merge_request_bytes() -> int:
    """Configured whole-request cap for /manual/merge, or the built-in default."""
    try:
        return load_config().manual_merge.max_request_bytes
    except Exception:
        logger.warning(
            "Could not read manual_merge limits; using the built-in defaults",
            exc_info=True,
        )
        return MAX_MANUAL_MERGE_REQUEST_BYTES


def _manual_merge_display_mb() -> int | None:
    """The user-facing file-size limit, not the larger multipart cap."""
    try:
        return load_config().manual_merge.max_total_mb
    except Exception:
        return None


def _configured_allowed_hosts() -> list[str]:
    """`server.allowed_hosts` from config, or `["*"]` if it cannot be read.

    Importing this module must not fail before `lifespan` gets the chance to
    report a bad config properly (and the test suite imports the app for
    route tests). Host checking is the safe default here: it is only a
    hardening layer, and a config that cannot be read will stop the server at
    startup anyway.
    """
    try:
        hosts = [h.strip() for h in load_config().server.allowed_hosts if h and h.strip()]
    except Exception:
        logger.warning(
            "Could not read server.allowed_hosts; Host header checking stays disabled",
            exc_info=True,
        )
        return ["*"]
    return hosts or ["*"]


def create_app(allowed_hosts: list[str] | None = None) -> FastAPI:
    """Build the application.

    `allowed_hosts` is injectable so a test can assert the TrustedHost wiring
    without writing a config file.
    """
    hosts = _configured_allowed_hosts() if allowed_hosts is None else list(allowed_hosts)

    app = FastAPI(title="AAM Merger V3", version="0.1.0", lifespan=lifespan)

    # Middleware order: the last added is the outermost. TrustedHost runs
    # before the body guard, which runs before the origin check.
    app.add_middleware(OriginGuardMiddleware)
    app.add_middleware(
        BodySizeLimitMiddleware,
        paths=("/manual/merge",),
        max_bytes=_manual_merge_request_bytes(),
        display_mb=_manual_merge_display_mb(),
    )
    if hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    # Mount static assets
    static_dir = Path(__file__).resolve().parent.parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Include routers
    app.include_router(sync_router)
    app.include_router(po_sets_router)
    app.include_router(manual_router)
    app.include_router(dashboard_router)

    @app.get("/health")
    def health() -> JSONResponse:
        # Liveness only. The old payload printed the input folder, database
        # path and pool name to any LAN host that asked — free reconnaissance
        # for no operational benefit (the operator has config.yaml).
        return JSONResponse({"status": "ok", "version": "0.1.0"})

    return app


app = create_app()
