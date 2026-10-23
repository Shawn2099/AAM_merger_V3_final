"""PO Sets routes - per-PO locked_by_action + 300s timeout (FR-CONC-1-4, FR-CONFIG-2).

All handlers are ``def`` (sync) so they run in FastAPI threadpool per SPEC §5.3.
Locks are stored in po_sets.locked_by_action (nullable text) - acquired at start
of every state-changing action and released on success or error. Stale locks
auto-release after concurrency.po_set_lock_timeout_seconds (default 300) per FR-CONFIG-2.

HTMX disabling (FR-CONC-3): detail/list responses include ``is_locked`` boolean
and ``locked_by_action``; dashboard templates should render buttons with
``disabled`` when ``is_locked`` is true and poll ``GET /po_sets/{id}`` via
``hx-get`` to re-enable after completion.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import ExtractionStatus, POSet, POSetStatus
from app.models.base import Base
from app.services.locking import acquire_lock, is_locked, release_if_stale, release_lock

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/po_sets", tags=["po_sets"])


def _doc_status_badge(status) -> str:
    """Render a document's extraction state as a labelled badge.

    A reviewer opening a PO Set has to be able to tell at a glance which of
    its files were actually read and which were not — otherwise a set stuck
    on `pending` gives no clue why.
    """
    val = status.value if hasattr(status, "value") else str(status)
    if val == ExtractionStatus.valid.value:
        label, css = "Read", "badge-merged"
    elif val == ExtractionStatus.failed.value:
        label, css = "Failed", "badge-failed"
    elif val == ExtractionStatus.processing.value:
        label, css = "Reading…", "badge-pending"
    else:
        label, css = "Not read", "badge-pending"
    return f'<span class="badge {css}" style="font-size:0.75rem;">{_html.escape(label)}</span>'


def _auto_release_if_stale(ps: POSet, cfg, session: Session) -> bool:
    """If lock is stale (> timeout), clear it and return True (released)."""
    return release_if_stale(ps, session, cfg)


def _acquire_lock(po_set_id: int, action: str, cfg) -> POSet:
    """Acquire per-PO lock or raise 409. Handles stale auto-release."""
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise HTTPException(status_code=404, detail=f"POSet {po_set_id} not found")
        # Every mutating action is blocked while a sync is running. The sync's
        # extraction can take minutes; allowing a merge/reclassify/upload in
        # that window lets two writers mutate the same set.
        from app.services import sync_lock

        if sync_lock._is_sync_running():
            raise HTTPException(
                status_code=409,
                detail=(
                    "A sync is currently running; wait for it to finish before "
                    "changing this PO Set."
                ),
            )
        # Archived sets are read-only. The archive action is the one exception:
        # it must stay idempotent if the operator clicks Delete twice.
        if ps.archived_at is not None and action != "quarantine_delete":
            raise HTTPException(
                status_code=409,
                detail=(f"This PO Set is archived and read-only. {action} is not allowed."),
            )
        if not acquire_lock(ps, action, s, cfg):
            raise HTTPException(
                status_code=409,
                detail=f"action already in progress on this PO Set: {ps.locked_by_action}",
            )
        return ps


def _release_lock(po_set_id: int, cfg, action: str) -> None:
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is not None:
            # action-scoped: never clear another in-flight action's lock (FR-CONC-2)
            release_lock(ps, s, action)


def _refresh_lock(po_set_id: int, cfg, action: str) -> None:
    """Heartbeat the lock while a long action is still running.

    A Re-Extract can call the VLM for every document in the set; one
    interaction can take most of the default 300s timeout. Without a
    heartbeat the dashboard's stale-lock cleanup re-enabled every button
    while extraction was still writing line items.
    """
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is not None and ps.locked_by_action == action:
            ps.locked_at = datetime.now(UTC)
            s.commit()


def _refuse_if_merged(po_set_id: int, cfg, action: str) -> None:
    """Merged is terminal for reconciliation and extraction.

    Re-reading a delivered set rewrites its doc types, PO/SI/DN numbers and
    line items while the packet on disk stays unchanged — the DB then no longer
    describes what was delivered. Customs append is the only post-merge write,
    and it has its own path.
    """
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is not None and ps.status == POSetStatus.merged:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This PO Set is already merged; {action} is not allowed. "
                    "Use the customs append path for a post-delivery document."
                ),
            )


def _po_to_dict(ps: POSet, cfg) -> dict:
    locked = is_locked(ps, cfg)
    # HTMX disable: when locked, buttons should be disabled (FR-CONC-3)
    return {
        "id": ps.id,
        "po_no_normalized": ps.po_no_normalized,
        "status": ps.status.value if hasattr(ps.status, "value") else str(ps.status),
        "locked_by_action": ps.locked_by_action,
        "is_locked": locked,
        # front-end helper: render disabled attribute when is_locked true
        "htmx_disabled": "disabled" if locked else "",
        "has_customs_toggle": ps.has_customs_toggle,
        "updated_at": ps.updated_at.isoformat() if ps.updated_at else None,
        "archived_at": ps.archived_at.isoformat() if ps.archived_at else None,
        "archived_by": ps.archived_by,
        "archive_reason": ps.archive_reason,
        "archive_path": ps.archive_path,
        "is_archived": ps.archived_at is not None,
    }


@router.get("")
def list_po_sets():
    """List PO Sets with lock state for HTMX polling/dashboard (FR-CONC-3)."""
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        # auto-release stale locks on list view so dashboard doesn't permanently disable buttons
        all_sets = s.query(POSet).filter(POSet.archived_at.is_(None)).all()
        for ps in all_sets:
            if ps.locked_by_action is not None:
                # release_if_stale commits only when it actually clears a row,
                # and only if the lock is still the stale one observed here.
                release_if_stale(ps, s, cfg)
        rows = s.query(POSet).filter(POSet.archived_at.is_(None)).all()
        return [_po_to_dict(ps, cfg) for ps in rows]


@router.get("/{po_set_id}")
def get_po_set(po_set_id: int):
    """Detail - includes lock state; HTMX can poll this to enable/disable buttons (FR-CONC-3)."""
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise HTTPException(status_code=404, detail=f"POSet {po_set_id} not found")
        # auto-release stale before responding so UI doesn't show stale lock
        if ps.locked_by_action is not None:
            release_if_stale(ps, s, cfg)
            s.refresh(ps)
        return _po_to_dict(ps, cfg)


@router.get("/{po_set_id}/detail", response_class=HTMLResponse)
def get_po_set_detail_html(po_set_id: int):
    """HTMX fragment - buttons disabled when locked (FR-CONC-3)."""
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise HTTPException(status_code=404, detail=f"POSet {po_set_id} not found")
        if ps.locked_by_action is not None:
            release_if_stale(ps, s, cfg)
            s.refresh(ps)
        d = _po_to_dict(ps, cfg)
        disabled = (
            'disabled title="archived; read-only"'
            if d["is_archived"]
            else ('disabled title="action already in progress"' if d["is_locked"] else "")
        )
        locked_msg = (
            f'<span class="locked-msg">Locked by '
            f"{_html.escape(str(d['locked_by_action'] or ''))}</span>"
            if d["is_locked"] and not d["is_archived"]
            else ""
        )
        archived_msg = (
            f'<p class="muted" style="margin:8px 0;">Archived '
            f"{_html.escape(d['archived_at'] or '')} by "
            f"{_html.escape(d['archived_by'] or 'unknown')}; read-only. "
            f"Export: {_html.escape(d['archive_path'] or '')}</p>"
            if d["is_archived"]
            else ""
        )

        # Related files. The set's status alone does not explain WHY it is
        # stuck: a set sitting on `pending` because its SI never parsed looks
        # identical to one waiting for the vendor to send a third document.
        # Listing the files, with their extraction state, is what makes
        # Redo/Re-extract an informed action rather than a guess.
        attempt_cap = int(getattr(cfg.extraction, "max_retries", 3) or 3)
        doc_rows = []
        for doc in sorted(ps.documents, key=lambda x: (str(x.doc_type), x.original_filename)):
            dtype = doc.doc_type.value if hasattr(doc.doc_type, "value") else str(doc.doc_type)
            n_items = len(doc.line_items)
            failed = doc.extraction_status == ExtractionStatus.failed
            doc_rows.append(
                f"<tr>"
                f'<td><a href="/documents/{doc.id}/preview" target="_blank">'
                f"{_html.escape(doc.original_filename)}</a></td>"
                f"<td>{_html.escape(dtype)}</td>"
                f"<td>{_doc_status_badge(doc.extraction_status)}</td>"
                f'<td style="text-align:right;">{n_items}</td>'
                f'<td style="font-size:0.75rem;">'
                + (
                    f"{doc.extraction_attempt_count or 0}/{attempt_cap} attempts used — "
                    f"Redo/Re-extract will retry this file"
                    if failed
                    else ""
                )
                + "</td></tr>"
            )
        if doc_rows:
            files_html = f"""
        <h4 style="margin:12px 0 6px;">Related files ({len(doc_rows)})</h4>
        <table class="data-table" style="margin-bottom:8px;">
          <thead><tr>
            <th>File</th><th>Type</th><th>Extraction</th>
            <th style="text-align:right;">Lines</th><th>Notes</th>
          </tr></thead>
          <tbody>{"".join(doc_rows)}</tbody>
        </table>"""
        else:
            files_html = (
                '<p class="muted" style="margin:12px 0;">No documents are attached to '
                "this PO Set yet.</p>"
            )

        # minimal HTMX fragment - real dashboard will use richer template
        html = f"""
        <div id=\"po-{po_set_id}\" hx-get=\"/po_sets/{po_set_id}/detail\" hx-trigger=\"every 2s\" hx-swap=\"outerHTML\">
          <h3>PO Set {_html.escape(str(d["po_no_normalized"]))} - {_html.escape(str(d["status"]))}</h3>
          {locked_msg}
          {archived_msg}
          {files_html}
          <button hx-post=\"/po_sets/{po_set_id}/force_merge\" {disabled}>Force Merge</button>
          <button hx-post=\"/po_sets/{po_set_id}/toggle_customs\" {disabled}>Toggle Customs</button>
          <button hx-delete=\"/po_sets/{po_set_id}/quarantine\" {disabled}>Delete Quarantined</button>
          <button hx-post=\"/po_sets/{po_set_id}/redo_extract\" {disabled}>Redo/Re-extract</button>
          <button hx-post=\"/po_sets/{po_set_id}/redo_match\" {disabled}>Redo matching</button>
        </div>
        """
        return HTMLResponse(content=html)


_ABS_PATH_RE = re.compile(
    # Drive-letter and UNC paths: directory segments may contain spaces
    # (word characters only, so prose is never swallowed)
    # ("C:\\Program Files\\AAM\\x.pdf"); the last segment ends at whitespace.
    r"(?<![\w.])(?:[A-Za-z]:[\\/]|\\\\)(?:(?:[^\s\\/:*?\"<>|]+|[\w\-() ]+)[\\/])*[^\s\\/:*?\"<>|']*"
    # POSIX absolute paths: no spaces.
    r"|(?<![\w.])/[^\s'\"<>|,;()]+"
)


def _scrub_paths(text: str) -> str:
    """Replace absolute filesystem paths in `text` with their final file name.

    Error text can carry server paths (stored documents, the DB file); the
    operator needs the file name, not the server's directory layout.
    """

    def _name_only(m: re.Match[str]) -> str:
        parts = [p for p in re.split(r"[\\/]", m.group(0)) if p]
        return parts[-1] if parts else "[path]"

    return _ABS_PATH_RE.sub(_name_only, text)


def _merge_failure(e: Exception, label: str, po_set_id: int) -> HTTPException:
    """Map a merge-action exception to an HTTP error with a path-free message.

    The full traceback goes to the server log. Only the operator-fixable cases
    get their own message: a missing or unreadable source, a naming collision
    and a bad justification. Anything else is a generic 500.
    """
    from app.services.merge import MergeNamingError, MergeSourceError

    logger.error("%s failed for PO Set %s", label, po_set_id, exc_info=e)
    if isinstance(e, MergeSourceError):
        return HTTPException(status_code=409, detail=_scrub_paths(str(e)))
    if isinstance(e, MergeNamingError):
        return HTTPException(
            status_code=409,
            detail=_scrub_paths(str(e)),
        )
    if isinstance(e, ValueError):
        return HTTPException(status_code=422, detail=_scrub_paths(str(e)))
    return HTTPException(status_code=500, detail=f"{label} failed; see the server log.")


@router.post("/{po_set_id}/force_merge")
def force_merge(po_set_id: int, justification: str = Form("")):
    """Force Merge - acquires per-PO lock, 409 if already locked (FR-CONC-1/2).

    `justification` is optional but, when given, must be a real note (>= 20
    chars) and is stored on the audit_log row.
    """
    cfg = load_config()
    _acquire_lock(po_set_id, "force_merge", cfg)
    try:
        from app.services.merge import force_merge as svc_force_merge

        result = svc_force_merge(po_set_id, cfg, justification=justification, source="operator")
        detail = {"merged_path": str(result) if result else None}
        return {"status": "merged", "po_set_id": po_set_id, "detail": detail}
    except HTTPException:
        raise
    except Exception as e:
        raise _merge_failure(e, "Force merge", po_set_id) from e
    finally:
        _release_lock(po_set_id, cfg, "force_merge")


@router.post("/{po_set_id}/toggle_customs")
def toggle_customs(po_set_id: int):
    """Customs toggle - also per-PO locked (FR-CONC-1)."""
    cfg = load_config()
    _acquire_lock(po_set_id, "toggle_customs", cfg)
    try:
        from app.services.customs import toggle_customs as svc_toggle

        updated = svc_toggle(po_set_id, cfg)
        rebuilt = None
        # A merged set whose customs requirement has just been switched on,
        # with both documents already attached: the packet is regenerated so
        # the delivered file contains them. Status stays `merged`.
        if (
            updated.status == POSetStatus.merged
            and updated.has_customs_toggle
            and (updated.customs_doc_count or 0) == 2
        ):
            try:
                from app.services.merge import append_customs_to_merged_packet

                rebuilt = str(append_customs_to_merged_packet(po_set_id, cfg))
            except Exception:
                logger.warning(
                    "Customs toggled on PO Set %s but the packet could not be rebuilt",
                    po_set_id,
                    exc_info=True,
                )
        return {
            "status": "toggled",
            "po_set_id": po_set_id,
            "has_customs_toggle": updated.has_customs_toggle,
            "packet_rebuilt": rebuilt is not None,
            "merged_output_path": rebuilt or updated.merged_output_path,
        }
    except HTTPException:
        raise
    except ValueError as e:
        # A merged set refuses the toggle: its packet is final (FR-14.6).
        # This is a conflict with the set's state, not a bad request.
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        _release_lock(po_set_id, cfg, "toggle_customs")


@router.post("/{po_set_id}/redo_extract")
def redo_extract(po_set_id: int):
    """Redo/Re-extract - per-PO locked (FR-CONC-1). 409 if already locked."""
    cfg = load_config()
    _refuse_if_merged(po_set_id, cfg, "Re-Extract")

    # The nightly sync may be holding this same document in `processing` and
    # calling the VLM. Resetting it to `pending` underneath that call would let
    # two extractions interleave and append duplicate line items. Refuse while
    # a sync is running (P2-21); the operator can retry when it finishes.
    # Imported here, not at module scope, to keep the route <-> sync_lock
    # import graph acyclic.
    from app.services import sync_lock

    if sync_lock._is_sync_running():
        raise HTTPException(
            status_code=409,
            detail=(
                "A sync is currently reading documents; Re-Extract could "
                "double-extract this set. Wait for the sync to finish and try again."
            ),
        )

    _acquire_lock(po_set_id, "redo_extract", cfg)
    try:
        from app.models import Document, ExtractionStatus
        from app.services.extraction import extract_document, is_manual_only
        from app.services.reconciliation import reconcile_po_set
        from app.services.retry import with_backoff_retry

        eng = get_engine(cfg)
        with Session(eng) as s:
            docs = s.query(Document).filter(Document.po_set_id == po_set_id).all()
            docs_to_extract = []
            for d in docs:
                dt_val = d.doc_type.value if hasattr(d.doc_type, "value") else str(d.doc_type)
                if not is_manual_only(dt_val) and not d.is_split_parent:
                    # Explicit operator intent: reset attempt count so extraction can re-run.
                    # is_split_parent rows are sterile parent documents — their data lives
                    # in child rows; resetting them would trigger an unnecessary VLM call
                    # and could corrupt the split invariant if the model re-classifies.
                    d.extraction_attempt_count = 0
                    d.extraction_status = ExtractionStatus.pending
                    docs_to_extract.append((d.id, dt_val))
            s.commit()

        # If this set was quarantined, the operator's explicit Redo action is the
        # gate to re-enter reconciliation. Reset status to pending so the quarantine
        # guard in _reconcile_po_set_inner does not short-circuit and preserve the
        # old (now-stale) reason.
        from app.models import POSetStatus as _PSS

        with Session(eng) as s_reset:
            ps_reset = s_reset.get(POSet, po_set_id)
            if ps_reset is not None and ps_reset.status == _PSS.quarantined:
                ps_reset.status = _PSS.pending
                s_reset.commit()

        extraction_results = []
        for doc_id, _dtype in docs_to_extract:
            _refresh_lock(po_set_id, cfg, "redo_extract")
            try:
                # Use the same retry envelope as the nightly Prefect task so a transient
                # VLM blip during redo_extract does not permanently burn an attempt (C1).
                extracted = with_backoff_retry(
                    lambda d=doc_id: extract_document(d, cfg),
                    max_retries=cfg.extraction.max_retries,
                    backoff_seconds=list(cfg.extraction.retry_backoff_seconds),
                    label=f"redo_extract doc={doc_id}",
                )
                extraction_results.append(
                    {
                        "doc_id": doc_id,
                        "status": extracted.extraction_status.value
                        if hasattr(extracted.extraction_status, "value")
                        else str(extracted.extraction_status),
                    }
                )
            except Exception as e:
                extraction_results.append({"doc_id": doc_id, "error": str(e)})

        rec_res = reconcile_po_set(po_set_id, cfg)
        return {
            "status": "redo_extract_complete",
            "po_set_id": po_set_id,
            "extractions": extraction_results,
            "reconciliation": rec_res,
        }
    finally:
        _release_lock(po_set_id, cfg, "redo_extract")


@router.post("/{po_set_id}/merge")
def merge_now(po_set_id: int):
    """Re-evaluate and merge if the set is now eligible (per-PO locked).

    NOT Force Merge. Every gate still applies: quantities must reconcile, the
    customs toggle must be satisfied, and the packet must be nameable. This is
    the button an operator presses after finishing manual steps — most often
    uploading the two customs documents — so they do not have to wait for the
    midnight sync to notice.

    Safe to press at any time. If the set is not eligible it simply comes back
    with the status and reason explaining why, and nothing is written.
    """
    cfg = load_config()
    _acquire_lock(po_set_id, "merge", cfg)
    try:
        from app.services.reconciliation import reconcile_po_set

        res = reconcile_po_set(po_set_id, cfg)
        return {
            "status": res.get("status"),
            "po_set_id": po_set_id,
            "merged_output_path": res.get("merged_output_path"),
            "reason": res.get("reason"),
            "detail": res.get("detail"),
            "flags": res.get("flags", []),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _merge_failure(e, "Merge", po_set_id) from e
    finally:
        _release_lock(po_set_id, cfg, "merge")


@router.post("/{po_set_id}/redo_match")
def redo_match(po_set_id: int):
    """Redo matching (no VLM) - per-PO locked (FR-CONC-1)."""
    cfg = load_config()
    _refuse_if_merged(po_set_id, cfg, "Re-Match")
    _acquire_lock(po_set_id, "redo_match", cfg)
    try:
        from app.models import POSetStatus as _PSS
        from app.services.reconciliation import reconcile_po_set

        # If the set was quarantined, the operator pressing Redo Matching is the
        # explicit gate to re-enter reconciliation with fresh eyes. Reset to pending
        # so the quarantine guard does not short-circuit and preserve stale reason.
        eng = get_engine(cfg)
        with Session(eng) as s_reset:
            ps_reset = s_reset.get(POSet, po_set_id)
            if ps_reset is not None and ps_reset.status == _PSS.quarantined:
                ps_reset.status = _PSS.pending
                s_reset.commit()

        rec_res = reconcile_po_set(po_set_id, cfg)
        return {
            "status": "redo_match_complete",
            "po_set_id": po_set_id,
            "reconciliation": rec_res,
        }
    finally:
        _release_lock(po_set_id, cfg, "redo_match")


@router.delete("/{po_set_id}/quarantine")
def delete_quarantined(po_set_id: int, response: Response, justification: str = Form("")):
    """Delete quarantined PO Set - per-PO locked (FR-CONC-1).

    `justification` is optional; when given it must be >= 20 chars and is
    stored on the audit_log row.
    """
    cfg = load_config()
    # A running sync may be attaching documents to this set. Archive snapshots
    # the set's documents, so refuse rather than race the attach sweep.
    from app.services import sync_lock

    if sync_lock._is_sync_running():
        raise HTTPException(
            status_code=409,
            detail=(
                "A sync is currently reading documents; archiving could race "
                "an attachment. Wait for the sync to finish and try again."
            ),
        )
    _acquire_lock(po_set_id, "quarantine_delete", cfg)
    try:
        from app.services.quarantine import delete_quarantined as svc_delete

        audit = svc_delete(po_set_id, cfg, justification=justification, source="operator")
        response.headers["HX-Redirect"] = "/quarantine"
        try:
            detail = json.loads(audit.detail or "{}")
        except (TypeError, ValueError):
            detail = {}
        return {
            "status": "archived",
            "audit_id": audit.id,
            "archive_path": detail.get("archive_path"),
        }
    except HTTPException:
        raise
    except Exception as e:
        # map "not quarantined" value error to 409/422
        if "not quarantined" in str(e).lower():
            raise HTTPException(status_code=409, detail=str(e)) from e
        raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        _release_lock(po_set_id, cfg, "quarantine_delete")
