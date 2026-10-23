from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import AuditAction, AuditLog, DocType, Document, POSet, POSetStatus
from app.models.base import Base

logger = logging.getLogger(__name__)


def _doc_type_val(doc) -> str:
    dt = doc.doc_type
    try:
        return dt.value if hasattr(dt, "value") else str(dt)
    except Exception:
        return str(dt)


def _si_numbers(po_set: POSet) -> list[str]:
    """Invoice numbers from SI documents, in deterministic document order.

    A set may hold more than one SI (n SI is part of the contract). Iterating
    the relationship without an order made the packet name depend on whatever
    row SQLite returned first; sorting by document id makes it reproducible,
    and the caller flags when the numbers disagree instead of hiding it.
    """
    numbers: list[str] = []
    for d in sorted(po_set.documents or [], key=lambda row: getattr(row, "id", 0) or 0):
        if _doc_type_val(d) != DocType.SI.value:
            continue
        n = getattr(d, "si_no", None) or getattr(d, "invoice_no", None)
        if n and n not in numbers:
            numbers.append(n)
    return numbers


def _si_number(po_set: POSet) -> str | None:
    """The packet's invoice number, read from the SI document.

    Strictly the SI document — a number printed on a DN or a PO is never used
    to name the packet, because a packet named from the wrong document is
    worse than an unnamed one.
    """
    numbers = _si_numbers(po_set)
    return numbers[0] if numbers else None


def _any_invoice_number(po_set: POSet) -> str | None:
    """Any document's invoice/SI number. Force Merge only.

    Force Merge is the operator's explicit override; it must still produce a
    file when the SI number is missing, so it may look wider than the auto
    path does. The auto path uses `_si_number` and nothing else.
    """
    for d in sorted(po_set.documents or [], key=lambda row: getattr(row, "id", 0) or 0):
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
    # Deterministic within a type: SQLite does not promise relationship order,
    # and a set with two SI documents must rebuild to the same page sequence.
    for members in groups.values():
        members.sort(key=lambda row: getattr(row, "id", 0) or 0)
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


class MergeSourceError(RuntimeError):
    """A source PDF is missing or unreadable.

    A named, recoverable failure: the set must quarantine and tell the operator
    which file to look at. It must never escape the reconcile as a raw pypdf
    exception, which leaves the set silently pending and retries forever.
    """


class MergeEmptyError(RuntimeError):
    """There is no page or line-item evidence to write a deliverable packet.

    A packet with no pages (or no reconciled line behind it) must never be
    delivered; the set quarantines instead. `reason_code` distinguishes the two
    so the dashboard does not tell an operator "no line items" when the real
    fault is a source PDF with no readable pages.
    """

    def __init__(self, message: str, reason_code: str = "no_line_item_evidence") -> None:
        super().__init__(message)
        self.reason_code = reason_code


class MergeStaleError(RuntimeError):
    """The set's documents changed after reconcile read them.

    The packet would be built from a different document list than the one the
    quantities were proved against, so nothing is written. The caller leaves the
    set pending and the next reconcile re-reads it.
    """


def _output_lock(out: Path) -> FileLock:
    """Serialise writers for one output name across processes.

    The per-PO lock stops two actions on the same set. It does not stop two
    different sets that resolve to the same filename (the duplicate-PO case).
    The lock is taken before the existence check so the check and the write are
    one critical section.

    The lock file lives in `output/.locks/`, not beside the packet: filelock
    never deletes it, so `output/` would otherwise fill with `*.pdf.lock` and
    the delivery folder would not contain only PDFs.
    """
    lock_dir = out.parent.parent / ".aam-merger-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    # Outside the output folder: that folder must contain only delivered
    # packets. The parent name keeps two output folders with the same packet
    # name from sharing a lock.
    return FileLock(str(lock_dir / f"{out.parent.name}__{out.name}.lock"), timeout=60)


_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _safe_stem(value: str) -> str:
    stem = "".join(c for c in str(value) if c.isalnum() or c in ("-", "_", "."))
    # A packet called CON.pdf targets the console device on Windows, not a
    # file. Prefix rather than refuse: the operator still gets a deliverable.
    if stem.upper().split(".", 1)[0] in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    return stem[:100]


