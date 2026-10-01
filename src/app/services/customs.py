"""Customs gate — FR-12.1-12.4."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import DocType, POSet, POSetStatus
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


def toggle_customs(po_set_id: int, cfg) -> POSet:
    """Flip has_customs_toggle regardless of current status (FR-12.1) and update status.

    - Flips has_customs_toggle.
    - If now True and NOT yet satisfied (is_blocked) → status = blocked_customs (FR-12.2).
    - If now True and already satisfied (both CUSTOMS + SHIPPING present, is_blocked is
      False) → status is unchanged. Forcing blocked_customs would immediately unblock on
      the next reconcile pass, so the net effect is always a no-op — leave it alone.
    - If now False and status was blocked_customs → status = pending (gate lifted).
    - Also maintains customs_doc_count (count of CUSTOMS + SHIPPING docs attached).
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise ValueError(f"POSet {po_set_id} not found")
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

        if ps.has_customs_toggle:
            # FR-12.1: if blocked, force into blocked_customs; if gate already satisfied, ensure not blocked
            if is_blocked(ps):
                ps.status = POSetStatus.blocked_customs
            elif ps.status == POSetStatus.blocked_customs:
                ps.status = POSetStatus.pending
        else:
            # toggled OFF -> clear blocked_customs if it was set
            if ps.status == POSetStatus.blocked_customs:
                ps.status = POSetStatus.pending

        s.commit()
        s.refresh(ps)
        # ensure documents are loaded for caller's is_blocked check
        # expire and reload relationship
        s.refresh(ps, attribute_names=["documents"])
        return ps
