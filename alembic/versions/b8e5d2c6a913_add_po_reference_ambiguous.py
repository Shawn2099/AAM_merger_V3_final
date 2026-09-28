"""add documents.po_reference_ambiguous (multi-PO documents quarantine)

A single commercial invoice can list items for several purchase orders. The VLM
reports this so grouping quarantines the document instead of attaching it to a
guessed PO, which would silently drop the other POs' invoices.

Revision ID: b8e5d2c6a913
Revises: 7d4e9c1a3f52
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b8e5d2c6a913"
down_revision: str | None = "7d4e9c1a3f52"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "po_reference_ambiguous",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "po_reference_ambiguous")
