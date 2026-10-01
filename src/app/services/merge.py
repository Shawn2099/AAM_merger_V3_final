from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import AuditAction, AuditLog, DocType, POSet, POSetStatus
from app.models.base import Base

logger = logging.getLogger(__name__)


def _doc_type_val(doc) -> str:
    dt = doc.doc_type
    try:
        return dt.value if hasattr(dt, "value") else str(dt)
    except Exception:
        return str(dt)


def _si_number(po_set: POSet) -> str | None:
    """The packet's invoice number, read from the SI document.

    Strictly the SI document — a number printed on a DN or a PO is never used
    to name the packet, because a packet named from the wrong document is
    worse than an unnamed one.
    """
    docs = list(po_set.documents or [])
    for d in docs:
        if _doc_type_val(d) != DocType.SI.value:
            continue
        n = getattr(d, "si_no", None) or getattr(d, "invoice_no", None)
        if n:
            return n
    return None


def _any_invoice_number(po_set: POSet) -> str | None:
    """Any document's invoice/SI number. Force Merge only.

    Force Merge is the operator's explicit override; it must still produce a
    file when the SI number is missing, so it may look wider than the auto
    path does. The auto path uses `_si_number` and nothing else.
    """
    for d in po_set.documents or []:
        n = getattr(d, "si_no", None) or getattr(d, "invoice_no", None)
        if n:
            return n
    return None


# Default packet order — kept as code fallback so callers without cfg behave
# exactly as configured. config.yaml merge.legal_order wins.
DEFAULT_LEGAL_ORDER = ["SI", "DN", "PO", "SHIPPING", "CUSTOMS"]


def _ordered_docs(po_set: POSet, cfg=None) -> list:
    docs = list(po_set.documents or [])
    groups: dict[str, list] = {}
    for d in docs:
        groups.setdefault(_doc_type_val(d), []).append(d)
    # Order: config merge.legal_order (editable, DECISIONS_LOG §8);
    # types absent from the order append in first-seen order so new manual
    # types (e.g. AWB) never silently vanish from a packet.
    order = list(DEFAULT_LEGAL_ORDER)
    if cfg is not None:
        with_ = getattr(cfg, "merge", None)
        if with_ is not None and getattr(with_, "legal_order", None):
            order = list(with_.legal_order)
    ordered: list = []
    for t in order:
        ordered.extend(groups.pop(t, []))
    seen: set[str] = set()
    for d in docs:
        t = _doc_type_val(d)
        if t in groups and t not in seen:
            seen.add(t)
            ordered.extend(groups.pop(t, []))
    ordered.extend([d for rest in groups.values() for d in rest])
    return ordered


class MergeNamingError(RuntimeError):
    """The merged packet cannot be named unambiguously.

    Raised instead of guessing, so the caller quarantines the set. Writing a
    clobbered or ambiguous filename would deliver the wrong document to the
    customer, which is the one failure this system must never make.
    """


def _safe_stem(value: str) -> str:
    return "".join(c for c in str(value) if c.isalnum() or c in ("-", "_", "."))


def _packet_name(po_set: POSet) -> tuple[str | None, bool]:
    """Filename stem, and whether the invoice number was missing.

    Preferred: the SI document's own number, and only that. A number printed on
    a DN or a PO is never used, because a packet named from the wrong document
    is worse than an unnamed one.

    Fallback: the PO number, when no invoice number was extracted at all. The
    quantities are what reconciliation proves, so a missing label must not veto
    an otherwise correct packet. The caller is told via the second element so
    the gap stays visible on the dashboard instead of passing silently.

    A PO number alone is safe as a filename: only one open set exists per PO key
    at a time, and a genuine duplicate still raises in `_resolve_output_path`.
    """
    si_no = _si_number(po_set)
    if si_no:
        stem = _safe_stem(si_no)
        if stem:
            return stem, False
    po_no = (po_set.po_no_normalized or "").strip()
    stem = _safe_stem(po_no) if po_no else ""
    if stem:
        return stem, True
    return None, True


def _resolve_output_path(
    safe: str, po_set_id: int, output_folder: Path, current_path: str | None = None
) -> Path:
    """Resolve the output path, refusing to overwrite an existing packet.

    Re-merging this same set onto its own existing file is fine. Any other
    collision means two different sets would share a filename, so we raise
    rather than disambiguate silently. The caller quarantines the set.
    """
    out = output_folder / f"{safe}.pdf"
    if current_path and Path(current_path).resolve() == out.resolve():
        return out
    if out.exists():
        raise MergeNamingError(
            f"output filename '{out.name}' already exists and belongs to another PO Set; "
            f"refusing to overwrite (PO Set {po_set_id})"
        )
    return out


def _write_merged(ordered: list, out: Path, allow_missing: bool = False) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    missing: list[str] = []
    for doc in ordered:
        p = Path(doc.stored_path)
        if not p.exists():
            doc_id = getattr(doc, "id", "?")
            doc_name = getattr(doc, "original_filename", "unknown")
            missing.append(f"Doc {doc_id} ({doc_name}): {p}")
            continue
        reader = PdfReader(str(p))
        for pg in reader.pages:
            writer.add_page(pg)

    if missing and not allow_missing:
        raise FileNotFoundError(f"Missing stored PDF(s) during merge: {'; '.join(missing)}")

    # pypdf requires at least one page; if empty, write empty PDF with no pages -> still create file
    writer.write(str(out))
    return out


