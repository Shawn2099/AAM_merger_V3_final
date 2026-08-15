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
        if status_filter not in _ALLOWED_STATUSES:
            return []
        all_sets = q.all()
        pools = [
            ps
            for ps in all_sets
            if (ps.status.value if hasattr(ps.status, "value") else str(ps.status)) == status_filter
        ]
    else:
        pools = q.all()
    out = []
    for ps in pools:
        docs = session.query(Document).filter_by(po_set_id=ps.id).all()
        status_val = ps.status.value if hasattr(ps.status, "value") else str(ps.status)
        has_merged_file = bool(ps.merged_output_path and Path(ps.merged_output_path).exists())
        out.append(
            {
                "id": ps.id,
                "po_no_normalized": ps.po_no_normalized,
                "status": ps.status,
                "status_val": status_val,
                "doc_count": len(docs),
                "has_merged_file": has_merged_file,
                "updated_at": ps.updated_at,
                "locked_by_action": ps.locked_by_action,
                "is_locked": _is_locked(ps, cfg),
            }
        )
    out.sort(key=lambda x: x["updated_at"] or x["id"], reverse=True)  # type: ignore[no-matching-overload]
    return out


def _sync_running_state() -> bool:
    try:
        from app.api.routes.sync import _is_sync_running

        return _is_sync_running()
    except Exception:
        return False