def _packet_name(po_set: POSet) -> tuple[str | None, bool, list[str]]:
    """Filename stem, whether the invoice number was missing, and all SI numbers.

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
    numbers = _si_numbers(po_set)
    if numbers:
        stem = _safe_stem(numbers[0])
        if stem:
            return stem, False, numbers
    po_no = (po_set.po_no_normalized or "").strip()
    stem = _safe_stem(po_no) if po_no else ""
    if stem:
        return stem, True, numbers
    return None, True, numbers


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
            f"output filename '{out.name}' already exists; refusing to overwrite "
            f"(PO Set {po_set_id}). If no delivered packet owns it, inspect/remove "
            "the file and re-run."
        )
    return out


def _write_merged(ordered: list, out: Path, allow_missing: bool = False) -> int:
    """Write the packet atomically and return its page count.

    The writer targets a unique temporary file in the destination folder, then
    `os.replace`s it. A crash, disk-full or unreadable source therefore never
    leaves a truncated PDF at the delivered filename. Any pypdf failure is
    wrapped in `MergeSourceError` so the caller can quarantine with a reason
    instead of letting a raw exception abort the reconcile.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.{uuid.uuid4().hex}.tmp")
    writer = PdfWriter()
    missing: list[str] = []
    try:
        for doc in ordered:
            raw_path = getattr(doc, "stored_path", None)
            p = Path(raw_path) if raw_path else None
            # An empty stored_path becomes Path("."), which "exists" and then
            # makes PdfReader open the cwd. A malformed row must be treated as a
            # missing file, not crash the merge and abort the sweep.
            if p is None or not p.is_file():
                doc_id = getattr(doc, "id", "?")
                doc_name = getattr(doc, "original_filename", "unknown")
                missing.append(f"Doc {doc_id} ({doc_name}): {p}")
                continue
            try:
                reader = PdfReader(str(p))
                for pg in reader.pages:
                    writer.add_page(pg)
            except Exception as e:
                doc_id = getattr(doc, "id", "?")
                doc_name = getattr(doc, "original_filename", "unknown")
                if allow_missing:
                    missing.append(f"Doc {doc_id} ({doc_name}): unreadable ({e})")
                    continue
                raise MergeSourceError(f"Doc {doc_id} ({doc_name}) is unreadable: {e}") from e

        if missing and not allow_missing:
            raise MergeSourceError(f"Missing stored PDF(s) during merge: {'; '.join(missing)}")

        writer.write(str(tmp))
        pages = len(writer.pages)
        if pages == 0:
            raise MergeEmptyError(
                "Refusing to write a 0-page packet: no readable pages in the attached documents",
                reason_code="packet_source_unreadable",
            )
        os.replace(tmp, out)
        return pages
    finally:
        tmp.unlink(missing_ok=True)


def _delete_inputs_for_merged(ps: POSet, cfg) -> None:
    """Remove the input copies once the packet is written.

    The nightly flow does this, but a manual/force merge did not: the files
    stayed in input until the next sweep, were re-hashed and de-duplicated on
    every run, and counted as new work. A merged set's input copy is done.
    """
    try:
        from app.services.ingestion import delete_input_files

        delete_input_files(ps, Path(cfg.paths.input_folder))
    except Exception:
        logger.warning("Could not clear input copies for PO Set %s", ps.id, exc_info=True)