def merge_po_set(po_set_id: int, cfg, info: dict | None = None) -> Path | None:
    """Auto-merge only when reconciled (FR-14.1). Returns None if not eligible.

    Order comes from `cfg.merge.legal_order` (default SI→DN→PO→
    SHIPPING→CUSTOMS); types absent from that list append in first-seen order.

    Filename is the SI's own number, falling back to the PO number when no
    invoice number was extracted. It is NOT `<invoice_no>_<po_no>` — an
    earlier version of this docstring claimed a two-part name, which the code
    has never produced. See `_packet_name`, which is the single place the
    naming rule lives.

    Immutable once merged (FR-14.6/14.7): first-completed wins.

    `info`, when given, is populated with naming details for the caller to
    surface (e.g. invoice_no_missing).
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
        # Single source of truth for the customs gate. This used to be a
        # byte-for-byte private copy living in this module, which meant the
        # merge path could silently drift from the reconciliation path's
        # version of the same rule.
        from app.services.customs import is_blocked

        if is_blocked(ps):
            return None

        ordered = _ordered_docs(ps, cfg)
        if not ordered:
            return None

        # W-22: refuse auto-merge with zero line-item evidence — a packet
        # with no numeric reconciliation behind it must never go out.
        # (force_merge bypasses by explicit operator intent.)
        total_lines = sum(len(list(d.line_items or [])) for d in ordered)
        if total_lines == 0:
            logger.warning("Auto-merge refused for PO Set %s: zero line-item evidence", po_set_id)
            return None

        # Named from the SI document's own number, falling back to the PO
        # number when no invoice number was extracted at all. The fallback is
        # reported through `info` so the reviewer sees the gap.
        stem, invoice_missing = _packet_name(ps)
        if not stem:
            raise MergeNamingError(
                f"PO Set {po_set_id} cannot be named: no invoice number and no PO number"
            )
        out = _resolve_output_path(
            stem, ps.id, Path(cfg.paths.output_folder), ps.merged_output_path
        )
        if info is not None:
            info["output_name"] = out.name
            info["invoice_no_missing"] = invoice_missing

        # Merge
        try:
            _write_merged(ordered, out, allow_missing=False)
        except FileNotFoundError as e:
            logger.warning("Auto-merge failed for PO Set %s: %s", po_set_id, e)
            return None

        ps.merged_output_path = str(out)
        ps.merged_at = datetime.now(UTC)
        ps.status = POSetStatus.merged
        s.commit()
        s.refresh(ps)
        # ty: ps.merged_output_path set just above, non-None
        assert ps.merged_output_path is not None
        return Path(ps.merged_output_path)


def force_merge(po_set_id: int, cfg, justification: str | None = None, source: str = "system") -> Path:
    """Force merge unconditional — bypasses the reconciliation and customs gates.

    The operator's explicit override. Still immutable once merged: returns the
    existing packet rather than rewriting it. Every path writes a force_merge
    audit row carrying the customs document count AND the operator's written
    justification.
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise ValueError(f"POSet {po_set_id} not found")

        from app.services.quarantine import validate_justification

        note = validate_justification(justification)

        if ps.status == POSetStatus.merged and ps.merged_output_path is not None:
            # Idempotent no-op returning the existing packet — but the press
            # itself is still audited (PRODUCT §6: every Force Merge writes a
            # row). Intent is what the log records, not just effects.
            out = Path(ps.merged_output_path)
            customs_count = sum(
                1
                for d in (ps.documents or [])
                if _doc_type_val(d) in (DocType.CUSTOMS.value, DocType.SHIPPING.value)
            )
            detail = json.dumps(
                {
                    "customs_doc_count": customs_count,
                    "output_name": out.name,
                    "already_merged": True,
                }
            )
            s.add(
                AuditLog(
                    po_set_id=ps.id,
                    action=AuditAction.force_merge,
                    detail=detail,
                    source=source,
                    justification=note,
                )
            )
            s.commit()
            return out

        ordered = _ordered_docs(ps, cfg)
        if not ordered:
            ordered = list(ps.documents or [])

        # Force Merge may look wider than the auto path for a name (any
        # document's invoice number, then the PO number) because the operator
        # asked for a file regardless. It still refuses a 0-page packet.
        if not ordered:
            raise MergeNamingError(
                f"PO Set {po_set_id} has no documents to merge — nothing to write"
            )
        name_source = _any_invoice_number(ps) or ps.po_no_normalized
        safe = _safe_stem(name_source)
        if not safe:
            raise MergeNamingError(
                f"PO Set {po_set_id} cannot be named: no invoice number and no PO number"
            )
        out = _resolve_output_path(
            safe, ps.id, Path(cfg.paths.output_folder), ps.merged_output_path
        )
        _write_merged(ordered, out, allow_missing=False)

        ps.merged_output_path = str(out)
        ps.merged_at = datetime.now(UTC)
        ps.status = POSetStatus.merged

        customs_count = sum(
            1
            for d in (ps.documents or [])
            if _doc_type_val(d) in (DocType.CUSTOMS.value, DocType.SHIPPING.value)
        )
        detail = json.dumps({"customs_doc_count": customs_count, "output_name": out.name})
        s.add(
            AuditLog(
                po_set_id=ps.id,
                action=AuditAction.force_merge,
                detail=detail,
                source=source,
                justification=note,
            )
        )
        s.commit()
        s.refresh(ps)
        assert ps.merged_output_path is not None
        return Path(ps.merged_output_path)
