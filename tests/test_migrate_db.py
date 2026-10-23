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
from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus  # noqa: E402
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
        conn.execute("CREATE UNIQUE INDEX ix_documents_sha256_hash ON documents (sha256_hash)")
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
        s.add(
            Document(
                sha256_hash="a" * 64,
                original_filename="kept.pdf",
                stored_path=str(tmp_path / "stored" / "kept.pdf"),
                doc_type=DocType.PO,
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps.id,
            )
        )
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
