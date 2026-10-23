"""Prefect sync flow — one flow per Sync, task per doc (FR-4.1-4.8, FR-12.3, FR-14.1-14.7)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from prefect import flow, task
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import ExtractionStatus
from app.models.base import Base
from app.services.sync_lock import heartbeat_sync_lock

logger = logging.getLogger(__name__)

#: Doc types allowed to mint a new PO Set (BLOCKER-5). DN/SI docs attach to
#: an already-open set or wait visibly unattached — they must never mint
#: orphan sets from decoy/secondary PO codes. A type outside PO/DN/SI never
#: attaches at all until an operator reclassifies it (grouping._ATTACHABLE).
_ANCHOR_TYPES = ("PO",)

#: How many files are stability-polled at the same time. The poll is pure
#: waiting (sample a size, sleep, sample again), so the threads spend their
#: time asleep and the host's two cores are free.
_STABILITY_POLL_WORKERS = 8


def _doc_type_val(doc) -> str:
    dt = doc.doc_type
    return dt.value if hasattr(dt, "value") else str(dt)


def _already_read(doc) -> bool:
    """True when this document has been read already and must not be read again.

    The same rule `services.extraction.extract_document` applies, used one
    level up so the task is never *scheduled* for such a document. That
    matters because a Prefect task on the `process` work pool launches a
    process per call: on the live vendor batch, scheduling a task for a file
    that was already read cost about eleven seconds per file — fifty minutes
    of a nightly sync spent launching processes to do nothing.

    The one exception is a split parent whose split never completed: it
    must be re-processed so the children are re-derived from the parent.
    """
    if doc.extraction_status != ExtractionStatus.valid:
        return False
    split_incomplete = bool(getattr(doc, "is_split_parent", False)) and not getattr(
        doc, "split_completed_at", None
    )
    return not split_incomplete


def _poll_stability(files, cfg) -> dict:
    """Sample every file's size until it stops changing (FR-4.5).

    A file still being copied into the input folder must not be read. The
    check is pure waiting: `interval` x `count` seconds per file, and the
    files are independent of each other. Polled one after another, a batch of
    200 files spends 13 minutes of the sync asleep. Polled together, it
    spends one poll's worth of time.

    Only the sampling is parallel; the ingest itself stays on one thread,
    because SQLite is a single-writer database.
    """
    from app.services.ingestion import is_file_stable

    if not files:
        return {}
    interval = cfg.ingestion.stability_poll_interval_seconds
    count = cfg.ingestion.stability_poll_count
    workers = max(1, min(_STABILITY_POLL_WORKERS, len(files)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(
                lambda f: (f, is_file_stable(f, interval=interval, count=count)),
                files,
            )
        )
    return dict(results)


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
    doc_id: int,
    cfg,
    eng,
    input_file: Path | None,
    touched_po_set_ids: set[int],
    reason: str | None = None,
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

    default_reason = (
        "Extraction failed permanently (attempt cap reached); document content could not be read."
    )
    try:
        quarantine_document(
            doc_id,
            cfg,
            reason=reason or default_reason,
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
    #
    # The write is a single conditional UPDATE, not read-then-write. A merge
    # running concurrently (the nightly sweep and a route merge can interleave)
    # may have committed `merged` between the read and this write; the old code
    # then overwrote a delivered set with `quarantined`, after which the
    # quarantine-delete route could delete its DB row while the packet stayed
    # on disk. A merged set is terminal and must never be re-statused by a
    # background writer.
    try:
        with Session(eng) as s:
            doc = s.get(_Doc, doc_id)
            po_set_id = doc.po_set_id if doc else None
        if po_set_id is not None:
            from sqlalchemy import update as _sa_update

            reason = (
                "Quarantined: a document in this set failed to read, "
                "so the set could not be verified."
            )
            with Session(eng) as s:
                result = s.execute(
                    _sa_update(POSet)
                    .where(
                        POSet.id == po_set_id,
                        POSet.archived_at.is_(None),
                        POSet.status.notin_([POSetStatus.merged, POSetStatus.quarantined]),
                    )
                    .values(status=POSetStatus.quarantined, reconcile_reason=reason)
                    .execution_options(synchronize_session=False)
                )
                s.commit()
                changed = getattr(result, "rowcount", 0) > 0
            if changed:
                touched_po_set_ids.add(po_set_id)
                quarantine_copy(
                    po_set_id,
                    cfg,
                    reason=reason,
                    detail="A member document failed extraction permanently.",
                )
                logger.error(
                    "PO Set %s quarantined: a member document failed permanently", po_set_id
                )
            else:
                logger.info(
                    "PO Set %s was already merged or quarantined; the failed document did not "
                    "re-status it",
                    po_set_id,
                )
    except Exception:
        logging.getLogger(__name__).warning(
            "Could not quarantine PO Set for document %s", doc_id, exc_info=True
        )


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


@flow(name="recovery_flow")
def recovery_flow(
    cfg_path: str | None = None,
    held_lock=None,
    initial_touched_po_set_ids: list[int] | None = None,
) -> dict:
    """Dedicated flow for pending document recovery, unattached document resolution, and open-set reconciliation sweep.

    Can run standalone (e.g. background recovery cron or manual trigger) or as
    a subflow from `sync_flow`.
    """
    from app.services.sync_lock import acquire_sync_lock, release_sync_lock

    own_lock = None
    if held_lock is None:
        own_lock = acquire_sync_lock(cfg_path)
        if own_lock is None:
            logger.warning("Recovery skipped: sync or recovery is already running")
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
        return _recovery_flow_locked(
            cfg_path=cfg_path, initial_touched_po_set_ids=initial_touched_po_set_ids
        )
    finally:
        if own_lock is not None:
            release_sync_lock(own_lock)


def _recovery_flow_locked(
    cfg_path: str | None = None,
    initial_touched_po_set_ids: list[int] | None = None,
) -> dict:
    cfg = load_config(cfg_path) if cfg_path else load_config()
    from app.core.logging_setup import setup_logging

    setup_logging(cfg)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    from app.models import Document as _Doc
    from app.models import POSet as _POSet
    from app.models import POSetStatus
    from app.services.extraction import reclaim_stale_processing
    from app.services.grouping import (
        attach_unattached_to_open_sets,
        group_document,
        resolve_unattached_documents,
    )
    from app.services.ingestion import delete_input_files
    from app.services.locking import acquire_lock, is_locked, release_lock
    from app.services.reconciliation import reconcile_po_set

    # A document claimed by a worker that died is still marked `processing`.
    # Release it first, so it is picked up as pending below and read again.
    reclaim_stale_processing(cfg)

    input_folder = Path(cfg.paths.input_folder)
    input_folder.mkdir(parents=True, exist_ok=True)

    touched_po_set_ids: set[int] = set(initial_touched_po_set_ids or [])
    recovered_processed = 0
    recovered_errors = 0
    recovered_extracted = 0

    def _reconcile_with_lock_guard(ps_id: int) -> dict | None:
        """Reconcile a POSet, skipping if a UI action currently holds the per-PO DB lock.

        Skipping is safe: UI routes (redo_extract, redo_match, force_merge) call
        reconcile_po_set themselves on completion, so the set is reconciled with
        the correct post-action state. The sweep never permanently orphans a set
        because the next sync run will encounter it again in Phase 2.

        The lock acquired here uses action name 'sync_reconcile'. If a UI action
        tries to act on this set while the sweep holds the lock it will get a 409,
        which is correct \u2014 one state-changing operation at a time per PO Set.
        """
        with Session(eng) as s:
            ps = s.get(_POSet, ps_id)
            if ps is None or ps.archived_at is not None:
                return None
            if is_locked(ps, cfg):
                logger.info(
                    "Reconcile sweep skipping POSet %s: held by UI action '%s'",
                    ps_id,
                    ps.locked_by_action,
                )
                return None
            acquired = acquire_lock(ps, "sync_reconcile", s, cfg)
            if not acquired:
                logger.info(
                    "Reconcile sweep skipping POSet %s: could not acquire lock (held by '%s')",
                    ps_id,
                    ps.locked_by_action,
                )
                return None

        try:
            res = reconcile_po_set(ps_id, cfg)
            if res.get("status") in ("merged", POSetStatus.merged.value):
                with Session(eng) as s3:
                    ps_merged = s3.get(_POSet, ps_id)
                    if ps_merged:
                        delete_input_files(ps_merged, input_folder)
            return res
        finally:
            # Release using a fresh session \u2014 same pattern as _release_lock in routes.
            # action-scoped release: never clears a lock set by a concurrent UI action.
            with Session(eng) as s_rel:
                ps_rel = s_rel.get(_POSet, ps_id)
                if ps_rel is not None:
                    release_lock(ps_rel, s_rel, action="sync_reconcile")

    # 1. Pending docs already in DB (from prior runs or interrupted syncs)
    with Session(eng) as s:
        pending_ids = [
            r[0]
            for r in s.query(_Doc.id)
            .filter(
                _Doc.extraction_status == ExtractionStatus.pending,
                _Doc.archived_at.is_(None),
            )
            .all()
        ]

    for doc_id in pending_ids:
        heartbeat_sync_lock(cfg_path)
        recovered_processed += 1
        try:
            _extract_task_for(cfg)(doc_id, cfg_path=cfg_path)
        except Exception:
            logger.warning("Extract task failed for pending doc %s", doc_id, exc_info=True)
            recovered_errors += 1
        else:
            persisted = _persisted_extraction_status(eng, doc_id)
            if persisted == ExtractionStatus.failed:
                logger.error(
                    "Extraction did not succeed for pending doc %s; counted as an error",
                    doc_id,
                )
                recovered_errors += 1
                _quarantine_broken_document(doc_id, cfg, eng, None, touched_po_set_ids)
            elif persisted == ExtractionStatus.valid:
                recovered_extracted += 1

        try:
            with Session(eng) as s_grp:
                d = s_grp.get(_Doc, doc_id)
                if d and not d.is_split_parent:
                    ps_id, problem = group_document(d, cfg)
                    if problem == "missing_po_number":
                        # Read successfully, no PO reference on the face of
                        # the document: it waits, visible, for the content to
                        # place it or for a human to name its PO.
                        logger.warning(
                            "Document %s (%s) carries no PO reference — it stays unplaced "
                            "and visible in the holding area",
                            d.id,
                            d.original_filename,
                        )
                    elif problem == "ambiguous_po_number":
                        logger.warning(
                            "Document %s (%s) shows more than one PO number — it was not "
                            "placed in a PO Set and waits for a human",
                            d.id,
                            d.original_filename,
                        )
                    elif ps_id is not None:
                        if d.po_set_id is None:
                            d.po_set_id = ps_id
                            s_grp.commit()
                        touched_po_set_ids.add(ps_id)
        except Exception:
            logger.warning("Grouping failed for pending doc %s", doc_id, exc_info=True)
            recovered_errors += 1

    # 2. Resolve unattached documents (e.g. Delivery Notes without PO printed on face)
    unattached_touched = resolve_unattached_documents(cfg)
    touched_po_set_ids.update(unattached_touched)

    # 3. Attach DN/SI/UNKNOWN docs that waited for their PO anchor (BLOCKER-5)
    attached_touched = attach_unattached_to_open_sets(cfg)
    touched_po_set_ids.update(attached_touched)

    # 4. Phase 1: Reconcile newly touched PO Sets (FR-4.8, FR-14.1)
    # Per-PO lock guard: if a UI action (force_merge, redo_extract) holds the
    # lock for this set, we skip it. The UI action calls reconcile_po_set on
    # completion, so the set is reconciled with correct post-action state.
    heartbeat_sync_lock(cfg_path)
    reconciled_count = 0
    reconciled_set_ids: set[int] = set()
    for ps_id in sorted(touched_po_set_ids):
        heartbeat_sync_lock(cfg_path)
        try:
            res = _reconcile_with_lock_guard(ps_id)
            if res is not None:
                reconciled_count += 1
                reconciled_set_ids.add(ps_id)
        except Exception:
            logger.warning("Reconciliation failed for PO Set %s", ps_id, exc_info=True)
            recovered_errors += 1

    # 5. Phase 2: Re-reconcile sweep of all open (non-merged) sets
    with Session(eng) as s_sweep:
        open_sets = (
            s_sweep.query(_POSet)
            .filter(
                _POSet.status != POSetStatus.merged,
                _POSet.archived_at.is_(None),
            )
            .with_entities(_POSet.id)
            .all()
        )
    for (ps_id,) in open_sets:
        heartbeat_sync_lock(cfg_path)
        if ps_id in reconciled_set_ids:
            continue
        try:
            res = _reconcile_with_lock_guard(ps_id)
            if res is not None:
                reconciled_count += 1
                reconciled_set_ids.add(ps_id)
        except Exception:
            logger.warning("Re-reconcile sweep failed for PO Set %s", ps_id, exc_info=True)
            recovered_errors += 1

    # 6. Retry customs appends that failed earlier. Phase 2 skips merged sets,
    # so a merged set that still owes its packet a customs rebuild would
    # otherwise never be revisited.
    from app.services.merge import append_customs_if_complete, set_customs_append_pending

    with Session(eng) as s_app:
        owing_ids = [
            r[0]
            for r in s_app.query(_POSet.id)
            .filter(
                _POSet.status == POSetStatus.merged,
                _POSet.archived_at.is_(None),
                _POSet.customs_append_pending.is_(True),
            )
            .all()
        ]
    for ps_id in owing_ids:
        heartbeat_sync_lock(cfg_path)
        with Session(eng) as s_lk:
            ps_lk = s_lk.get(_POSet, ps_id)
            if ps_lk is None or not acquire_lock(ps_lk, "sync_customs_append", s_lk, cfg):
                logger.info("Customs append retry skipping POSet %s: it is locked", ps_id)
                continue
            # The flag may have been cleared while we waited for the lock.
            s_lk.refresh(ps_lk)
            still_owed = bool(ps_lk.customs_append_pending)
        if not still_owed:
            with Session(eng) as s_rel:
                ps_rel = s_rel.get(_POSet, ps_id)
                if ps_rel is not None:
                    release_lock(ps_rel, s_rel, action="sync_customs_append")
            continue
        try:
            if append_customs_if_complete(ps_id, cfg, source="sync") is None:
                # Nothing to append any more (requirement off, or a document was
                # removed): the debt no longer exists, the badge covers the rest.
                set_customs_append_pending(ps_id, cfg, False)
        except Exception:
            logger.warning("Customs append retry failed for PO Set %s", ps_id, exc_info=True)
            recovered_errors += 1
        finally:
            with Session(eng) as s_rel:
                ps_rel = s_rel.get(_POSet, ps_id)
                if ps_rel is not None:
                    release_lock(ps_rel, s_rel, action="sync_customs_append")

    return {
        "processed": recovered_processed,
        "extracted": recovered_extracted,
        "errors": recovered_errors,
        "touched_po_sets": len(touched_po_set_ids),
        "reconciled_count": reconciled_count,
    }


@flow(name="sync_flow")
def sync_flow(cfg_path: str | None = None, held_lock=None) -> dict:
    """One Prefect flow per Sync run (FR-4.1-4.8).

    Pipeline sequence:
    1. Ingestion & dedup (SHA-256)
    2. VLM Extraction & Classification per doc (task with retry)
    3. Grouping by normalized PO number into POSet
    4. Recovery, unattached resolution, reconciliation, & input folder clearing via recovery_flow

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
    from app.core.logging_setup import setup_logging

    setup_logging(cfg)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    from app.models import Document as _Doc
    from app.services.extraction import reclaim_stale_processing
    from app.services.grouping import group_document
    from app.services.ingestion import (
        find_input_pdfs,
        ingest_file,
    )

    # Release documents whose worker died mid-extraction before anything else
    # looks at them, so they are read again in this run rather than being
    # reported as processed and never read.
    reclaim_stale_processing(cfg)

    input_folder = Path(cfg.paths.input_folder)
    input_folder.mkdir(parents=True, exist_ok=True)

    processed = 0
    extracted = 0
    errors = 0
    touched_po_set_ids: set[int] = set()

    # Discover PDFs in input folder. find_input_pdfs matches the suffix
    # case-insensitively in ONE pass — globbing "*.pdf" and "*.PDF" separately
    # would double the list on Windows and send every document to the VLM twice.
    files = find_input_pdfs(input_folder)

    # Stability poll (FR-4.5) for the whole batch, taken together.
    stable_map = _poll_stability(files, cfg)
    heartbeat_sync_lock(cfg_path)

    for f in files:
        heartbeat_sync_lock(cfg_path)
        try:
            if not stable_map.get(f, False):
                continue

            doc = ingest_file(f, cfg)
            processed += 1

            # Extract & Classify via VLM. A document that has been read
            # already is not scheduled at all (see _already_read): the task
            # would return it unchanged, and scheduling it costs a process
            # launch per file.
            if _already_read(doc):
                logger.debug("Document %s was read already; not scheduled again", doc.id)
                extracted += 1
            else:
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
                    else:
                        extracted += 1

            # Group into PO Set (FR-7.1-7.2). Only PO mints;
            # DN/SI/UNKNOWN attach to an open set or wait unattached.
            try:
                with Session(eng) as s2:
                    d2 = s2.get(_Doc, doc.id)
                    if d2 is None:
                        # The row was removed between extraction and grouping
                        # (a concurrent delete or quarantine). Nothing to place.
                        logger.warning("Document %s disappeared before grouping", doc.id)
                        continue
                    ps_id, problem = group_document(d2, cfg)
                    if problem == "missing_po_number":
                        # Read successfully, but no PO reference on the face
                        # of the document. It is NOT broken and it is NOT
                        # quarantined: it waits, visible, until the content
                        # allows it to be placed (a PO that names its DN) or
                        # until a human names its PO. Marking it failed
                        # would hide it from both the sweeps and the
                        # operator.
                        logger.warning(
                            "Document %s (%s) carries no PO reference — it stays unplaced "
                            "and visible in the holding area",
                            d2.id,
                            d2.original_filename,
                        )
                    elif problem == "ambiguous_po_number":
                        logger.warning(
                            "Document %s (%s) shows more than one PO number — it was not "
                            "placed in a PO Set and waits for a human",
                            d2.id,
                            d2.original_filename,
                        )
                    elif ps_id is not None:
                        if d2.po_set_id is None:
                            d2.po_set_id = ps_id
                            s2.commit()
                        touched_po_set_ids.add(ps_id)
            except Exception:
                logger.warning("Grouping failed for doc %s", doc.id, exc_info=True)
                errors += 1

        except Exception:
            logger.warning("Ingestion loop failed for file %s", f, exc_info=True)
            errors += 1
            continue

    # Run recovery flow as a Prefect subflow (or directly if outside Prefect context)
    from prefect.context import FlowRunContext

    if FlowRunContext.get():
        rec_res = recovery_flow(
            cfg_path=cfg_path,
            held_lock=True,
            initial_touched_po_set_ids=list(touched_po_set_ids),
        )
    else:
        run_recovery_fn = getattr(recovery_flow, "fn", recovery_flow)
        rec_res = run_recovery_fn(
            cfg_path=cfg_path,
            held_lock=True,
            initial_touched_po_set_ids=list(touched_po_set_ids),
        )

    total_processed = processed + rec_res.get("processed", 0)
    total_errors = errors + rec_res.get("errors", 0)

    return {
        "processed": total_processed,
        "extracted": extracted + rec_res.get("extracted", 0),
        "errors": total_errors,
        "touched_po_sets": rec_res.get("touched_po_sets", len(touched_po_set_ids)),
        "reconciled_count": rec_res.get("reconciled_count", 0),
    }
