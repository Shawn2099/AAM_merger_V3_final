"""Quarantine — FR-13.5-13.9 copy+delete keeps files + audit, manual isolated (FR-14.11-14.13)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import AuditAction, AuditLog, Document, LineItem, POSet, POSetStatus
from app.models.base import Base

# Windows reserved device names. The production host is Win2016, so a PO
# number normalising to one of these would create an unusable folder.
_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_MAX_FOLDER_LEN = 80


def _set_fingerprint(po_set) -> str:
    """A stable, collision-free discriminator for one PO set.

    Derived from the set's document hashes rather than its id: SQLite reuses
    the id of a deleted row, so a new set could otherwise inherit an old set's
    folder and overwrite its QUARANTINE.txt. Re-quarantining the same set gives
    the same fingerprint, so the folder stays stable across runs.
    """
    import hashlib

    hashes = sorted(
        (getattr(d, "sha256_hash", "") or "") for d in (po_set.documents or [])
    )
    joined = "|".join(hashes)
    return hashlib.sha1(joined.encode("utf-8", "replace")).hexdigest()[:8]


def _safe_po_folder(
    po_no: str | None, po_set_id: int | None = None, fingerprint: str | None = None
) -> str:
    """A filesystem-safe folder name for a PO set.

    Keeps the PO number readable: hyphens, underscores and dots survive, which
    matters because this is a folder a person opens and reads. Anything that
    could escape the quarantine root (separators, dots-only names) is stripped,
    and Windows reserved device names are prefixed. In production
    `po_no_normalized` is already alphanumeric, so this is a no-op there and a
    guard for values written directly to the database.

    A blank PO number falls back to UNIDENTIFIED_PO plus a content fingerprint.
    Without one, every unnumbered set would share a folder, pooling unrelated
    PDFs and overwriting each other's QUARANTINE.txt. The id is deliberately
    not used for this: SQLite reuses the id of a deleted row.
    """
    raw = "".join(ch for ch in (po_no or "") if ch.isalnum() or ch in "-_.")
    raw = raw.strip(".-")
    if not raw or set(raw) <= {"."}:
        tag = fingerprint or (f"{po_set_id}" if po_set_id is not None else None)
        return f"UNIDENTIFIED_PO_{tag}"[:_MAX_FOLDER_LEN] if tag else "UNIDENTIFIED_PO"
    if raw.upper() in _RESERVED_NAMES:
        raw = f"_{raw}"
    return raw[:_MAX_FOLDER_LEN]


def _format_qty(value) -> str:
    """Render a scaled-x1000 integer back as a printed quantity."""
    if value is None:
        return "-"
    return f"{int(value) / 1000:g}"


def _write_quarantine_report(
    folder: Path,
    po_no: str | None,
    status: str,
    reason: str | None,
    detail: str | None,
    flags: list[dict] | None,
    po_line_qty: dict | None = None,
) -> None:
    """Make the folder self-describing.

    A quarantined folder is a case a human has to work through. Writing the
    reason and the per-line numbers next to the PDFs means whoever opens the
    folder can see what the engine could not decide, instead of having to
    re-derive it.
    """
    lines: list[str] = [
        "QUARANTINED PO SET",
        "=" * 60,
        f"PO number   : {po_no or '(none)'}",
        f"Status      : {status}",
        f"Reason      : {reason or '(none recorded)'}",
        f"Detected    : {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
    ]
    if detail:
        lines.append(f"Detail      : {detail}")

    # Every PO line, so the reviewer sees the whole set rather than only the
    # rows that failed. Flags then overlay the vendor-side numbers.
    rows: dict[str, dict] = {
        str(k): {"po": v, "dn": None, "si": None} for k, v in (po_line_qty or {}).items()
    }
    for f in flags or []:
        key = str(f.get("line_item_no") or "?")
        row = rows.setdefault(key, {"po": None, "dn": None, "si": None})
        pool = (f.get("pool") or "").upper()
        if f.get("type") == "identification":
            row["note"] = f.get("reason") or "unmatched"
            if pool in ("DN", "SI"):
                row[pool.lower()] = f.get("vendor_quantity")
        elif pool in ("DN", "SI"):
            row[pool.lower()] = f.get("vendor_quantity")

    if rows:
        lines += [
            "",
            "Line-item quantities (PO vs sum of vendor lines)",
            "-" * 60,
            f"{'Line':<12}{'PO':>12}{'DN':>12}{'SI':>12}   Note",
        ]
        for key in sorted(rows, key=lambda k: (len(k), k)):
            r = rows[key]
            lines.append(
                f"{key:<12}{_format_qty(r['po']):>12}{_format_qty(r['dn']):>12}"
                f"{_format_qty(r['si']):>12}   {r.get('note', '')}"
            )
        lines += [
            "",
            "These are the exact numbers the engine compared. Reconciliation",
            "requires PO == sum(DN) and PO == sum(SI) for every line.",
        ]
    else:
        lines += ["", "(no line-item numbers were recorded for this set)"]

    lines += [
        "",
        "-" * 60,
        "To resolve: correct the source documents and re-run, or force-merge",
        "with a justification. Force-merge ships the packet as-is.",
        "",
    ]
    (folder / "QUARANTINE.txt").write_text("\n".join(lines), encoding="utf-8")


def _po_line_quantities(po_set) -> dict[str, int]:
    """Every PO line's quantity, keyed by line number."""
    from app.services.matching import normalize_line_no

    out: dict[str, int] = {}
    for doc in po_set.documents or []:
        if (doc.doc_type.value if hasattr(doc.doc_type, "value") else str(doc.doc_type)) not in (
            "PO",
            "COMBINED",
        ):
            continue
        for li in doc.line_items or []:
            key = normalize_line_no(li.line_item_no)
            if key:
                out[key] = out.get(key, 0) + (li.quantity or 0)
    return out


