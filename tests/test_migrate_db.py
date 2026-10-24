"""The migration entry point must work on a fresh DB *and* on the live one.

The live database was created by `Base.metadata.create_all` and has no
`alembic_version`, which makes a plain `alembic upgrade head` fail with
"table documents already exists". `create_all` also never adds a column to an
existing table, so an un-migrated DB breaks the app on `documents.po_no_source`.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_db  # noqa: E402

from app.core.config import load_config  # noqa: E402
from app.core.database import get_engine  # noqa: E402
from app.models import (  # noqa: E402
    DocType,
    Document,
    ExtractionStatus,
    LineItem,
    POSet,
    POSetStatus,
)
from app.models.base import Base  # noqa: E402


def _write_config(tmp_path: Path) -> Path:
    cfg = {
        "paths": {
            "input_folder": str(tmp_path / "input"),
            "output_folder": str(tmp_path / "output"),
            "quarantine_folder": str(tmp_path / "quarantine"),
            "stored_documents_folder": str(tmp_path / "stored"),
            "combined_folder": str(tmp_path / "combined"),
            "log_folder": str(tmp_path / "logs"),
            "database_path": str(tmp_path / "aam.db"),
        }
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg_path = _write_config(tmp_path)
    monkeypatch.setenv("AAM_CONFIG_PATH", str(cfg_path))
    # The loader caches by stat signature; a new path is a new key.
    return tmp_path, load_config()


def _drop_legacy_gap(db_path: Path) -> None:
    """Turn a current create_all DB into the shape the live DB has."""
    conn = sqlite3.connect(str(db_path))
    try:
        # Archive-aware indexes reference archived_at; drop them before the
        # columns. The old global document-hash index is restored afterwards so
        # the new revision has the same starting point as the live DB.
        for index in (
            "uq_po_sets_open_po_no",
            "ix_documents_sha256_hash",
            "ix_documents_archived_at",
            "ix_po_sets_archived_at",
            "ix_documents_po_set_id",
            "ix_line_items_document_id",
            "ix_audit_log_po_set_id",
        ):
            conn.execute(f"DROP INDEX IF EXISTS {index}")
        conn.execute("ALTER TABLE documents DROP COLUMN archived_at")
        conn.execute("ALTER TABLE po_sets DROP COLUMN archive_path")
        conn.execute("ALTER TABLE po_sets DROP COLUMN archive_reason")
        conn.execute("ALTER TABLE po_sets DROP COLUMN archived_by")
        conn.execute("ALTER TABLE po_sets DROP COLUMN archived_at")
        conn.execute("ALTER TABLE documents DROP COLUMN po_no_source")
        conn.execute("ALTER TABLE po_sets DROP COLUMN description_match_count")
        conn.execute("ALTER TABLE po_sets DROP COLUMN customs_append_pending")
        conn.execute("ALTER TABLE po_sets DROP COLUMN po_no_letters")
        conn.execute("CREATE UNIQUE INDEX ix_documents_sha256_hash ON documents (sha256_hash)")
        # The live DB still had the NOT NULL price column the code no longer writes.
        conn.execute("ALTER TABLE line_items ADD COLUMN unit_price INTEGER NOT NULL DEFAULT 0")
        conn.execute("DROP TABLE IF EXISTS alembic_version")
        conn.commit()
    finally:
        conn.close()


def test_a_fresh_database_is_built_to_head(rig):
    cfg = rig[1]
    db = Path(cfg.paths.database_path)
    assert not db.exists()

    report = migrate_db.migrate(backup=False)
    migrate_db.verify(report)

    assert report["stamped_from"] is None
    assert report["revision"] == migrate_db.HEAD_REVISION
    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    cols = {r[1] for r in conn.execute("pragma table_info(documents)")}
    indexes = {r[0] for r in conn.execute("select name from sqlite_master where type='index'")}
    assert {"documents", "line_items", "po_sets", "audit_log"} <= tables
    li_cols = {r[1] for r in conn.execute("pragma table_info(line_items)")}
    assert "unit_price" not in li_cols
    assert "po_no_source" in cols
    assert "archived_at" in cols
    assert "uq_po_sets_open_po_no" in indexes
    po_cols = {r[1] for r in conn.execute("pragma table_info(po_sets)")}
    assert {"archived_at", "archived_by", "archive_reason", "archive_path"} <= po_cols
    index_sql = {
        r[0]: r[1] for r in conn.execute("select name, sql from sqlite_master where type='index'")
    }
    assert "archived_at IS NULL" in index_sql["uq_po_sets_open_po_no"]
    assert "archived_at IS NULL" in index_sql["ix_documents_sha256_hash"]
    conn.close()


def test_archived_rows_do_not_reserve_the_po_or_the_content_hash(rig):
    cfg = rig[1]
    migrate_db.migrate(backup=False)
    db = Path(cfg.paths.database_path)

    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO po_sets (po_no_normalized, status, has_customs_toggle, "
            "customs_doc_count, created_at, updated_at, archived_at) VALUES "
            "('PO-ARCH', 'quarantined', 0, 0, datetime('now'), datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO po_sets (po_no_normalized, status, has_customs_toggle, "
            "customs_doc_count, created_at, updated_at) VALUES "
            "('PO-ARCH', 'pending', 0, 0, datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO documents (sha256_hash, original_filename, stored_path, "
            "doc_type, extraction_status, extraction_attempt_count, "
            "po_reference_ambiguous, is_split_parent, created_at, updated_at, archived_at) "
            "VALUES ('samehash', 'old.pdf', 'old.pdf', 'PO', 'valid', 0, 0, 0, "
            "datetime('now'), datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO documents (sha256_hash, original_filename, stored_path, "
            "doc_type, extraction_status, extraction_attempt_count, "
            "po_reference_ambiguous, is_split_parent, created_at, updated_at) "
            "VALUES ('samehash', 'new.pdf', 'new.pdf', 'PO', 'valid', 0, 0, 0, "
            "datetime('now'), datetime('now'))"
        )
        conn.commit()
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO documents (sha256_hash, original_filename, stored_path, "
                "doc_type, extraction_status, extraction_attempt_count, "
                "po_reference_ambiguous, is_split_parent, created_at, updated_at) "
                "VALUES ('samehash', 'third.pdf', 'third.pdf', 'PO', 'valid', 0, 0, 0, "
                "datetime('now'), datetime('now'))"
            )
    finally:
        conn.close()


def test_a_create_all_database_is_stamped_then_upgraded_and_keeps_data(rig):
    tmp_path, cfg = rig
    db = Path(cfg.paths.database_path)
    Base.metadata.create_all(get_engine(cfg))
    from sqlalchemy.orm import Session

    with Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized="PO-LIVE", status=POSetStatus.pending)
        s.add(ps)
        s.flush()
        kept = Document(
            sha256_hash="a" * 64,
            original_filename="kept.pdf",
            stored_path=str(tmp_path / "stored" / "kept.pdf"),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps.id,
        )
        s.add(kept)
        s.flush()
        s.add(LineItem(document_id=kept.id, line_item_no="1", description="W", quantity=5000))
        s.commit()

    _drop_legacy_gap(db)
    assert migrate_db.detect_stamp(db) == "e6f7a8b9c0d1"

    report = migrate_db.migrate(backup=True)
    migrate_db.verify(report)

    assert report["stamped_from"] == "e6f7a8b9c0d1"
    assert report["revision"] == migrate_db.HEAD_REVISION
    assert report["backup"] and Path(report["backup"]).is_file()

    with Session(get_engine(cfg)) as s:
        doc = s.query(Document).filter_by(sha256_hash="a" * 64).one()
        assert doc.original_filename == "kept.pdf"
        assert doc.po_no_source is None
        assert s.query(POSet).filter_by(po_no_normalized="PO-LIVE").one()
        assert s.query(LineItem).one().quantity == 5000
    conn = sqlite3.connect(str(db))
    try:
        assert "unit_price" not in {r[1] for r in conn.execute("pragma table_info(line_items)")}
        indexes = {r[0] for r in conn.execute("select name from sqlite_master where type='index'")}
        assert "ix_line_items_document_id" in indexes
        fks = conn.execute("pragma foreign_key_list(line_items)").fetchall()
        assert any(row[2] == "documents" for row in fks)
    finally:
        conn.close()


def test_a_current_database_without_a_version_row_is_stamped_at_head(rig):
    cfg = rig[1]
    db = Path(cfg.paths.database_path)
    Base.metadata.create_all(get_engine(cfg))
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE IF EXISTS alembic_version")
    conn.commit()
    conn.close()

    assert migrate_db.detect_stamp(db) == migrate_db.HEAD_REVISION
    report = migrate_db.migrate(backup=False)
    migrate_db.verify(report)
    assert report["stamped_from"] == migrate_db.HEAD_REVISION
    assert report["revision"] == migrate_db.HEAD_REVISION


def test_migrating_twice_is_idempotent(rig):
    first = migrate_db.migrate(backup=False)
    second = migrate_db.migrate(backup=False)
    assert first["revision"] == second["revision"] == migrate_db.HEAD_REVISION
    assert second["stamped_from"] is None


def test_duplicate_open_sets_are_refused_with_the_po_numbers(rig):
    cfg = rig[1]
    db = Path(cfg.paths.database_path)
    Base.metadata.create_all(get_engine(cfg))
    _drop_legacy_gap(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO po_sets (po_no_normalized, status, has_customs_toggle, customs_doc_count, "
        "created_at, updated_at) VALUES ('DUP', 'pending', 0, 0, datetime('now'), datetime('now'))"
    )
    conn.execute(
        "INSERT INTO po_sets (po_no_normalized, status, has_customs_toggle, customs_doc_count, "
        "created_at, updated_at) VALUES ('DUP', 'pending', 0, 0, datetime('now'), datetime('now'))"
    )
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="DUP"):
        migrate_db.migrate(backup=False)


def test_a_database_at_the_previous_head_with_unit_price_drops_the_column_and_keeps_rows(rig):
    cfg = rig[1]
    db = Path(cfg.paths.database_path)
    Base.metadata.create_all(get_engine(cfg))
    from sqlalchemy.orm import Session

    with Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized="PO-OLD", status=POSetStatus.pending)
        s.add(ps)
        s.flush()
        doc = Document(
            sha256_hash="b" * 64,
            original_filename="old.pdf",
            stored_path="old.pdf",
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps.id,
        )
        s.add(doc)
        s.flush()
        s.add_all(
            [
                LineItem(document_id=doc.id, line_item_no="1", description="A", quantity=1000),
                LineItem(document_id=doc.id, line_item_no="2", description="B", quantity=2000),
            ]
        )
        s.commit()
    conn = sqlite3.connect(str(db))
    conn.execute("ALTER TABLE line_items ADD COLUMN unit_price INTEGER NOT NULL DEFAULT 0")
    conn.execute("UPDATE line_items SET unit_price = 350000")
    conn.execute("DROP TABLE IF EXISTS alembic_version")
    conn.commit()
    conn.close()

    # Columns prove everything up to the previous head, not the drop.
    assert migrate_db.detect_stamp(db) == "a9b0c1d2e3f4"
    report = migrate_db.migrate(backup=False)
    migrate_db.verify(report)

    assert report["revision"] == migrate_db.HEAD_REVISION
    conn = sqlite3.connect(str(db))
    try:
        assert "unit_price" not in {r[1] for r in conn.execute("pragma table_info(line_items)")}
        rows = conn.execute("select line_item_no, quantity from line_items order by id").fetchall()
        assert rows == [("1", 1000), ("2", 2000)]
    finally:
        conn.close()


def test_verify_refuses_a_database_that_still_has_unit_price(rig):
    cfg = rig[1]
    report = migrate_db.migrate(backup=False)
    conn = sqlite3.connect(str(cfg.paths.database_path))
    conn.execute("ALTER TABLE line_items ADD COLUMN unit_price INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="unit_price"):
        migrate_db.verify(report)


def _drop_line_mapping_column(db: Path) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute("ALTER TABLE po_sets DROP COLUMN line_mapping_rule")
    conn.execute("DROP TABLE IF EXISTS alembic_version")
    conn.commit()
    conn.close()


def test_a_database_at_the_previous_head_gains_line_mapping_rule_and_keeps_rows(rig):
    cfg = rig[1]
    db = Path(cfg.paths.database_path)
    Base.metadata.create_all(get_engine(cfg))
    from sqlalchemy.orm import Session

    with Session(get_engine(cfg)) as s:
        s.add(POSet(po_no_normalized="PO-KEEP", status=POSetStatus.pending))
        s.commit()
    _drop_line_mapping_column(db)

    # The schema proves everything up to the previous head, not the new column.
    assert migrate_db.detect_stamp(db) == "d5e6f7a8b9c0"
    report = migrate_db.migrate(backup=False)
    migrate_db.verify(report)

    assert report["revision"] == migrate_db.HEAD_REVISION == "e8f9a0b1c2d3"
    conn = sqlite3.connect(str(db))
    try:
        assert "line_mapping_rule" in {r[1] for r in conn.execute("pragma table_info(po_sets)")}
        rows = conn.execute("select po_no_normalized, line_mapping_rule from po_sets").fetchall()
        assert rows == [("PO-KEEP", None)]
    finally:
        conn.close()
    # a second run is a no-op
    again = migrate_db.migrate(backup=False)
    assert again["revision"] == migrate_db.HEAD_REVISION
    assert again["stamped_from"] is None


def test_verify_refuses_a_database_without_line_mapping_rule(rig):
    cfg = rig[1]
    report = migrate_db.migrate(backup=False)
    conn = sqlite3.connect(str(cfg.paths.database_path))
    conn.execute("ALTER TABLE po_sets DROP COLUMN line_mapping_rule")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="line_mapping_rule"):
        migrate_db.verify(report)


def test_downgrade_of_line_mapping_rule_keeps_every_row_with_foreign_keys_on(rig):
    """A batch rebuild of po_sets would fail on documents.po_set_id or null audit_log.po_set_id."""
    from alembic import command

    cfg = rig[1]
    db = Path(cfg.paths.database_path)
    migrate_db.migrate(backup=False)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO po_sets (id, po_no_normalized, status, has_customs_toggle, customs_doc_count,"
        " created_at, updated_at, description_match_count, customs_append_pending)"
        " VALUES (1, 'PO-DOWN', 'merged', 0, 0, '2026-01-01', '2026-01-01', 0, 0)"
    )
    conn.execute(
        "INSERT INTO documents (id, sha256_hash, original_filename, stored_path, doc_type,"
        " extraction_status, extraction_attempt_count, po_set_id, created_at, updated_at,"
        " po_reference_ambiguous, is_split_parent)"
        " VALUES (1, 'h1', 'a.pdf', 'a.pdf', 'PO', 'valid', 1, 1, '2026-01-01', '2026-01-01', 0, 0)"
    )
    conn.execute(
        "INSERT INTO audit_log (id, po_set_id, action, detail, timestamp, source)"
        " VALUES (1, 1, 'merge', 'x', '2026-01-01', 'system')"
    )
    conn.commit()
    conn.close()

    command.downgrade(migrate_db._alembic_config(), "d5e6f7a8b9c0")

    conn = sqlite3.connect(str(db))
    try:
        assert "line_mapping_rule" not in {r[1] for r in conn.execute("pragma table_info(po_sets)")}
        assert conn.execute("select count(*) from po_sets").fetchone()[0] == 1
        assert conn.execute("select count(*) from documents").fetchone()[0] == 1
        assert conn.execute("select po_set_id from audit_log").fetchone()[0] == 1
        leftovers = [
            r[0]
            for r in conn.execute("select name from sqlite_master where name like '_alembic_tmp%'")
        ]
        assert leftovers == []
    finally:
        conn.close()
