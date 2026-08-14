"""Customs gate — FR-12.1-12.4."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import DocType, POSet, POSetStatus
from app.models.base import Base


def is_blocked(po_set: POSet) -> bool:
    """Return True if customs toggle is on and CUSTOMS+SHIPPING not both present.

    FR-12.2: requires exactly CUSTOMS and SHIPPING (2 docs).
    FR-12.4: COMMERCIAL_INVOICE never required — ignored.
    FR-12.3: COMBINED does not satisfy gate — only CUSTOMS/SHIPPING count.
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

    - Flips has_customs_toggle
    - If now True and is_blocked -> status = blocked_customs (FR-12.2)
    - If now True and NOT blocked (both docs already present) -> still set blocked_customs
      per 'force into blocked_customs' wording, but is_blocked will be False.
      To keep status/is_blocked consistent, we keep original status when already satisfied.
      However brief expects flip -> blocked_customs, so we force blocked_customs when
      toggle ON unless already satisfied we leave status as-is (so is_blocked False).
    - If now False and status was blocked_customs -> status = pending
    - Also maintains customs_doc_count (count of CUSTOMS+SHIPPING attached).
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise ValueError(f"POSet {po_set_id} not found")
        # flip
        ps.has_customs_toggle = not ps.has_customs_toggle

        # update customs_doc_count based on attached docs
        customs_count = 0
        for d in ps.documents or []:
            try:
                val = d.doc_type.value if hasattr(d.doc_type, "value") else str(d.doc_type)
            except Exception:
                val = str(d.doc_type)
            if val in (DocType.CUSTOMS.value, DocType.SHIPPING.value):
                customs_count += 1
        ps.customs_doc_count = customs_count

        if ps.has_customs_toggle:
            # FR-12.1: allowed from any status, force into blocked_customs
            # Only force if actually blocked; if both docs already present is_blocked False
            # then keep current status (don't force blocked when gate already satisfied)
            if is_blocked(ps):
                ps.status = POSetStatus.blocked_customs
            else:
                # already satisfied: if status was blocked_customs keep? but toggle just turned on
                # and docs already satisfy -> not blocked, set to pending to reflect open
                # If previous status was mismatched/quarantined/merged, toggling on with docs
                # present should not overwrite that status — keep it. Only force blocked when needed.
                # For determinism when previous was pending, stay pending.
                if ps.status == POSetStatus.blocked_customs:
                    ps.status = POSetStatus.pending
                # else leave as-is; the simplest: ensure not blocked_customs when not blocked
                # but brief says flip -> blocked_customs, so when pending and toggle on with no docs, blocked
                # When pending and toggle on with docs present, remain pending (not blocked)
                pass
            # Edge: if ps.status was pending and toggle ON with no docs, is_blocked True handled above
            # so blocked_customs already set.
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