def merge_po_set(
    po_set_id: int,
    cfg,
    info: dict | None = None,
    expected_doc_ids: frozenset[int] | None = None,
) -> Path | None:
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

    `expected_doc_ids` is the document snapshot the caller reconciled. If the
    set's live documents differ just before the write, `MergeStaleError` is
    raised and nothing is written.
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise ValueError(f"POSet {po_set_id} not found")
        if ps.archived_at is not None:
            return None
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
        # More than one PO document is a human decision. Reconcile returns
        # pending; this guard keeps a direct merge call (or a stale status)
        # from bypassing that hold and summing two baselines.
        if sum(1 for d in (ps.documents or []) if _doc_type_val(d) == DocType.PO.value) > 1:
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
            raise MergeEmptyError(
                f"PO Set {po_set_id} has documents but no line-item evidence — refusing to merge"
            )

        # Named from the SI document's own number, falling back to the PO
        # number when no invoice number was extracted at all. The fallback is
        # reported through `info` so the reviewer sees the gap.
        stem, invoice_missing, invoice_numbers = _packet_name(ps)
        if not stem:
            raise MergeNamingError(
                f"PO Set {po_set_id} cannot be named: no invoice number and no PO number"
            )
        out = _resolve_output_path(
            stem, ps.id, Path(cfg.paths.output_folder), ps.merged_output_path
        )
        # The per-PO lock does not stop a different set that resolves to the
        # same name. Re-check and write inside one cross-process critical
        # section, so the existence check and the packet write are atomic.
        with _output_lock(out):
            out = _resolve_output_path(
                stem, ps.id, Path(cfg.paths.output_folder), ps.merged_output_path
            )
            if expected_doc_ids is not None:
                live = frozenset(
                    s.scalars(
                        select(Document.id).where(
                            Document.po_set_id == po_set_id, Document.archived_at.is_(None)
                        )
                    )
                )
                if live != expected_doc_ids:
                    raise MergeStaleError(
                        f"PO Set {po_set_id} documents changed during merge: "
                        f"expected {sorted(expected_doc_ids)}, found {sorted(live)}"
                    )
            _write_merged(ordered, out, allow_missing=False)
            if info is not None:
                info["output_name"] = out.name
                info["invoice_no_missing"] = invoice_missing
                info["invoice_numbers"] = invoice_numbers
                info["multiple_invoice_numbers"] = len(invoice_numbers) > 1

            ps.merged_output_path = str(out)
            ps.merged_at = datetime.now(UTC)
            ps.status = POSetStatus.merged
            # A set that was quarantined or mismatched before an operator's
            # Re-Extract/Re-Match can reconcile and merge. Leaving the old
            # sentence on the row makes the dashboard show a delivered packet
            # as "could not be verified" forever; the override path already
            # clears it. `_persist_reason` may still append a naming note.
            ps.reconcile_reason = "Fully reconciled — packet merged"
            try:
                s.commit()
            except Exception:
                # The packet is not referenced by any DB row. Remove it rather
                # than leave an unowned file that the next run would call a
                # naming collision.
                out.unlink(missing_ok=True)
                raise
        s.refresh(ps)
        # ty: ps.merged_output_path set just above, non-None
        assert ps.merged_output_path is not None
        _delete_inputs_for_merged(ps, cfg)
        return Path(ps.merged_output_path)


def force_merge(
    po_set_id: int, cfg, justification: str | None = None, source: str = "system"
) -> Path:
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
        if ps.archived_at is not None:
            raise ValueError(f"POSet {po_set_id} is archived and cannot be merged")

        from app.services.quarantine import validate_justification

        note = validate_justification(justification)

        if ps.status == POSetStatus.merged and ps.merged_output_path is not None:
            out = Path(ps.merged_output_path)
            if not out.is_file():
                # Never report a delivered packet that is not there. The
                # documents are all attached, so the operator can rebuild by
                # restoring the file or re-running the merge after clearing the
                # row's merged state.
                raise MergeSourceError(
                    f"PO Set {po_set_id} is marked merged but its packet is missing: {out}"
                )
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
            _delete_inputs_for_merged(ps, cfg)
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
        with _output_lock(out):
            out = _resolve_output_path(
                safe, ps.id, Path(cfg.paths.output_folder), ps.merged_output_path
            )
            _write_merged(ordered, out, allow_missing=False)

            ps.merged_output_path = str(out)
            ps.merged_at = datetime.now(UTC)
            ps.status = POSetStatus.merged
            # The set was force-merged from a state that may have carried a
            # mismatch or quarantine sentence. Leaving that sentence on a
            # `merged` row makes the dashboard contradict itself forever.
            ps.reconcile_reason = (
                "Force-merged by operator — reconciliation and customs gates were bypassed"
            )

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
            try:
                s.commit()
            except Exception:
                out.unlink(missing_ok=True)
                raise
        s.refresh(ps)
        assert ps.merged_output_path is not None
        _delete_inputs_for_merged(ps, cfg)
        return Path(ps.merged_output_path)