def quarantine_copy(
    po_set, cfg, reason: str | None = None, detail: str | None = None, flags: list | None = None
) -> Path:
    """Copy every document tied to po_no into quarantine/<PO_NO>/ (FR-13.3).

    Uses shutil.copy (not move) so stored_path and quarantine copies both remain.
    Writes a QUARANTINE.txt summary so the folder explains itself.
    Returns quarantine folder Path.
    Accepts POSet object or po_set_id int.
    """
    if isinstance(po_set, int):
        eng = get_engine(cfg)
        Base.metadata.create_all(eng)
        with Session(eng) as s:
            ps = s.get(POSet, po_set)
            if ps is None:
                raise ValueError(f"POSet {po_set} not found")
            s.refresh(ps, attribute_names=["documents"])
            # copy within session context while documents are loaded
            folder = Path(cfg.paths.quarantine_folder) / _safe_po_folder(
                ps.po_no_normalized, ps.id, _set_fingerprint(ps)
            )
            folder.mkdir(parents=True, exist_ok=True)
            docs = list(ps.documents or [])
            for doc in docs:
                src = Path(doc.stored_path)
                if not src.exists():
                    continue
                dst = folder / src.name
                shutil.copy(str(src), str(dst))
            status = ps.status.value if hasattr(ps.status, "value") else str(ps.status)
            po_no = ps.po_no_normalized
            po_line_qty = _po_line_quantities(ps)
    else:
        folder = (
            Path(cfg.paths.quarantine_folder)
            / _safe_po_folder(po_set.po_no_normalized, po_set.id, _set_fingerprint(po_set))
        )
        folder.mkdir(parents=True, exist_ok=True)
        for doc in po_set.documents or []:
            src = Path(doc.stored_path)
            if not src.exists():
                continue
            dst = folder / src.name
            shutil.copy(str(src), str(dst))
        status = (
            po_set.status.value
            if hasattr(po_set.status, "value")
            else str(po_set.status)
        )
        po_no = po_set.po_no_normalized
        po_line_qty = _po_line_quantities(po_set)

    _write_quarantine_report(folder, po_no, status, reason, detail, flags, po_line_qty)
    return folder


