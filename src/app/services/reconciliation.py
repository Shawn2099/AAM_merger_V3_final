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
from app.services.matching import compare_aggregates, display_line_key, group_by_line_no
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
                   2 (quantity), plus `description_match` entries for links
                   made by the description fallback (advisory, not a failure)

    A line that is short of its PO quantity is still reported here; deciding
    whether that means "awaiting delivery" or "failed" is the caller's job,
    because only the engine knows the set's history.
    """
    dn_desc: list[dict] = []
    si_desc: list[dict] = []
    po_totals, dn_totals, dn_orphans, po_fail = group_by_line_no(
        po_lines, dn_lines, desc_threshold, desc_matches=dn_desc
    )
    _, si_totals, si_orphans, _ = group_by_line_no(
        po_lines, si_lines, desc_threshold, desc_matches=si_desc
    )

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

    # A fallback link is not a failure: the quantities may agree exactly. It is
    # recorded so the UI can show which lines were attached by description
    # rather than by a printed number, and the reviewer can check the item.
    for label, matches in (("DN", dn_desc), ("SI", si_desc)):
        for m in matches:
            po_label = display_line_key(m["po_key"])
            vendor_no = m["vendor_line_item_no"] or "(no line number)"
            flags.append(
                {
                    "priority": 2,
                    "type": "description_match",
                    "pool": label,
                    "line_item_no": po_label,
                    "vendor_line_item_no": m["vendor_line_item_no"],
                    "po_description": m["po_description"],
                    "vendor_description": m["vendor_description"],
                    "vendor_quantity": m["quantity"],
                    "reason": "matched_by_description",
                    "message": (
                        f"{label} row {vendor_no} was matched to PO line {po_label} by "
                        f"description ('{m['vendor_description']}' ≈ "
                        f"'{m['po_description']}'), not by a printed line number"
                    ),
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
    "documents_changed_during_merge": "Documents changed while merging; will re-check",
    "unmatched_vendor_line": "A delivery or invoice line could not be matched to any PO line",
    "po_line_missing_line_item_no": (
        "A PO line has neither a line number nor a description, so it cannot be compared"
    ),
    "multiple_po_documents": (
        "This PO Set holds more than one PO document; review which one is correct"
    ),
    "vendor_document_has_no_line_items": (
        "A delivery note or invoice was read but contains no line items"
    ),
    "document_extraction_failed": (
        "A document in this set could not be read; use Re-Extract or replace the file"
    ),
    "document_not_yet_read": "A document in this set has not been read yet",
    "unclassified_document_in_set": (
        "An unclassified document is attached to this set; reclassify or remove it"
    ),
    "blocked_customs": ("Customs requirement is ON; attach both a CUSTOMS and a SHIPPING document"),
    "packet_naming_failed": "The merged packet could not be named unambiguously",
    "packet_source_unreadable": (
        "A document's stored PDF is missing or unreadable; the packet cannot be written"
    ),
    "packet_write_failed": "The merged packet could not be written to the output folder",
    "no_line_item_evidence": "The set has no line-item evidence, so there is nothing to reconcile",
    "po_reference_mismatch": "A document references a different PO number",
    "duplicate_document_number": (
        "Two delivery notes or two invoices in this set carry the same document number"
    ),
    "quantity_mismatch": "PO, delivery, and invoice quantities do not agree",
    "po_document_has_no_line_items": (
        "The PO document was read but contains no line items — pages with the "
        "items may be missing from the input; re-extract or add the missing pages"
    ),
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
    if result.get("status") != "archived":
        _persist_reason(po_set_id, result, cfg)
    return result


# Reasons whose detail names the offending files. Persisting the generic
# REASON_TEXT sentence for these throws away the one thing the operator needs:
# which document to look at.
_DETAILED_REASONS = {
    "document_extraction_failed",
    "unclassified_document_in_set",
    "multiple_po_documents",
    "po_reference_mismatch",
    "duplicate_document_number",
    "vendor_document_has_no_line_items",
    "packet_naming_failed",
    "packet_source_unreadable",
    "packet_write_failed",
}


def _persist_reason(po_set_id: int, result: dict, cfg: AppConfig) -> None:
    """Write the plain-language reason for a verdict — but only when the
    engine has one to write.

    A verdict carries a reason CODE (`result["reason"]`), and `explain`
    renders a code into the sentence the dashboard shows. A verdict with no
    code and no quantity evidence says nothing new: the set is already in
    the state a previous pass put it in, with that pass's reason attached.
    Overwriting it with "Awaiting further processing" would tell the
    reviewer that a quarantined set is waiting for more documents, which is
    the one thing a quarantine must never look like.
    """
    status = result.get("status")
    if status == "archived":
        return
    flags = result.get("flags") or []
    naming = [f for f in flags if f.get("type") == "naming"]
    desc = [f for f in flags if f.get("type") == "description_match"]
    reason_code = result.get("reason")
    has_quantity_evidence = any(f.get("type") == "quantity" for f in flags)

    if status == "merged":
        note = "Fully reconciled — packet merged"
        extras: list[str] = []
        if naming:
            extras.append(naming[0].get("message", ""))
        if desc:
            lines = sorted({str(f.get("line_item_no") or "?") for f in desc})
            extras.append(
                f"{len(desc)} line(s) were matched by description, not by a printed "
                f"line number (line {', '.join(lines)}); review the item descriptions"
            )
        if extras:
            note = "Fully reconciled — packet merged. " + " ".join(e for e in extras if e)
    elif reason_code:
        if reason_code not in REASON_TEXT:
            # Every code the reconciler can emit must have a text, or the
            # dashboard degrades it to a generic message. Say so loudly.
            logger.warning(
                "Reason code %r has no text in REASON_TEXT — the dashboard will show a "
                "generic message. Add the text in the same change that adds the code.",
                reason_code,
            )
        detail = result.get("detail")
        if reason_code in _DETAILED_REASONS and detail:
            # The detail names the files ("this set holds 2 PO documents
            # (A.pdf, B.pdf)"). A generic "more than one PO" sentence makes the
            # operator open the set and guess which file is the duplicate.
            note = detail
        else:
            note = explain(reason_code, flags)
    elif has_quantity_evidence:
        # A mismatch is described by its numbers, not by a code.
        note = explain(None, flags)
    else:
        # Nothing new to say. Keep the reason already on the set.
        return
    try:
        eng = get_engine(cfg)
        with Session(eng) as s:
            ps = s.get(POSet, po_set_id)
            if ps is not None:
                if (
                    ps.status == POSetStatus.merged
                    and ps.reconcile_reason
                    and not naming
                    and not desc
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
    - Holds a set with more than one PO document pending for a human when
      `reconciliation.single_po_document` is on. It does not sum them and does
      not quarantine them: the documents may be page fragments or a re-issue.
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
        if ps.archived_at is not None:
            return {
                "status": "archived",
                "po_set_id": po_set_id,
                "flags": [],
            }

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
            # The set is already quarantined and a previous pass wrote the
            # reason that explains it. The engine has no new reason CODE to
            # give — only the stored sentence — so it returns none and leaves
            # the stored reason alone. A later sweep must not replace a
            # quarantine's explanation with a generic "Awaiting further
            # processing", which reads as a wait.
            return {
                "status": "quarantined",
                "reason": None,
                "detail": ps.reconcile_reason or "quarantined",
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

        # Validity guards run before any missing-side check: a set must never
        # be judged (or merged) on stale lines from a document that failed, or
        # on a document nobody has read yet, or on a type that cannot be part
        # of a set. Customs/shipping documents are separate add-ons and are
        # not read for line items.
        _addons = (DocType.CUSTOMS.value, DocType.SHIPPING.value)
        _set_types = (DocType.PO.value, DocType.DN.value, DocType.SI.value)
        checked = [d for d in docs if _get_type(d) not in _addons]

        def _status(d) -> str:
            st = d.extraction_status
            return st.value if hasattr(st, "value") else str(st)

        failed = [d for d in checked if _status(d) == "failed"]
        if failed:
            names = ", ".join(sorted(d.original_filename or "?" for d in failed))
            detail_msg = f"{names} could not be read; use Re-Extract or replace the file"
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg, reason="document_extraction_failed", detail=detail_msg)
            return {
                "status": "quarantined",
                "reason": "document_extraction_failed",
                "detail": detail_msg,
                "po_set_id": po_set_id,
                "flags": [{"priority": 1, "type": "identification", "message": detail_msg}],
            }
        unread = [d for d in checked if _status(d) in ("pending", "processing")]
        if unread:
            ps.status = POSetStatus.pending
            s.commit()
            return {
                "status": "pending",
                "reason": "document_not_yet_read",
                "po_set_id": po_set_id,
                "flags": [],
            }
        foreign = [d for d in docs if _get_type(d) not in _set_types + _addons]
        if foreign:
            names = ", ".join(sorted(d.original_filename or "?" for d in foreign))
            detail_msg = (
                f"Unclassified document(s) attached to this set ({names}); "
                "reclassify or remove them"
            )
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg, reason="unclassified_document_in_set", detail=detail_msg)
            return {
                "status": "quarantined",
                "reason": "unclassified_document_in_set",
                "detail": detail_msg,
                "po_set_id": po_set_id,
                "flags": [{"priority": 1, "type": "identification", "message": detail_msg}],
            }

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
                    "detail": msg,
                    "po_set_id": po_set_id,
                    "flags": [
                        {
                            "priority": 1,
                            "type": "identification",
                            "message": msg,
                        }
                    ],
                }

        # The PO key is the digits only; letters are the safety check. Each
        # attach path compares a document with the set's minting letters, so a
        # set minted from a letterless PO accepts TOGA-... and ARM-... alike, and
        # a re-read can change a document's letters while it stays attached.
        # Two different non-empty letter groups in one set are two different
        # POs sharing their digits: never merge them.
        from app.services.grouping import po_letters

        letter_groups: dict[str, list[str]] = {}
        if ps.po_no_letters:
            letter_groups.setdefault(ps.po_no_letters.upper(), []).append("PO Set")
        for d in docs:
            if _get_type(d) not in _set_types:
                continue
            try:
                letters = po_letters(d.po_no_raw)
            except TypeError:
                letters = ""
            if letters:
                letter_groups.setdefault(letters, []).append(d.original_filename or "?")
        if len(letter_groups) > 1:
            parts = "; ".join(
                f"{k}: {', '.join(sorted(v))}" for k, v in sorted(letter_groups.items())
            )
            msg = (
                f"PO reference mismatch: same PO digits ({ps.po_no_normalized}) but "
                f"different letters in the PO numbers ({parts})"
            )
            logger.warning("PO letters conflict in PO Set %s: %s", ps.id, parts)
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg, reason="po_reference_mismatch", detail=msg)
            return {
                "status": "quarantined",
                "reason": "po_reference_mismatch",
                "detail": msg,
                "po_set_id": po_set_id,
                "flags": [{"priority": 1, "type": "identification", "message": msg}],
            }

        # The same DN (or invoice) number on two documents is either one document
        # scanned twice (or re-issued) or the pages of one document. Summing a
        # re-scan would double the delivered (or invoiced) quantity and could
        # complete a PO that was only half delivered. Pages of one document carry
        # different line numbers and are safe to sum. So the set is held only
        # when the documents sharing a number also share a line number; a line
        # with no number counts as shared, since it cannot be told apart.
        for label, vendor_docs, attr in (
            ("DN", dn_docs, "dn_no"),
            ("SI", si_docs, "invoice_no"),
        ):
            by_number: dict[str, list] = {}
            shown: dict[str, str] = {}
            for d in vendor_docs:
                raw_no = getattr(d, attr, None) or (d.si_no if label == "SI" else None)
                key = "".join(c for c in (raw_no or "") if c.isalnum()).upper()
                if key:
                    by_number.setdefault(key, []).append(d)
                    shown.setdefault(key, (raw_no or "").strip())
            clashes: list[str] = []
            for key, group in sorted(by_number.items()):
                if len(group) < 2:
                    continue
                seen_lines: set[str] = set()
                overlap = False
                for d in group:
                    mine = {(li.line_item_no or "").strip().upper() for li in d.line_items}
                    if "" in mine or mine & seen_lines:
                        overlap = True
                    seen_lines |= mine
                if overlap:
                    names = ", ".join(sorted(d.original_filename or "?" for d in group))
                    clashes.append(f"{shown[key]}: {names}")
            if clashes:
                parts = "; ".join(clashes)
                msg = (
                    f"Duplicate {label} number in one PO Set ({parts}) with the same line "
                    "numbers; remove the extra copy or check that they are different documents"
                )
                logger.warning("Duplicate %s number in PO Set %s: %s", label, ps.id, parts)
                ps.status = POSetStatus.quarantined
                s.commit()
                quarantine_copy(ps.id, cfg, reason="duplicate_document_number", detail=msg)
                return {
                    "status": "quarantined",
                    "reason": "duplicate_document_number",
                    "detail": msg,
                    "po_set_id": po_set_id,
                    "flags": [{"priority": 1, "type": "identification", "message": msg}],
                }

        # More than one PO document is a human decision, not an engine guess.
        # Do not sum them (a re-issue would double quantities) and do not
        # quarantine them (they may be legitimate page fragments). Hold the
        # set pending for review. No new action or schema: the operator uses
        # the existing document tools, or leaves the set blocked.
        if (
            getattr(cfg, "reconciliation", None) is not None
            and getattr(cfg.reconciliation, "single_po_document", False)
            and len(po_docs) > 1
        ):
            names = ", ".join(sorted(d.original_filename or "?" for d in po_docs))
            detail_msg = (
                f"This PO Set holds {len(po_docs)} PO documents ({names}); "
                "review which PO document is correct before merging"
            )
            ps.status = POSetStatus.pending
            s.commit()
            return {
                "status": "pending",
                "reason": "multiple_po_documents",
                "detail": detail_msg,
                "po_set_id": po_set_id,
                "flags": [],
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

        # A PO document exists but extraction produced no line items. The fault
        # is in PO extraction (VLM returned nothing), not in vendor documents —
        # so the quarantine reason must say so explicitly rather than falling
        # through to "unmatched vendor line", which would point the operator at
        # the wrong files. This also covers the all-three-empty case: a set with
        # no line anywhere has nothing to reconcile and must never be reported
        # as merely waiting while the merge silently refuses.
        if not po_lines:
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg, reason="po_document_has_no_line_items")
            return {
                "status": "quarantined",
                "reason": "po_document_has_no_line_items",
                "po_set_id": po_set_id,
                "flags": [],
            }

        # A DN or SI document that read as valid but produced no lines looks
        # exactly like a vendor who has not delivered yet. It is not: the set
        # has its three document types, the comparison can never progress, and
        # the nightly sweep will report "waiting" forever. Quarantine the set
        # and name the file, so the operator can re-extract it.
        for label, vendor_docs in (("DN", dn_docs), ("SI", si_docs)):
            # A page with no lines (an invoice totals page) is covered when another
            # page of the same document number carries the lines.
            attr = "dn_no" if label == "DN" else "invoice_no"

            def _num(d, attr=attr) -> str:
                return "".join(c for c in (getattr(d, attr, None) or "") if c.isalnum()).upper()

            covered = {_num(d) for d in vendor_docs if d.line_items and _num(d)}
            empty = [d for d in vendor_docs if not d.line_items and _num(d) not in covered]
            if empty:
                names = ", ".join(sorted(d.original_filename or "?" for d in empty))
                detail_msg = (
                    f"The {label} document(s) read successfully but contain no "
                    f"line items ({names}); re-extract or inspect the file"
                )
                ps.status = POSetStatus.quarantined
                s.commit()
                quarantine_copy(
                    ps.id,
                    cfg,
                    reason="vendor_document_has_no_line_items",
                    detail=detail_msg,
                )
                return {
                    "status": "quarantined",
                    "reason": "vendor_document_has_no_line_items",
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

        # Only identification and quantity flags decide the verdict. A
        # description_match flag records that the engine attached a row by the
        # description fallback — real information for the reviewer, but the
        # quantities still agree, so it must not turn a merged set into
        # "pending" or "mismatched".
        failure_flags = [f for f in flags if f.get("type") in ("identification", "quantity")]
        if failure_flags:
            # A vendor line with no PO counterpart is unresolvable identity, not a
            # shortfall: quarantine rather than guess which PO line it belongs to.
            if any(f.get("type") == "identification" for f in failure_flags):
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
            qty_flags = [f for f in failure_flags if f.get("type") == "quantity"]
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
            return {
                "status": "blocked_customs",
                "reason": "blocked_customs",
                "po_set_id": po_set_id,
                "flags": flags,
            }

        # Auto-merge (FR-14.1)
        from app.services.merge import (
            MergeEmptyError,
            MergeNamingError,
            MergeSourceError,
            MergeStaleError,
            merge_po_set,
        )

        # Forward progress only: reconciled just now → clear to pending for
        # the merge; restore prior status if merge refuses (W-8).
        prior_status = ps.status
        ps.status = POSetStatus.pending
        s.commit()
        merge_info: dict = {}
        try:
            merged_path = merge_po_set(
                po_set_id,
                cfg,
                info=merge_info,
                expected_doc_ids=frozenset(d.id for d in docs if d.archived_at is None),
            )
        except MergeStaleError as e:
            # Documents moved between the reconcile read and the write. Hold the
            # set; the next reconcile re-proves it against the current documents.
            ps.status = POSetStatus.pending
            s.commit()
            logger.warning("Merge held for PO Set %s: %s", po_set_id, e)
            return {
                "status": "pending",
                "reason": "documents_changed_during_merge",
                "po_set_id": po_set_id,
                "flags": flags,
            }
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
        except MergeEmptyError as e:
            reason = getattr(e, "reason_code", "no_line_item_evidence")
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg, reason=reason, detail=str(e))
            return {
                "status": "quarantined",
                "reason": reason,
                "detail": str(e),
                "po_set_id": po_set_id,
                "flags": [],
            }
        except (MergeSourceError, OSError) as e:
            # A missing/encrypted/corrupt member, or a write failure, must be a
            # named quarantine with the offending file — never a raw exception
            # that leaves the set pending and retries every night.
            reason = (
                "packet_source_unreadable"
                if isinstance(e, MergeSourceError)
                else "packet_write_failed"
            )
            ps.status = POSetStatus.quarantined
            s.commit()
            quarantine_copy(ps.id, cfg, reason=reason, detail=str(e))
            return {
                "status": "quarantined",
                "reason": reason,
                "detail": str(e),
                "po_set_id": po_set_id,
                "flags": [],
            }
        s.refresh(ps)
        # Advisory only: how many vendor rows the fallback linked. Persisted so
        # the dashboard can show the amber review tag without re-running the
        # matcher on every 2-second poll.
        desc_flags = [f for f in flags if f.get("type") == "description_match"]
        if ps.description_match_count != len(desc_flags):
            ps.description_match_count = len(desc_flags)
            s.commit()
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
        if merged_path is not None and merge_info.get("multiple_invoice_numbers"):
            numbers = merge_info.get("invoice_numbers") or []
            flags.append(
                {
                    "priority": 3,
                    "type": "naming",
                    "message": (
                        "This set holds more than one invoice number ("
                        + ", ".join(str(n) for n in numbers)
                        + f"); packet named '{merge_info.get('output_name')}' from the "
                        "first SI document"
                    ),
                }
            )
        return {
            "status": ps.status.value if hasattr(ps.status, "value") else str(ps.status),
            "po_set_id": po_set_id,
            "merged_output_path": str(merged_path) if merged_path else None,
            "flags": flags,
        }
