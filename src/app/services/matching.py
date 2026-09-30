"""Line-item matching — the reconciliation rule, in full.

This module is the product. There is no second, richer matcher hiding
elsewhere: `reconcile_po_set` and the dashboard both call the functions here,
so the verdict an operator sees is the verdict the engine reached.

The rule:
  1. Group BOTH sides by `line_item_no` (whitespace/leading zeros normalised)
     and sum each group. One PO line delivered across several vendor rows
     therefore reconciles correctly.
  2. A vendor row carrying no usable line number may fall back to its
     description, and only then. It never overrides a real line number.
  3. For every PO group, the PO quantity must equal the vendor group in BOTH
     the DN pool and the SI pool, as exact scaled integers. No tolerance.
  4. A vendor group with no PO counterpart is an orphan — it quarantines the
     set, because a delivered line that resolves nowhere cannot be ignored.
  5. Anything that fails quarantines the whole set. There is no partial pass.

Quantities are the only signal. Price, SKU/part number, UOM, positional order
and ERP step-numbering are deliberately not matching inputs — see
`AAM_merger_V3_PRODUCT.md` for why each was removed and what that costs.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz


def _norm(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    # split digit<->letter boundary so "10kg" -> "10 kg" (fuzzy robustness)
    s = re.sub(r"(\d)([A-Za-z])", r"\1 \2", s)
    s = re.sub(r"([A-Za-z])(\d)", r"\1 \2", s)
    return s.strip()


def normalize_line_no(s: str | None) -> str:
    """Normalise a printed line number to its comparison key.

    Strips whitespace and leading zeros ('01' -> '1', ' 001 ' -> '1') but
    compares as a string so alphanumeric forms ('1a', '1-1') survive intact.
    '' / None -> '' (no usable number).
    """
    if s is None:
        return ""
    t = str(s).strip()
    if not t:
        return ""
    m = re.match(r"^0*(\d.*)$", t, re.DOTALL)
    if not m:
        return t
    m2 = re.match(r"^(\d+)(.*)$", m.group(1), re.DOTALL)
    if m2 is None:  # unreachable: the outer regex guarantees a leading digit
        return t
    num = m2.group(1).lstrip("0") or "0"
    return num + m2.group(2)


def group_by_line_no(
    po_lines: list[dict],
    vendor_lines: list[dict],
    desc_threshold: int = 85,
) -> tuple[dict[str, int], dict[str, int], list[dict], str | None]:
    """Sum PO and vendor quantities into per-line groups.

    Returns (po_totals, vendor_totals, orphans, failure_reason).
    A non-None failure_reason means the set must quarantine.

    Rows are summed rather than matched one-to-one, so a single PO line
    delivered across several vendor rows reconciles correctly.
    """
    po_totals: dict[str, int] = {}
    po_desc: dict[str, str] = {}

    for ln in po_lines:
        key = normalize_line_no(ln.get("line_item_no"))
        if not key:
            # A PO line we cannot address is unresolvable by definition.
            return {}, {}, [], "po_line_missing_line_item_no"
        qty = int(ln.get("quantity") or 0)
        po_totals[key] = po_totals.get(key, 0) + qty
        po_desc.setdefault(key, _norm(ln.get("description") or ""))

    vendor_totals: dict[str, int] = {}
    orphans: list[dict] = []

    for ln in vendor_lines:
        key = normalize_line_no(ln.get("line_item_no"))
        if not key and desc_threshold:
            # Fallback only: this row has no usable number, so try the
            # description. Never used to override a real line number.
            key = _best_desc_key(ln, po_desc, desc_threshold)
        if not key:
            orphans.append({**ln, "why": "no_line_number_and_no_description_match"})
        elif key not in po_totals:
            orphans.append({**ln, "why": "no_po_line_with_this_number"})
        else:
            vendor_totals[key] = vendor_totals.get(key, 0) + int(ln.get("quantity") or 0)

    return po_totals, vendor_totals, orphans, None


def _best_desc_key(vendor_line: dict, po_desc: dict[str, str], threshold: int) -> str:
    """Return the PO line key whose description is closest to this row's.

    Used only for rows with no usable line number. Returns "" when nothing
    clears the threshold — the caller then quarantines rather than guessing.
    """
    v_desc = _norm(vendor_line.get("description") or "")
    if not v_desc:
        return ""
    best_key, best_score = "", 0.0
    for key, p_desc in po_desc.items():
        if not p_desc:
            continue
        score = fuzz.token_sort_ratio(v_desc, p_desc)
        if score > best_score:
            best_key, best_score = key, score
    return best_key if best_score >= threshold else ""


def compare_aggregates(
    po_totals: dict[str, int],
    vendor_totals: dict[str, int],
    orphans: list[dict],
) -> list[dict]:
    """Compare per-line sums. Returns one discrepancy per offending line.

    Orphans are reported in place of quantity differences: a line that
    resolves to nothing is an identity failure, and reporting a quantity
    difference for it would misdescribe the problem to the reviewer.
    """
    if orphans:
        return [
            {
                "line": o.get("line_item_no") or o.get("description", "")[:40],
                "po_qty": None,
                "vendor_qty": o.get("quantity"),
                "reason": o.get("why", "orphan"),
            }
            for o in orphans
        ]

    diffs: list[dict] = []
    for key, po_qty in po_totals.items():
        v_qty = vendor_totals.get(key, 0)
        if v_qty != po_qty:
            diffs.append(
                {"line": key, "po_qty": po_qty, "vendor_qty": v_qty, "reason": "quantity_mismatch"}
            )
    return diffs
