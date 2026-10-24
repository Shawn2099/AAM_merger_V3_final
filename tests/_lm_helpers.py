"""Shared helpers for the line-number fallback tests (pure functions, no DB)."""

from __future__ import annotations

from app.core.config import LineNumberFallbackConfig
from app.services.reconciliation import compare_po_set_lines

A = LineNumberFallbackConfig()  # every rule on, the shipped default
R = A.rules()


def L(*keys, qty=1000, doc=None, desc="ITEM"):
    """Rows `{"line_item_no", "description", "quantity", "document_id"}` (qty scaled x1000)."""
    return [
        {"line_item_no": k, "description": desc, "quantity": qty, "document_id": doc} for k in keys
    ]


def rows(pairs, doc=None, desc="ITEM"):
    """Rows from `(line_no, qty)` pairs, qty scaled x1000."""
    return [
        {"line_item_no": k, "description": desc, "quantity": q, "document_id": doc}
        for k, q in pairs
    ]


def verdict(comparison: dict) -> str:
    """The engine verdict rules applied to a `compare_po_set_lines` result."""
    if comparison["po_fail"]:
        return "quarantined"
    failing = [f for f in comparison["flags"] if f["type"] in ("identification", "quantity")]
    if not failing:
        return "merged"
    if any(f["type"] == "identification" for f in failing):
        return "quarantined"
    if any((f.get("vendor_quantity") or 0) > 0 for f in failing if f["type"] == "quantity"):
        return "mismatched"
    return "pending"


def run(po, dn, si, config=A):
    """(verdict, DN rule, SI rule, comparison) for the given rows."""
    cmp = compare_po_set_lines(po, dn, si, line_fallback=config)
    lm = cmp["line_mapping"]
    return verdict(cmp), lm["DN"]["rule"], lm["SI"]["rule"], cmp
