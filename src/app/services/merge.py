"""Merge — pypdf SI→DN→PO→(AWB→Customs), filename invoice_no, immutable FR-14.1-14.7."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import AuditAction, AuditLog, DocType, POSet, POSetStatus
from app.models.base import Base


def _doc_type_val(doc) -> str:
    dt = doc.doc_type
    try:
        return dt.value if hasattr(dt, "value") else str(dt)
    except Exception:
        return str(dt)


def _invoice_name(po_set: POSet) -> str | None:
    """FR-14.5: filename is Invoice/SI number, no fallback beyond guarantee."""
    docs = po_set.documents or []
    # prefer SI's si_no/invoice_no
    for d in docs:
        if _doc_type_val(d) == DocType.SI.value:
            si_no = getattr(d, "si_no", None)
            if si_no:
                return si_no  # type: ignore[return-value]
            inv = getattr(d, "invoice_no", None)
            if inv:
                return inv  # type: ignore[return-value]
    # fallback any doc's invoice_no/si_no
    for d in docs:
        inv2 = getattr(d, "invoice_no", None)
        if inv2:
            return inv2  # type: ignore[return-value]
        si2 = getattr(d, "si_no", None)
        if si2:
            return si2  # type: ignore[return-value]
    return None


def _ordered_docs(po_set: POSet) -> list:
    docs = list(po_set.documents or [])
    si = [d for d in docs if _doc_type_val(d) == DocType.SI.value]
    dn = [d for d in docs if _doc_type_val(d) == DocType.DN.value]
    po = [d for d in docs if _doc_type_val(d) == DocType.PO.value]
    # COMBINED treated as self-contained bundle — append after PO (before customs) so
    # if only COMBINED exists it still merges; if both COMBINED + separate exist,
    # first-completed-wins is enforced by immutable status check above.
    combined = [d for d in docs if _doc_type_val(d) == DocType.COMBINED.value]
    shipping = [d for d in docs if _doc_type_val(d) == DocType.SHIPPING.value]
    customs = [d for d in docs if _doc_type_val(d) == DocType.CUSTOMS.value]
    # Order: SI→DN→PO→(SHIPPING→CUSTOMS); COMBINED after PO before shipping
    return si + dn + po + combined + shipping + customs


def _is_blocked(po_set: POSet) -> bool:
    if not po_set.has_customs_toggle:
        return False
    vals = {_doc_type_val(d) for d in (po_set.documents or [])}
    return not (DocType.CUSTOMS.value in vals and DocType.SHIPPING.value in vals)


def _write_merged(ordered: list, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for doc in ordered:
        p = Path(doc.stored_path)
        if not p.exists():
            continue
        reader = PdfReader(str(p))
        for pg in reader.pages:
            writer.add_page(pg)
    # pypdf requires at least one page; if empty, write empty PDF with no pages -> still create file
    writer.write(str(out))
    return out


def merge_po_set(po_set_id: int, cfg) -> Path | None:
    """Auto-merge only when reconciled (FR-14.1). Returns None if not eligible.

    Order: SI→DN→PO→(SHIPPING→CUSTOMS) (FR-14.3). Filename = invoice_no (FR-14.5).
    Immutable once merged (FR-14.6/14.7): first-completed wins.
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise ValueError(f"POSet {po_set_id} not found")
        # FR-14.6/14.7 immutable
        if ps.status == POSetStatus.merged:
            if ps.merged_output_path is not None:
                return Path(ps.merged_output_path)
            return None
        # FR-14.1: must not be mismatched/quarantined/blocked_customs
        blocked = (
            POSetStatus.mismatched,
            POSetStatus.quarantined,
            POSetStatus.blocked_customs,
        )
        if ps.status in blocked:
            return None
        if _is_blocked(ps):
            return None

        ordered = _ordered_docs(ps)
        if not ordered:
            return None

        invoice = _invoice_name(ps)
        if not invoice:
            # SPEC says SI presence guaranteed when reconciled; if missing, cannot name file -> None
            return None
        safe = "".join(c for c in str(invoice) if c.isalnum() or c in ("-", "_", "."))
        if not safe:
            safe = str(invoice)
        out = Path(cfg.paths.output_folder) / f"{safe}.pdf"

        # Merge
        _write_merged(ordered, out)

        ps.merged_output_path = str(out)
        ps.merged_at = datetime.now(UTC)
        ps.status = POSetStatus.merged
        s.commit()
        s.refresh(ps)
        # ty: ps.merged_output_path set just above, non-None
        assert ps.merged_output_path is not None
        return Path(ps.merged_output_path)


def force_merge(po_set_id: int, cfg) -> Path:
    """Force merge unconditional — bypasses matching/customs gates (FR-14.8-14.10).

    Still immutable if already merged (FR-14.6): returns existing.
    Writes AuditLog force_merge with customs count.
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise ValueError(f"POSet {po_set_id} not found")

        if ps.status == POSetStatus.merged and ps.merged_output_path is not None:
            return Path(ps.merged_output_path)

        ordered = _ordered_docs(ps)
        # also include COMMERCIAL_INVOICE optionally at end if no other docs provide content
        # but spec says merge with whatever CUSTOMS/SHIPPING exist (0,1,2) — so just use ordered
        # If ordered empty (no recognizable docs), try all docs as fallback
        if not ordered:
            ordered = list(ps.documents or [])

        if not ordered:
            # still create empty? better raise — but spec says merge with whatever exists
            # create empty placeholder out
            invoice = _invoice_name(ps) or ps.po_no_normalized
            safe = "".join(c for c in str(invoice) if c.isalnum() or c in ("-", "_", "."))
            if not safe:
                safe = ps.po_no_normalized
            out = Path(cfg.paths.output_folder) / f"{safe}.pdf"
            out.parent.mkdir(parents=True, exist_ok=True)
            PdfWriter().write(str(out))
            ps.merged_output_path = str(out)
            ps.merged_at = datetime.now(UTC)
            ps.status = POSetStatus.merged
            # audit even for empty
            customs_count = sum(
                1
                for d in (ps.documents or [])
                if _doc_type_val(d) in (DocType.CUSTOMS.value, DocType.SHIPPING.value)
            )
            detail = f'{{"customs_doc_count": {customs_count}}}'
            s.add(
                AuditLog(
                    po_set_id=ps.id,
                    action=AuditAction.force_merge,
                    detail=detail,
                    source="system",
                )
            )
            s.commit()
            s.refresh(ps)
            assert ps.merged_output_path is not None
            return Path(ps.merged_output_path)

        invoice = _invoice_name(ps) or ps.po_no_normalized
        safe = "".join(c for c in str(invoice) if c.isalnum() or c in ("-", "_", "."))
        if not safe:
            safe = str(invoice)
        out = Path(cfg.paths.output_folder) / f"{safe}.pdf"
        _write_merged(ordered, out)

        ps.merged_output_path = str(out)
        ps.merged_at = datetime.now(UTC)
        ps.status = POSetStatus.merged

        # FR-14.10 audit log with customs count
        customs_count = sum(
            1
            for d in (ps.documents or [])
            if _doc_type_val(d) in (DocType.CUSTOMS.value, DocType.SHIPPING.value)
        )
        detail = f'{{"customs_doc_count": {customs_count}}}'
        s.add(
            AuditLog(
                po_set_id=ps.id,
                action=AuditAction.force_merge,
                detail=detail,
                source="system",
            )
        )
        s.commit()
        s.refresh(ps)
        assert ps.merged_output_path is not None
        return Path(ps.merged_output_path)
