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

Quantities are the only signal. Price (not extracted), SKU/part number, UOM and positional
order are deliberately not matching inputs — see `AAM_merger_V3_PRODUCT.md` for why each was
removed and what that costs. Step-10 and `-1` numbering are mapped per pool, only when exact
matching leaves vendor keys unmatched (`resolve_line_mapping`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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


# --- Line-number fallback -------------------------------------------------
# Some customer ERPs number PO lines differently from the vendor's DN/SI
# (PO 10,20,30 vs DN 1,2,3; PO 1-1 vs DN 1, and the reverses). Exact matching
# always runs first. These rules only re-key a COPY of the vendor rows when
# exact matching would leave vendor keys unmatched. PO keys are never rewritten.

LINE_MAPPING_RULES: tuple[str, ...] = ("x10", "div10", "add_rel", "strip_rel")

RULE_TEXT: dict[str, str] = {
    "x10": "PO lines in steps of 10, DN/SI lines in steps of 1",
    "div10": "DN/SI lines in steps of 10, PO lines in steps of 1",
    "add_rel": "PO lines carry a -1 suffix, DN/SI lines do not",
    "strip_rel": "DN/SI lines carry a -1 suffix, PO lines do not",
}

_PLAIN = re.compile(r"^[1-9]\d*$")  # 1, 2, 10 ... (never "0")
_REL1 = re.compile(r"^([1-9]\d*)-1$")  # 1-1, 12-1 ... (suffix -1 only, ASCII hyphen)


@dataclass(frozen=True)
class PoolMapping:
    rule: str | None = None  # one of LINE_MAPPING_RULES, None = as printed
    map: dict[str, str] = field(default_factory=dict)  # normalised vendor key -> PO key


@dataclass(frozen=True)
class LineMapping:
    dn: PoolMapping = field(default_factory=PoolMapping)
    si: PoolMapping = field(default_factory=PoolMapping)
    rejections: tuple[tuple[str, str, str], ...] = ()  # (pool, reason_code, operator message)

    @property
    def label(self) -> str | None:
        """Short tag for the set: None, a rule id, or 'DN <rule|as printed>, SI <...>'."""
        if self.dn.rule is None and self.si.rule is None:
            return None
        if self.dn.rule == self.si.rule:
            return self.dn.rule
        return f"DN {self.dn.rule or 'as printed'}, SI {self.si.rule or 'as printed'}"


def _eligible_rules(po_keys: set[str], vendor_keys: set[str]) -> list[str]:
    """Structural preconditions. For non-empty key sets at most one rule is eligible (pinned)."""
    po_plain = all(_PLAIN.match(k) for k in po_keys)
    v_plain = all(_PLAIN.match(k) for k in vendor_keys)
    out: list[str] = []
    if po_plain and v_plain and all(int(k) % 10 == 0 for k in po_keys):
        out.append("x10")
    if (
        po_plain
        and v_plain
        and all(int(k) % 10 == 0 for k in vendor_keys)
        and not all(int(k) % 10 == 0 for k in po_keys)
    ):
        out.append("div10")
    if v_plain and all(_REL1.match(k) for k in po_keys):
        out.append("add_rel")
    if po_plain and all(_REL1.match(k) for k in vendor_keys):
        out.append("strip_rel")
    return out


def _apply_rule(rule: str, key: str) -> str:
    if rule == "x10":
        return str(int(key) * 10)
    if rule == "div10":
        return str(int(key) // 10)
    if rule == "add_rel":
        return f"{key}-1"
    m = _REL1.match(key)  # strip_rel; eligibility guarantees a match
    assert m is not None
    return m.group(1)


def _natural(key: str) -> tuple[int, str]:
    return (len(key), key)


def _resolve_pool(
    po_keys: set[str], rows: list[dict], rules: frozenset[str]
) -> tuple[str, str | None, dict[str, str]]:
    """(kind, rule, map); kind in {"empty", "identity", "mapped", "none", "rejected"}."""
    vendor_keys = {normalize_line_no(r.get("line_item_no")) for r in rows}
    if not vendor_keys:
        return ("empty", None, {})
    if vendor_keys <= po_keys:
        return ("identity", None, {})  # exact matching already covers the pool
    fits = [
        r
        for r in _eligible_rules(po_keys, vendor_keys)
        if r in rules and all(_apply_rule(r, k) in po_keys for k in vendor_keys)
    ]
    assert len(fits) <= 1
    if not fits:
        return ("none", None, {})
    rule = fits[0]
    by_doc: dict[int | None, set[str]] = {}
    for r in rows:
        by_doc.setdefault(r.get("document_id"), set()).add(normalize_line_no(r.get("line_item_no")))
    if any(keys <= po_keys for keys in by_doc.values()):  # a mixed pool is never mapped
        return ("rejected", rule, {})
    return ("mapped", rule, {k: _apply_rule(rule, k) for k in sorted(vendor_keys, key=_natural)})


def resolve_line_mapping(
    po_lines: list[dict],
    dn_lines: list[dict],
    si_lines: list[dict],
    rules: frozenset[str],
) -> LineMapping:
    """Per pool, decide whether the vendor keys need a line-number rule. Never touches PO keys."""
    if not rules:
        return LineMapping()
    if any(
        normalize_line_no(r.get("line_item_no")) == "" for r in (*po_lines, *dn_lines, *si_lines)
    ):
        return LineMapping()  # numberless row anywhere: exact only
    po_keys = {normalize_line_no(r.get("line_item_no")) for r in po_lines}
    if not po_keys:
        return LineMapping()  # nothing to anchor on (dashboard preview)
    pools: dict[str, PoolMapping] = {}
    rejections: list[tuple[str, str, str]] = []
    for pool, rows in (("DN", dn_lines), ("SI", si_lines)):
        kind, rule, mapping = _resolve_pool(po_keys, rows, rules)
        pools[pool] = PoolMapping(rule, mapping) if kind == "mapped" else PoolMapping()
        if kind == "rejected":
            assert rule is not None
            rejections.append(
                (
                    pool,
                    "document_fits_exact",
                    (
                        f"{pool} line numbers fit rule {rule} ({RULE_TEXT[rule]}), but a {pool} "
                        "document already matches the PO line numbers as printed; "
                        "line numbers were not mapped"
                    ),
                )
            )
    return LineMapping(dn=pools["DN"], si=pools["SI"], rejections=tuple(rejections))
