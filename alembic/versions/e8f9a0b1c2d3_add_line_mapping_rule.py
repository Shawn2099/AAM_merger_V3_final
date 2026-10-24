"""add po_sets.line_mapping_rule

Advisory: the line-number fallback label (x10/div10/add_rel/strip_rel, or
"DN <rule|as printed>, SI <rule|as printed>") the last reconcile used, NULL
when lines were compared as printed. Read by the UI; never decides a verdict.

Revision ID: e8f9a0b1c2d3
Revises: d5e6f7a8b9c0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("po_sets")}
    if "line_mapping_rule" in cols:
        return  # create_all DB built from current models already has it
    op.add_column("po_sets", sa.Column("line_mapping_rule", sa.Text(), nullable=True))


def downgrade() -> None:
    # Native DROP COLUMN: a batch rebuild of po_sets runs with foreign_keys=ON and either
    # fails on documents.po_set_id or nulls audit_log.po_set_id (ON DELETE SET NULL).
    op.drop_column("po_sets", "line_mapping_rule")
