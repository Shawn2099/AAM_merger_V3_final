"""add documents.parent_document_id + is_split_parent (multi-doc split)

A multi-document PDF is decomposed in Layer 1 into one child row per logical
document. Children point at their source file's row via `parent_document_id`;
the parent row is sterile (no po_no, no po_set) and is excluded from every
set-building query via `is_split_parent`.

Both columns are hierarchy vocabulary, not document-type vocabulary: no
Layer-2 code names a document type to use them. `parent_document_id` is
indexed because attach sweeps and the dashboard join through it.

See AAM_merger_V3_PLAN.md Step 1.

Revision ID: d4e5f6a7b8c9
Revises: c1a2b3d4e5f6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c1a2b3d4e5f6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Batch mode rebuilds `documents`, which has child rows in `line_items`.
    # With PRAGMA foreign_keys=ON (our connect default) the implicit delete
    # behind DROP TABLE fails, so enforcement is paused for the rebuild only.
    # All new values are NULL, so re-enabling cannot fail.
    op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("parent_document_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_documents_parent_document_id",
            "documents",
            ["parent_document_id"],
            ["id"],
        )
        batch_op.create_index("ix_documents_parent_document_id", ["parent_document_id"])
        batch_op.add_column(
            sa.Column(
                "is_split_parent",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            )
        )
    op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("fk_documents_parent_document_id", type_="foreignkey")
        batch_op.drop_index("ix_documents_parent_document_id")
        batch_op.drop_column("is_split_parent")
        batch_op.drop_column("parent_document_id")
    op.execute("PRAGMA foreign_keys=ON")