MIN_JUSTIFICATION_CHARS = 20


def validate_justification(text: str | None) -> str | None:
    """Normalize an optional operator justification (v20.5 §audit).

    None/absent stays None so existing callers and the UI keep working. When
    supplied it must be a real sentence-ish note, not a one-word checkbox.
    """
    if text is None:
        return None
    cleaned = str(text).strip()
    if not cleaned:
        return None
    if len(cleaned) < MIN_JUSTIFICATION_CHARS:
        raise ValueError(
            f"Justification must be at least {MIN_JUSTIFICATION_CHARS} characters "
            f"(got {len(cleaned)})"
        )
    return cleaned


def delete_quarantined(po_set_id: int, cfg, justification: str | None = None) -> AuditLog:
    """Delete quarantined POSet DB rows only, keep files, write audit_log (FR-13.6-13.7).

    Removes po_sets + documents + line_items rows scoped to po_set_id.
    Does NOT delete stored_path files nor quarantine folder copies.
    Inserts AuditLog(action=quarantine_delete). Verified status==quarantined.
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise ValueError(f"POSet {po_set_id} not found")
        note = validate_justification(justification)
        # verify quarantined status (handle both enum and string)
        status_val = ps.status.value if hasattr(ps.status, "value") else str(ps.status)
        if status_val != POSetStatus.quarantined.value:
            raise ValueError(f"POSet {po_set_id} is not quarantined (status={status_val})")
        po_no = ps.po_no_normalized
        # collect doc ids for line_items deletion
        docs = s.query(Document).filter_by(po_set_id=po_set_id).all()
        doc_ids = [d.id for d in docs]
        if doc_ids:
            s.query(LineItem).filter(LineItem.document_id.in_(doc_ids)).delete(
                synchronize_session=False
            )
        # audit FIRST with the real po_set_id (FK-valid at insert); the
        # ON DELETE SET NULL below preserves the row with full detail (W-14).
        detail = json.dumps({"po_no_normalized": po_no, "document_count": len(doc_ids)})
        audit = AuditLog(
            po_set_id=po_set_id,
            action=AuditAction.quarantine_delete,
            detail=detail,
            source="system",
            justification=note,
        )
        s.add(audit)
        s.flush()
        # delete documents scoped to this POSet
        s.query(Document).filter_by(po_set_id=po_set_id).delete(synchronize_session=False)
        # delete po_set row itself (SET NULL fires on the audit row)
        s.delete(ps)
        s.commit()
        s.refresh(audit)
        return audit


def manual_merge(
    files: list[Path],
    order: list[int],
    output_path: Path | None = None,
) -> Path:
    """Isolated manual PDF merger — no DB, no po_no association (FR-14.11-14.13).

    Concatenates PDFs in user-specified order via pypdf.
    Output destination and filename are user-selectable (FR-14.12); if output_path is None,
    a temp file is created (isolated from pipeline output_folder).
    Returns Path to merged PDF.
    """
    if not files:
        raise ValueError("No files provided for manual merge")
    if order is None:
        order = list(range(len(files)))
    if len(order) != len(files):
        raise ValueError(f"order length {len(order)} != files length {len(files)}")
    if set(order) != set(range(len(files))):
        raise ValueError(f"order must be permutation of 0..{len(files) - 1}, got {order}")

    if output_path is None:
        fd, tmp = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        output_path = Path(tmp)
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    ordered = [Path(files[i]) for i in order]
    for p in ordered:
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        reader = PdfReader(str(p))
        for pg in reader.pages:
            writer.add_page(pg)
    # handle empty writer (no pages) — still write file
    writer.write(str(output_path))
    return output_path
