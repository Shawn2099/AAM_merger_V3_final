"""Line-item matching — primary line_item_no, step-10 ERP alignment,
fuzzy fallback, reverse checks (FR-8.1-8.5).
"""

from __future__ import annotations

import itertools
import re

from rapidfuzz import fuzz


def _norm(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    # split digit<->letter boundary so "10kg" -> "10 kg" (FR-8 fuzzy robustness)
    s = re.sub(r"(\d)([A-Za-z])", r"\1 \2", s)
    s = re.sub(r"([A-Za-z])(\d)", r"\1 \2", s)
    return s.strip()


def normalize_line_no(s: str | None) -> str:
    """Normalize printed line numbers (DECISIONS_LOG §5).

    Strips whitespace + leading zeros ('01' → '1', ' 001 ' → '1') but compares
    as strings so alphanumeric ('1a') still works. '' / None → '' (missing).
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
    assert m2 is not None
    num = m2.group(1).lstrip("0") or "0"
    return num + m2.group(2)


def get_matching_candidates(
    po_line: dict,
    candidates: list[dict],
    all_po_lines: list[dict] | None = None,
    thr: int = 85,
) -> list[dict]:
    """Find matching candidate lines (DN or SI) for a given PO line item."""
    if not candidates:
        return []

    po_line_no = normalize_line_no(po_line.get("line_item_no"))
    po_desc = _norm(po_line.get("description") or "")

    # ASCII-digits-only gate: str.isdigit() also accepts characters that int()
    # rejects (superscripts and other numeric Unicode), which would raise.
    def _ascii_digits(s: str) -> bool:
        return bool(s) and all("0" <= c <= "9" for c in s)

    # 1. Exact line_item_no match (normalized: '01' == '1')
    if po_line_no:
        exact = [c for c in candidates if normalize_line_no(c.get("line_item_no")) == po_line_no]
        if exact:
            return exact

    # 2. Step-10 ERP alignment (FR-8.1a): PO line N (multiple of 10) maps to
    # DN/SI line N/10. Per-PO-line on purpose: one odd line must not disable
    # mapping for the rest. Exact matches (step 1) always win over this.
    if _ascii_digits(po_line_no):
        po_num = int(po_line_no)
        if po_num % 10 == 0 and po_num >= 10:
            expected_cand_no = str(po_num // 10)
            step10_cands = [
                c
                for c in candidates
                if normalize_line_no(c.get("line_item_no")) == expected_cand_no
            ]
            if step10_cands:
                return step10_cands

    # 3. Fuzzy description fallback (FR-8.2 / FR-8.5)
    fuzzy_matches = []
    for c in candidates:
        c_desc = _norm(c.get("description") or "")
        if c_desc and po_desc:
            score = fuzz.token_sort_ratio(po_desc, c_desc)
            if score >= thr:
                fuzzy_matches.append(c)

    return fuzzy_matches


def match_line(
    po: dict,
    dn_lines: list[dict],
    si_lines: list[dict],
    all_po_lines: list[dict] | None = None,
    thr: int = 85,
) -> dict:
    """Match a PO line item against DN and SI lines (FR-8.1-8.4).

    - Identifies matching DN and SI candidates.
    - Checks for conflicting descriptions on duplicate line references (FR-8.4 -> quarantine).
    - If a PO line is not yet fulfilled (no DN/SI line arrived yet), returns quarantine=False.
    """
    dn_cands = get_matching_candidates(po, dn_lines, all_po_lines=all_po_lines, thr=thr)
    si_cands = get_matching_candidates(po, si_lines, all_po_lines=all_po_lines, thr=thr)

    # Check conflicting descriptions on duplicate same-type candidates (FR-8.4).
    # Every pair is compared: with 3+ distinct descriptions, checking only
    # the first two lets a conflicting third slip through (W-4).
    if len(dn_cands) >= 2:
        norm_descs = {_norm(d.get("description") or "") for d in dn_cands}
        if len(norm_descs) > 1:
            for a, b in itertools.combinations(sorted(norm_descs), 2):
                if fuzz.token_sort_ratio(a, b) < thr:
                    return {"matched": False, "quarantine": True}

    if len(si_cands) >= 2:
        norm_descs = {_norm(s.get("description") or "") for s in si_cands}
        if len(norm_descs) > 1:
            for a, b in itertools.combinations(sorted(norm_descs), 2):
                if fuzz.token_sort_ratio(a, b) < thr:
                    return {"matched": False, "quarantine": True}

    matched = bool(dn_cands or not dn_lines) and bool(si_cands or not si_lines)
    return {"matched": matched, "quarantine": False}


def normalize_sku(sku: str | None) -> str:
    """v20.5 Step 3: normalize SKU/part number ('AB-100' -> 'AB100')."""
    if not sku:
        return ""
    s = re.sub(r"[\s\-_/.]", "", str(sku)).strip().upper()
    return s


def _desc_score(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    return float(fuzz.token_sort_ratio(na, nb))


def _best_fuzzy_index(
    v_desc: str,
    po_lines: list[dict],
    thr: int,
    margin: int,
    exclude: int | None = None,
) -> tuple[int | None, float]:
    """Best fuzzy PO line for a description, excluding one candidate.

    Shared by the normal Step 2 path and the re-index probe. Returns
    (index, score); index is None when nothing clears the threshold or the
    runner-up is within the margin.
    """
    scored: list[tuple[float, int]] = []
    for i, p in enumerate(po_lines):
        if i == exclude:
            continue
        s = _desc_score(v_desc, p.get("description") or "")
        if s > 0:
            scored.append((s, i))
    scored.sort(key=lambda t: (-t[0], t[1]))
    if not scored or scored[0][0] < thr:
        return None, (scored[0][0] if scored else 0.0)
    top = scored[0][0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if len(scored) == 1 or (top - second) >= margin:
        return scored[0][1], top
    return None, top


def _reindex_suggestion(
    v_no: str,
    v_desc: str,
    v_sku: str,
    po_lines: list[dict],
    matched_idx: int,
    thr: int,
    margin: int,
    use_sku: bool,
) -> dict | None:
    """Evidence that a vendor line was re-indexed by the vendor (FINDINGS F2).

    Triggered only when Step 1 found the printed number on the PO but the
    description guard rejected it, AND some *other* PO line explains the
    description (or a unique SKU) far better than the numbered one.

    This never resolves the line. It returns a suggestion for a human to
    confirm, so a vendor's renumbering cannot silently become a wrong merge.
    """
    alt, score = _best_fuzzy_index(v_desc, po_lines, thr, margin, exclude=matched_idx)
    source = "description"
    if alt is None and use_sku and v_sku:
        sku_hits = [
            i
            for i, p in enumerate(po_lines)
            if i != matched_idx and normalize_sku(p.get("part_no")) == v_sku
        ]
        if len(sku_hits) == 1:
            alt, source = sku_hits[0], "sku"
            score = 100.0
    if alt is None:
        return None
    target = po_lines[alt]
    return {
        "suggested_po_index": alt,
        "suggested_po_line_no": normalize_line_no(target.get("line_item_no")),
        "suggested_po_description": (target.get("description") or "")[:120],
        "suggested_by": source,
        "suggested_score": round(score, 1),
        "printed_line_no": v_no,
        "matched_po_line_no": normalize_line_no(po_lines[matched_idx].get("line_item_no")),
        "matched_po_description": (po_lines[matched_idx].get("description") or "")[:120],
    }


def match_vendor_line_detailed(
    vendor: dict,
    po_lines: list[dict],
    *,
    thr: int = 85,
    sanity: int = 40,
    margin: int = 5,
    use_sku: bool = True,
) -> tuple[int | None, str | None, dict | None]:
    """Resolve ONE vendor line to exactly ONE PO line index (v20.5 3-step).

    Iterating vendor->PO (not PO->vendor) makes the mapping injective, so a
    vendor line can never be counted in two PO aggregates — the double-count
    hole in the old per-PO-line fan-out.

    Returns (po_index, reason, detail). reason is None on success, else one of
    INDEX_DESCRIPTION_MISMATCH / AMBIGUOUS_LINE_MATCH / LINE_REINDEXED.
    detail is populated only for LINE_REINDEXED and carries the suggested
    target for a human to confirm.
    """
    v_no = normalize_line_no(vendor.get("line_item_no"))
    v_desc = vendor.get("description") or ""
    v_sku = normalize_sku(vendor.get("part_no") or vendor.get("item_code"))

    po_nos = {normalize_line_no(p.get("line_item_no")): i for i, p in enumerate(po_lines)}
    po_nos.pop("", None)

    # Step 1: exact line_item_no (+ description sanity guard)
    if v_no and v_no in po_nos:
        idx = po_nos[v_no]
        if _desc_score(v_desc, po_lines[idx].get("description") or "") < sanity:
            # FINDINGS F2: the number exists but the item does not match. If a
            # different PO line explains this description far better, the vendor
            # renumbered their lines — surface it instead of a bare quarantine.
            detail = _reindex_suggestion(v_no, v_desc, v_sku, po_lines, idx, thr, margin, use_sku)
            if detail:
                return None, "LINE_REINDEXED", detail
            return None, "INDEX_DESCRIPTION_MISMATCH", None
        return idx, None, None

    # Step 1b: step-10 ERP alignment (PO N <-> DN/SI N/10, per line)
    #
    # `str.isdigit()` is True for characters int() rejects (superscripts like
    # "²", and other numeric Unicode), so gate on ASCII digits explicitly and
    # wrap the conversion. A printed superscript is data to preserve, not a
    # number to reinterpret.
    if v_no and all("0" <= c <= "9" for c in v_no):
        n = int(v_no, 10)  # ASCII-digits-only gate above makes base 10 safe
        step10_no = str(n * 10) if 1 <= n < 10 else None
        if n >= 10 and n % 10 == 0:
            step10_no = str(n // 10)
        if step10_no and step10_no in po_nos:
            idx = po_nos[step10_no]
            if _desc_score(v_desc, po_lines[idx].get("description") or "") >= max(sanity, 1):
                return idx, None, None

    # Step 2: description fuzzy — ONLY when the number is absent or unlisted on
    # the PO (v20.5: fallback only, never overrides a real number mismatch).
    if not v_no or v_no not in po_nos:
        alt, _score = _best_fuzzy_index(v_desc, po_lines, thr, margin)
        if alt is not None:
            return alt, None, None

        # Step 3: SKU rescue — exactly one PO line shares the normalized SKU
        if use_sku and v_sku:
            sku_hits = [
                i for i, p in enumerate(po_lines) if normalize_sku(p.get("part_no")) == v_sku
            ]
            if len(sku_hits) == 1:
                return sku_hits[0], None, None
            if len(sku_hits) > 1:
                return None, "AMBIGUOUS_LINE_MATCH", None

    return None, "AMBIGUOUS_LINE_MATCH", None


def match_vendor_line(
    vendor: dict,
    po_lines: list[dict],
    *,
    thr: int = 85,
    sanity: int = 40,
    margin: int = 5,
    use_sku: bool = True,
) -> tuple[int | None, str | None]:
    """Backwards-compatible 2-tuple wrapper around match_vendor_line_detailed."""
    idx, reason, _detail = match_vendor_line_detailed(
        vendor, po_lines, thr=thr, sanity=sanity, margin=margin, use_sku=use_sku
    )
    return idx, reason

    return None, "AMBIGUOUS_LINE_MATCH"


def assign_lines_detailed(
    po_lines: list[dict],
    vendor_lines: list[dict],
    *,
    thr: int = 85,
    sanity: int = 40,
    margin: int = 5,
    use_sku: bool = True,
) -> tuple[dict[int, list[dict]], str | None, dict | None]:
    """Assign every vendor line to exactly one PO line (injective).

    Returns ({po_index: [vendor_lines]}, reason, detail). reason is set on the
    first failure; detail is populated for LINE_REINDEXED. A vendor line that
    resolves nowhere is never silently dropped.
    """
    assign: dict[int, list[dict]] = {}
    for v in vendor_lines:
        idx, reason, detail = match_vendor_line_detailed(
            v, po_lines, thr=thr, sanity=sanity, margin=margin, use_sku=use_sku
        )
        if idx is None:
            return {}, reason or "AMBIGUOUS_LINE_MATCH", detail
        assign.setdefault(idx, []).append(v)
    return assign, None, None


def assign_lines(
    po_lines: list[dict],
    vendor_lines: list[dict],
    *,
    thr: int = 85,
    sanity: int = 40,
    margin: int = 5,
    use_sku: bool = True,
) -> tuple[dict[int, list[dict]], str | None]:
    """Backwards-compatible 2-tuple wrapper around assign_lines_detailed."""
    assign, reason, _detail = assign_lines_detailed(
        po_lines, vendor_lines, thr=thr, sanity=sanity, margin=margin, use_sku=use_sku
    )
    return assign, reason


def find_unmatched(
    po_lines: list[dict],
    dn_lines: list[dict],
    si_lines: list[dict],
    thr: int = 85,
) -> list[dict]:
    """Reverse check (FR-8.5): Verify every DN and SI line matches at least one PO line.

    Returns list of unmatched DN / SI line dicts (rogue lines not present on PO).
    """
    unmatched: list[dict] = []

    for d in dn_lines:
        matched = False
        d_line_no = normalize_line_no(d.get("line_item_no"))
        d_desc = _norm(d.get("description") or "")

        # 1. Exact match
        if d_line_no and any(
            normalize_line_no(p.get("line_item_no")) == d_line_no for p in po_lines
        ):
            matched = True

        # 2. Step-10 ERP match, per line (FR-8.1a): DN line N matches PO N*10
        if not matched and d_line_no and all("0" <= c <= "9" for c in d_line_no):
            d_num = int(d_line_no)
            if 1 <= d_num < 10:
                expected_po_no = str(d_num * 10)
                if any(
                    normalize_line_no(p.get("line_item_no")) == expected_po_no for p in po_lines
                ):
                    matched = True

        # 3. Fuzzy description fallback (FR-8.2, FR-8.5)
        if not matched and d_desc:
            for p in po_lines:
                p_desc = _norm(p.get("description") or "")
                if p_desc and fuzz.token_sort_ratio(d_desc, p_desc) >= thr:
                    matched = True
                    break

        if not matched:
            unmatched.append(d)

    for s in si_lines:
        matched = False
        s_line_no = normalize_line_no(s.get("line_item_no"))
        s_desc = _norm(s.get("description") or "")

        # 1. Exact match
        if s_line_no and any(
            normalize_line_no(p.get("line_item_no")) == s_line_no for p in po_lines
        ):
            matched = True

        # 2. Step-10 ERP match, per line (FR-8.1a): SI line N matches PO N*10
        if not matched and s_line_no and all("0" <= c <= "9" for c in s_line_no):
            s_num = int(s_line_no)
            if 1 <= s_num < 10:
                expected_po_no = str(s_num * 10)
                if any(
                    normalize_line_no(p.get("line_item_no")) == expected_po_no for p in po_lines
                ):
                    matched = True

        # 3. Fuzzy description fallback (FR-8.2, FR-8.5)
        if not matched and s_desc:
            for p in po_lines:
                p_desc = _norm(p.get("description") or "")
                if p_desc and fuzz.token_sort_ratio(s_desc, p_desc) >= thr:
                    matched = True
                    break

        if not matched:
            unmatched.append(s)

    return unmatched


# ---------------------------------------------------------------------------
# Simplified reconciliation core (dev-simplified)
#
# The rule, in full:
#   1. Group BOTH sides by line_item_no and sum each group.
#   2. For every PO group, PO qty must equal the vendor group in BOTH the DN
#      and the SI pool.
#   3. No vendor group may lack a PO counterpart, or a delivered line would be
#      silently dropped.
#   4. Anything that fails quarantines the whole set.
#
# Quantities are the only signal. No price, no UOM, no SKU, no ERP step rules.
# The description is a fallback for rows that carry no usable line number, and
# only then.
# ---------------------------------------------------------------------------


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
    """Compare per-line sums. Returns one discrepancy per offending PO line."""
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
