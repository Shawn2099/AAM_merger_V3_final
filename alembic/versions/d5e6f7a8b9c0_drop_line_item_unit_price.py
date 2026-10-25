"""drop line_items.unit_price

Price is not extracted, matched, or shown: quantity is the sole reconciliation
signal. The column was NOT NULL, so it is dropped rather than left to fail
every line-item insert. Old `documents.raw_extraction_json` keeps any printed
price strings; they are ignored on re-population.

A database built by `create_all` from the current models never had the column,
so the upgrade is a no-op there.

Revision ID: d5e6f7a8b9c0
Revises: a9b0c1d2e3f4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "a9b0c1d2e3f4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("line_items")}
    if "unit_price" not in cols:
        return  # create_all DB built from current models never had it
    with op.batch_alter_table("line_items") as batch_op:
        batch_op.drop_column("unit_price")


def downgrade() -> None:
    with op.batch_alter_table("line_items") as batch_op:
        batch_op.add_column(
            sa.Column("unit_price", sa.Integer(), nullable=False, server_default="0")
        )
