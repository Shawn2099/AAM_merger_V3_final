"""add po_sets.po_no_letters (PO key is the digits; letters are a safety check)

`po_sets.po_no_normalized` now holds only the digits of the PO number. The
letters of the anchoring PO are kept here so a document whose digits match but
whose letters differ (TOGA-PO-25-26-163 vs ARM-PO-25-26-163) is held, not
grouped. No data migration: the database is created fresh for this key.

Revision ID: a9b0c1d2e3f4
Revises: e7f8a9b0c1d2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("po_sets", sa.Column("po_no_letters", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("po_sets", "po_no_letters")
