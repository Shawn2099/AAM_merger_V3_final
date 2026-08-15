"""Prefect sync flow — one flow per Sync, task per doc (FR-4.1-4.8, FR-12.3, FR-14.1-14.7)."""

from __future__ import annotations

import logging
from pathlib import Path

from prefect import flow, task
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models.base import Base

logger = logging.getLogger(__name__)


@task(name="classify_task", retries=3, retry_delay_seconds=[2, 5, 15])
def classify_task(doc_id: int, cfg_path: str | None = None) -> str:
    """Classify a single document — one Prefect task per doc (FR-5.1-5.3).

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
        raw = doc.original_filename or ""
        try:
            result = classify({"raw": raw})
        except Exception:
            logger.warning("Classification failed for doc %s", doc_id, exc_info=True)
            result = "UNKNOWN"
        if str(doc.doc_type) == "UNKNOWN" or doc.doc_type is None:
            from app.models import DocType

            try:
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


@task(name="extract_task", retries=3, retry_delay_seconds=[2, 5, 15])
def extract_task(doc_id: int, cfg_path: str | None = None) -> str:
    """Extract a single document — one Prefect task per doc (FR-6.1-6.8).

    Wraps app.services.extraction.extract_document with Prefect retry envelope.
    """
    cfg = load_config(cfg_path) if cfg_path else load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    from app.services.extraction import extract_document

    doc = extract_document(doc_id, cfg)
    return str(
        doc.extraction_status.value
        if hasattr(doc.extraction_status, "value")
        else doc.extraction_status
    )


@flow(name="sync_flow")
def sync_flow(cfg_path: str | None = None) -> dict:
    """One Prefect flow per Sync run (FR-4.1-4.8).

    Pipeline sequence:
    1. Ingestion & dedup (SHA-256)
    2. Classification per doc (task with retry)
    3. Extraction per doc (task with retry)
    4. Grouping by normalized PO number into POSet
    5. Reconciliation orchestrator (matching, exact qty aggregate, customs check, auto-merge)
    6. Input folder clearing for merged sets (FR-4.8)
    """
    cfg = load_config(cfg_path) if cfg_path else load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    from app.models import Document as _Doc
    from app.models import POSet as _POSet
    from app.models import POSetStatus
    from app.services.grouping import get_or_create_po_set
    from app.services.ingestion import delete_input_files, ingest_file, is_file_stable
    from app.services.reconciliation import reconcile_po_set

    input_folder = Path(cfg.paths.input_folder)
    input_folder.mkdir(parents=True, exist_ok=True)

    processed = 0
    classified = 0
    errors = 0
    touched_po_set_ids: set[int] = set()

    # Discover PDFs in input folder
    files = list(input_folder.glob("*.pdf")) + list(input_folder.glob("*.PDF"))
    for f in files:
        try:
            # Stability poll (FR-4.5)
            stable = is_file_stable(
                f,
                interval=cfg.ingestion.stability_poll_interval_seconds,
                count=cfg.ingestion.stability_poll_count,
            )
            if not stable:
                continue

            doc = ingest_file(f, cfg)
            processed += 1

            # Classify
            try:
                classify_task(doc.id, cfg_path=cfg_path)
                classified += 1
            except Exception:
                logger.warning("Classify task failed for doc %s", doc.id, exc_info=True)
                errors += 1

            # Extract
            try:
                extract_task(doc.id, cfg_path=cfg_path)
            except Exception:
                logger.warning("Extract task failed for doc %s", doc.id, exc_info=True)
                errors += 1

            # Group into PO Set (FR-7.1-7.2)
            try:
                with Session(eng) as s2:
                    d2 = s2.get(_Doc, doc.id)
                    if d2 and d2.po_no_normalized:
                        ps = get_or_create_po_set(d2.po_no_raw or d2.po_no_normalized, cfg)
                        if d2.po_set_id is None:
                            d2.po_set_id = ps.id
                            s2.commit()
                        touched_po_set_ids.add(ps.id)
            except Exception:
                logger.warning("Grouping failed for doc %s", doc.id, exc_info=True)
                errors += 1

        except Exception:
            logger.warning("Ingestion loop failed for file %s", f, exc_info=True)
            errors += 1
            continue

    # Also handle pending docs already in DB (e.g. from prior runs)
    from app.models import ExtractionStatus

    with Session(eng) as s:
        pending = s.query(_Doc).filter(_Doc.extraction_status == ExtractionStatus.pending).all()
        for doc in pending:
            try:
                classify_task(doc.id, cfg_path=cfg_path)
                classified += 1
            except Exception:
                logger.warning("Classify task failed for pending doc %s", doc.id, exc_info=True)
                errors += 1
            try:
                extract_task(doc.id, cfg_path=cfg_path)
            except Exception:
                logger.warning("Extract task failed for pending doc %s", doc.id, exc_info=True)
                errors += 1
            try:
                d = s.get(_Doc, doc.id)
                if d and d.po_no_normalized:
                    ps = get_or_create_po_set(d.po_no_raw or d.po_no_normalized, cfg)
                    if d.po_set_id is None:
                        d.po_set_id = ps.id
                        s.commit()
                    touched_po_set_ids.add(ps.id)
            except Exception:
                logger.warning("Grouping failed for pending doc %s", doc.id, exc_info=True)
                errors += 1

    # Reconcile all touched PO Sets and clear input files on merge (FR-4.8, FR-14.1)
    reconciled_count = 0
    for ps_id in touched_po_set_ids:
        try:
            res = reconcile_po_set(ps_id, cfg)
            reconciled_count += 1
            if res.get("status") == POSetStatus.merged.value or res.get("status") == "merged":
                with Session(eng) as s3:
                    ps_merged = s3.get(_POSet, ps_id)
                    if ps_merged:
                        delete_input_files(ps_merged, input_folder)
        except Exception:
            logger.warning("Reconciliation failed for PO Set %s", ps_id, exc_info=True)
            errors += 1

    return {
        "processed": processed,
        "classified": classified,
        "errors": errors,
        "touched_po_sets": len(touched_po_set_ids),
        "reconciled_count": reconciled_count,
    }
