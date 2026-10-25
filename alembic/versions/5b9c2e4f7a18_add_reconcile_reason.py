"""add reconcile_reason to po_sets (dashboard can explain *why* a set is stuck)

Revision ID: 5b9c2e4f7a18
Revises: 3c8e1d7a5e42
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "5b9c2e4f7a18"
down_revision: str | None = "3c8e1d7a5e42"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("po_sets", sa.Column("reconcile_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("po_sets", "reconcile_reason")
