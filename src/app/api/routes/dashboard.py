"""Dashboard + Audit + Polish — wires all views (S 8), audit read-only,
5-status filter, customs toggle, Redo split, Force Merge modal, HTMX refresh.
Cross-platform via pathlib. No hardcoded paths. Sync def handlers."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, selectinload

from app.api.routes.po_sets import _scrub_paths
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
from app.services.locking import acquire_lock, is_locked, release_if_stale, release_lock

router = APIRouter()

logger = logging.getLogger(__name__)

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
_templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[4] / "templates"))

# 5 statuses as defined in SPEC §6.3 — enum values are the source of truth
_ALLOWED_STATUSES = {s.value for s in POSetStatus}


def _holding_area_query(session: Session):
    """Every document waiting for a human, in one place.

    The holding area is more than "UNKNOWN documents": it also holds documents
    that failed permanently, documents showing more than one PO number, and
    documents read successfully but attached to no set. The dashboard badge
    used to count only UNKNOWN, so it said "2" while the page held 37 — the
    exact way work goes unseen. Every count must use this query.
    """
    # A split parent whose split COMPLETED is sterile: its children own the
    # content, and listing it would duplicate the whole packet. A parent whose
    # split FAILED — or never finished — is a loss, not a duplicate. Excluding
    # every parent (audit P0-3) hid exactly that loss from the only list an
    # operator can act on: it was in no set, on no page, and could not be
    # reclassified. A `valid` incomplete parent is excluded because
    # `extract_document` re-reads it on the next pass; it is not waiting.
    visible = or_(
        Document.is_split_parent.is_(False),
        and_(
            Document.is_split_parent.is_(True),
            Document.split_completed_at.is_(None),
            Document.extraction_status == ExtractionStatus.failed,
        ),
    )
    return session.query(Document).filter(
        Document.archived_at.is_(None),
        visible,
        # The holding area is every document NOT in a PO Set. That includes
        # valid unplaced documents, failures, unknowns and ambiguous PO
        # numbers. An attached document (even a failed one) belongs to its
        # set's detail page; listing it here invited the operator to reclassify
        # it out of a merged set.
        Document.po_set_id.is_(None),
    )


def _po_sets_with_doc_count(session: Session, status_filter: str | None, cfg) -> list[dict]:
    q = session.query(POSet).filter(POSet.archived_at.is_(None))
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
        .filter(
            Document.po_set_id.in_(po_set_ids),
            Document.archived_at.is_(None),
        )
        .group_by(Document.po_set_id)
        .all()
    )
    doc_counts = {r[0]: r[1] for r in doc_count_rows if r[0] is not None}

    out = []
    for ps in pools:
        status_val = ps.status.value if hasattr(ps.status, "value") else str(ps.status)
        has_merged_file = bool(
            ps.merged_output_path
            and _resolve_db_path(ps.merged_output_path, cfg.paths.output_folder)
        )
        out.append(
            {
                "id": ps.id,
                "po_no_normalized": ps.po_no_normalized,
                "status": ps.status,
                "status_val": status_val,
                "doc_count": doc_counts.get(ps.id, 0),
                "has_merged_file": has_merged_file,
                "updated_at": ps.updated_at,
                "age_seconds": _age_seconds(ps.updated_at),
                "reconcile_reason": ps.reconcile_reason,
                "locked_by_action": ps.locked_by_action,
                "is_locked": is_locked(ps, cfg),
                "has_customs_toggle": ps.has_customs_toggle,
                "customs_doc_count": ps.customs_doc_count or 0,
                "customs_append_pending": bool(ps.customs_append_pending),
                "description_match_count": ps.description_match_count or 0,
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


def _age_seconds(dt: datetime | None) -> int | None:
    """Age in seconds for a DB timestamp.

    SQLite hands back naive datetimes even for `DateTime(timezone=True)`; they
    are UTC. Calling `.timestamp()` on a naive value interprets it as local
    time and inflates every age by the host's UTC offset (4h in the UAE, 5.5h
    here). Normalise to UTC explicitly; this mirrors `locking.is_locked`.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - dt).total_seconds()))


