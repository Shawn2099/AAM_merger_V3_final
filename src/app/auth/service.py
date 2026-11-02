"""Authentication service — users, opaque DB-backed sessions, throttling.

All functions are synchronous and take a SQLAlchemy `Session`; FastAPI's
threadpool and the CLI both call them. No ORM object escapes to the web layer:
`AuthUser` is a small immutable value.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.passwords import dummy_hash, hash_password, verify_password
from app.models import AuthEvent, AuthSession, User, UserRole

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._@+-]{1,63}$")

#: Events written to `auth_events`; a closed set so reporting stays honest.
EVENT_LOGIN_SUCCESS = "login_success"
EVENT_LOGIN_FAILURE = "login_failure"
EVENT_LOGIN_THROTTLED = "login_throttled"
EVENT_LOGOUT = "logout"
EVENT_PASSWORD_CHANGED = "password_changed"
EVENT_USER_CREATED = "user_created"
EVENT_USER_DISABLED = "user_disabled"
EVENT_USER_ENABLED = "user_enabled"
EVENT_ROLE_CHANGED = "role_changed"
EVENT_SESSIONS_REVOKED = "sessions_revoked"


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC (how they were written)."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def normalize_username(value: str) -> str:
    """NFKC + casefold, so `Alice`, `alice` and a full-width variant are one account."""
    normalized = unicodedata.normalize("NFKC", value or "").strip().casefold()
    if not _USERNAME_RE.match(normalized):
        raise ValueError(
            "username must be 2-64 characters: letters, digits, dot, underscore, "
            "at-sign, plus or hyphen, starting with a letter or digit"
        )
    return normalized


def validate_password(password: str, *, username: str | None = None, cfg=None) -> None:
    """NIST-style length policy. No forced character classes; no composition rules."""
    minimum = int(getattr(getattr(cfg, "auth", None), "password_min_length", 12) or 12)
    if len(password or "") < minimum:
        raise ValueError(f"password must be at least {minimum} characters")
    if len(password) > 1024:
        raise ValueError("password must be at most 1024 characters")
    if username and password.casefold() == username.casefold():
        raise ValueError("password must not be the username")


@dataclass(frozen=True)
class AuthUser:
    id: int
    username: str
    display_name: str | None
    role: str
    must_change_password: bool
    csrf_token: str
    session_token_hash: str

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin.value


def create_user(
    s: Session,
    *,
    username: str,
    password: str,
    role: UserRole | str = UserRole.operator,
    display_name: str | None = None,
    must_change_password: bool = False,
    cfg=None,
) -> User:
    normalized = normalize_username(username)
    validate_password(password, username=normalized, cfg=cfg)
    if s.scalar(select(func.count()).select_from(User)) == 0:
        # The first account is always an admin: a fresh install must not be
        # locked out of Settings by a typo in the role flag.
        role = UserRole.admin
    role_value = UserRole(role) if not isinstance(role, UserRole) else role
    if s.scalar(select(User.id).where(User.username == normalized)) is not None:
        raise ValueError(f"user {normalized!r} already exists")
    user = User(
        username=normalized,
        display_name=display_name or None,
        password_hash=hash_password(password),
        role=role_value,
        is_active=True,
        must_change_password=must_change_password,
        password_changed_at=utcnow(),
    )
    s.add(user)
    s.commit()
    s.refresh(user)
    record_event(s, EVENT_USER_CREATED, user=user, detail=f"role={role_value.value}")
    return user


def set_password(s: Session, user: User, new_password: str, *, cfg=None) -> None:
    validate_password(new_password, username=user.username, cfg=cfg)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.password_changed_at = utcnow()
    s.commit()


def authenticate(s: Session, username: str, password: str) -> User | None:
    """Verify credentials. Unknown/inactive users still pay one Argon2 verify.

    The return value is deliberately identical for "no such user" and "wrong
    password"; the route shows one generic message for both.
    """
    try:
        normalized = normalize_username(username)
    except ValueError:
        verify_password(dummy_hash(), password)
        return None
    user = s.scalar(select(User).where(User.username == normalized))
    if user is None or not user.is_active:
        verify_password(dummy_hash(), password)
        return None
    ok, replacement = verify_password(user.password_hash, password)
    if not ok:
        return None
    if replacement:
        user.password_hash = replacement
    user.last_login_at = utcnow()
    s.commit()
    s.refresh(user)
    return user


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_session(
    s: Session,
    user: User,
    *,
    cfg,
    ip_address: str | None = None,
    user_agent: str | None = None,
    remember: bool = False,
) -> tuple[str, str]:
    """Create a session; returns (raw cookie token, CSRF token).

    Only the SHA-256 of the cookie token is stored. The CSRF token is stored in
    plain text because the server must compare it on every write; it is scoped
    to one session and rotated at login/password change.
    """
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = utcnow()
    idle = timedelta(minutes=int(cfg.auth.session_idle_minutes))
    absolute = now + timedelta(hours=int(cfg.auth.session_absolute_hours))
    if remember and int(cfg.auth.remember_me_days) > 0:
        absolute = max(absolute, now + timedelta(days=int(cfg.auth.remember_me_days)))
    s.add(
        AuthSession(
            token_hash=_token_hash(raw_token),
            user_id=user.id,
            csrf_token=csrf_token,
            created_at=now,
            last_seen_at=now,
            expires_at=min(now + idle, absolute),
            absolute_expires_at=absolute,
            ip_address=ip_address,
            user_agent=(user_agent or "")[:1000] or None,
        )
    )
    s.commit()
    return raw_token, csrf_token


def load_session(s: Session, raw_token: str | None, *, cfg) -> AuthUser | None:
    """Resolve a cookie token to a live user, or None. Never raises for a bad token."""
    if not raw_token:
        return None
    row = s.execute(
        select(AuthSession, User)
        .join(User, AuthSession.user_id == User.id)
        .where(AuthSession.token_hash == _token_hash(raw_token))
    ).first()
    if row is None:
        return None
    session, user = row
    now = utcnow()
    expires_at = as_utc(session.expires_at)
    absolute_expires_at = as_utc(session.absolute_expires_at)
    expired = (
        session.revoked_at is not None
        or not user.is_active
        or expires_at is None
        or expires_at <= now
        or absolute_expires_at is None
        or absolute_expires_at <= now
    )
    if expired:
        s.delete(session)
        s.commit()
        return None

    # Touch at most once a minute: an authenticated page polls every 2 s, and a
    # write per poll would compete with extraction on SQLite's single writer.
    last_seen = as_utc(session.last_seen_at)
    if last_seen is None or (now - last_seen) >= timedelta(seconds=60):
        try:
            session.last_seen_at = now
            session.expires_at = min(
                now + timedelta(minutes=int(cfg.auth.session_idle_minutes)),
                absolute_expires_at,
            )
            s.commit()
        except Exception:
            s.rollback()

    return AuthUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=(user.role.value if hasattr(user.role, "value") else str(user.role)),
        must_change_password=bool(user.must_change_password),
        csrf_token=session.csrf_token,
        session_token_hash=session.token_hash,
    )


def revoke_session(s: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    s.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(raw_token)))
    s.commit()


def revoke_user_sessions(s: Session, user_id: int, *, except_token_hash: str | None = None) -> int:
    stmt = delete(AuthSession).where(AuthSession.user_id == user_id)
    if except_token_hash:
        stmt = stmt.where(AuthSession.token_hash != except_token_hash)
    result = s.execute(stmt)
    s.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def record_event(
    s: Session,
    event: str,
    *,
    user: User | None = None,
    username: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    detail: str | None = None,
) -> None:
    s.add(
        AuthEvent(
            user_id=user.id if user is not None else None,
            username=(username or (user.username if user is not None else None)),
            event=event,
            ip_address=ip_address,
            user_agent=(user_agent or "")[:1000] or None,
            detail=detail,
        )
    )
    s.commit()


def _retry_after_for(
    s: Session,
    *,
    column,
    value: str | None,
    cfg,
    limit: int,
) -> int:
    """Seconds until the oldest failure leaves the window; 0 when under the limit."""
    if not value or limit <= 0:
        return 0
    window = timedelta(minutes=int(cfg.auth.login_window_minutes))
    start = utcnow() - window
    count = int(
        s.scalar(
            select(func.count())
            .select_from(AuthEvent)
            .where(
                AuthEvent.event == EVENT_LOGIN_FAILURE,
                column == value,
                AuthEvent.created_at >= start,
            )
        )
        or 0
    )
    if count < limit:
        return 0
    oldest = s.scalar(
        select(func.min(AuthEvent.created_at)).where(
            AuthEvent.event == EVENT_LOGIN_FAILURE,
            column == value,
            AuthEvent.created_at >= start,
        )
    )
    oldest_utc = as_utc(oldest)
    if oldest_utc is None:
        return 0
    remaining = int((oldest_utc + window - utcnow()).total_seconds())
    return max(1, remaining)


def throttle_retry_after(s: Session, username: str, ip_address: str | None, *, cfg) -> int:
    """0 when a login may be attempted, else Retry-After seconds.

    Two independent buckets: per attempted username (credential stuffing on
    one account) and per client IP (spraying many usernames). Both are in the
    database, so multiple uvicorn workers share them.
    """
    by_user = _retry_after_for(
        s,
        column=AuthEvent.username,
        value=username,
        cfg=cfg,
        limit=int(cfg.auth.login_max_failures),
    )
    by_ip = _retry_after_for(
        s,
        column=AuthEvent.ip_address,
        value=ip_address,
        cfg=cfg,
        limit=int(cfg.auth.login_max_failures_per_ip),
    )
    return max(by_user, by_ip)


def cleanup(s: Session, *, cfg) -> None:
    """Delete expired sessions and old auth events. Called on login and startup.

    Authentication must not depend on a background scheduler, so this is
    opportunistic. It is cheap (two indexed DELETEs).
    """
    now = utcnow()
    s.execute(delete(AuthSession).where(AuthSession.expires_at < now - timedelta(days=1)))
    cutoff = now - timedelta(days=int(cfg.auth.auth_event_retention_days))
    s.execute(delete(AuthEvent).where(AuthEvent.created_at < cutoff))
    s.commit()


def bootstrap_from_env(cfg) -> None:
    """Create the first admin from environment variables, only when no user exists.

    Intended for an unattended install. The password is never logged; the
    account is forced to change it at first login. If any user already exists
    the variables are ignored (and a warning is logged) — a stale environment
    variable must not become a hidden backdoor.
    """
    if not getattr(cfg.auth, "bootstrap_from_env", False):
        return
    username = os.getenv("AAM_BOOTSTRAP_ADMIN_USERNAME", "").strip()
    password = os.getenv("AAM_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not username or not password:
        return

    from sqlalchemy.orm import Session as _Session

    from app.core.database import ensure_schema

    eng = ensure_schema(cfg)
    with _Session(eng) as s:
        if s.scalar(select(func.count()).select_from(User)):
            import logging

            logging.getLogger(__name__).warning(
                "AAM_BOOTSTRAP_ADMIN_* is set but users already exist; ignoring it. "
                "Remove the variable to silence this warning."
            )
            return
        create_user(
            s,
            username=username,
            password=password,
            role=UserRole.admin,
            must_change_password=True,
            cfg=cfg,
        )
        import logging

        logging.getLogger(__name__).warning(
            "Created bootstrap admin %r. Log in and change the password, then remove "
            "AAM_BOOTSTRAP_ADMIN_USERNAME/AAM_BOOTSTRAP_ADMIN_PASSWORD from the environment.",
            username,
        )
