"""add justification to audit_logs (v20.5 governance)

Revision ID: 3c8e1d7a5e42
Revises: 9d1f4a7c2b30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "3c8e1d7a5e42"
down_revision: str | None = "9d1f4a7c2b30"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("justification", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "justification")
