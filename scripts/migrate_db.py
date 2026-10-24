"""Bring the configured SQLite database to the current schema — safely.

Why this exists
---------------
`alembic upgrade head` is only correct for an **empty** database. The live
database was created by `Base.metadata.create_all`, so it already has every
table but has no `alembic_version` row. Alembic then treats it as revision
`base` and the first migration fails with "table documents already exists"
(verified on a copy of the live DB, 2026-10-23). `create_all` also cannot add
a column to an existing table, so an un-migrated DB makes the current code
crash on `no such column: documents.po_no_source`.

This script detects what is already there and does the right thing:

* no database / no tables      -> `alembic upgrade head`
* tables but no version row    -> back up, stamp the newest schema revision the
                                  columns prove, then `upgrade head`
* a version row                -> `alembic upgrade head`

The stamp is derived from structural markers, never guessed from a date:

    documents.split_completed_at      -> e6f7a8b9c0d1
    documents.po_no_source            -> f7a8b9c0d1e2
    unique index uq_po_sets_open_po_no-> a1b2c3d4e5f6
    archive columns + both partial indexes -> c3d4e5f6a7b8
    po_sets.customs_append_pending    -> e7f8a9b0c1d2
    po_sets.po_no_letters             -> a9b0c1d2e3f4
    line_items.unit_price absent      -> d5e6f7a8b9c0

Usage:
    python scripts/migrate_db.py [--no-backup]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

#: Structural markers, oldest first. A schema that has the marker is *at least*
#: at that revision. Keep in step with alembic/versions when adding a column.
#: Values are keys in the set `detect_stamp` builds: `table.column` for real
#: columns, an index name, or one of the synthetic keys below.
_MARKERS: list[tuple[str, str]] = [
    ("po_sets.locked_at", "7a8e2b1c9d0f"),
    ("documents.raw_extraction_json", "c4d5e6f7a8b9"),
    ("audit_fk_set_null", "00628c4756fc"),
    ("documents.part_no", "9d1f4a7c2b30"),
    ("audit_log.justification", "3c8e1d7a5e42"),
    ("po_sets.reconcile_reason", "5b9c2e4f7a18"),
    ("line_items.dn_no", "7d4e9c1a3f52"),
    ("documents.po_reference_ambiguous", "b8e5d2c6a913"),
    # c1a2 dropped part_no/line_type: an ABSENCE, so it is a synthetic key.
    ("part_no_dropped", "c1a2b3d4e5f6"),
    ("documents.parent_document_id", "d4e5f6a7b8c9"),
    ("documents.split_completed_at", "e6f7a8b9c0d1"),
    ("documents.po_no_source", "f7a8b9c0d1e2"),
    ("uq_po_sets_open_po_no", "a1b2c3d4e5f6"),
    ("po_sets.description_match_count", "b2c3d4e5f6a7"),
    # The archive revision replaces two indexes. Index NAMES are reused, so
    # neither name proves the new predicate. This synthetic marker requires
    # both archive columns and both archive-aware predicates.
    ("archive_schema", "c3d4e5f6a7b8"),
    ("po_sets.customs_append_pending", "e7f8a9b0c1d2"),
    ("po_sets.po_no_letters", "a9b0c1d2e3f4"),
    # d5e6 dropped line_items.unit_price: an ABSENCE, so a synthetic key.
    ("unit_price_dropped", "d5e6f7a8b9c0"),
]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"pragma table_info({table})")}
    except sqlite3.Error:
        return set()


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "select name from sqlite_master where type='table' and name=?", (table,)
    ).fetchone()
    return row is not None


def _has_version(conn: sqlite3.Connection) -> bool:
    """True only when a version ROW exists, not merely the table.

    A table left empty (an interrupted Alembic run) must be treated as
    unstamped, or the next `upgrade head` starts from `base` and collides with
    the existing tables.
    """
    if not _has_table(conn, "alembic_version"):
        return False
    return conn.execute("select 1 from alembic_version limit 1").fetchone() is not None


def detect_stamp(db_path: Path) -> str | None:
    """The newest revision the existing schema proves, or None if empty.

    Returns None when there is nothing to stamp (fresh database), so the caller
    runs a plain `upgrade head`.
    """
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if _has_version(conn):
            return None  # already stamped/versioned
        tables = {
            row[0] for row in conn.execute("select name from sqlite_master where type='table'")
        }
        if "documents" not in tables:
            return None  # empty or unrelated file: let alembic build it
        known: set[str] = set()
        for table in ("documents", "po_sets", "line_items", "audit_log"):
            known |= {f"{table}.{column}" for column in _columns(conn, table)}
        index_sql = {
            row[0]: row[1] or ""
            for row in conn.execute(
                "select name, sql from sqlite_master where type='index' and sql is not null"
            )
        }
        known |= set(index_sql)
        # Synthetic markers for facts that are not a column.
        fk = conn.execute("pragma foreign_key_list(audit_log)").fetchall()
        if any((row[2] or "") == "po_sets" and (row[6] or "").upper() == "SET NULL" for row in fk):
            known.add("audit_fk_set_null")
        if "line_items.dn_no" in known and "documents.part_no" not in known:
            known.add("part_no_dropped")
        if "line_items.id" in known and "line_items.unit_price" not in known:
            known.add("unit_price_dropped")
        if (
            "po_sets.archived_at" in known
            and "documents.archived_at" in known
            and "archived_at IS NULL" in index_sql.get("uq_po_sets_open_po_no", "")
            and "archived_at IS NULL" in index_sql.get("ix_documents_sha256_hash", "")
        ):
            known.add("archive_schema")
        stamp: str | None = None
        for marker, revision in _MARKERS:
            if marker in known:
                stamp = revision
            elif marker == "documents.part_no":
                # part_no was ADDED at 9d1f and DROPPED at c1a2, so its absence
                # proves nothing about how far the schema has come. Every other
                # marker is additive.
                continue
            else:
                # Markers are ordered oldest -> newest. The first missing
                # additive marker means the schema stops before it.
                break
        if stamp is None:
            raise RuntimeError(
                f"{db_path} has tables but no alembic_version and matches no known "
                "schema revision (older than 7a8e2b1c9d0f). Back it up and stamp it "
                "by hand before upgrading; refusing to guess."
            )
        return stamp
    finally:
        conn.close()


def _backup(db_path: Path) -> Path | None:
    """A pre-migration copy in `<db dir>/backup/`, so a failed migration is recoverable.

    The name starts with `aam_merger_` so the existing backup retention pass
    sees and ages it out; writing it beside the DB as
    `aam_merger.db.pre-migrate-…` left files that nothing ever pruned.
    """
    if not db_path.exists():
        return None
    backup_dir = db_path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / (
        f"aam_merger_premigrate_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1000000:06d}.db"
    )
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


HEAD_REVISION = "d5e6f7a8b9c0"


def _current_revision(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if not _has_table(conn, "alembic_version"):
            return None
        row = conn.execute("select version_num from alembic_version").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def migrate(backup: bool = True) -> dict:
    """Bring the configured database to head. Idempotent.

    Returns a small report: the DB path, the stamp used (or None), whether a
    backup was written, and the resulting revision.
    """
    from alembic import command
    from app.core.config import load_config

    cfg = load_config()
    db_path = Path(cfg.paths.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    already_at_head = _current_revision(db_path) == HEAD_REVISION
    stamp = detect_stamp(db_path)
    # Do not write a backup on a no-op run: the installer is re-run on every
    # start, and a copy per run would fill the disk with identical snapshots.
    backup_path = (
        _backup(db_path) if (backup and db_path.exists() and not already_at_head) else None
    )

    acfg = _alembic_config()
    if stamp:
        command.stamp(acfg, stamp)
    command.upgrade(acfg, "head")

    return {
        "database": str(db_path),
        "stamped_from": stamp,
        "backup": str(backup_path) if backup_path else None,
        "revision": _current_revision(db_path),
    }


def verify(report: dict) -> None:
    """Refuse to report success unless the columns the code needs are present."""
    db_path = Path(report["database"])
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cols = _columns(conn, "documents")
        ps_cols = _columns(conn, "po_sets")
        li_cols = _columns(conn, "line_items")
        index_sql = {
            row[0]: row[1] or ""
            for row in conn.execute(
                "select name, sql from sqlite_master where type='index' and sql is not null"
            )
        }
    finally:
        conn.close()
    if "unit_price" in li_cols:
        raise RuntimeError(
            f"migration reported {report['revision']!r} but {db_path} still has "
            "line_items.unit_price (NOT NULL); every line-item insert would fail "
            "- do not start the app"
        )
    required = {
        "po_no_source",
        "split_completed_at",
        "is_split_parent",
        "po_reference_ambiguous",
        "archived_at",
    }
    missing = sorted(required - cols)
    if missing:
        raise RuntimeError(
            f"migration reported {report['revision']!r} but {db_path} is missing "
            f"documents columns {missing} — do not start the app"
        )
    required_ps = {
        "description_match_count",
        "customs_append_pending",
        "archived_at",
        "archived_by",
        "archive_reason",
        "archive_path",
    }
    missing_ps = sorted(required_ps - ps_cols)
    if missing_ps:
        raise RuntimeError(
            f"migration reported {report['revision']!r} but {db_path} is missing "
            f"po_sets columns {missing_ps} — do not start the app"
        )
    if "archived_at IS NULL" not in index_sql.get("uq_po_sets_open_po_no", ""):
        raise RuntimeError(
            f"migration reported {report['revision']!r} but {db_path} has the old "
            "one-open-set index; archived sets would block a new set for the same PO"
        )
    if "archived_at IS NULL" not in index_sql.get("ix_documents_sha256_hash", ""):
        raise RuntimeError(
            f"migration reported {report['revision']!r} but {db_path} has the old "
            "document hash index; archived bytes could never be re-ingested"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate the AAM Merger database safely.")
    parser.add_argument("--no-backup", action="store_true", help="skip the pre-migration copy")
    args = parser.parse_args()
    try:
        report = migrate(backup=not args.no_backup)
        verify(report)
    except Exception as exc:  # fail loudly; the installer treats this as fatal
        print(f"[ERROR] Database migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if report["stamped_from"]:
        print(
            f"[OK] Existing schema detected (no alembic_version); stamped "
            f"{report['stamped_from']} before upgrading."
        )
    print(f"[OK] Database at {report['revision']}: {report['database']}")
    if report["backup"]:
        print(f"[OK] Pre-migration backup: {report['backup']}")


if __name__ == "__main__":
    main()
