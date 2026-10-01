"""Reconciliation — group by line number, compare exact quantities, quarantine on failure.

One comparison, one implementation. `compare_po_set_lines` below is what both
`reconcile_po_set` (the engine) and the PO Set detail view (the dashboard) call,
so the verdict a reviewer reads is the verdict the engine reached. See
`AAM_merger_V3_PRODUCT.md` for the rule and its accepted limits.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import AppConfig
from app.core.database import get_engine
from app.models import DocType, POSet, POSetStatus
from app.models.base import Base
from app.services.matching import compare_aggregates, group_by_line_no
from app.services.quarantine import quarantine_copy

logger = logging.getLogger(__name__)


def compare_po_set_lines(
    po_lines: list[dict],
    dn_lines: list[dict],
    si_lines: list[dict],
    desc_threshold: int = 85,
) -> dict:
    """Compare PO lines against the DN pool and the SI pool, independently.

    Returns:
        po_totals / dn_totals / si_totals  per-line sums, keyed by normalised
                                           line number (scaled x1000)
        po_fail    non-None when a PO line carries no usable line number
        flags      one entry per discrepancy, priority 1 (identity) before
                   2 (quantity), ready to render or persist

    A line that is short of its PO quantity is still reported here; deciding
    whether that means "awaiting delivery" or "failed" is the caller's job,
    because only the engine knows the set's history.
    """
    po_totals, dn_totals, dn_orphans, po_fail = group_by_line_no(po_lines, dn_lines, desc_threshold)
    _, si_totals, si_orphans, _ = group_by_line_no(po_lines, si_lines, desc_threshold)

    flags: list[dict] = []
    for label, orphans, totals in (("DN", dn_orphans, dn_totals), ("SI", si_orphans, si_totals)):
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
    return {
        "po_totals": po_totals,
        "dn_totals": dn_totals,
        "si_totals": si_totals,
        "po_fail": po_fail,
        "flags": flags,
    }


# Plain-language, reviewer-facing wording per internal reason code. The whole
# point is that the dashboard can answer "why is this set stuck?" without the
# reviewer re-running reconciliation or reading logs. Every code the
# reconciler can emit must appear here or the dashboard falls back to
# "Awaiting further processing" and understates a quarantine as a wait.
REASON_TEXT: dict[str, str] = {
    "non_positive_quantity": "A quantity is zero, negative, or unreadable",
    "missing_po_document": "No purchase order document in this set yet",
    "missing_dn_document": "No delivery note in this set yet",
    "missing_si_document": "No invoice in this set yet",
    "partial_fulfillment": "Waiting on more deliveries or invoices",
    "unmatched_vendor_line": "A delivery or invoice line could not be matched to any PO line",
    "po_line_missing_line_item_no": "A PO line has no line number, so it cannot be compared",
    "multiple_po_documents": "This PO Set holds more than one PO document",
    "packet_naming_failed": "The merged packet could not be named unambiguously",
    "po_reference_mismatch": "A document references a different PO number",
    "quantity_mismatch": "PO, delivery, and invoice quantities do not agree",
    "po_document_has_no_line_items": "The PO document was read but contains no line items — re-extract or contact the VLM operator",
}


def explain(reason: str | None, flags: list[dict] | None = None) -> str:
    """Build a short human sentence for the dashboard reason column.

    The base sentence must match what the status means. A `mismatched` set
    carries no reason code, and defaulting it to "Awaiting further processing"
    told the reviewer to wait on a set that will never resolve itself and needs
    a human — the same failure class as showing a quarantine as a wait.
    """
    qty = [f for f in (flags or []) if f.get("type") == "quantity"]
    base = REASON_TEXT.get(reason or "", "")
    if not base:
        # No explicit code. Quantity flags mean a real disagreement; without
        # them the set genuinely is just waiting.
        base = REASON_TEXT["quantity_mismatch"] if qty else "Awaiting further processing"

    if not qty:
        return base
    parts = []
    for f in qty:
        po_q = f.get("po_quantity")
        v_q = f.get("vendor_quantity")
        if po_q is None or v_q is None:
            continue
        verb = "delivered" if f.get("pool") == "DN" else "invoiced"
        parts.append(f"{verb} {v_q / 1000:g} of {po_q / 1000:g}")
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
    naming = [f for f in flags if f.get("type") == "naming"]
    if status == "merged":
        note = "Fully reconciled — packet merged"
        if naming:
            note = f"Fully reconciled — packet merged. {naming[0].get('message', '')}"
    else:
        note = explain(result.get("reason"), flags)
    try:
        eng = get_engine(cfg)
        with Session(eng) as s:
            ps = s.get(POSet, po_set_id)
            if ps is not None:
                if (
                    ps.status == POSetStatus.merged
                    and ps.reconcile_reason
                    and not naming
                    and status == "merged"
                ):
                    return
                ps.reconcile_reason = note
                s.commit()
    except Exception:  # never fail a reconcile because a note could not be saved
        logger.warning("Could not persist reconcile_reason for PO Set %s", po_set_id)


def _reconcile_po_set_inner(po_set_id: int, cfg: AppConfig) -> dict:
    """Reconcile an entire PO Set over ordinary PO/DN/SI document rows.

    - Loads POSet with documents and line items.
    - Guards: if already merged, immutable (FR-14.6).
    - Gate: needs at least one PO, one DN and one SI row; a missing side
      waits visibly with an explicit reason (it is unfulfilled demand, not
      evidence).
    - Quarantines a set holding more than one PO document when
      `reconciliation.single_po_document` is on (summing two baselines would
      double every quantity).
    - Quarantines on PO-reference drift, non-positive quantities, unresolvable
      line numbers and unmatched vendor lines.
    - Compares exact integer aggregates per line, DN pool and SI pool
      independently; disagreement mismatches, outstanding delivery pends.
    - Checks customs gate, then triggers auto-merge.
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

        # Quarantine reason is preserved under automated sweeps. The Phase 2
        # sweep must not overwrite the reason written by the flow that originally
        # quarantined the set (e.g. 'extraction failed permanently' being replaced
        # by 'unmatched vendor line' because the broken PO doc has 0 lines).
        # Operator actions (redo_extract, redo_match) reset status to pending
        # before calling reconcile — that reset is the explicit gate for re-entry.
        if ps.status == POSetStatus.quarantined:
            return {
                "status": "quarantined",
                "reason": ps.reconcile_reason or "quarantined",
                "po_set_id": po_set_id,
                "flags": [],
            }

        docs = list(ps.documents or [])

        def _get_type(d):
            dt = d.doc_type
            return dt.value if hasattr(dt, "value") else str(dt)

        po_docs = [d for d in docs if _get_type(d) == DocType.PO.value]
        dn_docs = [d for d in docs if _get_type(d) == DocType.DN.value]
        si_docs = [d for d in docs if _get_type(d) == DocType.SI.value]

        # Every side must arrive as its own document row. A missing side is
        # unfulfilled demand, not evidence: the set waits with an explicit
        # reason instead of merging or failing.
        if not po_docs:
            ps.status = POSetStatus.pending
            s.commit()
            return {
                "status": "pending",
                "reason": "missing_po_document",
                "po_set_id": po_set_id,
                "flags": [],
            }
        if not dn_docs:
            ps.status = POSetStatus.pending
            s.commit()
            return {
                "status": "pending",
                "reason": "missing_dn_document",
                "po_set_id": po_set_id,
                "flags": [],
            }
        if not si_docs:
            ps.status = POSetStatus.pending
            s.commit()
            return {
                "status": "pending",
                "reason": "missing_si_document",
                "po_set_id": po_set_id,
                "flags": [],
            }

        # A PO Set is expected to hold exactly one PO. Two usually means a
        # re-issue or a mistyped PO number landing under the same key, and
        # summing them would silently double the baseline every quantity is
        # compared against. Configurable — see config.yaml reconciliation.
        if (
            getattr(cfg, "reconciliation", None) is not None
            and getattr(cfg.reconciliation, "single_po_document", False)
            and len(po_docs) > 1
        ):
            names = ", ".join(sorted(d.original_filename or "?" for d in po_docs))
            detail_msg = (
                f"This PO Set holds {len(po_docs)} PO documents ({names}); "
                f"only one PO is expected per PO Set"
            )
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg, reason="multiple_po_documents", detail=detail_msg)
            return {
                "status": "quarantined",
                "reason": "multiple_po_documents",
                "detail": detail_msg,
                "po_set_id": po_set_id,
                "flags": [
                    {
                        "priority": 1,
                        "type": "identification",
                        "message": detail_msg,
                    }
                ],
            }

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
            }
            for d in po_docs
            for li in d.line_items
        ]

        dn_source = dn_docs
        si_source = si_docs
        dn_lines = [
            {
                "line_item_no": li.line_item_no,
                "description": li.description,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
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
            }
            for d in si_source
            for li in d.line_items
        ]

        # A PO document exists but extraction produced zero line items while vendor
        # documents contain line items. The fault is in PO extraction (VLM returned nothing),
        # not in vendor documents — so the quarantine reason must say so explicitly rather than
        # falling through to "unmatched vendor line", which would point the operator at the wrong files.
        if not po_lines and (dn_lines or si_lines):
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg, reason="po_document_has_no_line_items")
            return {
                "status": "quarantined",
                "reason": "po_document_has_no_line_items",
                "po_set_id": po_set_id,
                "flags": [],
            }

        all_lines = po_lines + dn_lines + si_lines
        if any(int(line.get("quantity") or 0) <= 0 for line in all_lines):
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

        comparison = compare_po_set_lines(po_lines, dn_lines, si_lines, thr)
        po_fail = comparison["po_fail"]
        flags: list[dict] = comparison["flags"]

        if po_fail:
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg, reason=po_fail)
            return {
                "status": "quarantined",
                "reason": po_fail,
                "po_set_id": po_set_id,
                "flags": [
                    {
                        "priority": 1,
                        "type": "identification",
                        "message": REASON_TEXT[po_fail],
                    }
                ],
            }

        if flags:
            # A vendor line with no PO counterpart is unresolvable identity, not a
            # shortfall: quarantine rather than guess which PO line it belongs to.
            if any(f.get("type") == "identification" for f in flags):
                ps.status = POSetStatus.quarantined
                s.commit()
                quarantine_copy(ps.id, cfg, reason="unmatched_vendor_line", flags=flags)
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
            has_real_disagreement = any((f.get("vendor_quantity") or 0) > 0 for f in qty_flags)
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
        merge_info: dict = {}
        try:
            merged_path = merge_po_set(po_set_id, cfg, info=merge_info)
        except MergeNamingError as e:
            # The packet cannot be named unambiguously. Quarantine rather than
            # write a clobbered or ambiguous file into the output folder.
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg, reason="packet_naming_failed", detail=str(e))
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

        # The quantities proved this set, so a missing invoice number must not
        # block it — but a reviewer who expects an invoice-named file should be
        # told when it was named from the PO number instead.
        if merged_path is not None and merge_info.get("invoice_no_missing"):
            flags.append(
                {
                    "priority": 3,
                    "type": "naming",
                    "message": (
                        "No invoice number was extracted; packet named "
                        f"'{merge_info.get('output_name')}' from the PO number"
                    ),
                }
            )
        return {
            "status": ps.status.value if hasattr(ps.status, "value") else str(ps.status),
            "po_set_id": po_set_id,
            "merged_output_path": str(merged_path) if merged_path else None,
            "flags": flags,
        }
