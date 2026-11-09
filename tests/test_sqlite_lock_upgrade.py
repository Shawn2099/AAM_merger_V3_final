"""SQLite write-lock behaviour under a competing writer.

The extraction claim used to read the document and then UPDATE it inside the
same session. SQLite holds a read lock after the SELECT; if another connection
already holds the write lock, the upgrade to writer fails immediately with
``database is locked`` (busy_timeout does not help a deadlock-prone upgrade).

These tests pin the fix: the claim and the result persistence must start their
transaction with a write, so a competing writer only makes them wait, never
fail. The probe that reproduced the old failure is documented in
``docs/SQLITE_CONCURRENCY_EVALUATION_2026-11-09.md``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import ensure_schema
from app.models import DocType, Document, ExtractionStatus


@pytest.fixture()
def cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "lock_upgrade.db"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    for path in (
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
    ):
        Path(path).mkdir(parents=True, exist_ok=True)
    ensure_schema(cfg)
    return cfg


def _add_document(cfg, name: str) -> int:
    pdf = Path(cfg.paths.stored_documents_folder) / f"{name}.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    eng = ensure_schema(cfg)
    with Session(eng) as s:
        doc = Document(
            sha256_hash=f"sha_{name}",
            original_filename=f"{name}.pdf",
            stored_path=str(pdf),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.pending,
            extraction_attempt_count=0,
        )
        s.add(doc)
        s.commit()
        return doc.id


def _hold_write_lock(cfg, doc_id: int, hold_seconds: float) -> threading.Timer:
    """Hold a real SQLite write transaction on ``doc_id`` for ``hold_seconds``.

    A raw DBAPI connection is used so the timer can commit from another thread
    without sharing a SQLAlchemy Session across threads.
    """
    eng = ensure_schema(cfg)
    raw = eng.raw_connection()
    raw.execute("BEGIN IMMEDIATE")
    raw.execute(
        "UPDATE documents SET extraction_status = ? WHERE id = ?",
        (ExtractionStatus.processing.value, doc_id),
    )

    def release() -> None:
        try:
            raw.commit()
        finally:
            raw.close()

    timer = threading.Timer(hold_seconds, release)
    timer.daemon = True
    timer.start()
    return timer


def _vlm_result() -> dict:
    return {
        "document_type": "PO",
        "document_number": "PO-LOCK-1",
        "po_no_raw": "PO-LOCK-1",
        "po_no_normalized": "POLOCK1",
        "line_items": [{"line_item_no": "1", "description": "x", "quantity": "2"}],
    }


def test_runtime_sqlite_supports_update_returning():
    import sqlite3

    assert sqlite3.sqlite_version_info >= (3, 35, 0), sqlite3.sqlite_version


def test_old_sqlite_is_refused_with_a_clear_error(tmp_path, monkeypatch):
    import sqlite3

    from app.core import database

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "old_sqlite.db"
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 0))

    with pytest.raises(RuntimeError, match=r"3\.35"):
        database.get_engine(cfg)


def test_sqlite_pragmas_are_set(cfg):
    """WAL, busy_timeout, foreign_keys and the WAL size cap are engine-wide."""
    eng = ensure_schema(cfg)
    with eng.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA journal_size_limit").scalar() == 67108864


def test_claim_waits_for_a_competing_writer_instead_of_lock_upgrade_failure(cfg, monkeypatch):
    """A writer on another row must make the claim wait, not fail.

    Before the fix this raised ``OperationalError: database is locked`` as soon
    as ``extract_document`` tried to upgrade its read transaction. The fix opens
    the claim with the UPDATE itself, so SQLite waits for the competing writer
    and the claim succeeds.
    """
    from app.services import extraction as extraction_module

    target_id = _add_document(cfg, "target")
    blocker_id = _add_document(cfg, "blocker")
    monkeypatch.setattr(extraction_module, "_call_vlm", lambda *_a, **_k: _vlm_result())

    timer = _hold_write_lock(cfg, blocker_id, hold_seconds=0.4)
    started = time.monotonic()
    try:
        doc = extraction_module.extract_document(target_id, cfg)
    finally:
        timer.cancel()
        timer.join(timeout=5)

    elapsed = time.monotonic() - started
    assert elapsed >= 0.2, "the claim did not wait for the competing writer"
    assert doc.extraction_status == ExtractionStatus.valid
    with Session(ensure_schema(cfg)) as s:
        row = s.get(Document, target_id)
        assert row.extraction_status == ExtractionStatus.valid
        assert row.extraction_attempt_count == 1


def test_extraction_transactions_start_with_a_write(cfg, monkeypatch):
    """Pin the transaction shape, not just the outcome.

    SQLite's failure mode is specifically a transaction that starts with a
    SELECT and later writes. The extraction claim and the post-VLM persistence
    must each start with an UPDATE; the short inspection read is allowed to be
    a SELECT because it is closed before the write session opens.
    """
    from sqlalchemy import event

    from app.services import extraction as extraction_module

    target_id = _add_document(cfg, "order_target")
    monkeypatch.setattr(extraction_module, "_call_vlm", lambda *_a, **_k: _vlm_result())
    eng = ensure_schema(cfg)

    transactions: list[list[str]] = []

    def on_begin(_conn) -> None:
        transactions.append([])

    def on_statement(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        transactions[-1].append(" ".join(statement.split()))

    event.listen(eng, "begin", on_begin)
    event.listen(eng, "before_cursor_execute", on_statement)
    try:
        extraction_module.extract_document(target_id, cfg)
    finally:
        event.remove(eng, "begin", on_begin)
        event.remove(eng, "before_cursor_execute", on_statement)

    bad = [
        transaction
        for transaction in transactions
        if transaction
        and transaction[0].upper().startswith("SELECT DOCUMENTS")
        and any(
            statement.upper().startswith(("UPDATE DOCUMENTS", "INSERT INTO LINE_ITEMS"))
            for statement in transaction[1:]
        )
    ]
    assert not bad, f"read->write upgrade transactions remain: {bad}"
    assert any(
        transaction and transaction[0].upper().startswith("UPDATE DOCUMENTS")
        for transaction in transactions
    ), "no transaction started with the claim/persist write"


def test_persist_waits_for_a_competing_writer_instead_of_lock_upgrade_failure(cfg, monkeypatch):
    """The post-VLM persistence path must also start with a write.

    The VLM call is blocked while another connection takes the write lock.
    When the VLM returns, the result session reads the document and then writes
    it. That read-to-write upgrade is the same SQLite failure mode; it must
    wait, not fail.
    """
    from app.services import extraction as extraction_module

    target_id = _add_document(cfg, "persist_target")
    blocker_id = _add_document(cfg, "persist_blocker")
    vlm_entered = threading.Event()
    vlm_release = threading.Event()
    errors: list[BaseException] = []
    result: list[Document] = []

    def fake_vlm(*_args, **_kwargs):
        vlm_entered.set()
        assert vlm_release.wait(10), "test never released the VLM"
        return _vlm_result()

    monkeypatch.setattr(extraction_module, "_call_vlm", fake_vlm)

    def run() -> None:
        try:
            result.append(extraction_module.extract_document(target_id, cfg))
        except BaseException as exc:  # pragma: no cover - the assertion reports it
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert vlm_entered.wait(10), "the VLM was never called"

    timer = _hold_write_lock(cfg, blocker_id, hold_seconds=0.4)
    started = time.monotonic()
    try:
        vlm_release.set()
        worker.join(timeout=15)
    finally:
        timer.cancel()
        timer.join(timeout=5)

    elapsed = time.monotonic() - started
    assert not worker.is_alive(), "the persistence path did not finish"
    assert not errors, f"persistence raised: {errors[0]!r}"
    assert elapsed >= 0.2, "the persistence path did not wait for the competing writer"
    assert result and result[0].extraction_status == ExtractionStatus.valid
    with Session(ensure_schema(cfg)) as s:
        row = s.get(Document, target_id)
        assert row.extraction_status == ExtractionStatus.valid
        assert row.extraction_attempt_count == 1
