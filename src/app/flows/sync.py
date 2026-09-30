"""Prefect sync flow — one flow per Sync, task per doc (FR-4.1-4.8, FR-12.3, FR-14.1-14.7)."""

from __future__ import annotations

import logging
from pathlib import Path

from prefect import flow, task
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import ExtractionStatus
from app.models.base import Base

logger = logging.getLogger(__name__)

#: Doc types allowed to mint a new PO Set (BLOCKER-5). DN/SI/UNKNOWN docs
#: attach to an already-open set or wait visibly unattached — they must
#: never mint orphan sets from decoy/secondary PO codes.
_ANCHOR_TYPES = ("PO",)


def _doc_type_val(doc) -> str:
    dt = doc.doc_type
    return dt.value if hasattr(dt, "value") else str(dt)


def _persisted_extraction_status(eng, doc_id) -> ExtractionStatus | None:
    """Re-read a document's stored extraction status.

    The Prefect task's own outcome is NOT a reliable success signal. Prefect
    places a task in a COMPLETED state whenever it returns any Python object
    (Prefect v3 docs, "Task return values"), and `extract_document` returns
    normally on the attempt-cap path — a terminal failure, not a success.
    Trusting the task return is how a permanently lost document came to be
    reported as `errors: 0`.

    The document row is the single source of truth for what happened to a
    file, in the same spirit as batch-recovery guidance to treat the updated
    tables as the final point of truth regardless of what the log says.
    """
    from app.models import Document as _Doc

    with Session(eng) as s:
        doc = s.get(_Doc, doc_id)
        if doc is None:
            return None
        return doc.extraction_status


def _quarantine_broken_document(
    doc_id: int, cfg, eng, input_file: Path | None, touched_po_set_ids: set[int]
) -> None:
    """Take a document that failed permanently out of the running.

    The product rule is that anything broken, or that cannot be confirmed, is
    quarantined rather than left looking like normal work. That is applied at
    both levels it can apply to:

    * the document — its input-folder copy is removed and the file is copied
      into `quarantine/_documents/` with a reason, so the next sync does not
      re-hash a dead file and re-report it as an error every night;
    * the PO Set, if the document belongs to one — an unconfirmable set must
      not sit in an apparently-normal `pending` state waiting on a document
      that is never going to arrive.

    Never raises: a failure to tidy up must not abort the sync run. The
    document is already recorded as `failed` and counted in the summary, so a
    problem here is cosmetic, not a silent loss.
    """
    from app.models import Document as _Doc
    from app.models import POSet, POSetStatus
    from app.services.quarantine import quarantine_copy, quarantine_document

    try:
        quarantine_document(
            doc_id,
            cfg,
            reason="Extraction failed permanently (attempt cap reached); "
            "document content could not be read.",
        )
    except Exception:
        logger.warning("Could not quarantine broken document %s", doc_id, exc_info=True)

    # The input copy is what makes the next run re-encounter this file.
    if input_file is not None:
        try:
            Path(input_file).unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "Could not remove quarantined file from input: %s", input_file, exc_info=True
            )

    # Quarantine the set too, when there is one. A set containing a document
    # that cannot be read cannot be confirmed, so it must not look like it is
    # merely waiting.
    try:
        with Session(eng) as s:
            doc = s.get(_Doc, doc_id)
            po_set_id = doc.po_set_id if doc else None
        if po_set_id is not None:
            with Session(eng) as s:
                ps = s.get(POSet, po_set_id)
                if ps is not None and ps.status not in (
                    POSetStatus.merged,
                    POSetStatus.quarantined,
                ):
                    ps.status = POSetStatus.quarantined
                    ps.reconcile_reason = (
                        "Quarantined: a document in this set failed to read, "
                        "so the set could not be verified."
                    )
                    s.commit()
                    touched_po_set_ids.add(po_set_id)
                    quarantine_copy(
                        po_set_id,
                        cfg,
                        reason=ps.reconcile_reason,
                        detail="A member document failed extraction permanently.",
                    )
                    logger.error(
                        "PO Set %s quarantined: a member document failed permanently", po_set_id
                    )
    except Exception:
        logger.warning("Could not quarantine PO Set for document %s", doc_id, exc_info=True)


