"""Admin-only account management.

The CLI remains the bootstrap path (and the only way to recover a locked-out
install); this page gives a signed-in administrator the same day-to-day
actions: create, reset, disable/enable, change role and revoke sessions.

Every route is double-gated: `AuthMiddleware` requires a session and
`RequireAdmin` requires the `admin` role. The settings router is included with
`csrf_protect`, so every POST also needs the session's synchronizer token.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.templating import templates as _templates
from app.auth.context import actor_source
from app.auth.middleware import RequireAdmin
from app.auth.service import (
    EVENT_ROLE_CHANGED,
    EVENT_SESSIONS_REVOKED,
    EVENT_USER_DISABLED,
    EVENT_USER_ENABLED,
    create_user,
    record_event,
    revoke_user_sessions,
    set_password,
)
from app.core.config import load_config
from app.core.database import ensure_schema
from app.models import AuthEvent, AuthSession, User, UserRole

router = APIRouter(tags=["users"], dependencies=[Depends(RequireAdmin())])


def _active_admin_count(s: Session, *, excluding: int | None = None) -> int:
    stmt = (
        select(func.count())
        .select_from(User)
        .where(User.role == UserRole.admin, User.is_active.is_(True))
    )
    if excluding is not None:
        stmt = stmt.where(User.id != excluding)
    return int(s.scalar(stmt) or 0)


def _user_rows(s: Session) -> list[dict]:
    users = s.scalars(select(User).order_by(User.username)).all()
    counts = {
        int(user_id): int(count)
        for user_id, count in s.execute(
            select(AuthSession.user_id, func.count()).group_by(AuthSession.user_id)
        ).all()
    }
    return [
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name or "",
            "role": user.role.value if hasattr(user.role, "value") else str(user.role),
            "is_active": bool(user.is_active),
            "must_change_password": bool(user.must_change_password),
            "last_login_at": user.last_login_at,
            "session_count": counts.get(user.id, 0),
        }
        for user in users
    ]


def _event_rows(s: Session) -> list[dict]:
    events = s.scalars(
        select(AuthEvent).order_by(AuthEvent.created_at.desc()).limit(25)
    ).all()
    return [
        {
            "event": event.event,
            "username": event.username or "—",
            "detail": event.detail or "",
            "created_at": event.created_at,
        }
        for event in events
    ]


def _render(
    request: Request,
    *,
    error: str | None = None,
    ok: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    cfg = load_config()
    with Session(ensure_schema(cfg)) as s:
        users = _user_rows(s)
        events = _event_rows(s)
    actor = getattr(request.state, "user", None)
    return _templates.TemplateResponse(
        request,
        "settings_users.html",
        {
            "request": request,
            "users": users,
            "events": events,
            "roles": [role.value for role in UserRole],
            "current_user_id": getattr(actor, "id", None),
            "error": error,
            "ok": ok,
        },
        status_code=status,
    )


def _redirect(message: str) -> RedirectResponse:
    return RedirectResponse(f"/settings/users?ok={quote(message)}", status_code=303)


def _get_user(s: Session, user_id: int) -> User:
    user = s.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/settings/users", response_class=HTMLResponse)
def users_page(request: Request) -> HTMLResponse:
    return _render(
        request,
        ok=request.query_params.get("ok"),
        error=request.query_params.get("error"),
    )


@router.post("/settings/users/create", response_class=HTMLResponse)
async def create_user_page(request: Request) -> HTMLResponse:
    form = await request.form()
    username = str(form.get("username", "")).strip()
    display_name = str(form.get("display_name", "")).strip() or None
    role = str(form.get("role", UserRole.operator.value))
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm_password", ""))
    must_change = form.get("must_change_password") == "on"

    if password != confirm:
        return _render(request, error="The two passwords do not match.", status=422)

    cfg = load_config()
    try:
        with Session(ensure_schema(cfg)) as s:
            user = create_user(
                s,
                username=username,
                password=password,
                role=role,
                display_name=display_name,
                must_change_password=must_change,
                cfg=cfg,
            )
            created = user.username
    except ValueError as exc:
        return _render(request, error=str(exc), status=422)
    return _redirect(f"Created account {created}.")


@router.post("/settings/users/{user_id}/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, user_id: int) -> HTMLResponse:
    form = await request.form()
    password = str(form.get("new_password", ""))
    confirm = str(form.get("confirm_password", ""))
    if password != confirm:
        return _render(request, error="The two passwords do not match.", status=422)

    cfg = load_config()
    with Session(ensure_schema(cfg)) as s:
        user = _get_user(s, user_id)
        try:
            set_password(s, user, password, cfg=cfg)
            user.must_change_password = True
            s.commit()
        except ValueError as exc:
            return _render(request, error=str(exc), status=422)
        revoked = revoke_user_sessions(s, user.id)
        record_event(
            s,
            EVENT_SESSIONS_REVOKED,
            user=user,
            detail=f"password_reset by {actor_source()}",
        )
        username = user.username
    return _redirect(
        f"Password reset for {username}; {revoked} session(s) revoked; "
        "the user must change it at next login."
    )


@router.post("/settings/users/{user_id}/disable", response_class=HTMLResponse)
async def disable_user_page(request: Request, user_id: int) -> HTMLResponse:
    cfg = load_config()
    with Session(ensure_schema(cfg)) as s:
        user = _get_user(s, user_id)
        if user.role == UserRole.admin and _active_admin_count(s, excluding=user.id) == 0:
            return _render(request, error="Refusing to disable the last active admin.", status=422)
        user.is_active = False
        s.commit()
        revoked = revoke_user_sessions(s, user.id)
        record_event(
            s,
            EVENT_USER_DISABLED,
            user=user,
            detail=f"by {actor_source()}",
        )
        username = user.username
    return _redirect(f"Disabled {username}; {revoked} session(s) revoked.")


@router.post("/settings/users/{user_id}/enable", response_class=HTMLResponse)
async def enable_user_page(request: Request, user_id: int) -> HTMLResponse:
    cfg = load_config()
    with Session(ensure_schema(cfg)) as s:
        user = _get_user(s, user_id)
        user.is_active = True
        s.commit()
        record_event(
            s,
            EVENT_USER_ENABLED,
            user=user,
            detail=f"by {actor_source()}",
        )
        username = user.username
    return _redirect(f"Enabled {username}.")


@router.post("/settings/users/{user_id}/role", response_class=HTMLResponse)
async def set_role_page(request: Request, user_id: int) -> HTMLResponse:
    form = await request.form()
    try:
        role = UserRole(str(form.get("role", "")))
    except ValueError:
        return _render(request, error="Choose a valid role.", status=422)

    cfg = load_config()
    with Session(ensure_schema(cfg)) as s:
        user = _get_user(s, user_id)
        if (
            user.role == UserRole.admin
            and role != UserRole.admin
            and _active_admin_count(s, excluding=user.id) == 0
        ):
            return _render(request, error="Refusing to demote the last active admin.", status=422)
        user.role = role
        s.commit()
        record_event(
            s,
            EVENT_ROLE_CHANGED,
            user=user,
            detail=f"role={role.value} by {actor_source()}",
        )
        username = user.username
    return _redirect(f"{username} is now {role.value}.")


@router.post("/settings/users/{user_id}/revoke-sessions", response_class=HTMLResponse)
async def revoke_sessions_page(request: Request, user_id: int) -> HTMLResponse:
    cfg = load_config()
    with Session(ensure_schema(cfg)) as s:
        user = _get_user(s, user_id)
        revoked = revoke_user_sessions(s, user.id)
        record_event(
            s,
            EVENT_SESSIONS_REVOKED,
            user=user,
            detail=f"by {actor_source()}",
        )
        username = user.username
    return _redirect(f"Revoked {revoked} session(s) for {username}.")
