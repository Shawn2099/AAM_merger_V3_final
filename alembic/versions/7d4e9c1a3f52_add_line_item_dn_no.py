"""add line_items.dn_no (per-line delivery note reference)

Some vendors print the delivery-note number against each individual line
rather than once in the document header. Nullable; overrides documents.dn_no
for that row when present. Used for attachment reverification only.

Revision ID: 7d4e9c1a3f52
Revises: 5b9c2e4f7a18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "7d4e9c1a3f52"
down_revision: str | None = "5b9c2e4f7a18"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("line_items", sa.Column("dn_no", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("line_items", "dn_no")
