"""Operator account CLI.

    python -m app.auth.cli create-user --username alice --role admin
    python -m app.auth.cli reset-password --username alice
    python -m app.auth.cli list-users
    python -m app.auth.cli disable-user --username alice
    python -m app.auth.cli set-role --username bob --role operator

Passwords are prompted with `getpass`, never passed on the command line and
never written to config. `--password-stdin` exists for an unattended installer:
one line on stdin, no echo, no shell history.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.service import (
    EVENT_ROLE_CHANGED,
    EVENT_SESSIONS_REVOKED,
    EVENT_USER_DISABLED,
    EVENT_USER_ENABLED,
    create_user,
    normalize_username,
    record_event,
    revoke_user_sessions,
    set_password,
)
from app.core.config import load_config
from app.core.database import ensure_schema
from app.models import User, UserRole


def _read_password(args) -> str:
    if args.password_stdin:
        value = sys.stdin.readline().rstrip("\r\n")
        if not value:
            raise SystemExit("[ERROR] --password-stdin received an empty line")
        return value
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("[ERROR] passwords do not match")
    return first


def _get_user(s: Session, username: str) -> User:
    try:
        normalized = normalize_username(username)
    except ValueError as exc:
        raise SystemExit(f"[ERROR] {exc}") from exc
    user = s.scalar(select(User).where(User.username == normalized))
    if user is None:
        raise SystemExit(f"[ERROR] no such user: {normalized}")
    return user


def _active_admin_count(s: Session, *, excluding: int | None = None) -> int:
    stmt = (
        select(func.count())
        .select_from(User)
        .where(User.role == UserRole.admin, User.is_active.is_(True))
    )
    if excluding is not None:
        stmt = stmt.where(User.id != excluding)
    return int(s.scalar(stmt) or 0)


def _cmd_create_user(s: Session, args, cfg) -> int:
    role = UserRole(args.role)
    user = create_user(
        s,
        username=args.username,
        password=_read_password(args),
        role=role,
        display_name=args.display_name,
        must_change_password=args.must_change_password,
        cfg=cfg,
    )
    print(f"[OK] created user {user.username!r} with role {user.role.value}")
    if user.must_change_password:
        print("     The user must change the password at first login.")
    return 0


def _cmd_reset_password(s: Session, args, cfg) -> int:
    user = _get_user(s, args.username)
    set_password(s, user, _read_password(args), cfg=cfg)
    user.must_change_password = True
    s.commit()
    revoked = revoke_user_sessions(s, user.id)
    record_event(s, EVENT_SESSIONS_REVOKED, user=user, detail="password_reset")
    print(f"[OK] password reset for {user.username!r}; {revoked} session(s) revoked")
    return 0


def _cmd_disable_user(s: Session, args) -> int:
    user = _get_user(s, args.username)
    if user.role == UserRole.admin and _active_admin_count(s, excluding=user.id) == 0:
        raise SystemExit("[ERROR] refusing to disable the last active admin")
    user.is_active = False
    s.commit()
    revoked = revoke_user_sessions(s, user.id)
    record_event(s, EVENT_USER_DISABLED, user=user)
    print(f"[OK] disabled {user.username!r}; {revoked} session(s) revoked")
    return 0


def _cmd_enable_user(s: Session, args) -> int:
    user = _get_user(s, args.username)
    user.is_active = True
    s.commit()
    record_event(s, EVENT_USER_ENABLED, user=user)
    print(f"[OK] enabled {user.username!r}")
    return 0


def _cmd_set_role(s: Session, args) -> int:
    user = _get_user(s, args.username)
    role = UserRole(args.role)
    if (
        user.role == UserRole.admin
        and role != UserRole.admin
        and _active_admin_count(s, excluding=user.id) == 0
    ):
        raise SystemExit("[ERROR] refusing to demote the last active admin")
    user.role = role
    s.commit()
    record_event(s, EVENT_ROLE_CHANGED, user=user, detail=f"role={role.value}")
    print(f"[OK] {user.username!r} is now {role.value}")
    return 0


def _cmd_revoke_sessions(s: Session, args) -> int:
    user = _get_user(s, args.username)
    revoked = revoke_user_sessions(s, user.id)
    record_event(s, EVENT_SESSIONS_REVOKED, user=user, detail="cli")
    print(f"[OK] revoked {revoked} session(s) for {user.username!r}")
    return 0


def _cmd_list_users(s: Session, _args) -> int:
    users = s.scalars(select(User).order_by(User.username)).all()
    if not users:
        print("(no users)")
        return 0
    print(f"{'USERNAME':<32} {'ROLE':<10} {'ACTIVE':<7} LAST LOGIN")
    for u in users:
        last = u.last_login_at.isoformat(sep=" ", timespec="minutes") if u.last_login_at else "-"
        print(f"{u.username:<32} {u.role.value:<10} {u.is_active!s:<7} {last}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.auth.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="create a named operator account")
    create.add_argument("--username", required=True)
    create.add_argument(
        "--role", choices=[r.value for r in UserRole], default=UserRole.operator.value
    )
    create.add_argument("--display-name", default=None)
    create.add_argument("--must-change-password", action="store_true")
    create.add_argument("--password-stdin", action="store_true")
    create.set_defaults(func=_cmd_create_user)

    reset = sub.add_parser("reset-password", help="set a new password and revoke sessions")
    reset.add_argument("--username", required=True)
    reset.add_argument("--password-stdin", action="store_true")
    reset.set_defaults(func=_cmd_reset_password)

    disable = sub.add_parser("disable-user", help="disable an account and revoke sessions")
    disable.add_argument("--username", required=True)
    disable.set_defaults(func=_cmd_disable_user)

    enable = sub.add_parser("enable-user", help="re-enable an account")
    enable.add_argument("--username", required=True)
    enable.set_defaults(func=_cmd_enable_user)

    role = sub.add_parser("set-role", help="change an account's role")
    role.add_argument("--username", required=True)
    role.add_argument("--role", choices=[r.value for r in UserRole], required=True)
    role.set_defaults(func=_cmd_set_role)

    revoke = sub.add_parser("revoke-sessions", help="sign an account out everywhere")
    revoke.add_argument("--username", required=True)
    revoke.set_defaults(func=_cmd_revoke_sessions)

    show = sub.add_parser("list-users", help="list accounts")
    show.set_defaults(func=_cmd_list_users)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()
    eng = ensure_schema(cfg)
    with Session(eng) as s:
        if args.command == "list-users":
            return args.func(s, args)
        if args.command == "disable-user":
            return args.func(s, args)
        if args.command == "enable-user":
            return args.func(s, args)
        if args.command == "set-role":
            return args.func(s, args)
        if args.command == "revoke-sessions":
            return args.func(s, args)
        return args.func(s, args, cfg)


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
