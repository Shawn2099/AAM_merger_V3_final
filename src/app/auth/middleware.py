"""Authentication middleware — default-deny for every route except a small allowlist.

Pure ASGI so it runs before routing and before request bodies are parsed. The
database lookup is handed to the threadpool: a synchronous SQLAlchemy call must
never block the event loop (SPEC §5.3).

Response shape depends on the caller:
* browser navigation -> 303 to `/login?next=...`
* HTMX              -> 401 + `HX-Redirect` (HTMX performs a full-page redirect)
* API/JSON          -> 401 JSON
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.context import reset_actor, set_actor
from app.auth.service import AuthUser, load_session
from app.core.config import load_config
from app.core.database import ensure_schema

logger = logging.getLogger(__name__)

#: Routes that must work without a session. Everything else is denied by default.
_PUBLIC_EXACT = frozenset({"/login", "/health", "/favicon.ico"})
_PUBLIC_PREFIXES = ("/static/",)

#: Pages a user with `must_change_password` may still reach.
_PASSWORD_CHANGE_PATHS = frozenset({"/account/password", "/logout", "/health"})


def _is_public(path: str) -> bool:
    return path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIXES)


def _next_target(scope: Scope) -> str:
    path = scope.get("path", "/") or "/"
    query = scope.get("query_string", b"")
    if query:
        path = f"{path}?{query.decode('latin-1')}"
    return quote(path, safe="")


def _resolve_user(raw_token: str | None, cfg) -> AuthUser | str | None:
    """Return the user, None for an invalid session, or "error" for a DB fault."""
    try:
        eng = ensure_schema(cfg)
        from sqlalchemy.orm import Session

        with Session(eng) as s:
            return load_session(s, raw_token, cfg=cfg)
    except Exception:
        logger.exception("Authentication lookup failed")
        return "error"


class AuthMiddleware:
    def __init__(self, app: ASGIApp, *, enabled: bool | None = None) -> None:
        self.app = app
        #: `None` follows config; an explicit value is used by tests that need
        #: the middleware on before a config file exists.
        self.enabled_override = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")
        if _is_public(path):
            await self.app(scope, receive, send)
            return

        cfg = load_config()
        enabled = cfg.auth.enabled if self.enabled_override is None else self.enabled_override
        if not enabled:
            # Lab/test mode: behave exactly as before authentication existed.
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        raw_token = None
        for chunk in headers.get("cookie", "").split(";"):
            name, _, value = chunk.strip().partition("=")
            if name == cfg.auth.cookie_name and value:
                raw_token = value
                break

        resolved = await run_in_threadpool(_resolve_user, raw_token, cfg)
        if resolved == "error":
            response = JSONResponse(
                {"detail": "Authentication is temporarily unavailable. Try again."},
                status_code=503,
            )
            await response(scope, receive, send)
            return
        if resolved is None:
            await self._deny(scope, headers, receive, send)
            return

        user: AuthUser = resolved
        if user.must_change_password and path not in _PASSWORD_CHANGE_PATHS:
            response = RedirectResponse("/account/password", status_code=303)
            await response(scope, receive, send)
            return

        scope.setdefault("state", {})
        scope["state"]["auth_enforced"] = True
        scope["state"]["user"] = user
        scope["state"]["csrf_token"] = user.csrf_token
        actor_token = set_actor(user.username)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_actor(actor_token)

    async def _deny(self, scope: Scope, headers: Headers, receive: Receive, send: Send) -> None:
        target = _next_target(scope)
        login_url = f"/login?next={target}"
        if headers.get("hx-request", "").casefold() == "true":
            response: Response = Response(
                status_code=401,
                headers={"HX-Redirect": login_url},
                media_type="text/plain",
            )
        else:
            accept = headers.get("accept", "")
            if "text/html" in accept:
                response = RedirectResponse(login_url, status_code=303)
            else:
                response = JSONResponse(
                    {"detail": "Authentication required", "login_url": "/login"},
                    status_code=401,
                )
        await response(scope, receive, send)


class RequireAdmin:
    """Dependency: the current user must be an admin.

    With authentication off (lab/tests) the check is a no-op so existing
    workflows keep working; production config enables auth.
    """

    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled_override = enabled

    async def __call__(self, request: Request) -> None:
        # No marker means authentication is off for this app (tests/lab): the
        # historical no-auth behaviour is preserved.
        if not getattr(request.state, "auth_enforced", False):
            return
        user = getattr(request.state, "user", None)
        if user is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="Authentication required")
        if not user.is_admin:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=403,
                detail="This action is restricted to an administrator.",
            )
