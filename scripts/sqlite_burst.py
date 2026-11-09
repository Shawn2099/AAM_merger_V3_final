"""SQLite parallel-extraction burst harness.

Runs N documents through the real bounded extraction pool with a fake VLM,
while a dashboard reader and a competing writer simulate the rest of the app.
It fails on any database-locked/error outcome and reports WAL size, max
in-flight calls, and throughput.

This is an evidence tool, not production code. It never touches the configured
application database: every run gets its own temporary database and folders
unless --workdir is supplied.

Usage:
    uv run python scripts/sqlite_burst.py --documents 200 --workers 3
    uv run python scripts/sqlite_burst.py --documents 600 --workers 3 --keep
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from app.core.config import load_config  # noqa: E402
from app.core.database import ensure_schema  # noqa: E402
from app.flows.sync import ExtractionJob, run_extractions  # noqa: E402
from app.models import DocType, Document, ExtractionStatus, LineItem  # noqa: E402
from app.services import extraction as extraction_module  # noqa: E402


def _configure(workdir: Path, workers: int):
    cfg = load_config(str(ROOT / "config.example.yaml"))
    cfg.paths.database_path = workdir / "burst.db"
    cfg.paths.input_folder = workdir / "input"
    cfg.paths.output_folder = workdir / "output"
    cfg.paths.quarantine_folder = workdir / "quarantine"
    cfg.paths.stored_documents_folder = workdir / "stored"
    cfg.paths.combined_folder = workdir / "combined"
    cfg.paths.archive_folder = workdir / "archive"
    cfg.paths.log_folder = workdir / "logs"
    for path in (
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.combined_folder,
        cfg.paths.archive_folder,
        cfg.paths.log_folder,
    ):
        Path(path).mkdir(parents=True, exist_ok=True)
    cfg.schedule.poll_enabled = False
    cfg.auth.enabled = False
    cfg.prefect.max_concurrent_extraction_tasks = workers
    return cfg


def _seed(cfg, documents: int) -> tuple[list[int], int]:
    """Insert ``documents`` pending POs plus one heartbeat row for the writer."""
    ids: list[int] = []
    stored = Path(cfg.paths.stored_documents_folder)
    eng = ensure_schema(cfg)
    with Session(eng) as s:
        for i in range(documents):
            pdf = stored / f"burst-{i}.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            doc = Document(
                sha256_hash=f"burst-{i:06d}",
                original_filename=f"burst-{i}.pdf",
                stored_path=str(pdf),
                doc_type=DocType.PO,
                extraction_status=ExtractionStatus.pending,
                extraction_attempt_count=0,
            )
            s.add(doc)
            s.flush()
            ids.append(doc.id)
        heartbeat = Document(
            sha256_hash="heartbeat-burst",
            original_filename="heartbeat.pdf",
            stored_path=str(stored / "heartbeat.pdf"),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.valid,
            extraction_attempt_count=0,
        )
        s.add(heartbeat)
        s.commit()
        heartbeat_id = heartbeat.id
    return ids, heartbeat_id


def _fake_vlm(latency_seconds: float, items: int):
    active = 0
    max_active = 0
    lock = threading.Lock()

    def call(stored_path, _dtype, _cfg):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(latency_seconds)
            stem = Path(stored_path).stem
            return {
                "document_type": "PO",
                "document_number": f"PO-{stem}",
                "po_no_raw": f"PO-{stem}",
                "vendor_name": "BURST VENDOR",
                "line_items": [
                    {
                        "line_item_no": str(n),
                        "description": f"burst item {n}",
                        "quantity": "2",
                    }
                    for n in range(1, items + 1)
                ],
            }
        finally:
            with lock:
                active -= 1

    return call, lambda: max_active


def run_burst(
    *,
    documents: int = 200,
    workers: int = 3,
    vlm_latency_ms: float = 25.0,
    dashboard_interval_ms: float = 50.0,
    writer_interval_ms: float = 20.0,
    items: int = 3,
    workdir: Path | None = None,
    keep: bool = False,
) -> dict:
    """Run the burst and return a summary. Raises AssertionError on evidence of failure."""
    workdir = Path(workdir) if workdir is not None else Path(
        tempfile.mkdtemp(prefix="aam-sqlite-burst-")
    )
    workdir.mkdir(parents=True, exist_ok=True)
    cfg = _configure(workdir, workers)
    ids, heartbeat_id = _seed(cfg, documents)
    eng = ensure_schema(cfg)
    db_path = Path(cfg.paths.database_path)
    errors: list[dict] = []
    outcomes = []

    fake_vlm, max_active = _fake_vlm(vlm_latency_ms / 1000.0, items)
    original_vlm = extraction_module._call_vlm
    extraction_module._call_vlm = fake_vlm

    stop = threading.Event()
    wal_peak = 0
    dashboard_reads = 0
    writer_commits = 0

    def dashboard_reader() -> None:
        nonlocal dashboard_reads
        while not stop.is_set():
            try:
                with Session(eng) as s:
                    s.execute(select(func.count()).select_from(Document)).scalar()
                    s.execute(select(Document).order_by(Document.id.desc()).limit(20)).all()
                    dashboard_reads += 1
            except Exception as exc:  # pragma: no cover - evidence, not production
                errors.append({"source": "dashboard", "error": repr(exc)})
            stop.wait(dashboard_interval_ms / 1000.0)

    def competing_writer() -> None:
        nonlocal writer_commits
        while not stop.is_set():
            try:
                with Session(eng) as s:
                    s.execute(
                        update(Document)
                        .where(Document.id == heartbeat_id)
                        .values(updated_at=datetime.now(UTC))
                    )
                    s.commit()
                    writer_commits += 1
            except Exception as exc:  # pragma: no cover - evidence, not production
                errors.append({"source": "writer", "error": repr(exc)})
            stop.wait(writer_interval_ms / 1000.0)

    def wal_monitor() -> None:
        nonlocal wal_peak
        wal = Path(str(db_path) + "-wal")
        while not stop.is_set():
            with contextlib.suppress(OSError):
                wal_peak = max(wal_peak, wal.stat().st_size)
            stop.wait(0.02)

    threads = [
        threading.Thread(target=dashboard_reader, daemon=True),
        threading.Thread(target=competing_writer, daemon=True),
        threading.Thread(target=wal_monitor, daemon=True),
    ]
    for thread in threads:
        thread.start()

    def extract_fn(doc_id: int):
        try:
            return extraction_module.extract_document(doc_id, cfg)
        except Exception as exc:
            errors.append({"doc_id": doc_id, "error": f"{type(exc).__name__}: {exc}"})
            raise

    jobs = [
        ExtractionJob(doc_id=doc_id, input_file=None, filename=f"burst-{doc_id}.pdf")
        for doc_id in ids
    ]
    extraction_module.reset_provider_breaker()
    started = time.monotonic()
    try:
        outcomes = run_extractions(jobs, cfg, extract_fn=extract_fn)
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)
        extraction_module.end_provider_breaker()
        extraction_module._call_vlm = original_vlm

    elapsed = time.monotonic() - started
    with Session(eng) as s:
        valid = s.execute(
            select(func.count())
            .select_from(Document)
            .where(
                Document.sha256_hash.like("burst-%"),
                Document.extraction_status == ExtractionStatus.valid,
            )
        ).scalar_one()
        attempts = s.execute(
            select(func.count())
            .select_from(Document)
            .where(Document.sha256_hash.like("burst-%"), Document.extraction_attempt_count == 1)
        ).scalar_one()
        line_items = s.execute(select(func.count()).select_from(LineItem)).scalar_one()

    raw = eng.raw_connection()
    try:
        raw.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        raw.close()
    wal_after = Path(str(db_path) + "-wal")
    wal_after_mb = wal_after.stat().st_size / 1024 / 1024 if wal_after.exists() else 0.0
    db_mb = db_path.stat().st_size / 1024 / 1024 if db_path.exists() else 0.0

    statuses: dict[str, int] = {}
    for outcome in outcomes:
        statuses[outcome.status] = statuses.get(outcome.status, 0) + 1

    summary = {
        "workdir": str(workdir),
        "documents": documents,
        "workers": workers,
        "items_per_document": items,
        "vlm_latency_ms": vlm_latency_ms,
        "duration_seconds": round(elapsed, 3),
        "documents_per_second": round(documents / elapsed, 1) if elapsed else None,
        "max_in_flight": max_active(),
        "outcome_statuses": statuses,
        "valid": valid,
        "attempted_once": attempts,
        "line_items": line_items,
        "errors": errors,
        "dashboard_reads": dashboard_reads,
        "writer_commits": writer_commits,
        "wal_peak_mb": round(wal_peak / 1024 / 1024, 2),
        "wal_after_checkpoint_mb": round(wal_after_mb, 2),
        "db_mb": round(db_mb, 2),
    }

    failures = []
    if errors:
        failures.append(f"{len(errors)} error(s), first={errors[0]}")
    if summary["max_in_flight"] > workers:
        failures.append(f"max_in_flight {summary['max_in_flight']} > workers {workers}")
    if valid != documents:
        failures.append(f"valid {valid} != documents {documents}")
    if attempts != documents:
        failures.append(f"attempted_once {attempts} != documents {documents}")
    if line_items != documents * items:
        failures.append(f"line_items {line_items} != {documents * items}")
    if any(outcome.status == "error" for outcome in outcomes):
        failures.append("at least one extraction outcome is 'error'")
    summary["failures"] = failures
    summary["passed"] = not failures

    if not keep:
        shutil.rmtree(workdir, ignore_errors=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite parallel-extraction burst harness")
    parser.add_argument("--documents", type=int, default=200)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--vlm-latency-ms", type=float, default=25.0)
    parser.add_argument("--dashboard-interval-ms", type=float, default=50.0)
    parser.add_argument("--writer-interval-ms", type=float, default=20.0)
    parser.add_argument("--items", type=int, default=3)
    parser.add_argument("--workdir", type=Path, default=None)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    summary = run_burst(
        documents=args.documents,
        workers=args.workers,
        vlm_latency_ms=args.vlm_latency_ms,
        dashboard_interval_ms=args.dashboard_interval_ms,
        writer_interval_ms=args.writer_interval_ms,
        items=args.items,
        workdir=args.workdir,
        keep=args.keep,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
