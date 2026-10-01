"""Dashboard + Audit + Polish — wires all views (S 8), audit read-only,
5-status filter, customs toggle, Redo split, Force Merge modal, HTMX refresh.
Cross-platform via pathlib. No hardcoded paths. Sync def handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import (
    AuditLog,
    DocType,
    Document,
    ExtractionStatus,
    LineItem,
    POSet,
    POSetStatus,
)
from app.models.base import Base
from app.services.locking import acquire_lock, is_locked, release_lock

router = APIRouter()

#: Document types an operator may assign by hand on the unclassified page.
#: Mirrors the `<select>` in `templates/unclassified.html`. The multi-document
#: packaging type is intentionally absent: Layer 1's split decides it, and it is
#: not something a human can assert about a single document.
_HAND_ASSIGNABLE_DOC_TYPES = frozenset(
    {
        DocType.PO,
        DocType.DN,
        DocType.SI,
        DocType.CUSTOMS,
        DocType.SHIPPING,
    }
)

# Jinja templates — directory = "templates" (cross-platform, not hardcoded absolute)
_templates = Jinja2Templates(directory="templates")

# 5 statuses as defined in SPEC §6.3 — enum values are the source of truth
_ALLOWED_STATUSES = {s.value for s in POSetStatus}


def _po_sets_with_doc_count(session: Session, status_filter: str | None, cfg) -> list[dict]:
    q = session.query(POSet)
    if status_filter:
        if status_filter not in _ALLOWED_STATUSES:
            return []
        try:
            target_status = POSetStatus(status_filter)
            q = q.filter(POSet.status == target_status)
        except ValueError:
            return []
    pools = q.all()

    if not pools:
        return []

    po_set_ids = [ps.id for ps in pools]
    doc_count_rows = (
        session.query(Document.po_set_id, func.count(Document.id))
        .filter(Document.po_set_id.in_(po_set_ids))
        .group_by(Document.po_set_id)
        .all()
    )
    doc_counts = {r[0]: r[1] for r in doc_count_rows if r[0] is not None}

    out = []
    for ps in pools:
        status_val = ps.status.value if hasattr(ps.status, "value") else str(ps.status)
        has_merged_file = bool(ps.merged_output_path and Path(ps.merged_output_path).exists())
        out.append(
            {
                "id": ps.id,
                "po_no_normalized": ps.po_no_normalized,
                "status": ps.status,
                "status_val": status_val,
                "doc_count": doc_counts.get(ps.id, 0),
                "has_merged_file": has_merged_file,
                "updated_at": ps.updated_at,
                "reconcile_reason": ps.reconcile_reason,
                "locked_by_action": ps.locked_by_action,
                "is_locked": is_locked(ps, cfg),
            }
        )

    def _sort_key(x: dict) -> tuple[datetime, int]:
        dt = x.get("updated_at")
        if dt is None:
            return (datetime.min, x.get("id") or 0)
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
        return (dt, x.get("id") or 0)

    out.sort(key=_sort_key, reverse=True)
    return out


def _now_ts() -> float:
    """Current epoch seconds, passed to templates so relative ages render
    server-side (no client clock, no layout shift on refresh)."""
    import time

    return time.time()


def _sync_running_state() -> bool:
    try:
        from app.api.routes.sync import _is_sync_running

        return _is_sync_running()
    except Exception:
        return False


def _get_stats(session: Session) -> dict:
    rows = session.query(POSet.status, func.count(POSet.id)).group_by(POSet.status).all()
    counts = {}
    for st, cnt in rows:
        st_val = st.value if hasattr(st, "value") else str(st)
        counts[st_val] = cnt

    total_c = sum(counts.values())
    merged_c = counts.get("merged", 0)
    pct = round(merged_c / total_c * 100) if total_c > 0 else 0
    unclassified_count = (
        session.query(Document).filter(Document.doc_type == DocType.UNKNOWN).count()
    )

    return {
        "total": total_c,
        "merged": merged_c,
        "merged_pct": pct,
        "mismatched": counts.get("mismatched", 0),
        "blocked_customs": counts.get("blocked_customs", 0),
        "quarantined": counts.get("quarantined", 0),
        "pending": counts.get("pending", 0),
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
            if ps.locked_by_action is not None and not is_locked(ps, cfg):
                release_lock(ps, s)
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
                    "now_ts": _now_ts(),
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
                "now_ts": _now_ts(),
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
                "now_ts": _now_ts(),
            },
        )


# ---------------------------------------------------------------------------
# PO Set detail — full page (wires customs toggle, redo split, force merge modal, HTMX poll)
# ---------------------------------------------------------------------------


@router.get("/po_sets/{po_set_id}/view", response_class=HTMLResponse)
def po_set_detail_view(po_set_id: int, request: Request, notice: str | None = None):
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
        locked = is_locked(ps, cfg)
        docs = s.query(Document).filter_by(po_set_id=po_set_id).all()
        doc_ids = [d.id for d in docs]
        flags = []
        enriched = []
        matrix_rows = []
        if doc_ids:
            items = s.query(LineItem).filter(LineItem.document_id.in_(doc_ids)).all()
            doc_type_map = {
                d.id: (d.doc_type.value if hasattr(d.doc_type, "value") else str(d.doc_type))
                for d in docs
            }
            enriched = [
                {
                    "line_item_no": li.line_item_no,
                    "description": li.description,
                    "quantity": li.quantity,
                    "unit_price": li.unit_price,
                    "doc_type": doc_type_map.get(li.document_id, ""),
                }
                for li in items
            ]

            # The detail view runs the SAME comparison the engine runs, so the
            # verdict on screen is the verdict that set the status. It is a
            # preview only: nothing here writes state.
            from app.services.reconciliation import REASON_TEXT, compare_po_set_lines

            po_lines = [li for li in enriched if li["doc_type"] == "PO"]
            dn_lines = [li for li in enriched if li["doc_type"] == "DN"]
            si_lines = [li for li in enriched if li["doc_type"] == "SI"]
            thr = getattr(cfg.matching, "fuzzy_description_threshold", 85)

            comparison = compare_po_set_lines(po_lines, dn_lines, si_lines, thr)

            if comparison["po_fail"]:
                flags.append(
                    {
                        "priority": 1,
                        "badge": "badge-quarantined",
                        "type": "Identification Mismatch",
                        "message": REASON_TEXT[comparison["po_fail"]],
                    }
                )

            for f in comparison["flags"]:
                if f["type"] == "identification":
                    flags.append(
                        {
                            "priority": 1,
                            "badge": "badge-quarantined",
                            "type": "Identification Mismatch",
                            "message": f"{f['pool']} line has no matching PO line",
                        }
                    )
                else:
                    po_q = (f["po_quantity"] or 0) / 1000
                    v_q = (f["vendor_quantity"] or 0) / 1000
                    verb = "delivered" if f["pool"] == "DN" else "invoiced"
                    flags.append(
                        {
                            "priority": 2,
                            "badge": "badge-mismatched",
                            "type": "Quantity Mismatch",
                            "message": f"Line #{f['line_item_no']}: {verb} {v_q:g} of {po_q:g}",
                        }
                    )

            # Per-PO-line 3-way matrix, built from the same totals the
            # comparison just used. Keys are normalised so a PO printing "01"
            # lines up with a DN printing "1".
            from app.services.matching import normalize_line_no

            for p in po_lines:
                raw_line_no = p.get("line_item_no")
                key = normalize_line_no(str(raw_line_no) if raw_line_no is not None else None)
                dn_scaled = comparison["dn_totals"].get(key, 0)
                si_scaled = comparison["si_totals"].get(key, 0)
                agg_dn = dn_scaled / 1000
                agg_si = si_scaled / 1000
                raw_qty = int(p.get("quantity") or 0)
                raw_price = int(p.get("unit_price") or 0)
                po_q = raw_qty / 1000
                reconciled = (bool(dn_lines) and dn_scaled == raw_qty) and (
                    bool(si_lines) and si_scaled == raw_qty
                )
                if reconciled:
                    row_class, badge, verdict = "row-match", "badge-merged", "✅ Match"
                else:
                    row_class, badge = "row-mismatch", "badge-mismatched"
                    verdict = f"❌ Mismatch (PO: {po_q:g}, DN: {agg_dn:g}, SI: {agg_si:g})"
                matrix_rows.append(
                    {
                        "line_item_no": key or "—",
                        "description": p.get("description") or "",
                        "po_qty": po_q,
                        "dn_agg_qty": agg_dn,
                        "si_agg_qty": agg_si,
                        "po_price": raw_price / 1000,
                        "si_price": None,
                        "row_class": row_class,
                        "badge": badge,
                        "verdict": verdict,
                    }
                )

            flags.sort(key=lambda f: f["priority"])

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
                "is_locked": locked,
                "has_merged_file": has_merged_file,
                "unclassified_count": unclassified_count,
                "notice": notice,
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
# Manual document upload (CUSTOMS/SHIPPING) — cross-platform
# ---------------------------------------------------------------------------


@router.post("/po_sets/{po_set_id}/upload", response_class=HTMLResponse)
def upload_manual_doc(
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
    ):
        raise HTTPException(
            status_code=422,
            detail=f"doc_type must be CUSTOMS or SHIPPING, got {doc_type}",
        )
    # lock check (per-PO)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise HTTPException(status_code=404, detail=f"POSet {po_set_id} not found")
        if not acquire_lock(ps, "manual_upload", s, cfg):
            raise HTTPException(
                status_code=409,
                detail=f"action already in progress on this PO Set: {ps.locked_by_action}",
            )
        try:
            # Bounded read (W-12): cap enforced on the stream, not after.
            from app.core.limits import MAX_UPLOAD_BYTES

            data = file.file.read(MAX_UPLOAD_BYTES + 1)
            if not data:
                raise HTTPException(status_code=422, detail="empty file")
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=422,
                    detail=f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB upload cap",
                )
            import hashlib

            sha = hashlib.sha256(data).hexdigest()
            # sanitize filename + extension allowlist (W-18)
            safe_name = Path(file.filename or "upload.pdf").name
            if Path(safe_name).suffix.lower() != ".pdf":
                raise HTTPException(status_code=422, detail="only .pdf uploads accepted")
            # Dedup by hash BEFORE writing (W-12). Identical bytes are ONE
            # document: `documents.sha256_hash` is unique, so a second upload
            # of the same file cannot create a second row, and re-uploading to
            # the same set is idempotent.
            #
            # The case that used to be a silent no-op: the same PDF already
            # exists as a document belonging to a DIFFERENT PO Set. The row was
            # left exactly as it was, not attached here, no error, HTTP 302 to
            # a page that still showed the gate as unsatisfied. The operator had
            # no way to tell a working upload from a discarded one, and the
            # customs gate could never clear. It is now a 409 that names the
            # owning PO Set.
            existing = s.query(Document).filter_by(sha256_hash=sha).first()
            if existing is not None and existing.po_set_id == po_set_id:
                already_attached = True
            else:
                already_attached = False
            if existing is not None and existing.po_set_id not in (None, po_set_id):
                owner = existing.po_set_id
                owner_po = None
                if owner is not None:
                    owner_ps = s.get(POSet, owner)
                    owner_po = owner_ps.po_no_normalized if owner_ps else None
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This exact file is already attached to another PO Set"
                        + (f" ({owner_po})" if owner_po else f" (id {owner})")
                        + ". Documents are deduplicated by content, so the same"
                        " file cannot be attached twice. Remove it from that set"
                        " first, or upload the correct document."
                    ),
                )
            if existing is None:
                # PDF content sniff (W-12): magic header + readable pages
                if not data.startswith(b"%PDF"):
                    raise HTTPException(status_code=422, detail="not a PDF file")
                try:
                    import io

                    from pypdf import PdfReader

                    if len(PdfReader(io.BytesIO(data)).pages) < 1:
                        raise HTTPException(status_code=422, detail="PDF has no pages")
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(status_code=422, detail=f"unreadable PDF: {e}") from e
                stored = Path(cfg.paths.stored_documents_folder) / f"{sha}.pdf"
                stored.parent.mkdir(parents=True, exist_ok=True)
                stored.write_bytes(data)
                from app.models import ExtractionStatus as ES

                doc = Document(
                    sha256_hash=sha,
                    original_filename=safe_name,
                    stored_path=str(stored),
                    doc_type=DocType(doc_type),
                    extraction_status=ES.valid,
                    po_set_id=po_set_id,
                )
                s.add(doc)
                s.flush()
            # update customs_doc_count (distinct required types: CUSTOMS and SHIPPING)
            docs = s.query(Document).filter_by(po_set_id=po_set_id).all()
            types_present = {
                d.doc_type.value if hasattr(d.doc_type, "value") else str(d.doc_type) for d in docs
            }
            cnt = (1 if DocType.CUSTOMS.value in types_present else 0) + (
                1 if DocType.SHIPPING.value in types_present else 0
            )
            ps.customs_doc_count = cnt
            if ps.has_customs_toggle and cnt == 2 and ps.status == POSetStatus.blocked_customs:
                ps.status = POSetStatus.pending
            s.commit()
            if already_attached:
                # Same file, same set: nothing changed. Say so rather than
                # redirecting as if a document had been added. No session
                # middleware exists, so this rides the query string the way
                # the other POST routes report outcomes.
                return RedirectResponse(
                    url=(
                        f"/po_sets/{po_set_id}/view?notice="
                        + quote(
                            f"That file is already attached to this PO Set "
                            f"({safe_name}); nothing was changed."
                        )
                    ),
                    status_code=302,
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        finally:
            release_lock(ps, s, "manual_upload")
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
        po_set_ids = [ps.id for ps in qs]
        doc_counts = (
            {
                r[0]: r[1]
                for r in (
                    s.query(Document.po_set_id, func.count(Document.id))
                    .filter(Document.po_set_id.in_(po_set_ids))
                    .group_by(Document.po_set_id)
                    .all()
                )
                if r[0] is not None
            }
            if po_set_ids
            else {}
        )
        enriched = [
            {
                "id": ps.id,
                "po_no_normalized": ps.po_no_normalized,
                "doc_count": doc_counts.get(ps.id, 0),
            }
            for ps in qs
        ]
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
        po_set_ids = [ps.id for ps in qs]
        doc_counts = (
            {
                r[0]: r[1]
                for r in (
                    s.query(Document.po_set_id, func.count(Document.id))
                    .filter(Document.po_set_id.in_(po_set_ids))
                    .group_by(Document.po_set_id)
                    .all()
                )
                if r[0] is not None
            }
            if po_set_ids
            else {}
        )
        enriched = [
            {
                "id": ps.id,
                "po_no_normalized": ps.po_no_normalized,
                "doc_count": doc_counts.get(ps.id, 0),
            }
            for ps in qs
        ]
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
        # The holding area shows untyped documents AND permanently failed ones
        # of any type: a failure is a loss the operator must see, wherever it
        # happened. Failed rows never attach (sweeps only take `valid`).
        docs = (
            s.query(Document)
            .filter(
                or_(
                    Document.doc_type == DocType.UNKNOWN,
                    Document.extraction_status == ExtractionStatus.failed,
                )
            )
            .all()
        )
        # A document whose extraction has permanently failed is NOT waiting for
        # a human to classify it — it is a loss. It stays in this view (its
        # doc_type was never advanced off UNKNOWN) and must be countable
        # separately, or the holding area reports a clean sheet while holding
        # files that will never be read.
        failed_count = sum(1 for d in docs if d.extraction_status == ExtractionStatus.failed)
        return _templates.TemplateResponse(
            request,
            "unclassified.html",
            {
                "request": request,
                "documents": docs,
                "unclassified_count": len(docs),
                "failed_count": failed_count,
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
    # Hand-assignable types, positively enumerated. This is an allowlist rather
    # than a denylist so that no Layer-2 code has to name a type it must never
    # produce: the multi-document packaging type is decided by the split in
    # Layer 1 and has no single-document meaning for an operator to assert here.
    # Rejecting anything outside the list keeps that rule true automatically as
    # types are added, instead of relying on one explicit check to be maintained.
    if new_doc_type not in _HAND_ASSIGNABLE_DOC_TYPES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"doc_type {new_doc_type.value} cannot be hand-tagged; "
                "assign the type of an individual document instead"
            ),
        )

    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
        doc.doc_type = new_doc_type
        if po_no and po_no.strip():
            from app.services.grouping import normalize_po_no

            raw = po_no.strip()
            doc.po_no_raw = raw
            doc.po_no_normalized = normalize_po_no(raw)
            ps = get_or_create_po_set(raw, cfg)
            doc.po_set_id = ps.id

        # Update customs_doc_count and status transition if attached to a PO Set
        if doc.po_set_id is not None:
            ps_target = s.get(POSet, doc.po_set_id)
            if ps_target is not None:
                docs = s.query(Document).filter_by(po_set_id=ps_target.id).all()
                types_present = {
                    d.doc_type.value if hasattr(d.doc_type, "value") else str(d.doc_type)
                    for d in docs
                }
                cnt = (1 if DocType.CUSTOMS.value in types_present else 0) + (
                    1 if DocType.SHIPPING.value in types_present else 0
                )
                ps_target.customs_doc_count = cnt
                if (
                    ps_target.has_customs_toggle
                    and cnt == 2
                    and ps_target.status == POSetStatus.blocked_customs
                ):
                    ps_target.status = POSetStatus.pending
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
