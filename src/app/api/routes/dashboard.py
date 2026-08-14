"""Dashboard + Audit + Polish — wires all views (S 8), audit read-only,
5-status filter, customs toggle, Redo split, Force Merge modal, HTMX refresh.
Cross-platform via pathlib. No hardcoded paths. Sync def handlers."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import AuditLog, DocType, Document, LineItem, POSet, POSetStatus
from app.models.base import Base

router = APIRouter()

# Jinja templates — directory = "templates" (cross-platform, not hardcoded absolute)
_templates = Jinja2Templates(directory="templates")

# 5 statuses as defined in SPEC §6.3 — enum values are the source of truth
_ALLOWED_STATUSES = {s.value for s in POSetStatus}


def _is_locked(ps: POSet, cfg) -> bool:
    if ps.locked_by_action is None:
        return False
    from datetime import UTC, datetime

    updated = ps.updated_at
    if updated is None:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    timeout = cfg.concurrency.po_set_lock_timeout_seconds
    return (now - updated).total_seconds() <= timeout


def _po_sets_with_doc_count(session: Session, status_filter: str | None, cfg) -> list[dict]:
    q = session.query(POSet)
    if status_filter:
        # validate filter is one of 5 statuses; if invalid, return empty
        if status_filter not in _ALLOWED_STATUSES:
            return []
        # compare via string value to support both enum and str storage
        all_sets = q.all()
        filtered = [
            ps
            for ps in all_sets
            if (ps.status.value if hasattr(ps.status, "value") else str(ps.status)) == status_filter
        ]
        pools = filtered
    else:
        pools = q.all()
    out = []
    for ps in pools:
        # doc_count via relationship or query
        doc_count = session.query(Document).filter_by(po_set_id=ps.id).count()
        out.append(
            {
                "id": ps.id,
                "po_no_normalized": ps.po_no_normalized,
                "status": ps.status,
                "doc_count": doc_count,
                "updated_at": ps.updated_at,
                "locked_by_action": ps.locked_by_action,
                "is_locked": _is_locked(ps, cfg),
            }
        )
    # sort by updated_at desc for dashboard usability
    out.sort(key=lambda x: x["updated_at"] or x["id"], reverse=True)
    return out


def _sync_running_state() -> bool:
    try:
        from app.api.routes.sync import _is_sync_running

        return _is_sync_running()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=302)


# ---------------------------------------------------------------------------
# Dashboard — full page + HTMX table fragment
# ---------------------------------------------------------------------------


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, status: str | None = None):
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        # auto-release stale locks so UI doesn't permanently disable
        for ps in s.query(POSet).all():
            if ps.locked_by_action is not None and not _is_locked(ps, cfg):
                ps.locked_by_action = None
        s.commit()
        po_sets = _po_sets_with_doc_count(s, status, cfg)
        sync_running = _sync_running_state()
        return _templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "request": request,
                "po_sets": po_sets,
                "current_status": status if status in _ALLOWED_STATUSES else None,
                "sync_running": sync_running,
            },
        )


@router.get("/dashboard/table", response_class=HTMLResponse)
def dashboard_table(request: Request, status: str | None = None):
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        po_sets = _po_sets_with_doc_count(s, status, cfg)
        return _templates.TemplateResponse(
            request,
            "_dashboard_table.html",
            {
                "request": request,
                "po_sets": po_sets,
                "current_status": status if status in _ALLOWED_STATUSES else None,
            },
        )


# ---------------------------------------------------------------------------
# PO Set detail — full page (wires customs toggle, redo split, force merge modal, HTMX poll)
# ---------------------------------------------------------------------------


@router.get("/po_sets/{po_set_id}/view", response_class=HTMLResponse)
def po_set_detail_view(po_set_id: int, request: Request):
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise HTTPException(status_code=404, detail=f"POSet {po_set_id} not found")
        if ps.locked_by_action is not None and not _is_locked(ps, cfg):
            ps.locked_by_action = None
            s.commit()
            s.refresh(ps)
        is_locked = _is_locked(ps, cfg)
        docs = s.query(Document).filter_by(po_set_id=po_set_id).all()
        # line items via join
        doc_ids = [d.id for d in docs]
        if doc_ids:
            items = s.query(LineItem).filter(LineItem.document_id.in_(doc_ids)).all()
            # annotate with doc_type for display
            doc_type_map = {
                d.id: (d.doc_type.value if hasattr(d.doc_type, "value") else str(d.doc_type))
                for d in docs
            }
            enriched = []
            for li in items:
                enriched.append(
                    {
                        "line_item_no": li.line_item_no,
                        "description": li.description,
                        "quantity": li.quantity,
                        "unit_price": li.unit_price,
                        "doc_type": doc_type_map.get(li.document_id, ""),
                    }
                )
        else:
            enriched = []
        return _templates.TemplateResponse(
            request,
            "po_set_detail.html",
            {
                "request": request,
                "po_set": ps,
                "documents": docs,
                "line_items": enriched,
                "is_locked": is_locked,
            },
        )


# ---------------------------------------------------------------------------
# Manual document upload (CUSTOMS/SHIPPING/COMMERCIAL_INVOICE) — cross-platform
# ---------------------------------------------------------------------------


@router.post("/po_sets/{po_set_id}/upload", response_class=HTMLResponse)
async def upload_manual_doc(
    po_set_id: int,
    request: Request,
    file: UploadFile = File(...),  # noqa: B008
    doc_type: str = Form(...),
):
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    # validate doc_type
    if doc_type not in (
        DocType.CUSTOMS.value,
        DocType.SHIPPING.value,
        DocType.COMMERCIAL_INVOICE.value,
    ):
        raise HTTPException(
            status_code=422,
            detail=f"doc_type must be CUSTOMS/SHIPPING/COMMERCIAL_INVOICE, got {doc_type}",
        )
    # lock check (per-PO)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise HTTPException(status_code=404, detail=f"POSet {po_set_id} not found")
        if ps.locked_by_action is not None and _is_locked(ps, cfg):
            raise HTTPException(
                status_code=409,
                detail=f"action already in progress on this PO Set: {ps.locked_by_action}",
            )
        ps.locked_by_action = "manual_upload"
        s.commit()
        try:
            data = await file.read()
            if not data:
                raise HTTPException(status_code=422, detail="empty file")
            import hashlib

            sha = hashlib.sha256(data).hexdigest()
            # cross-platform stored path via pathlib
            stored = (
                Path(cfg.paths.stored_documents_folder)
                / f"{sha}{Path(file.filename or 'upload.pdf').suffix or '.pdf'}"
            )
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(data)
            # check dedup by hash
            existing = s.query(Document).filter_by(sha256_hash=sha).first()
            if existing is None:
                from app.models import ExtractionStatus as ES

                doc = Document(
                    sha256_hash=sha,
                    original_filename=file.filename or "upload.pdf",
                    stored_path=str(stored),
                    doc_type=DocType(doc_type),
                    extraction_status=ES.valid,
                    po_set_id=po_set_id,
                )
                s.add(doc)
                s.flush()
            else:
                # reuse existing hash; keep original association, just ensure file exists  # noqa: E501
                pass
            # update customs_doc_count if applicable
            if doc_type in (DocType.CUSTOMS.value, DocType.SHIPPING.value):
                # recount
                docs = s.query(Document).filter_by(po_set_id=po_set_id).all()
                cnt = sum(
                    1
                    for d in docs
                    if (d.doc_type.value if hasattr(d.doc_type, "value") else str(d.doc_type))
                    in (DocType.CUSTOMS.value, DocType.SHIPPING.value)
                )
                ps.customs_doc_count = cnt
            s.commit()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        finally:
            # release lock
            ps2 = s.get(POSet, po_set_id)
            if ps2 is not None and ps2.locked_by_action == "manual_upload":
                ps2.locked_by_action = None
                s.commit()
    return RedirectResponse(url=f"/po_sets/{po_set_id}/view", status_code=302)


# ---------------------------------------------------------------------------
# Audit log — read-only (GET only, no POST/PUT/DELETE)
# ---------------------------------------------------------------------------


@router.get("/audit", response_class=HTMLResponse)
def audit_log(request: Request):
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        entries = s.query(AuditLog).order_by(AuditLog.timestamp.desc()).all()
        return _templates.TemplateResponse(
            request,
            "audit.html",
            {"request": request, "entries": entries},
        )


# ---------------------------------------------------------------------------
# Quarantine — full page + HTMX fragment
# ---------------------------------------------------------------------------


@router.get("/quarantine", response_class=HTMLResponse)
def quarantine_view(request: Request):
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        qs = s.query(POSet).filter(POSet.status == POSetStatus.quarantined).all()
        enriched = []
        for ps in qs:
            cnt = s.query(Document).filter_by(po_set_id=ps.id).count()
            enriched.append(
                {"id": ps.id, "po_no_normalized": ps.po_no_normalized, "doc_count": cnt}
            )
        return _templates.TemplateResponse(
            request,
            "quarantine.html",
            {"request": request, "po_sets": enriched},
        )


@router.get("/quarantine/table", response_class=HTMLResponse)
def quarantine_table(request: Request):
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        qs = s.query(POSet).filter(POSet.status == POSetStatus.quarantined).all()
        enriched = []
        for ps in qs:
            cnt = s.query(Document).filter_by(po_set_id=ps.id).count()
            enriched.append(
                {"id": ps.id, "po_no_normalized": ps.po_no_normalized, "doc_count": cnt}
            )
        return _templates.TemplateResponse(
            request,
            "_quarantine_table.html",
            {"request": request, "po_sets": enriched},
        )


# ---------------------------------------------------------------------------
# Unclassified holding area (UNKNOWN docs) — FR-5.3
# ---------------------------------------------------------------------------


@router.get("/unclassified", response_class=HTMLResponse)
def unclassified_view(request: Request):
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        docs = s.query(Document).filter(Document.doc_type == DocType.UNKNOWN).all()
        return _templates.TemplateResponse(
            request,
            "unclassified.html" if Path("templates/unclassified.html").exists() else "base.html",
            {"request": request, "documents": docs},
        )