def _get_stats(session: Session) -> dict:
    all_sets = session.query(POSet).all()
    unclassified_count = (
        session.query(Document).filter(Document.doc_type == DocType.UNKNOWN).count()
    )

    def count_status(s_name: str) -> int:
        return sum(
            1
            for ps in all_sets
            if (ps.status.value if hasattr(ps.status, "value") else str(ps.status)) == s_name
        )

    merged_c = count_status("merged")
    total_c = len(all_sets)
    pct = round(merged_c / total_c * 100) if total_c > 0 else 0

    return {
        "total": total_c,
        "merged": merged_c,
        "merged_pct": pct,
        "mismatched": count_status("mismatched"),
        "blocked_customs": count_status("blocked_customs"),
        "quarantined": count_status("quarantined"),
        "pending": count_status("pending"),
        "unclassified": unclassified_count,
    }


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
        for ps in s.query(POSet).all():
            if ps.locked_by_action is not None and not _is_locked(ps, cfg):
                ps.locked_by_action = None
        s.commit()
        po_sets = _po_sets_with_doc_count(s, status, cfg)
        sync_running = _sync_running_state()
        stats = _get_stats(s)

        if request.headers.get("HX-Request") == "true":
            return _templates.TemplateResponse(
                request,
                "_dashboard_table.html",
                {
                    "request": request,
                    "po_sets": po_sets,
                    "current_status": status if status in _ALLOWED_STATUSES else None,
                },
            )
        return _templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "request": request,
                "po_sets": po_sets,
                "current_status": status if status in _ALLOWED_STATUSES else None,
                "sync_running": sync_running,
                "stats": stats,
                "unclassified_count": stats["unclassified"],
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
        doc_ids = [d.id for d in docs]
        flags = []
        if doc_ids:
            items = s.query(LineItem).filter(LineItem.document_id.in_(doc_ids)).all()
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

            # compute reconciliation flags in priority order (FR-11.2)
            from app.services.matching import _norm, find_unmatched, match_line
            from app.services.reconciliation import check_price, reconcile

            po_lines = [li for li in enriched if li["doc_type"] == "PO"]
            dn_lines = [li for li in enriched if li["doc_type"] == "DN"]
            si_lines = [li for li in enriched if li["doc_type"] == "SI"]
            thr = getattr(cfg.matching, "fuzzy_description_threshold", 85)

            # (1) Identification flags
            unmatched = find_unmatched(po_lines, dn_lines, si_lines, thr=thr)
            for u in unmatched:
                u_item = u.get("line_item_no") or "—"
                flags.append(
                    {
                        "priority": 1,
                        "badge": "badge-quarantined",
                        "type": "Identification Mismatch",
                        "message": f"Unmatched line item #{u_item}: {u.get('description')}",
                    }
                )
            for p in po_lines:
                m_res = match_line(p, dn_lines, si_lines, thr=thr)
                if m_res.get("quarantine"):
                    p_item = p.get("line_item_no") or "—"
                    flags.append(
                        {
                            "priority": 1,
                            "badge": "badge-quarantined",
                            "type": "Identification Conflict",
                            "message": f"Conflicting line item #{p_item}: {p.get('description')}",
                        }
                    )

            # (2) Quantity flags & (3) Price flags
            for p in po_lines:
                p_no = p.get("line_item_no")
                p_desc = p.get("description") or ""
                if p_no:
                    matching_dn = [d for d in dn_lines if d.get("line_item_no") == p_no]
                    matching_si = [s for s in si_lines if s.get("line_item_no") == p_no]
                else:
                    from rapidfuzz import fuzz

                    matching_dn = [
                        d
                        for d in dn_lines
                        if not d.get("line_item_no")
                        and fuzz.token_sort_ratio(_norm(p_desc), _norm(d.get("description") or ""))
                        >= thr
                    ]
                    matching_si = [
                        s
                        for s in si_lines
                        if not s.get("line_item_no")
                        and fuzz.token_sort_ratio(_norm(p_desc), _norm(s.get("description") or ""))
                        >= thr
                    ]

                agg_dn = sum(d["quantity"] for d in matching_dn)
                agg_si = sum(s["quantity"] for s in matching_si)
                rec = reconcile(p["quantity"], agg_dn, agg_si)
                if not rec["ok"]:
                    po_q = p["quantity"] / 1000
                    dn_q = agg_dn / 1000
                    si_q = agg_si / 1000
                    flags.append(
                        {
                            "priority": 2,
                            "badge": "badge-mismatched",
                            "type": "Quantity Mismatch",
                            "message": (
                                f"Line #{p_no or '—'}: PO ({po_q:g}) != "
                                f"DN ({dn_q:g}) or SI ({si_q:g})"
                            ),
                        }
                    )
                if matching_si:
                    p_check = check_price(p["unit_price"], matching_si[0]["unit_price"])
                    if p_check["flag"]:
                        po_pr = p["unit_price"] / 1000
                        si_pr = matching_si[0]["unit_price"] / 1000
                        flags.append(
                            {
                                "priority": 3,
                                "badge": "badge-pending",
                                "type": "Price Flag",
                                "message": (
                                    f"Line #{p_no or '—'}: PO price ({po_pr:g}) != "
                                    f"SI price ({si_pr:g})"
                                ),
                            }
                        )

            # Compute 3-Way Reconciliation Comparison Matrix rows
            matrix_rows = []
            combined_lines = [li for li in enriched if li["doc_type"] == "COMBINED"]
            base_lines = po_lines if po_lines else combined_lines

            for p in base_lines:
                p_no = p.get("line_item_no")
                p_desc = p.get("description") or ""
                if p_no:
                    matching_dn = [d for d in dn_lines if d.get("line_item_no") == p_no]
                    matching_si = [s for s in si_lines if s.get("line_item_no") == p_no]
                else:
                    from rapidfuzz import fuzz

                    matching_dn = [
                        d
                        for d in dn_lines
                        if not d.get("line_item_no")
                        and fuzz.token_sort_ratio(_norm(p_desc), _norm(d.get("description") or ""))
                        >= thr
                    ]
                    matching_si = [
                        s
                        for s in si_lines
                        if not s.get("line_item_no")
                        and fuzz.token_sort_ratio(_norm(p_desc), _norm(s.get("description") or ""))
                        >= thr
                    ]

                agg_dn = sum(d["quantity"] for d in matching_dn)
                agg_si = sum(s["quantity"] for s in matching_si)
                rec = reconcile(p["quantity"], agg_dn, agg_si) if po_lines else {"ok": True}
                price_flag = False
                si_pr = None
                if matching_si:
                    p_check = check_price(p["unit_price"], matching_si[0]["unit_price"])
                    price_flag = p_check["flag"]
                    si_pr = matching_si[0]["unit_price"] / 1000

                if not rec["ok"]:
                    row_class = "row-mismatch"
                    v_badge = "badge-mismatched"
                    po_g = p["quantity"] / 1000
                    dn_g = agg_dn / 1000
                    si_g = agg_si / 1000
                    v_text = f"❌ Mismatch (PO: {po_g:g}, DN: {dn_g:g}, SI: {si_g:g})"
                elif price_flag:
                    row_class = "row-match"
                    v_badge = "badge-pending"
                    v_text = "⚠️ Price Difference"
                else:
                    row_class = "row-match"
                    v_badge = "badge-merged"
                    v_text = "✅ Match"

                matrix_rows.append(
                    {
                        "line_item_no": p_no or "—",
                        "description": p_desc,
                        "po_qty": p["quantity"] / 1000,
                        "dn_agg_qty": (agg_dn / 1000) if dn_lines else (p["quantity"] / 1000),
                        "si_agg_qty": (agg_si / 1000) if si_lines else (p["quantity"] / 1000),
                        "dn_count": len(matching_dn),
                        "si_count": len(matching_si),
                        "po_price": p["unit_price"] / 1000,
                        "si_price": si_pr,
                        "row_class": row_class,
                        "badge": v_badge,
                        "verdict": v_text,
                    }
                )

            flags.sort(key=lambda f: f["priority"])

        else:
            enriched = []
            matrix_rows = []

        has_merged_file = bool(ps.merged_output_path and Path(ps.merged_output_path).exists())
        unclassified_count = s.query(Document).filter(Document.doc_type == DocType.UNKNOWN).count()

        return _templates.TemplateResponse(
            request,
            "po_set_detail.html",
            {
                "request": request,
                "po_set": ps,
                "documents": docs,
                "line_items": enriched,
                "matrix_rows": matrix_rows,
                "flags": flags,
                "is_locked": is_locked,
                "has_merged_file": has_merged_file,
                "unclassified_count": unclassified_count,
            },
        )


