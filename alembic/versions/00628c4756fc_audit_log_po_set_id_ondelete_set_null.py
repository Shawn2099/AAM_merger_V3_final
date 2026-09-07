"""audit_log po_set_id ondelete SET NULL

Revision ID: 00628c4756fc
Revises: c4d5e6f7a8b9
Create Date: 2026-09-05 23:25:21.008341
"""

from typing import Sequence, Union
from alembic import op


revision: str = "00628c4756fc"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite cannot ALTER a FK constraint: rebuild audit_log with identical
    # column DDL plus ON DELETE SET NULL (W-14). Column types match the
    # create_all DDL exactly (verified via sqlite_master on a fresh DB).
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        op.execute("ALTER TABLE audit_log RENAME TO audit_log_old")
        op.execute(
            """CREATE TABLE audit_log (
                id INTEGER NOT NULL,
                po_set_id INTEGER,
                action VARCHAR(20) NOT NULL,
                detail TEXT,
                timestamp DATETIME NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(po_set_id) REFERENCES po_sets (id) ON DELETE SET NULL
            )"""
        )
        op.execute(
            "INSERT INTO audit_log (id, po_set_id, action, detail, timestamp, source)"
            " SELECT id, po_set_id, action, detail, timestamp, source FROM audit_log_old"
        )
        op.execute("DROP TABLE audit_log_old")
    finally:
        op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        op.execute("ALTER TABLE audit_log RENAME TO audit_log_old")
        op.execute(
            """CREATE TABLE audit_log (
                id INTEGER NOT NULL,
                po_set_id INTEGER,
                action VARCHAR(20) NOT NULL,
                detail TEXT,
                timestamp DATETIME NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(po_set_id) REFERENCES po_sets (id)
            )"""
        )
        op.execute(
            "INSERT INTO audit_log (id, po_set_id, action, detail, timestamp, source)"
            " SELECT id, po_set_id, action, detail, timestamp, source FROM audit_log_old"
        )
        op.execute("DROP TABLE audit_log_old")
    finally:
        op.execute("PRAGMA foreign_keys=ON")
