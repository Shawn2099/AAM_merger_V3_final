"""Reconciliation — exact-match math, quarantine on <=0, independent aggregates (FR-9.1-11.2)."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.core.config import AppConfig
from app.core.database import get_engine
from app.models import DocType, POSet, POSetStatus
from app.models.base import Base
from app.services.matching import compare_aggregates, group_by_line_no
from app.services.quarantine import quarantine_copy

logger = logging.getLogger(__name__)


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


# Plain-language, reviewer-facing wording per internal reason code. The whole
# point is that the dashboard can answer "why is this set stuck?" without the
# reviewer re-running reconciliation or reading logs.
def _identity_flag(label: str, reason: str, detail: dict | None) -> dict:
    """Priority-1 flag: why a line could not be identified, in plain language."""
    if reason == "LINE_REINDEXED" and detail:
        msg = (
            f"{label} line is printed as '{detail.get('printed_line_no')}' but describes "
            f"'{detail.get('suggested_po_description', '')[:60]}', which is PO line "
            f"{detail.get('suggested_po_line_no')}. Looks like the vendor renumbered the lines."
        )
        return {
            "priority": 1,
            "type": "identification",
            "reason": reason,
            "message": msg,
            "suggestion": detail,
        }
    if reason == "INDEX_DESCRIPTION_MISMATCH":
        msg = f"{label} line number matches the PO but the description does not"
    else:
        msg = f"A {label} line could not be matched to any PO line"
    return {"priority": 1, "type": "identification", "reason": reason, "message": msg}


REASON_TEXT: dict[str, str] = {
    "non_positive_quantity": "A quantity is zero, negative, or unreadable",
    "combined_unverified": "Combined document is missing a PO, DN, or SI section",
    "partial_fulfillment": "Waiting on more deliveries or invoices",
    "unmatched_lines": "A DN or SI line could not be matched to any PO line",
    "ambiguous_line_match": "A DN or SI line could not be matched to any PO line",
    "index_description_mismatch": "A line number matches but the description does not",
    "line_reindexed": "A line looks renumbered by the vendor — needs confirmation",
    "conflicting_descriptions": "Two lines for the same item describe different things",
    "po_reference_mismatch": "A document references a different PO number",
    "over_delivery": "Delivered or invoiced quantity exceeds the PO quantity",
    "quantity_mismatch": "PO, delivery, and invoice quantities do not agree",
}


def explain(reason: str | None, flags: list[dict] | None = None) -> str:
    """Build a short human sentence for the dashboard reason column."""
    base = REASON_TEXT.get(reason or "", "Awaiting further processing")
    if not flags:
        return base
    for f in flags:
        sug = f.get("suggestion")
        if sug:
            return (
                f"{base} — printed line {sug.get('printed_line_no')} looks like it should "
                f"be PO line {sug.get('suggested_po_line_no')}"
            )
    qty = [f for f in flags if f.get("type") == "quantity"]
    if not qty:
        return base
    f = qty[0]
    parts = []
    if f.get("agg_dn_quantity") is not None and f.get("agg_dn_quantity") != f.get("po_quantity"):
        parts.append(f"delivered {f['agg_dn_quantity'] / 1000:g} of {f['po_quantity'] / 1000:g}")
    if f.get("agg_si_quantity") is not None and f.get("agg_si_quantity") != f.get("po_quantity"):
        parts.append(f"invoiced {f['agg_si_quantity'] / 1000:g} of {f['po_quantity'] / 1000:g}")
    if parts:
        return base + ": " + "; ".join(parts)
    return base


def reconcile_po_set(po_set_id: int, cfg: AppConfig) -> dict:
    """Public entry point: run reconciliation, then persist a plain-language
    reason on the PO Set so the dashboard can explain the state without the
    reviewer re-running anything (or reading logs after a restart).
    """
    result = _reconcile_po_set_inner(po_set_id, cfg)
    _persist_reason(po_set_id, result, cfg)
    return result


def _persist_reason(po_set_id: int, result: dict, cfg: AppConfig) -> None:
    status = result.get("status")
    flags = result.get("flags") or []
    if status == "merged":
        note = "Fully reconciled — packet merged"
    else:
        note = explain(result.get("reason"), flags)
    try:
        eng = get_engine(cfg)
        with Session(eng) as s:
            ps = s.get(POSet, po_set_id)
            if ps is not None:
                ps.reconcile_reason = note
                s.commit()
    except Exception:  # never fail a reconcile because a note could not be saved
        logger.warning("Could not persist reconcile_reason for PO Set %s", po_set_id)


def _reconcile_po_set_inner(po_set_id: int, cfg: AppConfig) -> dict:
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

        # 1. COMBINED-only sets (no separate PO baseline to deduct against):
        # verified 3-section evidence IS the reconciliation (DECISIONS_LOG §2).
        # When a separate PO exists, COMBINED lines instead join BOTH pools in
        # the standard flow below (Option A atomic: overflow in either pool
        # blocks the merge, nothing half-commits).
        if combined_docs and not po_docs:
            all_comb_items = [li for cd in combined_docs for li in cd.line_items]
            if any(li.quantity <= 0 for li in all_comb_items):
                ps.status = POSetStatus.quarantined
                s.commit()
                quarantine_copy(ps.id, cfg, reason="non_positive_quantity")
                return {
                    "status": "quarantined",
                    "reason": "non_positive_quantity",
                    "po_set_id": po_set_id,
                    "flags": [],
                }

            # COMBINED 3-section re-verification (FR-6.7/W-1): the extraction
            # gate runs once at VLM time, but reclassify/manual paths can tag
            # a doc COMBINED with no section evidence. Re-read the persisted
            # raw JSON here; missing/incomplete evidence waits visibly instead
            # of auto-merging an unverified packet.
            for cd in combined_docs:
                try:
                    raw = json.loads(cd.raw_extraction_json) if cd.raw_extraction_json else {}
                except Exception:
                    raw = {}
                if not (
                    raw.get("has_po_section")
                    and raw.get("has_dn_section")
                    and raw.get("has_si_section")
                ):
                    logger.warning(
                        "COMBINED doc %s unverified (missing PO/DN/SI section "
                        "evidence) — holding PO Set %s pending",
                        cd.id,
                        po_set_id,
                    )
                    ps.status = POSetStatus.pending
                    s.commit()
                    return {
                        "status": "pending",
                        "reason": "combined_unverified",
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

            # Forward progress: this set just verified reconciled, so clear
            # to pending for the merge. If merge refuses (None), restore the
            # prior status instead of stranding in pending (W-8).
            prior_status = ps.status
            ps.status = POSetStatus.pending
            s.commit()
            merged_path = merge_po_set(po_set_id, cfg)
            s.refresh(ps)
            if merged_path is None:
                if ps.status != prior_status:
                    ps.status = prior_status
                    s.commit()
                    s.refresh(ps)
                logger.warning(
                    "Merge refused for COMBINED PO Set %s — kept %s",
                    po_set_id,
                    prior_status,
                )
            return {
                "status": ps.status.value if hasattr(ps.status, "value") else str(ps.status),
                "po_set_id": po_set_id,
                "merged_output_path": str(merged_path) if merged_path else None,
                "flags": [],
            }

        # 2. Standard multi-doc sets (PO + DN + SI)
        # Needs at least PO and SI to evaluate reconciliation; a COMBINED doc
        # satisfies the SI requirement since its lines join both pools below.
        if not po_docs or (not si_docs and not combined_docs):
            ps.status = POSetStatus.pending
            s.commit()
            return {"status": "pending", "po_set_id": po_set_id, "flags": []}

        # Decoy PO / Cross-document PO Reference Validation (SPEC §7.3 FR-6.3)
        for d in docs:
            if (
                _get_type(d) in (DocType.PO.value, DocType.DN.value, DocType.SI.value)
                and d.po_no_normalized
                and d.po_no_normalized != ps.po_no_normalized
            ):
                # W-16: log both values — re-extraction overwrites
                # doc.po_no_normalized from the VLM while po_set_id stays,
                # so VLM po-reference drift can false-positive here.
                logger.warning(
                    "PO reference mismatch: doc %s (%s) != PO Set %s (%s)",
                    d.id,
                    d.po_no_normalized,
                    ps.id,
                    ps.po_no_normalized,
                )
                ps.status = POSetStatus.quarantined
                s.commit()
                msg = (
                    f"PO reference mismatch: doc {d.original_filename} "
                    f"({d.po_no_normalized}) != PO Set ({ps.po_no_normalized})"
                )
                quarantine_copy(ps.id, cfg, reason="po_reference_mismatch", detail=msg)
                return {
                    "status": "quarantined",
                    "reason": "po_reference_mismatch",
                    "po_set_id": po_set_id,
                    "flags": [
                        {
                            "priority": 1,
                            "type": "identification",
                            "message": msg,
                        }
                    ],
                }

        po_lines = [
            {
                "line_item_no": li.line_item_no,
                "description": li.description,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
                "part_no": li.part_no,
                "line_type": li.line_type or "GOODS",
            }
            for d in po_docs
            for li in d.line_items
        ]
        # Option A (DECISIONS_LOG §2): a COMBINED doc supplies BOTH pools —
        # deducted from dn_remaining and si_remaining simultaneously. Any
        # overflow fails the whole set (mismatched/quarantine); the merge below
        # fires only if every line passes in both pools (atomic).
        #
        # Exclusivity (FR-14.7, mirrors merge._ordered_docs): when a COMBINED
        # doc is present it is the authoritative record, so separate DN/SI lines
        # are EXCLUDED. Counting both would double every quantity and strand a
        # perfectly consistent set at `mismatched` forever.
        dn_source = combined_docs if combined_docs else dn_docs
        si_source = combined_docs if combined_docs else si_docs
        dn_lines = [
            {
                "line_item_no": li.line_item_no,
                "description": li.description,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
                "part_no": li.part_no,
                "line_type": li.line_type or "GOODS",
            }
            for d in dn_source
            for li in d.line_items
        ]
        si_lines = [
            {
                "line_item_no": li.line_item_no,
                "description": li.description,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
                "part_no": li.part_no,
                "line_type": li.line_type or "GOODS",
            }
            for d in si_source
            for li in d.line_items
        ]

        # Non-item rows (tax, freight, fee, discount) are never quantity
        # reconciled: they would inflate or defeat every aggregate. They stay
        # stored and merged as-is, but stay out of the math entirely.
        po_lines = [ln for ln in po_lines if (ln.get("line_type") or "GOODS") == "GOODS"]
        dn_lines = [ln for ln in dn_lines if (ln.get("line_type") or "GOODS") == "GOODS"]
        si_lines = [ln for ln in si_lines if (ln.get("line_type") or "GOODS") == "GOODS"]

        all_lines = po_lines + dn_lines + si_lines
        if any(line["quantity"] <= 0 for line in all_lines):
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg, reason="non_positive_quantity")
            return {
                "status": "quarantined",
                "reason": "non_positive_quantity",
                "po_set_id": po_set_id,
                "flags": [],
            }

        thr = getattr(cfg.matching, "fuzzy_description_threshold", 85)

        # ------------------------------------------------------------------
        # Simplified rule (dev-simplified).
        #
        # Quantities are the only signal. Both sides are grouped by
        # line_item_no and summed, so one PO line delivered across several
        # vendor rows reconciles. For every PO group we require
        # PO == AggDN and PO == AggSI exactly, and no vendor group may lack a
        # PO counterpart. Any failure quarantines the set: this engine either
        # gives an absolute answer or it gives none.
        # ------------------------------------------------------------------
        po_totals, dn_totals, dn_orphans, po_fail = group_by_line_no(po_lines, dn_lines, thr)
        _, si_totals, si_orphans, _ = group_by_line_no(po_lines, si_lines, thr)

        if po_fail:
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg, reason=po_fail)
            return {
                "status": "quarantined",
                "reason": po_fail,
                "po_set_id": po_set_id,
                "flags": [_identity_flag("PO", po_fail, None)],
            }

        flags: list[dict] = []
        pools = (("DN", dn_orphans, dn_totals), ("SI", si_orphans, si_totals))
        for label, orphans, totals in pools:
            for d in compare_aggregates(po_totals, totals, orphans):
                flags.append(
                    {
                        "priority": 1 if d["po_qty"] is None else 2,
                        "type": "identification" if d["po_qty"] is None else "quantity",
                        "pool": label,
                        "line_item_no": d["line"],
                        "po_quantity": d["po_qty"],
                        "vendor_quantity": d["vendor_qty"],
                        "reason": d["reason"],
                    }
                )
        flags.sort(key=lambda f: f.get("priority", 99))

        if flags:
            # A vendor line with no PO counterpart is unresolvable identity, not a
            # shortfall: quarantine rather than guess which PO line it belongs to.
            if any(f.get("type") == "identification" for f in flags):
                ps.status = POSetStatus.quarantined
                s.commit()
                quarantine_copy(
                    ps.id, cfg, reason="unmatched_vendor_line", flags=flags
                )
                return {
                    "status": "quarantined",
                    "reason": "unmatched_vendor_line",
                    "po_set_id": po_set_id,
                    "flags": flags,
                }

            # Partial delivery is only genuine when the vendor reported NOTHING
            # for those lines. A line both sides reported with different
            # quantities is a real disagreement, not an outstanding delivery.
            qty_flags = [f for f in flags if f.get("type") == "quantity"]
            has_real_disagreement = any(
                (f.get("vendor_quantity") or 0) > 0 for f in qty_flags
            )
            if not has_real_disagreement:
                ps.status = POSetStatus.pending
                s.commit()
                return {
                    "status": "pending",
                    "reason": "partial_fulfillment",
                    "po_set_id": po_set_id,
                    "flags": flags,
                }
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
        from app.services.merge import MergeNamingError, merge_po_set

        # Forward progress only: reconciled just now → clear to pending for
        # the merge; restore prior status if merge refuses (W-8).
        prior_status = ps.status
        ps.status = POSetStatus.pending
        s.commit()
        try:
            merged_path = merge_po_set(po_set_id, cfg)
        except MergeNamingError as e:
            # The packet cannot be named unambiguously. Quarantine rather than
            # write a clobbered or ambiguous file into the output folder.
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(
                ps.id, cfg, reason="packet_naming_failed", detail=str(e)
            )
            return {
                "status": "quarantined",
                "reason": "packet_naming_failed",
                "detail": str(e),
                "po_set_id": po_set_id,
                "flags": [
                    {
                        "priority": 1,
                        "type": "identification",
                        "message": f"Cannot name the merged packet: {e}",
                    }
                ],
            }
        s.refresh(ps)
        if merged_path is None:
            if ps.status != prior_status:
                ps.status = prior_status
                s.commit()
                s.refresh(ps)
            logger.warning("Auto-merge refused for PO Set %s — kept %s", po_set_id, prior_status)
        return {
            "status": ps.status.value if hasattr(ps.status, "value") else str(ps.status),
            "po_set_id": po_set_id,
            "merged_output_path": str(merged_path) if merged_path else None,
            "flags": flags,
        }
