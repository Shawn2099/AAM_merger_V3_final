"""indexes, one-open-set-per-PO, and COMMERCIAL_INVOICE cleanup

Revision ID: a1b2c3d4e5f6
Revises: f7a8b9c0d1e2
Create Date: 2026-10-23 00:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # A value that is no longer in the model's enum makes the ORM raise
    # LookupError on load, so a historical row would poison every page that
    # touches it. UNKNOWN is the honest bucket for a type the product retired.
    bind.execute(
        sa.text("UPDATE documents SET doc_type = 'UNKNOWN' WHERE doc_type = 'COMMERCIAL_INVOICE'")
    )

    # The application refuses to mint a second open set, but two workers can
    # both pass that SELECT. The index is the only guarantee. Detect legacy
    # duplicates first so the failure names the PO numbers instead of raising a
    # bare IntegrityError halfway through a migration.
    dupes = bind.execute(
        sa.text(
            "SELECT po_no_normalized, COUNT(*) FROM po_sets "
            "WHERE status != 'merged' GROUP BY po_no_normalized HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if dupes:
        detail = ", ".join(f"{row[0]} ({row[1]} sets)" for row in dupes)
        raise RuntimeError(
            "Cannot create the one-open-set-per-PO index: duplicate open sets exist "
            f"for {detail}. Resolve or merge them, then re-run the migration."
        )

    op.create_index("ix_documents_po_set_id", "documents", ["po_set_id"])
    op.create_index("ix_line_items_document_id", "line_items", ["document_id"])
    op.create_index("ix_audit_log_po_set_id", "audit_log", ["po_set_id"])
    op.create_index(
        "uq_po_sets_open_po_no",
        "po_sets",
        ["po_no_normalized"],
        unique=True,
        sqlite_where=sa.text("status != 'merged'"),
    )


def downgrade() -> None:
    op.drop_index("uq_po_sets_open_po_no", table_name="po_sets")
    op.drop_index("ix_audit_log_po_set_id", table_name="audit_log")
    op.drop_index("ix_line_items_document_id", table_name="line_items")
    op.drop_index("ix_documents_po_set_id", table_name="documents")
    # The enum cleanup is intentionally not reversed: resurrecting a doc_type
    # the model cannot load would re-break the app.
