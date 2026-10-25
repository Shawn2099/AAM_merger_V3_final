"""add po_sets.description_match_count

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-23 09:30:00.000000

The count of vendor lines the engine attached by the DESCRIPTION fallback
rather than by a printed line number. It is advisory: the merge outcome does
not change. The UI uses it for an amber "Merged - review" tag.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "po_sets",
        sa.Column(
            "description_match_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("po_sets", "description_match_count")
