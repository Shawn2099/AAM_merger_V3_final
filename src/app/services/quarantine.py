"""Quarantine — FR-13.5-13.9 copy+delete keeps files + audit, manual isolated (FR-14.11-14.13)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import AuditAction, AuditLog, Document, LineItem, POSet, POSetStatus
from app.models.base import Base


def quarantine_copy(po_set, cfg) -> Path:
    """Copy every document tied to po_no into quarantine folder (FR-13.3).

    Uses shutil.copy (not move) so stored_path and quarantine copies both remain.
    Returns quarantine folder Path.
    Accepts POSet object or po_set_id int.
    """
    # handle int id: fetch POSet
    if isinstance(po_set, int):
        eng = get_engine(cfg)
        Base.metadata.create_all(eng)
        with Session(eng) as s:
            ps = s.get(POSet, po_set)
            if ps is None:
                raise ValueError(f"POSet {po_set} not found")
            s.refresh(ps, attribute_names=["documents"])
            # copy within session context while documents are loaded
            q = Path(cfg.paths.quarantine_folder) / ps.po_no_normalized
            q.mkdir(parents=True, exist_ok=True)
            docs = list(ps.documents or [])
            for doc in docs:
                src = Path(doc.stored_path)
                if not src.exists():
                    continue
                dst = q / src.name
                shutil.copy(str(src), str(dst))
            return q

    q = Path(cfg.paths.quarantine_folder) / po_set.po_no_normalized
    q.mkdir(parents=True, exist_ok=True)
    for doc in po_set.documents or []:
        src = Path(doc.stored_path)
        if not src.exists():
            continue
        dst = q / src.name
        shutil.copy(str(src), str(dst))
    return q


def delete_quarantined(po_set_id: int, cfg) -> AuditLog:
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
        # delete documents scoped to this POSet
        s.query(Document).filter_by(po_set_id=po_set_id).delete(synchronize_session=False)
        # delete po_set row itself
        s.delete(ps)
        s.flush()
        # audit: po_set_id=None since parent row is deleted (FK ON would block reference)
        detail = json.dumps({"po_no_normalized": po_no, "document_count": len(doc_ids)})
        audit = AuditLog(
            po_set_id=None,
            action=AuditAction.quarantine_delete,
            detail=detail,
            source="system",
        )
        s.add(audit)
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