@router.get("/documents/{doc_id}/preview")
def preview_document(doc_id: int):
    """Stream stored document PDF for inline preview drawer."""
    from fastapi.responses import FileResponse

    cfg = load_config()
    eng = get_engine(cfg)
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if not doc or not doc.stored_path:
            raise HTTPException(status_code=404, detail="Document not found")
        p = Path(doc.stored_path)
        if not p.exists():
            raise HTTPException(status_code=404, detail="Stored PDF file missing from disk")
        return FileResponse(p, media_type="application/pdf")


@router.get("/po_sets/{po_set_id}/merged_pdf")
def download_merged_pdf(po_set_id: int):
    """Stream final merged PDF for downloading or inline inspection."""
    from fastapi.responses import FileResponse

    cfg = load_config()
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if not ps or not ps.merged_output_path:
            raise HTTPException(status_code=404, detail="Merged PDF not available")
        p = Path(ps.merged_output_path)
        if not p.exists():
            raise HTTPException(status_code=404, detail="Merged output PDF missing from disk")
        return FileResponse(p, media_type="application/pdf", filename=p.name)


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
        unclassified_count = s.query(Document).filter(Document.doc_type == DocType.UNKNOWN).count()
        return _templates.TemplateResponse(
            request,
            "audit.html",
            {
                "request": request,
                "entries": entries,
                "unclassified_count": unclassified_count,
            },
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
        unclassified_count = s.query(Document).filter(Document.doc_type == DocType.UNKNOWN).count()
        return _templates.TemplateResponse(
            request,
            "quarantine.html",
            {
                "request": request,
                "po_sets": enriched,
                "unclassified_count": unclassified_count,
            },
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
            "unclassified.html",
            {
                "request": request,
                "documents": docs,
                "unclassified_count": len(docs),
            },
        )


@router.post("/unclassified/{doc_id}/reclassify", response_class=HTMLResponse)
def reclassify_document(
    doc_id: int,
    request: Request,
    doc_type: str = Form(...),
    po_no: str | None = Form(None),
):
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    from app.services.grouping import get_or_create_po_set

    try:
        new_doc_type = DocType(doc_type)
    except Exception as err:
        raise HTTPException(status_code=422, detail=f"Invalid doc_type: {doc_type}") from err

    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
        doc.doc_type = new_doc_type
        if po_no and po_no.strip():
            import re

            raw = po_no.strip()
            norm = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
            doc.po_no_raw = raw
            doc.po_no_normalized = norm
            ps = get_or_create_po_set(raw, cfg)
            doc.po_set_id = ps.id
        s.commit()

        if request.headers.get("HX-Request") == "true":
            msg = f"Reclassified document #{doc_id} as {new_doc_type.value}"
            html = (
                f'<tr id="doc-row-{doc_id}">'
                f'<td colspan="7" style="color: #166534; background: #dcfce7; padding: 8px;">'
                f"{msg}</td></tr>"
            )
            return HTMLResponse(content=html)
        return RedirectResponse(url="/unclassified", status_code=302)
