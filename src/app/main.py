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
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import URL, Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.manual_merger import router as manual_router
from app.api.routes.po_sets import router as po_sets_router
from app.api.routes.settings import router as settings_router
from app.api.routes.sync import router as sync_router
from app.api.templating import templates
from app.core.config import load_config
from app.core.http_errors import BodyTooLarge, error_response, wants_html
from app.core.limits import MAX_MANUAL_MERGE_REQUEST_BYTES, MAX_UPLOAD_BYTES

logger = logging.getLogger(__name__)

#: Methods that change state. GET/HEAD/OPTIONS are deliberately absent: the
#: dashboard polls with GET every 2 s and a browser sends no Origin for it.
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


#: Port a URL means when it names none. Taken from the Origin/Referer's own scheme on BOTH sides
#: of the comparison, so a TLS-terminating proxy that forwards the bare Host cannot break it.
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_parts(value: str) -> tuple[str, int | None, int | None] | None:
    """Return (hostname, explicit port, default port) of an Origin/Referer, or None if unusable.

    `urlsplit` tolerates a malformed authority but raises ValueError when `.port` is read
    (`http://pc:abc`, `:99999`); that is unusable, not a server error. The opaque `null`
    Origin has no hostname and is rejected too.
    """
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if host is None:
        return None
    return host, port, _DEFAULT_PORTS.get(parts.scheme.casefold())


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

        parts = _origin_parts(source)
        try:
            request_url = URL(scope=scope)
            request_host, request_port = request_url.hostname, request_url.port
        except ValueError:
            request_host, request_port = None, None
        if parts is None or request_host is None:
            same_origin = False
            source_host, source_port = (parts[0] if parts else None), None
        else:
            source_host, explicit_port, default_port = parts
            source_port = explicit_port or default_port
            same_origin = source_host.casefold() == request_host.casefold() and source_port == (
                request_port or default_port
            )
        if not same_origin:
            logger.warning(
                "Refused %s %s: Origin/Referer %r:%r does not match Host %r:%r",
                scope.get("method"),
                scope.get("path"),
                source_host,
                source_port,
                request_host,
                request_port,
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

    A path is matched by prefix (`paths`) or, for a route with an id in it, by an
    exact `path_pattern` (and `methods`).

    FastAPI parses `File(...)`/`Form(...)` parameters before the handler runs and turns
    ANY exception raised while it does so into HTTP 400. When the cap trips, whatever
    response the app then produces (other than its own 413) is dropped and the 413 below
    is sent instead, so a chunked body over the cap is a 413, not a 400.
    """

    def __init__(
        self,
        app: ASGIApp,
        paths: tuple[str, ...] = (),
        max_bytes: int = 0,
        display_mb: int | None = None,
        path_pattern: re.Pattern[str] | None = None,
        methods: tuple[str, ...] | None = None,
        heading: str = "We couldn't merge those files",
        back_href: str | Callable[[Scope], str] = "/manual/merger",
    ) -> None:
        self.app = app
        self.paths = paths
        self.max_bytes = max_bytes
        self.display_mb = display_mb
        self.path_pattern = path_pattern
        self.methods = methods
        self.heading = heading
        self.back_href = back_href

    def _applies(self, scope: Scope) -> bool:
        if scope.get("type") != "http":
            return False
        if self.methods is not None and scope.get("method", "") not in self.methods:
            return False
        path = scope.get("path", "")
        if self.path_pattern is not None and self.path_pattern.match(path):
            return True
        return any(path.startswith(prefix) for prefix in self.paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._applies(scope):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, send)
            return

        received = 0
        started = False
        tripped = False
        swallowing = False

        async def send_wrapper(message) -> None:
            nonlocal started, swallowing
            if message["type"] == "http.response.start":
                if tripped and message.get("status") != 413:
                    swallowing = True
                    return
                started = True
            elif swallowing:
                return
            await send(message)

        async def limited_receive():
            nonlocal received, tripped
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    tripped = True
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
            return
        if swallowing:
            await self._reject(scope, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        limit_mb = self.display_mb or self.max_bytes // (1024 * 1024)
        request = Request(scope)
        back = self.back_href if isinstance(self.back_href, str) else self.back_href(scope)
        response = error_response(
            request,
            413,
            "too_large",
            f"That upload is larger than the {limit_mb} MB limit. "
            "Please remove a file or use smaller ones.",
            heading=self.heading,
            back_href=back,
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

    # A service that stopped or crashed mid-sync leaves its sidecar behind, and every
    # operator action is refused while a sidecar looks alive. If nobody holds the sync lock,
    # take it and release it: that removes the orphan. (If another process holds it, a sync
    # really is running and nothing is touched.)
    try:
        from app.services.sync_lock import acquire_sync_lock, release_sync_lock

        orphan_check = acquire_sync_lock()
        if orphan_check is not None:
            release_sync_lock(orphan_check)
    except Exception:
        logger.warning("Could not clear a stale sync sidecar at startup", exc_info=True)

    # D5: poll the input folder inside this service when switched on in the config.
    from app.services.poller import start_if_enabled

    poller = start_if_enabled(cfg)
    app.state.poller = poller
    try:
        yield
    finally:
        if poller is not None:
            poller.stop()


_SET_UPLOAD_PATH = re.compile(r"^/po_sets/\d+/upload$")


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


def _styled_http_error(request: Request, exc: Exception):
    """A browser navigation to an unknown page gets the themed error page.

    API clients, HTMX requests and any non-404 status keep the JSON body that
    FastAPI returns by default. Synchronous on purpose: it does no I/O.
    """
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    if exc.status_code == 404 and wants_html(request) and "HX-Request" not in request.headers:
        message = (
            "There is no page at this address. Check the link, or go back to the dashboard."
            if exc.detail in (None, "Not Found")
            else str(exc.detail)
        )
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": 404, "heading": "Page not found", "message": message},
            status_code=404,
        )
    return JSONResponse(
        {"detail": exc.detail},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


def create_app(allowed_hosts: list[str] | None = None) -> FastAPI:
    """Build the application.

    `allowed_hosts` is injectable so a test can assert the TrustedHost wiring
    without writing a config file.
    """
    hosts = _configured_allowed_hosts() if allowed_hosts is None else list(allowed_hosts)

    app = FastAPI(title="AAM Merger V3", version="0.1.0", lifespan=lifespan)
    app.add_exception_handler(StarletteHTTPException, _styled_http_error)

    # Middleware order: the last added is the outermost. TrustedHost runs
    # before the body guard, which runs before the origin check.
    app.add_middleware(OriginGuardMiddleware)
    app.add_middleware(
        BodySizeLimitMiddleware,
        paths=("/manual/merge",),
        max_bytes=_manual_merge_request_bytes(),
        display_mb=_manual_merge_display_mb(),
    )
    # The set page's customs/shipping upload. The cap is the per-file cap plus form overhead (the
    # same allowance the manual merger uses); the in-route read cap stays as the second line.
    app.add_middleware(
        BodySizeLimitMiddleware,
        path_pattern=_SET_UPLOAD_PATH,
        methods=("POST",),
        max_bytes=MAX_UPLOAD_BYTES + 2 * 1024 * 1024,
        display_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
        heading="That upload didn't work",
        back_href=lambda scope: scope["path"].removesuffix("/upload") + "/view",
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
    app.include_router(settings_router)
    app.include_router(dashboard_router)

    @app.get("/health")
    def health() -> JSONResponse:
        # Liveness only. The old payload printed the input folder, database
        # path and pool name to any LAN host that asked — free reconnaissance
        # for no operational benefit (the operator has config.yaml).
        return JSONResponse({"status": "ok", "version": "0.1.0"})

    return app


app = create_app()
