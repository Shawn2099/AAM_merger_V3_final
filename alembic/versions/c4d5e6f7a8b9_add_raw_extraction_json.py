"""add raw_extraction_json to documents

Revision ID: c4d5e6f7a8b9
Revises: 7a8e2b1c9d0f
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "7a8e2b1c9d0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("raw_extraction_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "raw_extraction_json")