def build_matrix_rows(
    po_lines: list[dict],
    dn_lines: list[dict],
    si_lines: list[dict],
    comparison: dict,
    set_status: str | None = None,
) -> list[dict]:
    """One matrix row per comparison key, using the engine's own totals.

    The old code iterated raw PO rows and looked up the vendor aggregate: a PO
    printing line 1 twice (40 + 60) produced two rows that each compared 40/60
    against the vendor's 100 and showed a false mismatch on a merged set.
    """
    from app.services.matching import display_line_key, po_item_key

    po_first: dict[str, dict] = {}
    for p in po_lines:
        po_first.setdefault(po_item_key(p), p)

    # Lines the engine attached by the description fallback. The flag carries
    # the displayed key, which is what the matrix shows.
    desc_keys = {
        str(f.get("line_item_no"))
        for f in comparison.get("flags", [])
        if f.get("type") == "description_match"
    }

    rows: list[dict] = []
    for key, po_scaled in comparison["po_totals"].items():
        p = po_first.get(key, {})
        dn_scaled = comparison["dn_totals"].get(key, 0)
        si_scaled = comparison["si_totals"].get(key, 0)
        agg_dn = dn_scaled / 1000
        agg_si = si_scaled / 1000
        po_q = po_scaled / 1000
        missing_pool = "DN" if not dn_lines else ("SI" if not si_lines else None)
        reconciled = not missing_pool and dn_scaled == po_scaled and si_scaled == po_scaled
        if reconciled:
            row_class, badge, verdict = "row-match", "badge-merged", "\u2705 Match"
        elif missing_pool:
            row_class, badge = "row-pending", "badge-pending"
            verdict = f"Awaiting {missing_pool}"
        elif set_status == "pending":
            # A pending set is waiting on a vendor, not failing. Painting the
            # row red made 33 real pending sets look like mismatches.
            row_class, badge = "row-pending", "badge-pending"
            verdict = f"Awaiting (PO: {po_q:g}, DN: {agg_dn:g}, SI: {agg_si:g})"
        else:
            row_class, badge = "row-mismatch", "badge-mismatched"
            verdict = f"\u274c Mismatch (PO: {po_q:g}, DN: {agg_dn:g}, SI: {agg_si:g})"
        rows.append(
            {
                "line_item_no": display_line_key(key) or "—",
                "description": p.get("description") or "",
                "po_qty": po_q,
                "dn_agg_qty": agg_dn,
                "si_agg_qty": agg_si,
                "row_class": row_class,
                "badge": badge,
                "verdict": verdict,
                "desc_matched": (display_line_key(key) or "—") in desc_keys,
            }
        )
    return rows


def _sync_running_state() -> bool:
    try:
        from app.api.routes.sync import _is_sync_running

        return _is_sync_running()
    except Exception:
        return False


