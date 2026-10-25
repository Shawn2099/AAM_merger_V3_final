"""Customs gate — FR-12.1-12.4."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import AuditAction, AuditLog, DocType, POSet, POSetStatus
from app.models.base import Base


def is_blocked(po_set: POSet) -> bool:
    """Return True if customs toggle is on and CUSTOMS+SHIPPING not both present.

    FR-12.2: requires exactly CUSTOMS and SHIPPING (2 docs) and nothing else.
    """
    if not po_set.has_customs_toggle:
        return False
    # collect doc_types; handle both Enum and str (DocType(str, Enum) compares equal)
    doc_types = set()
    for d in po_set.documents or []:
        dt = d.doc_type
        # normalize to string value for comparison simplicity
        try:
            val = dt.value if hasattr(dt, "value") else str(dt)
        except Exception:
            val = str(dt)
        doc_types.add(val)
    has_customs = DocType.CUSTOMS.value in doc_types
    has_shipping = DocType.SHIPPING.value in doc_types
    return not (has_customs and has_shipping)


class CustomsToggleRefused(Exception):
    """Turning the customs requirement OFF was refused: customs files are still attached."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def toggle_customs(po_set_id: int, cfg) -> POSet:
    """Flip has_customs_toggle without hiding the set's real verdict.

    The customs requirement is a gate, not a reconciliation result. A
    quarantined, mismatched, or still-missing-document set keeps its status:
    writing `blocked_customs` over it hid the quarantine from the quarantine
    worklist and put an "Awaiting Customs" header over a quantity failure.
    This function owns only its own state: clearing `blocked_customs` when
    the requirement is switched off, and healing the historical inconsistent
    case where `blocked_customs` is set with the gate off and both documents
    present. Every other transition belongs to `reconcile_po_set`.

    A merged set may be toggled ON. Its packet was written before the
    customs requirement existed; the caller then rebuilds the packet once
    both documents are attached. The status stays `merged`.
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise ValueError(f"POSet {po_set_id} not found")
        if ps.archived_at is not None:
            raise ValueError(f"POSet {po_set_id} is archived and read-only")
        # Turning the requirement OFF on an UNMERGED set is refused while CUSTOMS or
        # SHIPPING files are attached: remove them first (decision 2026-10-25). ON is
        # always allowed. A merged set keeps its switch rule (D5/D18).
        if ps.has_customs_toggle and ps.status != POSetStatus.merged:
            attached = [
                d.original_filename
                for d in ps.documents or []
                if d.archived_at is None and d.doc_type in (DocType.CUSTOMS, DocType.SHIPPING)
            ]
            if attached:
                raise CustomsToggleRefused(
                    "Customs is required for this set, and these files are attached: "
                    f"{', '.join(attached)}. Delete them first, then turn the requirement off."
                )
        # flip
        ps.has_customs_toggle = not ps.has_customs_toggle

        # update customs_doc_count based on distinct required types attached (0, 1, or 2)
        types_present = set()
        for d in ps.documents or []:
            try:
                val = d.doc_type.value if hasattr(d.doc_type, "value") else str(d.doc_type)
            except Exception:
                val = str(d.doc_type)
            if val in (DocType.CUSTOMS.value, DocType.SHIPPING.value):
                types_present.add(val)
        ps.customs_doc_count = len(types_present)

        # With the requirement off nothing is owed to the packet.
        if not ps.has_customs_toggle:
            ps.customs_append_pending = False

        # Toggling the gate OFF clears only the state this gate owns. A
        # quarantine or mismatch is a real verdict and stays put.
        if not ps.has_customs_toggle and ps.status == POSetStatus.blocked_customs:
            ps.status = POSetStatus.pending
            ps.reconcile_reason = "Customs requirement removed; re-evaluate this set to merge."
        # Heal the historical inconsistency: `blocked_customs` with the toggle
        # OFF but both documents present. Toggling it ON must not leave the set
        # stuck in a state the gate no longer explains.
        if (
            ps.has_customs_toggle
            and ps.status == POSetStatus.blocked_customs
            and not is_blocked(ps)
        ):
            ps.status = POSetStatus.pending

        # The switch decides whether the packet must contain customs pages, so each
        # flip is recorded in the same commit as the flip.
        s.add(
            AuditLog(
                po_set_id=ps.id,
                action=AuditAction.manual_status_change,
                detail=json.dumps(
                    {
                        "note": f"Customs check switched {'ON' if ps.has_customs_toggle else 'OFF'}",
                        "customs_check": ps.has_customs_toggle,
                    }
                ),
                source="operator",
            )
        )
        s.commit()
        s.refresh(ps)
        # ensure documents are loaded for caller's is_blocked check
        # expire and reload relationship
        s.refresh(ps, attribute_names=["documents"])
        return ps
