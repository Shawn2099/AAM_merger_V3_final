"""Reconciliation — exact-match math, quarantine on <=0, independent DN/SI aggregates (FR-9.1-11.2)."""

from __future__ import annotations

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.core.config import AppConfig
from app.core.database import get_engine
from app.models import DocType, POSet, POSetStatus
from app.models.base import Base
from app.services.matching import _norm, find_unmatched, match_line
from app.services.quarantine import quarantine_copy


def aggregate(lines):
    """Sum quantities in integer scale x1000."""
    return sum(line["quantity"] for line in lines)


def reconcile(po: int, dn: int, si: int) -> dict:
    """Check quantity equality and non-positive condition (FR-10.1, FR-10.3)."""
    if po <= 0 or dn < 0 or si < 0 or dn == 0 or si == 0:  # negative/zero → quarantine (FR-10.3)
        return {"ok": False, "quarantine": True}
    ok = po == dn and po == si
    return {"ok": ok, "quarantine": False}


def check_price(po_price: int, agg_price: int) -> dict:
    """Exact-match price check as secondary condition (FR-11.1). Flag only."""
    return {"flag": po_price != agg_price}


def reconcile_po_set(po_set_id: int, cfg: AppConfig) -> dict:
    """Reconcile an entire PO Set (FR-9.1 - FR-14.7):

    - Loads POSet with documents and line items.
    - Guards: if already merged, immutable (FR-14.6).
    - Checks for COMBINED document fast-path or standard multi-doc set.
    - Checks for negative/zero quantities (FR-10.3) -> quarantine.
    - Runs reverse unmatched check (FR-8.5) and conflict check (FR-8.4) -> quarantine.
    - Checks exact integer aggregate quantities (FR-10.1) -> mismatched if failed.
    - Checks secondary price flag (FR-11.1).
    - Checks customs gate (FR-12.2 / FR-12.3).
    - Triggers auto-merge (FR-14.1) -> merged.
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise ValueError(f"POSet {po_set_id} not found")

        # Immutable once merged (FR-14.6)
        if ps.status == POSetStatus.merged:
            return {
                "status": "merged",
                "po_set_id": po_set_id,
                "merged_output_path": ps.merged_output_path,
                "flags": [],
            }

        docs = list(ps.documents or [])

        def _get_type(d):
            dt = d.doc_type
            return dt.value if hasattr(dt, "value") else str(dt)

        combined_docs = [d for d in docs if _get_type(d) == DocType.COMBINED.value]
        po_docs = [d for d in docs if _get_type(d) == DocType.PO.value]
        dn_docs = [d for d in docs if _get_type(d) == DocType.DN.value]
        si_docs = [d for d in docs if _get_type(d) == DocType.SI.value]

        # 1. Handle COMBINED documents
        if combined_docs:
            all_comb_items = [li for cd in combined_docs for li in cd.line_items]
            if any(li.quantity <= 0 for li in all_comb_items):
                ps.status = POSetStatus.quarantined
                s.commit()
                quarantine_copy(ps.id, cfg)
                return {
                    "status": "quarantined",
                    "reason": "non_positive_quantity",
                    "po_set_id": po_set_id,
                    "flags": [],
                }

            # Customs gate check (FR-12.3)
            from app.services.customs import is_blocked

            if ps.has_customs_toggle and is_blocked(ps):
                ps.status = POSetStatus.blocked_customs
                s.commit()
                return {"status": "blocked_customs", "po_set_id": po_set_id, "flags": []}

            from app.services.merge import merge_po_set

            merged_path = merge_po_set(po_set_id, cfg)
            s.refresh(ps)
            return {
                "status": ps.status.value if hasattr(ps.status, "value") else str(ps.status),
                "po_set_id": po_set_id,
                "merged_output_path": str(merged_path) if merged_path else None,
                "flags": [],
            }

        # 2. Standard multi-doc sets (PO + DN + SI)
        if not po_docs or (not dn_docs and not si_docs):
            ps.status = POSetStatus.pending
            s.commit()
            return {"status": "pending", "po_set_id": po_set_id, "flags": []}

        po_lines = [
            {
                "line_item_no": li.line_item_no,
                "description": li.description,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
            }
            for d in po_docs
            for li in d.line_items
        ]
        dn_lines = [
            {
                "line_item_no": li.line_item_no,
                "description": li.description,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
            }
            for d in dn_docs
            for li in d.line_items
        ]
        si_lines = [
            {
                "line_item_no": li.line_item_no,
                "description": li.description,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
            }
            for d in si_docs
            for li in d.line_items
        ]

        all_lines = po_lines + dn_lines + si_lines
        if any(line["quantity"] <= 0 for line in all_lines):
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg)
            return {
                "status": "quarantined",
                "reason": "non_positive_quantity",
                "po_set_id": po_set_id,
                "flags": [],
            }

        thr = getattr(cfg.matching, "fuzzy_description_threshold", 85)

        # Reverse unmatched line check (FR-8.5)
        unmatched = find_unmatched(po_lines, dn_lines, si_lines, thr=thr)
        if unmatched:
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg)
            return {
                "status": "quarantined",
                "reason": "unmatched_lines",
                "unmatched": unmatched,
                "po_set_id": po_set_id,
                "flags": [
                    {
                        "priority": 1,
                        "type": "identification",
                        "message": f"Unmatched line item: {unmatched}",
                    }
                ],
            }

        # Forward match & conflicting description check (FR-8.4)
        flags = []
        for p in po_lines:
            m_res = match_line(p, dn_lines, si_lines, thr=thr)
            if m_res.get("quarantine"):
                ps.status = POSetStatus.quarantined
                s.commit()
                quarantine_copy(ps.id, cfg)
                return {
                    "status": "quarantined",
                    "reason": "conflicting_descriptions",
                    "po_set_id": po_set_id,
                    "flags": [
                        {
                            "priority": 1,
                            "type": "identification",
                            "message": f"Conflicting line item: {p.get('line_item_no')}",
                        }
                    ],
                }

        # Independent quantity aggregation and price check per PO line (FR-9.1, FR-10.1, FR-11.1)
        reconciled_all = True
        for p in po_lines:
            p_no = p.get("line_item_no")
            p_desc = p.get("description") or ""

            if p_no:
                matching_dn = [d for d in dn_lines if d.get("line_item_no") == p_no]
                matching_si = [s for s in si_lines if s.get("line_item_no") == p_no]
            else:
                matching_dn = [
                    d
                    for d in dn_lines
                    if not d.get("line_item_no")
                    and fuzz.token_sort_ratio(_norm(p_desc), _norm(d.get("description") or ""))
                    >= thr
                ]
                matching_si = [
                    s
                    for s in si_lines
                    if not s.get("line_item_no")
                    and fuzz.token_sort_ratio(_norm(p_desc), _norm(s.get("description") or ""))
                    >= thr
                ]

            agg_dn = sum(d["quantity"] for d in matching_dn)
            agg_si = sum(s["quantity"] for s in matching_si)

            rec = reconcile(p["quantity"], agg_dn, agg_si)
            if not rec["ok"]:
                reconciled_all = False
                flags.append(
                    {
                        "priority": 2,
                        "type": "quantity",
                        "line_item_no": p_no,
                        "po_quantity": p["quantity"],
                        "agg_dn_quantity": agg_dn,
                        "agg_si_quantity": agg_si,
                    }
                )

            # Secondary price check (FR-11.1)
            if matching_si:
                si_price = matching_si[0].get("unit_price", 0)
                p_check = check_price(p["unit_price"], si_price)
                if p_check["flag"]:
                    flags.append(
                        {
                            "priority": 3,
                            "type": "price",
                            "line_item_no": p_no,
                            "po_unit_price": p["unit_price"],
                            "si_unit_price": si_price,
                        }
                    )

        # Sort flags by priority: (1) identification -> (2) quantity -> (3) price (FR-11.2)
        flags.sort(key=lambda f: f.get("priority", 99))

        if not reconciled_all:
            ps.status = POSetStatus.mismatched
            s.commit()
            return {"status": "mismatched", "po_set_id": po_set_id, "flags": flags}

        # Reconciled! Check Customs Gate (FR-12.2)
        from app.services.customs import is_blocked

        if ps.has_customs_toggle and is_blocked(ps):
            ps.status = POSetStatus.blocked_customs
            s.commit()
            return {"status": "blocked_customs", "po_set_id": po_set_id, "flags": flags}

        # Auto-merge (FR-14.1)
        from app.services.merge import merge_po_set

        merged_path = merge_po_set(po_set_id, cfg)
        s.refresh(ps)
        return {
            "status": ps.status.value if hasattr(ps.status, "value") else str(ps.status),
            "po_set_id": po_set_id,
            "merged_output_path": str(merged_path) if merged_path else None,
            "flags": flags,
        }
