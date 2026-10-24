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

Quantities are the only signal. Price (not extracted), SKU/part number, UOM, positional order
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

    Strips whitespace and leading zeros ('01' -> '1', ' 001 ' -> '1', '01-01' -> '1-1')
    but compares as a string so alphanumeric forms ('1a', '1-1') survive intact.
    Zeros are stripped only at the start and after '-', '_' or '/'. A '.' segment is
    kept verbatim ('1.05' stays '1.05'): dotted numbers are sub-line identifiers, and
    collapsing them ('1.05' -> '1.5') can merge distinct lines. A false quarantine is
    the safe direction.
    '' / None -> '' (no usable number).
    """
    if s is None:
        return ""
    t = str(s).strip()
    if not t:
        return ""
    return re.sub(r"(^|[-_/])0+(\d+)", r"\1\2", t)


#: Synthetic keys for PO lines that carry no number. The description is the
#: only identity such a row has. The prefix cannot collide with a printed line
#: number (those are alphanumeric).
_DESC_KEY_PREFIX = "@desc:"


def po_item_key(line: dict) -> str:
    """The comparison key for a PO line.

    A printed line number is authoritative. A PO row with no number is still
    addressable by its description — the table content names the same item even
    when the vendor omitted the number. Returns "" only when the row has
    neither a number nor a description, which nothing can match.
    """
    key = normalize_line_no(line.get("line_item_no"))
    if key:
        return key
    desc = _norm(line.get("description") or "")
    return f"{_DESC_KEY_PREFIX}{desc}" if desc else ""


def display_line_key(key: str) -> str:
    """The operator-facing form of a key (no synthetic prefix)."""
    return key[len(_DESC_KEY_PREFIX) :] if key.startswith(_DESC_KEY_PREFIX) else key


def group_by_line_no(
    po_lines: list[dict],
    vendor_lines: list[dict],
    desc_threshold: int = 85,
    *,
    desc_matches: list[dict] | None = None,
) -> tuple[dict[str, int], dict[str, int], list[dict], str | None]:
    """Sum PO and vendor quantities into per-line groups.

    Returns (po_totals, vendor_totals, orphans, failure_reason).
    A non-None failure_reason means the set must quarantine.

    Rows are summed rather than matched one-to-one, so a single PO line
    delivered across several vendor rows reconciles correctly.

    A PO line with no printed number is keyed by its description (see
    `po_item_key`); a vendor row with no number, or with a number the PO does
    not carry, is matched by description. A printed number that the PO does
    carry is always authoritative — description never overrides it.

    `desc_matches`, when supplied, is populated with one entry per vendor row
    that was attached by the DESCRIPTION fallback rather than by a printed line
    number. That link is a guess the operator should see (an amber review tag
    in the UI); callers that do not care pass nothing.
    """
    po_totals: dict[str, int] = {}
    po_desc: dict[str, str] = {}
    numberless_po_keys: set[str] = set()

    for ln in po_lines:
        key = po_item_key(ln)
        if not key:
            # Neither a number nor a description: nothing can address this row.
            return {}, {}, [], "po_line_missing_line_item_no"
        if key.startswith(_DESC_KEY_PREFIX):
            numberless_po_keys.add(key)
        qty = int(ln.get("quantity") or 0)
        po_totals[key] = po_totals.get(key, 0) + qty
        po_desc.setdefault(key, _norm(ln.get("description") or ""))

    vendor_totals: dict[str, int] = {}
    orphans: list[dict] = []

    for ln in vendor_lines:
        key = normalize_line_no(ln.get("line_item_no"))
        target = key if key in po_totals else None
        matched_by_description = False

        if target is None and desc_threshold:
            # No number, or a number the PO does not carry. A numbered vendor
            # row can only fall back to a PO line that has no number of its
            # own; allowing it to match a different numbered PO line would let
            # a typo override a printed key.
            candidates = {k: po_desc[k] for k in numberless_po_keys} if key else po_desc
            target = _best_desc_key(ln, candidates, desc_threshold)
            matched_by_description = bool(target)

        if target:
            vendor_totals[target] = vendor_totals.get(target, 0) + int(ln.get("quantity") or 0)
            if matched_by_description and desc_matches is not None:
                desc_matches.append(
                    {
                        "vendor_line_item_no": ln.get("line_item_no"),
                        "vendor_description": ln.get("description"),
                        "po_key": target,
                        "po_description": po_desc.get(target),
                        "quantity": int(ln.get("quantity") or 0),
                    }
                )
        elif not key:
            orphans.append({**ln, "why": "no_line_number_and_no_description_match"})
        else:
            orphans.append({**ln, "why": "no_po_line_with_this_number"})

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
                {
                    "line": display_line_key(key),
                    "po_qty": po_qty,
                    "vendor_qty": v_qty,
                    "reason": "quantity_mismatch",
                }
            )
    return diffs
