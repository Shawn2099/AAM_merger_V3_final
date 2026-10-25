"""drop line_items.part_no and line_items.line_type

Both columns existed only to serve the retired v20.5 3-step matcher (SKU
rescue and non-GOODS row exclusion). That matcher is no longer part of the
product, and neither column was ever populated: the VLM was never asked for
either value, so both columns were permanently NULL / always the 'GOODS'
default. They are dropped rather than left in place so the schema states what
the product actually uses.

`part_no` also had an index (ix_line_items_part_no) which goes with it.

See AAM_merger_V3_PRODUCT.md for the rule these columns were removed from.

Revision ID: c1a2b3d4e5f6
Revises: b8e5d2c6a913
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c1a2b3d4e5f6"
down_revision: str | None = "b8e5d2c6a913"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("line_items") as batch_op:
        batch_op.drop_index("ix_line_items_part_no")
        batch_op.drop_column("part_no")
        batch_op.drop_column("line_type")


def downgrade() -> None:
    with op.batch_alter_table("line_items") as batch_op:
        batch_op.add_column(
            sa.Column(
                "line_type",
                sa.String(length=16),
                nullable=False,
                server_default="GOODS",
            )
        )
        batch_op.add_column(sa.Column("part_no", sa.Text(), nullable=True))
        batch_op.create_index("ix_line_items_part_no", ["part_no"])
