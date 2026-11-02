"""add users, auth_sessions and auth_events

Handover hardening (2026-11-02): named operator accounts, opaque DB-backed
sessions and the authentication event log. Opaque sessions (not signed
cookies) keep logout/revocation working and are portable to Postgres when the
service moves off a single host.

Revision ID: f1a2b3c4d5e6
Revises: e8f9a0b1c2d3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e8f9a0b1c2d3"
branch_labels: str | None = None
depends_on: str | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()
    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("display_name", sa.String(length=128), nullable=True),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column(
                "role",
                sa.Enum("admin", "operator", name="userrole"),
                nullable=False,
            ),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("username", name="uq_users_username"),
        )

    if "auth_sessions" not in _tables():
        op.create_table(
            "auth_sessions",
            sa.Column("token_hash", sa.String(length=64), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("csrf_token", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.Column("user_agent", sa.Text(), nullable=True),
        )
        op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
        op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    if "auth_events" not in _tables():
        op.create_table(
            "auth_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("username", sa.String(length=64), nullable=True),
            sa.Column("event", sa.String(length=32), nullable=False),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.Column("user_agent", sa.Text(), nullable=True),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_auth_events_created_at", "auth_events", ["created_at"])
        op.create_index("ix_auth_events_username_event", "auth_events", ["username", "event"])
        op.create_index("ix_auth_events_ip_event", "auth_events", ["ip_address", "event"])


def downgrade() -> None:
    existing = _tables()
    if "auth_events" in existing:
        op.drop_table("auth_events")
    if "auth_sessions" in existing:
        op.drop_table("auth_sessions")
    if "users" in existing:
        op.drop_table("users")
