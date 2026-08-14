"""Prefect sync flow — one flow per Sync, task per doc (FR-4.3)."""

from __future__ import annotations

from pathlib import Path

from prefect import flow, task
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models.base import Base


@task(name="classify_task", retries=0)
def classify_task(doc_id: int, cfg_path: str | None = None) -> str:
    """Classify a single document — one Prefect task per doc (Task 9).

    Loads Document by id, runs keyword heuristic via app.services.classification,
    updates doc.doc_type if UNKNOWN, returns doc_type string.
    """
    cfg = load_config(cfg_path) if cfg_path else load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    from app.models import Document
    from app.services.classification import classify

    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            return "UNKNOWN"
        # use stored_path or original_filename heuristic; here use raw from doc_type or filename
        raw = doc.original_filename or ""
        # call classify stub — expects dict with raw
        try:
            result = classify({"raw": raw})
        except Exception:
            result = "UNKNOWN"
        # update doc_type if still UNKNOWN
        if str(doc.doc_type) == "UNKNOWN" or doc.doc_type is None:
            from app.models import DocType

            try:
                # classify returns str like "PO"; map to enum if possible
                enum_val = (
                    DocType(result)
                    if result in DocType.__members__.values()
                    or result in [e.value for e in DocType]
                    else DocType.UNKNOWN
                )  # type: ignore[arg-type]
            except Exception:
                enum_val = DocType.UNKNOWN
            doc.doc_type = enum_val
            s.commit()
        return result


@flow(name="sync_flow")
def sync_flow(cfg_path: str | None = None) -> dict:
    """One Prefect flow per Sync run (FR-4.3). Iterates input_folder, classifies per doc.

    For each file found, ingests (dedup) and fires a classify_task.
    Returns summary dict.
    """
    cfg = load_config(cfg_path) if cfg_path else load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    from app.services.ingestion import ingest_file, is_file_stable

    input_folder = Path(cfg.paths.input_folder)
    input_folder.mkdir(parents=True, exist_ok=True)

    processed = 0
    classified = 0
    errors = 0

    # discover PDFs
    files = list(input_folder.glob("*.pdf")) + list(input_folder.glob("*.PDF"))
    for f in files:
        try:
            # stability check: 2 polls configurable
            stable = is_file_stable(
                f,
                interval=cfg.ingestion.stability_poll_interval_seconds,
                count=cfg.ingestion.stability_poll_count,
            )
            if not stable:
                continue
            doc = ingest_file(f, cfg)
            processed += 1
            # one task per doc
            try:
                classify_task(doc.id, cfg_path=cfg_path)
                classified += 1
            except Exception:
                errors += 1
        except Exception:
            errors += 1
            continue

    # handle pending docs not yet in input folder (already ingested but pending)
    from app.models import Document, ExtractionStatus

    with Session(eng) as s:
        pending = (
            s.query(Document).filter(Document.extraction_status == ExtractionStatus.pending).all()
        )
        for doc in pending:
            try:
                classify_task(doc.id, cfg_path=cfg_path)
                classified += 1
            except Exception:
                errors += 1

    return {"processed": processed, "classified": classified, "errors": errors}