def _get_stats(session: Session) -> dict:
    rows = (
        session.query(POSet.status, func.count(POSet.id))
        .filter(POSet.archived_at.is_(None))
        .group_by(POSet.status)
        .all()
    )
    counts = {}
    for st, cnt in rows:
        st_val = st.value if hasattr(st, "value") else str(st)
        counts[st_val] = cnt

    total_c = sum(counts.values())
    merged_c = counts.get("merged", 0)
    pct = round(merged_c / total_c * 100) if total_c > 0 else 0
    unclassified_count = _holding_area_query(session).count()

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
    # The select's "All statuses" option submits `?status=`; an empty value
    # means no filter, not an unknown status.
    if status and status not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown status filter: {status}")
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        for ps in s.query(POSet).filter(POSet.archived_at.is_(None)).all():
            if ps.locked_by_action is not None:
                # Conditional on the observed lock: a fresh lock acquired in
                # the gap must not be cleared by this poll.
                release_if_stale(ps, s, cfg)
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
    if status and status not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown status filter: {status}")
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
        if ps.locked_by_action is not None:
            release_if_stale(ps, s, cfg)
            s.refresh(ps)
        locked = is_locked(ps, cfg)
        docs = (
            s.query(Document)
            .options(selectinload(Document.line_items))
            .filter_by(po_set_id=po_set_id)
            .all()
        )
        doc_ids = [d.id for d in docs]
        flags = []
        enriched = []
        matrix_rows = []
        has_any_lines = False
        has_po_lines = False
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
            has_any_lines = bool(enriched)
            has_po_lines = bool(po_lines)
            thr = getattr(cfg.matching, "fuzzy_description_threshold", 85)

            comparison = compare_po_set_lines(po_lines, dn_lines, si_lines, thr)
            set_status_value = ps.status.value if hasattr(ps.status, "value") else str(ps.status)

            # A pending set is waiting, not failing. Comparing a set whose DN
            # has not arrived yet produces vendor-line "identifications" that
            # are really just the missing side; painting them red made 33 real
            # pending sets look like quarantines. The matrix rows say what is
            # missing; the reason card explains it.
            if comparison["po_fail"] and set_status_value != "pending":
                flags.append(
                    {
                        "priority": 1,
                        "badge": "badge-quarantined",
                        "type": "Identification Mismatch",
                        "message": REASON_TEXT[comparison["po_fail"]],
                    }
                )

            for f in comparison["flags"]:
                if set_status_value == "pending" and f["type"] in (
                    "identification",
                    "quantity",
                ):
                    continue
                if f["type"] == "identification":
                    vendor_qty = (f.get("vendor_quantity") or 0) / 1000
                    line_label = f.get("line_item_no") or "?"
                    flags.append(
                        {
                            "priority": 1,
                            "badge": "badge-quarantined",
                            "type": "Identification Mismatch",
                            "message": (
                                f"{f['pool']} line {line_label} "
                                f"(qty {vendor_qty:g}) has no matching PO line"
                            ),
                        }
                    )
                elif f["type"] == "quantity":
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
                elif f["type"] == "description_match":
                    flags.append(
                        {
                            "priority": f.get("priority", 2),
                            "badge": "badge-desc",
                            "type": "Description Match",
                            "message": f.get("message")
                            or (
                                f"{f.get('pool')} line '{f.get('vendor_description')}' was "
                                f"matched to PO line {f.get('line_item_no')} by description"
                            ),
                        }
                    )

            # Per-line 3-way matrix, built from the same totals the comparison
            # just used. One row per comparison key: a PO that prints the same
            # line number twice is summed by the engine, so comparing each raw
            # row to the summed vendor total (the old code) showed a false
            # mismatch on a set that had already merged.
            matrix_rows = build_matrix_rows(
                po_lines,
                dn_lines,
                si_lines,
                comparison,
                set_status=set_status_value,
            )

            flags.sort(key=lambda f: f["priority"])

        has_merged_file = bool(
            ps.merged_output_path
            and _resolve_db_path(ps.merged_output_path, cfg.paths.output_folder)
        )
        unclassified_count = _holding_area_query(s).count()

        return _templates.TemplateResponse(
            request,
            "po_set_detail.html",
            {
                "request": request,
                "po_set": ps,
                "documents": docs,
                "line_items": enriched,
                "matrix_rows": matrix_rows,
                "has_any_lines": has_any_lines,
                "has_po_lines": has_po_lines,
                "attempt_cap": int(getattr(cfg.extraction, "max_retries", 3) or 3),
                "flags": flags,
                "is_locked": locked,
                "has_merged_file": has_merged_file,
                "unclassified_count": unclassified_count,
                "notice": notice,
                "is_archived": ps.archived_at is not None,
                "archive_folder": str(cfg.paths.archive_folder),
            },
        )


def _resolve_db_path(path: str | Path, root: str | Path) -> Path | None:
    """Resolve a path stored in the DB, including the legacy relative form.

    Early runs stored `data\\stored\\...` and `data\\output\\...`; the config
    file (not the process CWD) is the authority for where `data/` lives. Try
    the stored value first, then CWD, then the project root derived from the
    configured folder, so a service started elsewhere still serves the file.
    """
    raw = Path(path)
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(Path.cwd() / raw)
        root_path = Path(root).resolve()
        # <project>/data/stored -> <project>
        candidates.append(root_path.parent.parent / raw)
        candidates.append(root_path / raw)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _served_file(path: str | None, root: str | Path, error: str) -> Path:
    """Resolve a DB-stored file and confine it to its configured folder.

    Every writer today uses the configured folders, so this is hardening - but
    serving whatever a DB string happens to contain (including a symlink that
    escaped the folder) is not a property the app should rely on.
    """
    if not path:
        raise HTTPException(status_code=404, detail=error)
    root_path = Path(root).resolve()
    p = _resolve_db_path(path, root)
    if p is None or not p.is_relative_to(root_path):
        raise HTTPException(status_code=404, detail=error)
    return p


