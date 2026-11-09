"""Parallel extraction: bounded concurrency, one call per document, safe failures.

The VLM is faked with events, never with `sleep`: a test that relies on timing
is a test that fails on the production host's 2 cores.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import ensure_schema
from app.flows.sync import ExtractionJob, run_extractions
from app.models import DocType, Document, ExtractionStatus


@pytest.fixture()
def cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "parallel.db"
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


def _jobs(n: int) -> list[ExtractionJob]:
    return [
        ExtractionJob(doc_id=i, input_file=None, filename=f"doc-{i}.pdf") for i in range(1, n + 1)
    ]


def test_concurrency_cap_is_respected_and_each_job_runs_once(cfg):
    cfg.prefect.max_concurrent_extraction_tasks = 3
    barrier = threading.Barrier(3, timeout=5)
    lock = threading.Lock()
    calls: list[int] = []
    active = 0
    max_active = 0

    def extract(doc_id):
        nonlocal active, max_active
        with lock:
            calls.append(doc_id)
            active += 1
            max_active = max(max_active, active)
        try:
            barrier.wait()
        finally:
            with lock:
                active -= 1
        return SimpleNamespace(extraction_status=ExtractionStatus.valid)

    outcomes = run_extractions(_jobs(6), cfg, extract_fn=extract)

    assert len(outcomes) == 6
    assert sorted(calls) == [1, 2, 3, 4, 5, 6]
    assert len(calls) == len(set(calls)) == 6
    assert max_active == 3


def test_a_slow_document_does_not_block_the_others(cfg):
    cfg.prefect.max_concurrent_extraction_tasks = 3
    release_first = threading.Event()
    fast_done = threading.Event()
    finished: list[int] = []
    lock = threading.Lock()

    def extract(doc_id):
        if doc_id == 1:
            assert release_first.wait(10), "first document was never released"
        else:
            with lock:
                finished.append(doc_id)
                if len(finished) == 2:
                    fast_done.set()
        return SimpleNamespace(extraction_status=ExtractionStatus.valid)

    runner = threading.Thread(
        target=lambda: run_extractions(_jobs(3), cfg, extract_fn=extract),
        daemon=True,
    )
    runner.start()
    assert fast_done.wait(10), (
        "the two fast documents did not finish while the slow one was blocked"
    )
    release_first.set()
    runner.join(10)
    assert not runner.is_alive()
    assert sorted(finished) == [2, 3]


def test_breaker_stops_new_work_and_marks_the_rest_skipped(cfg, monkeypatch):
    from app.services.extraction import ProviderRequestRejected

    cfg.prefect.max_concurrent_extraction_tasks = 2
    tripped = False
    calls: list[int] = []

    def extract(doc_id):
        nonlocal tripped
        calls.append(doc_id)
        tripped = True
        raise ProviderRequestRejected("provider rejected the request")

    monkeypatch.setattr("app.services.extraction.provider_breaker_tripped", lambda: tripped)
    outcomes = run_extractions(_jobs(5), cfg, extract_fn=extract)

    # Two workers may already be in flight when the first one trips; no NEW
    # document (3..5) is ever launched after the trip.
    assert all(doc_id <= 2 for doc_id in calls), calls
    assert all(o.status in ("halted", "skipped") for o in outcomes)
    assert sum(1 for o in outcomes if o.status == "skipped") >= 3
    assert all(o.halted for o in outcomes)


def test_empty_job_list_returns_empty(cfg):
    assert run_extractions([], cfg) == []


def test_duplicate_job_ids_are_read_once(cfg):
    calls: list[int] = []

    def extract(doc_id):
        calls.append(doc_id)
        return SimpleNamespace(extraction_status=ExtractionStatus.valid)

    jobs = [
        ExtractionJob(doc_id=1, input_file=None, filename="a.pdf"),
        ExtractionJob(doc_id=1, input_file=None, filename="a-copy.pdf"),
        ExtractionJob(doc_id=2, input_file=None, filename="b.pdf"),
    ]
    outcomes = run_extractions(jobs, cfg, extract_fn=extract)

    assert sorted(calls) == [1, 2]
    assert len(outcomes) == 2
    assert {outcome.job.doc_id for outcome in outcomes} == {1, 2}


def test_one_document_failure_does_not_stop_the_batch(cfg):
    cfg.prefect.max_concurrent_extraction_tasks = 2

    def extract(doc_id):
        if doc_id == 2:
            raise RuntimeError("this document is broken")
        return SimpleNamespace(extraction_status=ExtractionStatus.valid)

    outcomes = run_extractions(_jobs(4), cfg, extract_fn=extract)
    by_id = {o.job.doc_id: o for o in outcomes}
    assert by_id[2].status == "error"
    assert [by_id[i].status for i in (1, 3, 4)] == ["valid", "valid", "valid"]


def test_database_locked_is_infrastructure_not_a_document_verdict(cfg, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.services import extraction as extraction_module

    pdf = Path(cfg.paths.stored_documents_folder) / "locked.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    eng = ensure_schema(cfg)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="locked_sha",
            original_filename="locked.pdf",
            stored_path=str(pdf),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.pending,
            extraction_attempt_count=0,
        )
        s.add(doc)
        s.commit()
        doc_id = doc.id

    def boom(*_args, **_kwargs):
        raise OperationalError("UPDATE documents", {}, Exception("database is locked"))

    monkeypatch.setattr(extraction_module, "_call_vlm", boom)
    with pytest.raises(OperationalError):
        extraction_module.extract_document(doc_id, cfg)

    with Session(eng) as s:
        row = s.get(Document, doc_id)
        assert row.extraction_status == ExtractionStatus.pending
        assert row.extraction_attempt_count == 0


def test_two_threads_race_one_document_and_only_one_vlm_call_happens(cfg, monkeypatch):
    from app.services import extraction as extraction_module

    pdf = Path(cfg.paths.stored_documents_folder) / "race.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    eng = ensure_schema(cfg)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="race_sha",
            original_filename="race.pdf",
            stored_path=str(pdf),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.pending,
            extraction_attempt_count=0,
        )
        s.add(doc)
        s.commit()
        doc_id = doc.id

    entered = threading.Event()
    release = threading.Event()
    calls = 0
    lock = threading.Lock()

    def fake_vlm(*_args, **_kwargs):
        nonlocal calls
        with lock:
            calls += 1
        entered.set()
        assert release.wait(10), "the winner was never released"
        return {
            "document_type": "PO",
            "document_number": "PO-RACE",
            "po_no_raw": "PO-RACE",
            "line_items": [{"line_item_no": "1", "description": "x", "quantity": "2"}],
        }

    monkeypatch.setattr(extraction_module, "_call_vlm", fake_vlm)
    barrier = threading.Barrier(2, timeout=5)
    results = []

    def worker():
        barrier.wait()
        results.append(extraction_module.extract_document(doc_id, cfg))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    assert entered.wait(10)
    release.set()
    for t in threads:
        t.join(15)
    assert calls == 1, f"the VLM was called {calls} times for one document"
    with Session(eng) as s:
        row = s.get(Document, doc_id)
        assert row.extraction_status == ExtractionStatus.valid
        assert row.extraction_attempt_count == 1


def test_progress_counts_are_monotonic_for_out_of_order_completion():
    from app.services.sync_progress import SyncProgressTracker

    tracker = SyncProgressTracker()
    tracker.start(10)
    percent = []
    for name in ("c", "a", "b"):
        tracker.task_started(name)
        tracker.task_finished(name)
        percent.append(tracker.snapshot()["percent"])
    assert percent == sorted(percent)
    snap = tracker.snapshot()
    assert snap["completed"] == 3
    assert snap["in_flight"] == 0
    tracker.finish()
    assert tracker.snapshot()["percent"] == 100