def append_customs_to_merged_packet(po_set_id: int, cfg, source: str = "operator") -> Path:
    """Rebuild an already-merged packet so it contains CUSTOMS and SHIPPING.

    The set was merged before the customs requirement was switched on. The
    operator uploaded both documents afterwards. The packet is regenerated
    from every attached document in the legal order (SHIPPING and CUSTOMS are
    last), written to a temporary file and atomically swapped in, so a failed
    rebuild never truncates the delivered packet.

    The set stays `merged` and `merged_at` is untouched: the first merge is
    still the moment the packet became deliverable. The append is recorded as
    its own audit row.

    Idempotent: rebuilding from the same documents produces the same packet.
    Raises ValueError when the preconditions are not met, so a caller cannot
    silently append a customs document that is not there.
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise ValueError(f"POSet {po_set_id} not found")
        if ps.archived_at is not None:
            raise ValueError(f"POSet {po_set_id} is archived and cannot be modified")
        if ps.status != POSetStatus.merged:
            raise ValueError(f"POSet {po_set_id} is not merged (status={ps.status})")
        if not ps.merged_output_path:
            raise ValueError(f"POSet {po_set_id} has no merged packet path")
        if not ps.has_customs_toggle:
            raise ValueError(f"POSet {po_set_id} does not require customs documents")

        docs = list(ps.documents or [])
        present = {_doc_type_val(d) for d in docs}
        if not {DocType.CUSTOMS.value, DocType.SHIPPING.value} <= present:
            raise ValueError(
                f"POSet {po_set_id} needs both a CUSTOMS and a SHIPPING document "
                "before the packet can be rebuilt"
            )

        ordered = _ordered_docs(ps, cfg)
        if not ordered:
            raise ValueError(f"POSet {po_set_id} has no documents to merge")

        # Verify every page source before touching the delivered file.
        for d in ordered:
            raw = getattr(d, "stored_path", None)
            p = Path(raw) if raw else None
            if p is None or not p.is_file():
                raise FileNotFoundError(
                    f"Doc {getattr(d, 'id', '?')} ({getattr(d, 'original_filename', '?')}) "
                    f"is missing: {p}"
                )

        out = Path(ps.merged_output_path)
        # `_write_merged` writes a unique temp and atomically replaces `out`;
        # holding the output-name lock keeps a concurrent rebuild or first
        # merge from swapping a half-written packet.
        with _output_lock(out):
            _write_merged(ordered, out, allow_missing=False)

        detail = json.dumps(
            {
                "action": "customs_append",
                "output_name": out.name,
                "appended": sorted(
                    d.original_filename or "?"
                    for d in docs
                    if _doc_type_val(d) in (DocType.CUSTOMS.value, DocType.SHIPPING.value)
                ),
            }
        )
        s.add(
            AuditLog(
                po_set_id=ps.id,
                action=AuditAction.customs_append,
                detail=detail,
                source=source,
            )
        )
        # The debt is paid in the same commit as the audit row.
        ps.customs_append_pending = False
        s.commit()
        logger.info(
            "Rebuilt merged packet for PO Set %s with customs/shipping documents: %s",
            po_set_id,
            out.name,
        )
        return out


def append_customs_if_complete(po_set_id: int, cfg, source: str = "operator") -> Path | None:
    """Rebuild a merged packet only when the append actually applies.

    Returns None when the set is not merged, does not require customs, or does
    not yet have both documents. Lets the placement paths (upload, toggle,
    holding-area reclassify) call one function without duplicating the guard.
    Real failures (missing stored file, unwritable packet) still raise, after
    the set is marked `customs_append_pending` so the next sync retries it.
    """
    try:
        return append_customs_to_merged_packet(po_set_id, cfg, source=source)
    except ValueError:
        return None
    except Exception:
        set_customs_append_pending(po_set_id, cfg, True)
        raise


def set_customs_append_pending(po_set_id: int, cfg, pending: bool) -> None:
    """Persist whether a merged set still owes its packet a customs rebuild.

    Best effort on a missing or archived set; never raises for those.
    """
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None or ps.archived_at is not None:
            return
        if ps.customs_append_pending != pending:
            ps.customs_append_pending = pending
            s.commit()
