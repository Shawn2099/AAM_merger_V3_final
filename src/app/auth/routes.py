"""Login, logout and password-change routes.

These are the only HTML routes that do not sit behind the authentication
middleware (login must be reachable anonymously). Logout and password change
still require a session and a CSRF token.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.templating import templates
from app.auth.csrf import csrf_protect
from app.auth.passwords import verify_password
from app.auth.service import (
    EVENT_LOGIN_FAILURE,
    EVENT_LOGIN_SUCCESS,
    EVENT_LOGIN_THROTTLED,
    EVENT_LOGOUT,
    EVENT_PASSWORD_CHANGED,
    authenticate,
    cleanup,
    create_session,
    normalize_username,
    record_event,
    revoke_session,
    revoke_user_sessions,
    set_password,
    throttle_retry_after,
)
from app.core.config import load_config
from app.core.database import ensure_schema
from app.models import AuthSession, User

router = APIRouter(tags=["auth"])

_GENERIC_FAILURE = "Invalid username or password."
_THROTTLED = "Too many failed attempts. Wait a few minutes and try again."


def safe_next(value: str | None) -> str:
    """Only same-site absolute paths are allowed as a post-login destination.

    `//evil.example`, `https://evil.example` and backslash tricks are open
    redirects; they fall back to the dashboard.
    """
    if not value:
        return "/dashboard"
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/dashboard"
    return value


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _cookie_secure(request: Request, cfg) -> bool:
    mode = cfg.auth.cookie_secure
    if mode == "always":
        return True
    if mode == "never":
        return False
    return request.url.scheme == "https"


def _set_session_cookie(response, raw_token: str, request: Request, cfg, remember: bool) -> None:
    max_age = None
    if remember and int(cfg.auth.remember_me_days) > 0:
        max_age = int(cfg.auth.remember_me_days) * 86400
    response.set_cookie(
        key=cfg.auth.cookie_name,
        value=raw_token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request, cfg),
        path="/",
    )


def _clear_session_cookie(response, request: Request, cfg) -> None:
    response.delete_cookie(
        key=cfg.auth.cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request, cfg),
    )


def _login_page(request: Request, cfg, next_url: str, error: str | None, status: int, headers=None):
    no_accounts = False
    try:
        with Session(ensure_schema(cfg)) as s:
            no_accounts = int(s.scalar(select(func.count()).select_from(User)) or 0) == 0
    except Exception:
        no_accounts = False
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "next": safe_next(next_url),
            "error": error,
            "username": "",
            "auth_enabled": cfg.auth.enabled,
            "no_accounts": no_accounts,
        },
        status_code=status,
        headers=headers or {"Cache-Control": "no-store"},
    )


@router.get("/login", include_in_schema=False)
def login_page(request: Request, next: str = "/dashboard"):
    cfg = load_config()
    if not cfg.auth.enabled:
        return RedirectResponse("/dashboard", status_code=303)
    return _login_page(request, cfg, next, None, 200)


@router.post("/login", include_in_schema=False)
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    remember: str | None = Form(None),
    next: str = Form("/dashboard"),
):
    cfg = load_config()
    if not cfg.auth.enabled:
        return RedirectResponse("/dashboard", status_code=303)

    eng = ensure_schema(cfg)
    ip = _client_ip(request)
    agent = _user_agent(request)
    try:
        normalized = normalize_username(username)
    except ValueError:
        normalized = ""

    with Session(eng) as s:
        retry_after = throttle_retry_after(s, normalized, ip, cfg=cfg) if normalized else 0
        if retry_after:
            record_event(
                s,
                EVENT_LOGIN_THROTTLED,
                username=normalized or None,
                ip_address=ip,
                user_agent=agent,
            )
            return _login_page(
                request,
                cfg,
                next,
                _THROTTLED,
                429,
                {"Cache-Control": "no-store", "Retry-After": str(retry_after)},
            )

        user = authenticate(s, normalized, password) if normalized else None
        if user is None:
            record_event(
                s,
                EVENT_LOGIN_FAILURE,
                username=normalized or None,
                ip_address=ip,
                user_agent=agent,
            )
            return _login_page(request, cfg, next, _GENERIC_FAILURE, 401)

        raw_token, _csrf = create_session(
            s,
            user,
            cfg=cfg,
            ip_address=ip,
            user_agent=agent,
            remember=bool(remember),
        )
        record_event(s, EVENT_LOGIN_SUCCESS, user=user, ip_address=ip, user_agent=agent)
        cleanup(s, cfg=cfg)

    response = RedirectResponse(safe_next(next), status_code=303)
    _set_session_cookie(response, raw_token, request, cfg, bool(remember))
    return response


@router.post("/logout", dependencies=[Depends(csrf_protect)], include_in_schema=False)
def logout(request: Request):
    cfg = load_config()
    raw_token = request.cookies.get(cfg.auth.cookie_name)
    user = getattr(request.state, "user", None)
    with Session(ensure_schema(cfg)) as s:
        if user is not None:
            record_event(
                s,
                EVENT_LOGOUT,
                username=user.username,
                ip_address=_client_ip(request),
                user_agent=_user_agent(request),
            )
        revoke_session(s, raw_token)
    response = RedirectResponse("/login", status_code=303)
    _clear_session_cookie(response, request, cfg)
    return response


def _password_page(request: Request, error: str | None = None, status: int = 200):
    return templates.TemplateResponse(
        request,
        "account_password.html",
        {"error": error, "minimum": load_config().auth.password_min_length},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/account/password", include_in_schema=False)
def password_page(request: Request):
    return _password_page(request)


@router.post("/account/password", dependencies=[Depends(csrf_protect)], include_in_schema=False)
def password_change(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    cfg = load_config()
    current = getattr(request.state, "user", None)
    if current is None:
        return RedirectResponse("/login", status_code=303)
    if new_password != confirm_password:
        return _password_page(request, "The two new passwords do not match.", 400)

    with Session(ensure_schema(cfg)) as s:
        user = s.get(User, current.id)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ok, _replacement = verify_password(user.password_hash, current_password)
        if not ok:
            record_event(
                s,
                EVENT_LOGIN_FAILURE,
                user=user,
                ip_address=_client_ip(request),
                user_agent=_user_agent(request),
                detail="password_change_bad_current",
            )
            return _password_page(request, "The current password is incorrect.", 400)
        try:
            set_password(s, user, new_password, cfg=cfg)
        except ValueError as exc:
            return _password_page(request, str(exc), 400)

        # Keep this browser signed in, but sign out every other session: a
        # password change is how an operator evicts a stolen session.
        revoke_user_sessions(s, user.id, except_token_hash=current.session_token_hash)
        session_row = s.scalar(
            select(AuthSession).where(AuthSession.token_hash == current.session_token_hash)
        )
        if session_row is not None:
            session_row.csrf_token = secrets.token_urlsafe(32)
            s.commit()
        record_event(s, EVENT_PASSWORD_CHANGED, user=user, ip_address=_client_ip(request))

    return RedirectResponse("/dashboard", status_code=303)
