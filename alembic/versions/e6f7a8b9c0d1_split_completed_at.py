"""add documents.split_completed_at (split crash-recovery authority)

PLAN Rev 3 §6.7: set when child files have been cut; absent means children must
be re-derived from the parent SHA, never trusted to exist.

Revision ID: e6f7a8b9c0d1
Revises: d4e5f6a7b8c9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("split_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("split_completed_at")
    op.execute("PRAGMA foreign_keys=ON")
