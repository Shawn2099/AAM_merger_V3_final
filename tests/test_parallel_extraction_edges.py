"""Parallel-extraction edge cases introduced by the claim/persist split.

These pin the races that are easy to miss when three workers run at once:
a stale snapshot must never overwrite a newer archive/reclassification/reset,
a breaker trip after the claim must release only the row it claimed, and two
workers racing a stale claim must still make exactly one VLM call.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import ensure_schema
from app.models import DocType, Document, ExtractionStatus


@pytest.fixture()
def cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "parallel_edges.db"
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


def _add_document(
    cfg,
    name: str,
    *,
    doc_type: DocType = DocType.PO,
    status: ExtractionStatus = ExtractionStatus.pending,
    attempts: int = 0,
    updated_at: datetime | None = None,
) -> int:
    pdf = Path(cfg.paths.stored_documents_folder) / f"{name}.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    eng = ensure_schema(cfg)
    with Session(eng) as s:
        doc = Document(
            sha256_hash=f"edge_{name}",
            original_filename=f"{name}.pdf",
            stored_path=str(pdf),
            doc_type=doc_type,
            extraction_status=status,
            extraction_attempt_count=attempts,
        )
        if updated_at is not None:
            doc.updated_at = updated_at
        s.add(doc)
        s.commit()
        return doc.id


def _vlm_result(name: str = "edge") -> dict:
    return {
        "document_type": "PO",
        "document_number": f"PO-{name}",
        "po_no_raw": f"PO-{name}",
        "line_items": [{"line_item_no": "1", "description": "x", "quantity": "2"}],
    }


def test_release_claim_does_not_overwrite_a_valid_document(cfg):
    from app.services import extraction as extraction_module

    doc_id = _add_document(cfg, "valid", status=ExtractionStatus.valid, attempts=1)
    doc = extraction_module._release_claim(ensure_schema(cfg), doc_id, 1)

    assert doc is not None
    assert doc.extraction_status == ExtractionStatus.valid
    assert doc.extraction_attempt_count == 1


def test_claim_skips_a_document_archived_after_the_inspection_read(cfg, monkeypatch):
    from app.services import extraction as extraction_module

    doc_id = _add_document(cfg, "archived_race")
    real_snapshot = extraction_module._read_document_snapshot
    archived = False
    vlm_called = False

    def snapshot(eng, target_id):
        nonlocal archived
        doc = real_snapshot(eng, target_id)
        if not archived:
            archived = True
            with Session(eng) as s:
                s.execute(
                    update(Document)
                    .where(Document.id == target_id)
                    .values(archived_at=datetime.now(UTC))
                )
                s.commit()
        return doc

    def fake_vlm(*_args, **_kwargs):
        nonlocal vlm_called
        vlm_called = True
        return _vlm_result()

    monkeypatch.setattr(extraction_module, "_read_document_snapshot", snapshot)
    monkeypatch.setattr(extraction_module, "_call_vlm", fake_vlm)

    doc = extraction_module.extract_document(doc_id, cfg)

    assert vlm_called is False
    assert doc.archived_at is not None
    with Session(ensure_schema(cfg)) as s:
        row = s.get(Document, doc_id)
        assert row.archived_at is not None
        assert row.extraction_attempt_count == 0


def test_claim_skips_a_document_reclassified_manual_only_after_the_inspection_read(
    cfg, monkeypatch
):
    from app.services import extraction as extraction_module

    doc_id = _add_document(cfg, "reclassified_race")
    real_snapshot = extraction_module._read_document_snapshot
    reclassified = False
    vlm_called = False

    def snapshot(eng, target_id):
        nonlocal reclassified
        doc = real_snapshot(eng, target_id)
        if not reclassified:
            reclassified = True
            with Session(eng) as s:
                s.execute(
                    update(Document)
                    .where(Document.id == target_id)
                    .values(doc_type=DocType.CUSTOMS)
                )
                s.commit()
        return doc

    def fake_vlm(*_args, **_kwargs):
        nonlocal vlm_called
        vlm_called = True
        return _vlm_result()

    monkeypatch.setattr(extraction_module, "_read_document_snapshot", snapshot)
    monkeypatch.setattr(extraction_module, "_call_vlm", fake_vlm)

    doc = extraction_module.extract_document(doc_id, cfg)

    assert vlm_called is False
    assert doc.doc_type == DocType.CUSTOMS
    with Session(ensure_schema(cfg)) as s:
        row = s.get(Document, doc_id)
        assert row.doc_type == DocType.CUSTOMS
        assert row.extraction_attempt_count == 0


def test_attempt_cap_does_not_overwrite_an_operator_reset(cfg, monkeypatch):
    from app.services import extraction as extraction_module

    cfg.extraction.max_retries = 3
    doc_id = _add_document(cfg, "cap_reset", attempts=3)
    real_snapshot = extraction_module._read_document_snapshot
    reset = False

    def snapshot(eng, target_id):
        nonlocal reset
        doc = real_snapshot(eng, target_id)
        if not reset:
            reset = True
            with Session(eng) as s:
                s.execute(
                    update(Document)
                    .where(Document.id == target_id)
                    .values(extraction_attempt_count=0)
                )
                s.commit()
        return doc

    monkeypatch.setattr(extraction_module, "_read_document_snapshot", snapshot)
    doc = extraction_module.extract_document(doc_id, cfg)

    assert doc.extraction_status == ExtractionStatus.pending
    assert doc.extraction_attempt_count == 0
    with Session(ensure_schema(cfg)) as s:
        row = s.get(Document, doc_id)
        assert row.extraction_status == ExtractionStatus.pending
        assert row.extraction_attempt_count == 0


def test_persistence_discards_a_document_archived_mid_extraction(cfg, monkeypatch):
    from app.services import extraction as extraction_module

    doc_id = _add_document(cfg, "archived_mid")
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def fake_vlm(*_args, **_kwargs):
        entered.set()
        assert release.wait(10)
        return _vlm_result("archived_mid")

    monkeypatch.setattr(extraction_module, "_call_vlm", fake_vlm)

    def run():
        try:
            extraction_module.extract_document(doc_id, cfg)
        except BaseException as exc:  # pragma: no cover - reported by the test
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert entered.wait(10)
    with Session(ensure_schema(cfg)) as s:
        s.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(archived_at=datetime.now(UTC))
        )
        s.commit()
    release.set()
    thread.join(15)

    assert not thread.is_alive()
    assert not errors, repr(errors[0])
    with Session(ensure_schema(cfg)) as s:
        row = s.get(Document, doc_id)
        assert row.archived_at is not None
        assert row.raw_extraction_json is None


def test_persistence_discards_a_manual_reclassification_mid_extraction(cfg, monkeypatch):
    from app.services import extraction as extraction_module

    doc_id = _add_document(cfg, "manual_mid")
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def fake_vlm(*_args, **_kwargs):
        entered.set()
        assert release.wait(10)
        return _vlm_result("manual_mid")

    monkeypatch.setattr(extraction_module, "_call_vlm", fake_vlm)

    def run():
        try:
            extraction_module.extract_document(doc_id, cfg)
        except BaseException as exc:  # pragma: no cover - reported by the test
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert entered.wait(10)
    with Session(ensure_schema(cfg)) as s:
        s.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(doc_type=DocType.CUSTOMS)
        )
        s.commit()
    release.set()
    thread.join(15)

    assert not thread.is_alive()
    assert not errors, repr(errors[0])
    with Session(ensure_schema(cfg)) as s:
        row = s.get(Document, doc_id)
        assert row.doc_type == DocType.CUSTOMS
        assert row.extraction_status == ExtractionStatus.pending
        assert row.raw_extraction_json is None


def test_two_workers_racing_a_stale_claim_make_one_vlm_call(cfg, monkeypatch):
    from app.services import extraction as extraction_module

    cfg.extraction.stale_processing_seconds = 300
    stale_at = datetime.now(UTC) - timedelta(seconds=600)
    doc_id = _add_document(
        cfg,
        "stale_race",
        status=ExtractionStatus.processing,
        attempts=1,
        updated_at=stale_at,
    )
    calls = 0
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=5)

    def fake_vlm(*_args, **_kwargs):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.05)
        return _vlm_result("stale_race")

    monkeypatch.setattr(extraction_module, "_call_vlm", fake_vlm)

    results = []
    errors = []

    def run():
        try:
            barrier.wait()
            results.append(extraction_module.extract_document(doc_id, cfg))
        except BaseException as exc:  # pragma: no cover - reported by the test
            errors.append(exc)

    threads = [threading.Thread(target=run, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)

    assert all(not thread.is_alive() for thread in threads)
    assert not errors, repr(errors[0])
    assert calls == 1
    assert len(results) == 2
    with Session(ensure_schema(cfg)) as s:
        row = s.get(Document, doc_id)
        assert row.extraction_status == ExtractionStatus.valid
        # Attempt 1 was the dead worker's stale claim; the successful reclaim
        # is attempt 2. The two racing workers still made exactly one VLM call,
        # so the attempt was not charged twice.
        assert row.extraction_attempt_count == 2


def test_attempt_cap_race_charges_the_last_attempt_once(cfg, monkeypatch):
    from app.services import extraction as extraction_module

    cfg.extraction.max_retries = 3
    doc_id = _add_document(cfg, "cap_race", attempts=2)
    calls = 0
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=5)

    def fake_vlm(*_args, **_kwargs):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.05)
        return _vlm_result("cap_race")

    monkeypatch.setattr(extraction_module, "_call_vlm", fake_vlm)

    errors = []

    def run():
        try:
            barrier.wait()
            extraction_module.extract_document(doc_id, cfg)
        except BaseException as exc:  # pragma: no cover - reported by the test
            errors.append(exc)

    threads = [threading.Thread(target=run, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)

    assert all(not thread.is_alive() for thread in threads)
    assert not errors, repr(errors[0])
    assert calls == 1
    with Session(ensure_schema(cfg)) as s:
        row = s.get(Document, doc_id)
        assert row.extraction_status == ExtractionStatus.valid
        assert row.extraction_attempt_count == 3


def test_persistence_discards_a_status_changed_mid_extraction(cfg, monkeypatch):
    from app.services import extraction as extraction_module

    doc_id = _add_document(cfg, "status_mid")
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def fake_vlm(*_args, **_kwargs):
        entered.set()
        assert release.wait(10)
        return _vlm_result("status_mid")

    monkeypatch.setattr(extraction_module, "_call_vlm", fake_vlm)

    def run():
        try:
            extraction_module.extract_document(doc_id, cfg)
        except BaseException as exc:  # pragma: no cover - reported by the test
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert entered.wait(10)
    with Session(ensure_schema(cfg)) as s:
        s.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(extraction_status=ExtractionStatus.pending)
        )
        s.commit()
    release.set()
    thread.join(15)

    assert not thread.is_alive()
    assert not errors, repr(errors[0])
    with Session(ensure_schema(cfg)) as s:
        row = s.get(Document, doc_id)
        assert row.extraction_status == ExtractionStatus.pending
        assert row.raw_extraction_json is None


def test_valid_split_parent_without_completion_is_claimed(cfg, monkeypatch):
    from app.services import extraction as extraction_module

    doc_id = _add_document(cfg, "split_incomplete", status=ExtractionStatus.valid, attempts=1)
    eng = ensure_schema(cfg)
    with Session(eng) as s:
        s.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(is_split_parent=True, split_completed_at=None)
        )
        s.commit()

    calls = 0

    def fake_vlm(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _vlm_result("split_incomplete")

    monkeypatch.setattr(extraction_module, "_call_vlm", fake_vlm)
    doc = extraction_module.extract_document(doc_id, cfg)

    assert calls == 1
    assert doc.extraction_status == ExtractionStatus.valid
    with Session(eng) as s:
        row = s.get(Document, doc_id)
        assert row.extraction_attempt_count == 2


def test_completed_split_parent_is_not_claimed(cfg, monkeypatch):
    from app.services import extraction as extraction_module

    doc_id = _add_document(cfg, "split_done_race", status=ExtractionStatus.valid, attempts=1)
    eng = ensure_schema(cfg)
    with Session(eng) as s:
        s.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(is_split_parent=True, split_completed_at=None)
        )
        s.commit()

    real_snapshot = extraction_module._read_document_snapshot
    completed = False

    def snapshot(session_eng, target_id):
        nonlocal completed
        doc = real_snapshot(session_eng, target_id)
        if not completed:
            completed = True
            with Session(session_eng) as s:
                s.execute(
                    update(Document)
                    .where(Document.id == target_id)
                    .values(split_completed_at=datetime.now(UTC))
                )
                s.commit()
        return doc

    monkeypatch.setattr(extraction_module, "_read_document_snapshot", snapshot)
    monkeypatch.setattr(
        extraction_module,
        "_call_vlm",
        lambda *_a, **_k: pytest.fail("a completed split parent must not be re-read"),
    )

    doc = extraction_module.extract_document(doc_id, cfg)

    assert doc.extraction_status == ExtractionStatus.valid
    assert doc.split_completed_at is not None
    with Session(eng) as s:
        row = s.get(Document, doc_id)
        assert row.extraction_attempt_count == 1


def test_breaker_trip_after_the_claim_releases_only_that_row(cfg, monkeypatch):
    from app.services import extraction as extraction_module

    doc_id = _add_document(cfg, "breaker_after_claim")
    calls = 0

    def tripped():
        nonlocal calls
        calls += 1
        return calls >= 2

    monkeypatch.setattr(extraction_module, "provider_breaker_tripped", tripped)
    monkeypatch.setattr(
        extraction_module,
        "_call_vlm",
        lambda *_a, **_k: pytest.fail("the VLM must not be called after the breaker trip"),
    )

    doc = extraction_module.extract_document(doc_id, cfg)

    assert doc.extraction_status == ExtractionStatus.pending
    assert doc.extraction_attempt_count == 0
    with Session(ensure_schema(cfg)) as s:
        row = s.get(Document, doc_id)
        assert row.extraction_status == ExtractionStatus.pending
        assert row.extraction_attempt_count == 0