@router.get("/documents/{doc_id}/preview")
def preview_document(doc_id: int):
    """Stream stored document PDF for inline preview drawer."""
    from fastapi.responses import FileResponse

    cfg = load_config()
    eng = get_engine(cfg)
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        p = _served_file(
            doc.stored_path,
            cfg.paths.stored_documents_folder,
            "Stored PDF file missing from disk",
        )
        # Two real rows (164/165) are all-NUL files that were read as failed.
        # Serving them as application/pdf gave the drawer an empty document
        # with no explanation; say the file is not a PDF instead.
        with p.open("rb") as fh:
            if fh.read(4) != b"%PDF":
                raise HTTPException(
                    status_code=422,
                    detail="Stored file is not a readable PDF; re-extract this document.",
                )
        return FileResponse(p, media_type="application/pdf")


@router.get("/po_sets/{po_set_id}/merged_pdf")
def download_merged_pdf(po_set_id: int):
    """Stream final merged PDF for downloading or inline inspection."""
    from fastapi.responses import FileResponse

    cfg = load_config()
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if not ps:
            raise HTTPException(status_code=404, detail="Merged PDF not available")
        p = _served_file(
            ps.merged_output_path,
            cfg.paths.output_folder,
            "Merged output PDF missing from disk",
        )
        return FileResponse(p, media_type="application/pdf", filename=p.name)


# ---------------------------------------------------------------------------
# Manual document upload (CUSTOMS/SHIPPING) — cross-platform
# ---------------------------------------------------------------------------


def _wants_html(request: Request) -> bool:
    """True for a browser form navigation (not an API/HTMX client).

    A plain HTML form POST that fails used to replace the page with raw JSON
    (`{"detail":"..."}`) — for a routine case such as re-uploading a file that
    is already attached. Browsers send `Accept: text/html`; TestClient/curl and
    HTMX do not, so their contract is unchanged.
    """
    return "text/html" in (request.headers.get("accept") or "")


@router.post("/po_sets/{po_set_id}/upload", response_class=HTMLResponse)
def upload_manual_doc(
    po_set_id: int,
    request: Request,
    file: UploadFile = File(...),  # noqa: B008
    doc_type: str = Form(...),
):
    try:
        return _upload_manual_doc_impl(po_set_id, request, file, doc_type)
    except HTTPException as exc:
        # A missing set has no page to redirect to; keep the 404 contract
        # instead of redirecting the browser to a JSON 404.
        if exc.status_code == 404:
            raise
        if _wants_html(request) and request.headers.get("HX-Request") != "true":
            return RedirectResponse(
                url=(f"/po_sets/{po_set_id}/view?notice=" + quote(str(exc.detail))),
                status_code=302,
            )
        raise