@task(name="extract_task", retries=3, retry_delay_seconds=[2, 5, 15])
def extract_task(doc_id: int, cfg_path: str | None = None) -> str:
    """Extract and classify a single document via native VLM — one Prefect task per doc (FR-6.1-6.8).

    Wraps app.services.extraction.extract_document with Prefect retry envelope.
    Decorator values are fallback defaults; _extract_task_for applies the
    live config via with_options at every call site (FR-6.5, NFR-2).
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


def _extract_task_for(cfg):
    """extract_task with retry policy driven by config (NFR-2).

    Tasks run sequentially in the sync loop (one call at a time), so
    prefect.max_concurrent_extraction_tasks is trivially satisfied.
    """
    return extract_task.with_options(
        retries=cfg.extraction.max_retries,
        retry_delay_seconds=list(cfg.extraction.retry_backoff_seconds),
    )


@flow(name="sync_flow")
def sync_flow(cfg_path: str | None = None, held_lock=None) -> dict:
    """One Prefect flow per Sync run (FR-4.1-4.8).

    Pipeline sequence:
    1. Ingestion & dedup (SHA-256)
    2. VLM Extraction & Classification per doc (task with retry)
    3. Grouping by normalized PO number into POSet
    4. Reconciliation orchestrator (matching, exact qty aggregate, customs check, auto-merge)
    5. Input folder clearing for merged sets (FR-4.8)

    Concurrency (FR-4.3): the inter-process sync lock is held for the whole
    run. Route-triggered runs pass their already-held lock via held_lock;
    direct invocations (midnight cron) acquire here and return a `skipped`
    summary when another sync holds it.
    """
    from app.services.sync_lock import acquire_sync_lock, release_sync_lock

    own_lock = None
    if held_lock is None:
        own_lock = acquire_sync_lock(cfg_path)
        if own_lock is None:
            logger.warning("Sync skipped: another sync is already running")
            return {
                "status": "skipped",
                "reason": "sync_already_running",
                "processed": 0,
                "extracted": 0,
                "errors": 0,
                "touched_po_sets": 0,
                "reconciled_count": 0,
            }
    try:
        # The OS lock is held for the whole run: either own_lock (acquired
        # above, released below) or held_lock (owned by the route caller).
        return _sync_flow_locked(cfg_path)
    finally:
        if own_lock is not None:
            release_sync_lock(own_lock)


def _sync_flow_locked(cfg_path: str | None = None) -> dict:
    cfg = load_config(cfg_path) if cfg_path else load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    from app.models import Document as _Doc
    from app.models import POSet as _POSet
    from app.models import POSetStatus
    from app.services.grouping import get_or_create_po_set
    from app.services.ingestion import (
        delete_input_files,
        find_input_pdfs,
        ingest_file,
        is_file_stable,
    )
    from app.services.reconciliation import reconcile_po_set

    input_folder = Path(cfg.paths.input_folder)
    input_folder.mkdir(parents=True, exist_ok=True)

    processed = 0
    errors = 0
    touched_po_set_ids: set[int] = set()

    # Discover PDFs in input folder. find_input_pdfs matches the suffix
    # case-insensitively in ONE pass — globbing "*.pdf" and "*.PDF" separately
    # would double the list on Windows and send every document to the VLM twice.
    files = find_input_pdfs(input_folder)
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

            # Extract & Classify via VLM
            try:
                _extract_task_for(cfg)(doc.id, cfg_path=cfg_path)
            except Exception:
                logger.warning("Extract task failed for doc %s", doc.id, exc_info=True)
                errors += 1
            else:
                # A clean task return is NOT proof of success — see
                # _persisted_extraction_status. Count the document from the
                # row it left behind, and quarantine it if it is broken.
                if _persisted_extraction_status(eng, doc.id) == ExtractionStatus.failed:
                    logger.error(
                        "Extraction did not succeed for doc %s; counted as an error",
                        doc.id,
                    )
                    errors += 1
                    _quarantine_broken_document(doc.id, cfg, eng, f, touched_po_set_ids)

            # Group into PO Set (FR-7.1-7.2). Only PO mints;
            # DN/SI/UNKNOWN attach to an open set or wait unattached.
            try:
                with Session(eng) as s2:
                    d2 = s2.get(_Doc, doc.id)
                    if d2 and d2.po_no_normalized:
                        ps = get_or_create_po_set(
                            d2.po_no_raw or d2.po_no_normalized,
                            cfg,
                            create=_doc_type_val(d2) in _ANCHOR_TYPES,
                        )
                        if ps is None:
                            continue
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
    with Session(eng) as s:
        pending = s.query(_Doc).filter(_Doc.extraction_status == ExtractionStatus.pending).all()
        for doc in pending:
            try:
                _extract_task_for(cfg)(doc.id, cfg_path=cfg_path)
            except Exception:
                logger.warning("Extract task failed for pending doc %s", doc.id, exc_info=True)
                errors += 1
            else:
                if _persisted_extraction_status(eng, doc.id) == ExtractionStatus.failed:
                    logger.error(
                        "Extraction did not succeed for pending doc %s; counted as an error",
                        doc.id,
                    )
                    errors += 1
                    _quarantine_broken_document(doc.id, cfg, eng, None, touched_po_set_ids)
            try:
                d = s.get(_Doc, doc.id)
                if d and d.po_no_normalized:
                    ps = get_or_create_po_set(
                        d.po_no_raw or d.po_no_normalized,
                        cfg,
                        create=_doc_type_val(d) in _ANCHOR_TYPES,
                    )
                    if ps is None:
                        continue
                    if d.po_set_id is None:
                        d.po_set_id = ps.id
                        s.commit()
                    touched_po_set_ids.add(ps.id)
            except Exception:
                logger.warning("Grouping failed for pending doc %s", doc.id, exc_info=True)
                errors += 1

    # Resolve unattached documents (e.g. Delivery Notes without PO printed on face)
    from app.services.grouping import attach_unattached_to_open_sets, resolve_unattached_documents

    unattached_touched = resolve_unattached_documents(cfg)
    touched_po_set_ids.update(unattached_touched)

    # Attach DN/SI/UNKNOWN docs that waited for their PO anchor (BLOCKER-5).
    # Never mints: keys without an open set keep waiting indefinitely.
    attached_touched = attach_unattached_to_open_sets(cfg)
    touched_po_set_ids.update(attached_touched)

    # Phase 1: Reconcile newly touched PO Sets (FR-4.8, FR-14.1)
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

    # Phase 2: Re-reconcile all open (non-merged) sets — catches stale mismatched/pending
    # sets whose documents were already present before this run started.
    with Session(eng) as s_sweep:
        open_sets = (
            s_sweep.query(_POSet)
            .filter(_POSet.status != POSetStatus.merged)
            .with_entities(_POSet.id)
            .all()
        )
    for (ps_id,) in open_sets:
        if ps_id in touched_po_set_ids:
            continue  # already reconciled in phase 1
        try:
            res = reconcile_po_set(ps_id, cfg)
            reconciled_count += 1
            if res.get("status") == POSetStatus.merged.value or res.get("status") == "merged":
                with Session(eng) as s3:
                    ps_merged = s3.get(_POSet, ps_id)
                    if ps_merged:
                        delete_input_files(ps_merged, input_folder)
        except Exception:
            logger.warning("Re-reconcile sweep failed for PO Set %s", ps_id, exc_info=True)
            errors += 1

    return {
        "processed": processed,
        # `processed` counts files that were successfully ingested, while
        # `errors` counts failures at ANY stage including ingestion itself.
        # The two are therefore not a partition of the same set, so the
        # subtraction is clamped — an operator must never be shown a negative
        # number of extractions.
        "extracted": max(0, processed - errors),
        "errors": errors,
        "touched_po_sets": len(touched_po_set_ids),
        "reconciled_count": reconciled_count,
    }
