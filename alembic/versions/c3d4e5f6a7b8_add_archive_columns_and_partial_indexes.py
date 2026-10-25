"""archive PO sets without deleting them

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-23 12:00:00.000000

The pilot's Delete button must stop destroying records. A quarantined set is
exported to `data/development/archived_sets/` and its rows are marked
`archived_at`, not deleted.

Keeping the rows changes two uniqueness rules that were written for hard
deletes:

* `uq_po_sets_open_po_no` must ignore archived sets, or an archived PO number
  can never form a new set.
* `documents.sha256_hash` must be unique only among ACTIVE documents, or the
  archived row permanently reserves its content hash and the same PDF can
  never be ingested again.

Both are SQLite partial unique indexes. The old indexes are dropped and
recreated with the archive-aware predicates; no table rebuild is needed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Archive metadata on the set.
    op.add_column(
        "po_sets",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("po_sets", sa.Column("archived_by", sa.Text(), nullable=True))
    op.add_column("po_sets", sa.Column("archive_reason", sa.Text(), nullable=True))
    op.add_column("po_sets", sa.Column("archive_path", sa.Text(), nullable=True))
    op.create_index("ix_po_sets_archived_at", "po_sets", ["archived_at"])

    # Archive marker on documents. This is what lets the content hash be
    # reused by a fresh active row after the old set is archived.
    op.add_column(
        "documents",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_documents_archived_at", "documents", ["archived_at"])

    # An archived set is no longer an "open" set for the one-set-per-PO rule.
    op.drop_index("uq_po_sets_open_po_no", table_name="po_sets")
    op.create_index(
        "uq_po_sets_open_po_no",
        "po_sets",
        ["po_no_normalized"],
        unique=True,
        sqlite_where=sa.text("status != 'merged' AND archived_at IS NULL"),
    )

    # Archived documents keep their hash for audit but do not reserve it.
    op.drop_index("ix_documents_sha256_hash", table_name="documents")
    op.create_index(
        "ix_documents_sha256_hash",
        "documents",
        ["sha256_hash"],
        unique=True,
        sqlite_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Restoring the global rules is impossible once an archived row duplicates
    # an active one. Fail with the offending values instead of a bare
    # IntegrityError halfway through the downgrade.
    po_dupes = bind.execute(
        sa.text(
            "SELECT po_no_normalized, COUNT(*) FROM po_sets "
            "WHERE status != 'merged' GROUP BY po_no_normalized HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if po_dupes:
        detail = ", ".join(f"{row[0]} ({row[1]} sets)" for row in po_dupes)
        raise RuntimeError(
            "Cannot restore the one-open-set-per-PO index while archived sets "
            f"share a PO with an active set: {detail}. Delete or renumber the "
            "archived rows first, then retry the downgrade."
        )

    hash_dupes = bind.execute(
        sa.text(
            "SELECT sha256_hash, COUNT(*) FROM documents GROUP BY sha256_hash HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if hash_dupes:
        detail = ", ".join(f"{str(row[0])[:12]}... ({row[1]} rows)" for row in hash_dupes)
        raise RuntimeError(
            "Cannot restore the global document hash index while archived and "
            f"active rows share content hashes: {detail}. Remove the duplicate "
            "rows first, then retry the downgrade."
        )

    op.drop_index("ix_documents_sha256_hash", table_name="documents")
    op.create_index(
        "ix_documents_sha256_hash",
        "documents",
        ["sha256_hash"],
        unique=True,
    )

    op.drop_index("uq_po_sets_open_po_no", table_name="po_sets")
    op.create_index(
        "uq_po_sets_open_po_no",
        "po_sets",
        ["po_no_normalized"],
        unique=True,
        sqlite_where=sa.text("status != 'merged'"),
    )

    op.drop_index("ix_documents_archived_at", table_name="documents")
    op.drop_index("ix_po_sets_archived_at", table_name="po_sets")
    op.drop_column("documents", "archived_at")
    op.drop_column("po_sets", "archive_path")
    op.drop_column("po_sets", "archive_reason")
    op.drop_column("po_sets", "archived_by")
    op.drop_column("po_sets", "archived_at")
