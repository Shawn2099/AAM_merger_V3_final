"""add part_no and line_type to line_items (v20.5 Step 3 SKU rescue + non-GOODS skip)

Revision ID: 9d1f4a7c2b30
Revises: 00628c4756fc
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "9d1f4a7c2b30"
down_revision: str | None = "00628c4756fc"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # SQLite cannot ADD CONSTRAINT, so line_type is a plain String with a
    # server default rather than a CHECK — the app validates the value set.
    op.add_column(
        "line_items",
        sa.Column("part_no", sa.Text(), nullable=True),
    )
    op.add_column(
        "line_items",
        sa.Column(
            "line_type",
            sa.String(length=16),
            nullable=False,
            server_default="GOODS",
        ),
    )
    op.create_index("ix_line_items_part_no", "line_items", ["part_no"])


def downgrade() -> None:
    op.drop_index("ix_line_items_part_no", table_name="line_items")
    op.drop_column("line_items", "line_type")
    op.drop_column("line_items", "part_no")
