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

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import POSet
from app.models.base import Base
from app.services.locking import acquire_lock, is_locked, release_lock

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/po_sets", tags=["po_sets"])


def _auto_release_if_stale(ps: POSet, cfg, session: Session) -> bool:
    """If lock is stale (> timeout), clear it and return True (released)."""
    if ps.locked_by_action is None:
        return False
    if not is_locked(ps, cfg):
        release_lock(ps, session)
        return True
    return False


def _acquire_lock(po_set_id: int, action: str, cfg) -> POSet:
    """Acquire per-PO lock or raise 409. Handles stale auto-release."""
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise HTTPException(status_code=404, detail=f"POSet {po_set_id} not found")
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
    }


@router.get("")
def list_po_sets():
    """List PO Sets with lock state for HTMX polling/dashboard (FR-CONC-3)."""
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        # auto-release stale locks on list view so dashboard doesn't permanently disable buttons
        all_sets = s.query(POSet).all()
        for ps in all_sets:
            if ps.locked_by_action is not None and not is_locked(ps, cfg):
                release_lock(ps, s)
        s.commit()
        rows = s.query(POSet).all()
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
        if ps.locked_by_action is not None and not is_locked(ps, cfg):
            release_lock(ps, s)
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
        if ps.locked_by_action is not None and not is_locked(ps, cfg):
            release_lock(ps, s)
            s.refresh(ps)
        d = _po_to_dict(ps, cfg)
        disabled = 'disabled title="action already in progress"' if d["is_locked"] else ""
        locked_msg = (
            f'<span class="locked-msg">Locked by {d["locked_by_action"]}</span>'
            if d["is_locked"]
            else ""
        )
        # minimal HTMX fragment - real dashboard will use richer template
        html = f"""
        <div id=\"po-{po_set_id}\" hx-get=\"/po_sets/{po_set_id}/detail\" hx-trigger=\"every 2s\" hx-swap=\"outerHTML\">
          <h3>PO Set {d["po_no_normalized"]} - {d["status"]}</h3>
          {locked_msg}
          <button hx-post=\"/po_sets/{po_set_id}/force_merge\" {disabled}>Force Merge</button>
          <button hx-post=\"/po_sets/{po_set_id}/toggle_customs\" {disabled}>Toggle Customs</button>
          <button hx-delete=\"/po_sets/{po_set_id}/quarantine\" {disabled}>Delete Quarantined</button>
          <button hx-post=\"/po_sets/{po_set_id}/redo_extract\" {disabled}>Redo/Re-extract</button>
          <button hx-post=\"/po_sets/{po_set_id}/redo_match\" {disabled}>Redo matching</button>
        </div>
        """
        return HTMLResponse(content=html)


@router.post("/{po_set_id}/force_merge")
def force_merge(po_set_id: int):
    """Force Merge - acquires per-PO lock, 409 if already locked (FR-CONC-1/2)."""
    cfg = load_config()
    _acquire_lock(po_set_id, "force_merge", cfg)
    try:
        from app.services.merge import force_merge as svc_force_merge

        result = svc_force_merge(po_set_id, cfg)
        detail = {"merged_path": str(result) if result else None}
        return {"status": "merged", "po_set_id": po_set_id, "detail": detail}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Force merge failed for PO Set %s: %s", po_set_id, e)
        raise HTTPException(status_code=422, detail=f"Force merge failed: {e}") from e
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
        return {
            "status": "toggled",
            "po_set_id": po_set_id,
            "has_customs_toggle": updated.has_customs_toggle,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        _release_lock(po_set_id, cfg, "toggle_customs")


@router.post("/{po_set_id}/redo_extract")
def redo_extract(po_set_id: int):
    """Redo/Re-extract - per-PO locked (FR-CONC-1). 409 if already locked."""
    cfg = load_config()
    _acquire_lock(po_set_id, "redo_extract", cfg)
    try:
        from app.models import Document, ExtractionStatus
        from app.services.extraction import extract_document, is_manual_only
        from app.services.reconciliation import reconcile_po_set

        eng = get_engine(cfg)
        with Session(eng) as s:
            docs = s.query(Document).filter(Document.po_set_id == po_set_id).all()
            docs_to_extract = []
            for d in docs:
                dt_val = d.doc_type.value if hasattr(d.doc_type, "value") else str(d.doc_type)
                if not is_manual_only(dt_val):
                    # Explicit operator intent: reset attempt count so extraction can re-run
                    d.extraction_attempt_count = 0
                    d.extraction_status = ExtractionStatus.pending
                    docs_to_extract.append((d.id, dt_val))
            s.commit()

        extraction_results = []
        for doc_id, _dtype in docs_to_extract:
            try:
                extracted = extract_document(doc_id, cfg)
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


@router.post("/{po_set_id}/redo_match")
def redo_match(po_set_id: int):
    """Redo matching (no VLM) - per-PO locked (FR-CONC-1)."""
    cfg = load_config()
    _acquire_lock(po_set_id, "redo_match", cfg)
    try:
        from app.services.reconciliation import reconcile_po_set

        rec_res = reconcile_po_set(po_set_id, cfg)
        return {
            "status": "redo_match_complete",
            "po_set_id": po_set_id,
            "reconciliation": rec_res,
        }
    finally:
        _release_lock(po_set_id, cfg, "redo_match")


@router.delete("/{po_set_id}/quarantine")
def delete_quarantined(po_set_id: int):
    """Delete quarantined PO Set - per-PO locked (FR-CONC-1)."""
    cfg = load_config()
    _acquire_lock(po_set_id, "quarantine_delete", cfg)
    try:
        from app.services.quarantine import delete_quarantined as svc_delete

        audit = svc_delete(po_set_id, cfg)
        return {"status": "deleted", "audit_id": audit.id}
    except HTTPException:
        raise
    except Exception as e:
        # map "not quarantined" value error to 409/422
        if "not quarantined" in str(e).lower():
            raise HTTPException(status_code=409, detail=str(e)) from e
        raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        _release_lock(po_set_id, cfg, "quarantine_delete")
