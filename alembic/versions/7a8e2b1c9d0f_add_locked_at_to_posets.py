"""add locked_at to posets

Revision ID: 7a8e2b1c9d0f
Revises: 510f6e0fcc4e
Create Date: 2026-08-15 22:58:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7a8e2b1c9d0f"
down_revision: str | Sequence[str] | None = "510f6e0fcc4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("po_sets") as batch_op:
        batch_op.add_column(
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True, default=None)
        )


def downgrade() -> None:
    with op.batch_alter_table("po_sets") as batch_op:
        batch_op.drop_column("locked_at")