def _upload_manual_doc_impl(
    po_set_id: int,
    request: Request,
    file: UploadFile,
    doc_type: str,
):
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    from app.services import sync_lock

    if sync_lock._is_sync_running():
        raise HTTPException(
            status_code=409,
            detail=(
                "A sync is currently reading documents; wait for it to finish "
                "before uploading a document."
            ),
        )
    should_rebuild = False
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
        if ps.archived_at is not None:
            raise HTTPException(
                status_code=409,
                detail="This PO Set is archived and read-only; uploads are not allowed.",
            )
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
            existing = (
                s.query(Document)
                .filter_by(sha256_hash=sha)
                .filter(Document.archived_at.is_(None))
                .first()
            )
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
            elif existing.po_set_id is None:
                # The same bytes are already in the system, attached to
                # nothing. The file the operator is uploading IS this set's
                # customs/shipping document, so it is attached to the set
                # rather than left where it is: the gate could otherwise
                # never be satisfied and the operator had no way to tell a
                # working upload from a discarded one.
                #
                # A row that already carries pipeline data is refused: it is
                # a PO, DN or SI, or the parent of a multi-document split.
                # Re-typing it as a customs document would corrupt a real
                # document. The split parent is recognised by the generic
                # hierarchy column, not by naming the packaging type.
                pipeline_types = {DocType.PO.value, DocType.DN.value, DocType.SI.value}
                existing_type = (
                    existing.doc_type.value
                    if hasattr(existing.doc_type, "value")
                    else str(existing.doc_type)
                )
                has_lines = bool(list(existing.line_items or []))
                is_split_parent = bool(getattr(existing, "is_split_parent", False))
                if existing_type in pipeline_types or has_lines or is_split_parent:
                    label = "multi-document PDF" if is_split_parent else existing_type
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "This exact file is already in the system as a "
                            f"{label} document"
                            + (f" (document #{existing.id})" if existing.id else "")
                            + ". It carries pipeline data, so it cannot be re-typed as a "
                            f"{doc_type} document. Attach it to this set from the "
                            "Unclassified page instead."
                        ),
                    )
                from app.models import ExtractionStatus as ES

                existing.po_set_id = po_set_id
                existing.doc_type = DocType(doc_type)
                existing.extraction_status = ES.valid
                s.flush()
            # update customs_doc_count (distinct required types: CUSTOMS and SHIPPING)
            docs = (
                s.query(Document)
                .filter_by(po_set_id=po_set_id)
                .filter(Document.archived_at.is_(None))
                .all()
            )
            types_present = {
                d.doc_type.value if hasattr(d.doc_type, "value") else str(d.doc_type) for d in docs
            }
            cnt = (1 if DocType.CUSTOMS.value in types_present else 0) + (
                1 if DocType.SHIPPING.value in types_present else 0
            )
            ps.customs_doc_count = cnt
            if ps.has_customs_toggle and cnt == 2 and ps.status == POSetStatus.blocked_customs:
                ps.status = POSetStatus.pending
            # A merged packet can still receive customs/shipping documents.
            # The rebuild runs below while this same per-PO lock is still held;
            # releasing it first let two concurrent uploads write the same
            # temporary file and race `os.replace`.
            rebuild_flag = ps.status == POSetStatus.merged and ps.has_customs_toggle and cnt == 2
            s.commit()
            should_rebuild = rebuild_flag
            if already_attached and not should_rebuild:
                # Same file, same set, and nothing to rebuild: say so rather
                # than redirecting as if a document had been added. No session
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
            if not should_rebuild:
                release_lock(ps, s, "manual_upload")

    if should_rebuild:
        try:
            from app.services.merge import append_customs_to_merged_packet

            append_customs_to_merged_packet(po_set_id, cfg)
            return RedirectResponse(
                url=(
                    f"/po_sets/{po_set_id}/view?notice="
                    + quote("Customs and shipping were appended to the merged packet.")
                ),
                status_code=302,
            )
        except Exception as e:
            logger.warning(
                "Customs documents attached to PO Set %s but the packet could not be rebuilt",
                po_set_id,
                exc_info=True,
            )
            from app.services.merge import set_customs_append_pending

            set_customs_append_pending(po_set_id, cfg, True)
            return RedirectResponse(
                url=(
                    f"/po_sets/{po_set_id}/view?notice="
                    + quote(
                        "Documents attached, but the merged packet was not rebuilt: "
                        f"{_scrub_paths(str(e)) or type(e).__name__}. "
                        "It will retry on the next sync."
                    )
                ),
                status_code=302,
            )
        finally:
            # Release the lock only after the rebuild, using a fresh session:
            # the upload session is closed and the lock is a row, not a
            # connection. This is what stops a second upload racing the same
            # packet while the first is rebuilding it.
            with Session(get_engine(cfg)) as s_rel:
                ps_rel = s_rel.get(POSet, po_set_id)
                if ps_rel is not None:
                    release_lock(ps_rel, s_rel, "manual_upload")
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
        unclassified_count = _holding_area_query(s).count()
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
        qs = (
            s.query(POSet)
            .filter(
                POSet.status == POSetStatus.quarantined,
                POSet.archived_at.is_(None),
            )
            .all()
        )
        po_set_ids = [ps.id for ps in qs]
        doc_counts = (
            {
                r[0]: r[1]
                for r in (
                    s.query(Document.po_set_id, func.count(Document.id))
                    .filter(
                        Document.po_set_id.in_(po_set_ids),
                        Document.archived_at.is_(None),
                    )
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
                "reconcile_reason": ps.reconcile_reason,
                "updated_at": ps.updated_at,
            }
            for ps in qs
        ]
        unclassified_count = _holding_area_query(s).count()
        return _templates.TemplateResponse(
            request,
            "quarantine.html",
            {
                "request": request,
                "po_sets": enriched,
                "unclassified_count": unclassified_count,
                "now_ts": _now_ts(),
                "archive_folder": str(cfg.paths.archive_folder),
            },
        )


@router.get("/quarantine/table", response_class=HTMLResponse)
def quarantine_table(request: Request):
    cfg = load_config()
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        qs = (
            s.query(POSet)
            .filter(
                POSet.status == POSetStatus.quarantined,
                POSet.archived_at.is_(None),
            )
            .all()
        )
        po_set_ids = [ps.id for ps in qs]
        doc_counts = (
            {
                r[0]: r[1]
                for r in (
                    s.query(Document.po_set_id, func.count(Document.id))
                    .filter(
                        Document.po_set_id.in_(po_set_ids),
                        Document.archived_at.is_(None),
                    )
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
                "reconcile_reason": ps.reconcile_reason,
                "updated_at": ps.updated_at,
            }
            for ps in qs
        ]
        return _templates.TemplateResponse(
            request,
            "_quarantine_table.html",
            {
                "request": request,
                "po_sets": enriched,
                "now_ts": _now_ts(),
                "archive_folder": str(cfg.paths.archive_folder),
            },
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
        # The holding area shows every document that is not in the pipeline
        # yet, because every one of them is waiting for something:
        #
        #   - untyped documents (nothing could identify them)
        #   - documents whose extraction failed permanently (a loss — they
        #     will never be read again)
        #   - documents that show more than one PO number (both attach
        #     sweeps refuse them on purpose)
        #   - documents that were read successfully but carry no PO
        #     reference, and are in no PO Set. They are not broken: they
        #     wait for the content to place them, or for a human to name
        #     their PO.
        #
        # A document nothing can place must not wait unseen: a document
        # waiting for a human the human cannot see waits forever.
        docs = _holding_area_query(s).all()
        # A document whose extraction has permanently failed is NOT waiting for
        # a human to classify it — it is a loss. It stays in this view (its
        # doc_type was never advanced off UNKNOWN) and must be countable
        # separately, or the holding area reports a clean sheet while holding
        # files that will never be read.
        failed_count = sum(1 for d in docs if d.extraction_status == ExtractionStatus.failed)
        ambiguous_count = sum(1 for d in docs if d.po_reference_ambiguous)
        unplaced_count = sum(
            1 for d in docs if d.extraction_status == ExtractionStatus.valid and d.po_set_id is None
        )
        # Documents held away from an open set because their PO letters differ
        # from the set's (same digits, different PO). Say so on the row.
        from app.services.grouping import HOLD_REASON_TEXT, held_letters_conflict

        hold_notes: dict[int, str] = {}
        for d in docs:
            clash = held_letters_conflict(s, d)
            if clash is not None:
                hold_notes[d.id] = (
                    f"Held: {HOLD_REASON_TEXT['po_letters_conflict']} "
                    f"(open PO Set #{clash.id}, key {clash.po_no_normalized}, "
                    f"letters {clash.po_no_letters or 'none'})"
                )
        return _templates.TemplateResponse(
            request,
            "unclassified.html",
            {
                "request": request,
                "documents": docs,
                "unclassified_count": len(docs),
                "failed_count": failed_count,
                "ambiguous_count": ambiguous_count,
                "unplaced_count": unplaced_count,
                "hold_notes": hold_notes,
                "attempt_cap": int(getattr(cfg.extraction, "max_retries", 3) or 3),
            },
        )


@router.get("/unclassified/normalize")
def normalize_po_preview(po_no: str = ""):
    """The exact key the server will store for a typed PO number.

    The old Alpine preview stripped non-alphanumerics itself, so it disagreed
    with `normalize_po_no` on labels and the comma revision counter — the one
    field that decides which set a document joins.
    """
    from app.services.grouping import normalize_po_no

    try:
        return {"normalized": normalize_po_no(po_no)}
    except Exception:
        return {"normalized": ""}


def _open_set_or_409(raw: str, cfg, *, create: bool):
    """`get_or_create_po_set`, with a letters clash reported as a 409 to the operator."""
    from app.services.grouping import PoLettersConflict, get_or_create_po_set

    try:
        return get_or_create_po_set(raw, cfg, create=create)
    except PoLettersConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    from app.services import sync_lock

    # Reclassifying writes a document's type/PO while the sync may be
    # attaching the same row. Refuse rather than race the sweep.
    if sync_lock._is_sync_running():
        raise HTTPException(
            status_code=409,
            detail=(
                "A sync is currently reading documents; wait for it to finish "
                "before reclassifying a document."
            ),
        )

    attached_ps_id: int | None = None

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
        if doc.archived_at is not None:
            raise HTTPException(
                status_code=409,
                detail="This document belongs to an archived PO Set and is read-only.",
            )
        current_ps = s.get(POSet, doc.po_set_id) if doc.po_set_id else None
        if current_ps is not None and current_ps.status == POSetStatus.merged:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This document belongs to a merged PO Set and is read-only. "
                    "Use the customs append path for a post-delivery document."
                ),
            )
        # Only a held document may be placed here. One already attached to a set
        # (open, since merged and archived were refused above) would keep its old
        # line items, leave that set's status stale and race a Force Merge on its
        # lock. There is no UI path that moves it (see PRODUCT section 8).
        held = _holding_area_query(s).filter(Document.id == doc.id).first() is not None
        if not held and doc.po_set_id is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This document is already placed in PO Set #{doc.po_set_id}. "
                    "If it is in the wrong set: Re-Extract with AI sends it back to "
                    "Unclassified only when no PO number is printed on it. Otherwise, "
                    "if the set is quarantined, Delete (archive) the set and add the "
                    "files again; if not, ask the administrator."
                ),
            )
        # A split parent is the source of the child rows. Once its split
        # COMPLETED it has no line items and its stored_path is the whole
        # combined PDF, so attaching it would duplicate every child page in the
        # delivered packet. The upload and re-extract paths already refuse it;
        # this is the third entry point.
        #
        # A parent whose split FAILED never produced usable children. Refusing
        # it forever left a readable packet in no set and on no page — the
        # operator's only recovery is to reclassify it here, so that case is
        # allowed (and the row stops pretending to be a parent).
        if getattr(doc, "is_split_parent", False):
            if getattr(doc, "split_completed_at", None) is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This row is a split parent: its children are the documents "
                        "to classify. Attaching the parent would duplicate the whole "
                        "PDF in the packet."
                    ),
                )
            children = s.query(Document).filter(Document.parent_document_id == doc.id).count()
            if children:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This document's split was interrupted after cutting "
                        f"{children} child file(s). Re-run the split before "
                        "reclassifying it, or the packet will contain the pages twice."
                    ),
                )
            doc.is_split_parent = False
            doc.split_completed_at = None
        doc.doc_type = new_doc_type
        if po_no and po_no.strip():
            from app.services.grouping import normalize_po_no

            raw = po_no.strip()
            norm = normalize_po_no(raw)
            if not norm:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"PO number {raw!r} has no digits, so it cannot key a PO Set; "
                        "enter the number printed on the document."
                    ),
                )
            doc.po_no_raw = raw
            doc.po_no_normalized = norm
            doc.po_no_source = "manual"
            # A human naming the PO number resolves the ambiguity the VLM
            # reported. Left set, the document would be refused by both
            # attach sweeps if it were ever detached again, and it would keep
            # showing in the holding area as unresolved.
            doc.po_reference_ambiguous = False

            if new_doc_type in (DocType.CUSTOMS, DocType.SHIPPING):
                # Customs/shipping documents may join a packet that has
                # already been delivered; that is the append workflow. The
                # generic lookup excludes merged sets, which would strand the
                # document in a new empty set that can never merge.
                from app.services.grouping import letters_compatible, po_letters

                merged = [
                    m
                    for m in (
                        s.query(POSet)
                        .filter(
                            POSet.po_no_normalized == norm,
                            POSet.status == POSetStatus.merged,
                            POSet.archived_at.is_(None),
                        )
                        .order_by(POSet.id.desc())
                        .all()
                    )
                    if letters_compatible(m.po_no_letters, po_letters(raw))
                ]
                if len(merged) > 1:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"{len(merged)} merged packets exist for {norm}. "
                            "Open the intended PO Set and attach the document "
                            "there, so it is appended to the right packet."
                        ),
                    )
                if merged:
                    doc.po_set_id = merged[0].id
                else:
                    # No delivered packet yet: attach to an open set if one
                    # exists, otherwise leave it waiting for a human.
                    open_ps = _open_set_or_409(raw, cfg, create=False)
                    if open_ps is not None:
                        doc.po_set_id = open_ps.id
            else:
                # A human naming a PO is the explicit gate PRODUCT 8 describes:
                # place the document, creating the set when no open one exists.
                # Unlike the automatic sweeps, this is not a filename guess.
                ps = _open_set_or_409(raw, cfg, create=True)
                doc.po_set_id = ps.id

        # Update customs_doc_count and status transition if attached to a PO Set
        if doc.po_set_id is not None:
            attached_ps_id = doc.po_set_id
            ps_target = s.get(POSet, doc.po_set_id)
            if ps_target is not None and ps_target.archived_at is not None:
                raise HTTPException(
                    status_code=409,
                    detail="The target PO Set is archived and read-only.",
                )
            if ps_target is not None:
                docs = (
                    s.query(Document)
                    .filter_by(po_set_id=ps_target.id)
                    .filter(Document.archived_at.is_(None))
                    .all()
                )
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
                # A customs document placed on a delivered packet is a debt the
                # packet owes; record it with the attach so a busy lock cannot
                # lose it. A successful append clears it.
                if (
                    ps_target.status == POSetStatus.merged
                    and ps_target.has_customs_toggle
                    and cnt == 2
                ):
                    ps_target.customs_append_pending = True
        s.commit()

    # A document the operator has just placed must not wait for the next
    # nightly scan to affect its set: attaching it is the trigger. This runs
    # after the session closes — the reconcile opens its own, and SQLite
    # writes cannot overlap two writer sessions on one file.
    append_failed: str | None = None
    if attached_ps_id is not None:
        lock_ok = False
        with Session(get_engine(cfg)) as s_lock:
            ps_lock = s_lock.get(POSet, attached_ps_id)
            if ps_lock is not None:
                lock_ok = acquire_lock(ps_lock, "reclassify", s_lock, cfg)
        if not lock_ok:
            logger.info(
                "Placed document %s but PO Set %s is busy; the next reconcile will evaluate it",
                doc_id,
                attached_ps_id,
            )
            return RedirectResponse(url="/unclassified", status_code=302)
        try:
            try:
                from app.services.reconciliation import reconcile_po_set

                reconcile_po_set(attached_ps_id, cfg)
            except Exception:
                logger.warning(
                    "Placed document %s but could not reconcile PO Set %s",
                    doc_id,
                    attached_ps_id,
                    exc_info=True,
                )
            # A customs/shipping document placed from the holding area on an
            # already-merged set must reach the packet too, exactly as an upload
            # does.
            try:
                from app.services.merge import append_customs_if_complete

                append_customs_if_complete(attached_ps_id, cfg)
            except Exception as e:
                # append_customs_if_complete has already persisted the pending
                # flag; the operator is told rather than left with a log line.
                append_failed = _scrub_paths(str(e)) or type(e).__name__
                logger.warning(
                    "Placed document %s but could not rebuild the packet for PO Set %s",
                    doc_id,
                    attached_ps_id,
                    exc_info=True,
                )
        finally:
            with Session(get_engine(cfg)) as s_rel:
                ps_rel = s_rel.get(POSet, attached_ps_id)
                if ps_rel is not None:
                    release_lock(ps_rel, s_rel, "reclassify")

    if append_failed is not None:
        notice = (
            "Document placed, but the merged packet was NOT rebuilt with customs/shipping: "
            f"{append_failed}. It will retry on the next sync."
        )
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse(
                content=(
                    f'<tr id="doc-row-{doc_id}"><td colspan="6" '
                    'style="color:#92400e;background:#fffbeb;padding:8px;">'
                    f"{escape(notice)}</td></tr>"
                ),
            )
        return RedirectResponse(
            url=f"/po_sets/{attached_ps_id}/view?notice=" + quote(notice),
            status_code=302,
        )

    if attached_ps_id is None:
        msg = (
            f"Type set to {new_doc_type.value}, but no open PO Set exists for "
            "this document; it remains in the holding area."
        )
        if request.headers.get("HX-Request") == "true":
            # Reload the holding area so the row (with its new type) stays
            # visible. Removing it made a failed placement look successful.
            return HTMLResponse(
                content=(
                    f'<tr id="doc-row-{doc_id}"><td colspan="6" '
                    'style="color:#92400e;background:#fffbeb;padding:8px;">'
                    f"{msg}</td></tr>"
                ),
                headers={"HX-Refresh": "true"},
            )
        return RedirectResponse(url="/unclassified", status_code=302)

    if request.headers.get("HX-Request") == "true":
        msg = f"Reclassified document #{doc_id} as {new_doc_type.value}"
        html = (
            f'<tr id="doc-row-{doc_id}">'
            f'<td colspan="6" style="color: #166534; background: #dcfce7; padding: 8px;">'
            f"{msg}</td></tr>"
        )
        return HTMLResponse(content=html)
    return RedirectResponse(url="/unclassified", status_code=302)
