"""add po_sets.customs_append_pending (a merged set owes its packet a customs rebuild)

When the customs requirement is on for an already-merged set and the packet
rebuild fails, the failure used to be logged and forgotten. The flag persists
the debt so the next sync retries it and the UI can say so.

Revision ID: e7f8a9b0c1d2
Revises: c3d4e5f6a7b8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "po_sets",
        sa.Column(
            "customs_append_pending",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("po_sets", "customs_append_pending")
