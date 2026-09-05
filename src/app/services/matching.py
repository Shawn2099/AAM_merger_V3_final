"""Line-item matching — primary line_item_no, step-10 ERP alignment,
fuzzy fallback, reverse checks (FR-8.1-8.5).
"""

from __future__ import annotations

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


def get_matching_candidates(
    po_line: dict,
    candidates: list[dict],
    all_po_lines: list[dict] | None = None,
    thr: int = 85,
) -> list[dict]:
    """Find matching candidate lines (DN or SI) for a given PO line item."""
    if not candidates:
        return []

    po_line_no = str(po_line.get("line_item_no") or "").strip()
    po_desc = _norm(po_line.get("description") or "")

    # 1. Exact line_item_no match
    if po_line_no:
        exact = [c for c in candidates if str(c.get("line_item_no") or "").strip() == po_line_no]
        if exact:
            return exact

    # 2. Step-10 ERP alignment (FR-8.1a): PO line N (multiple of 10) maps to
    # DN/SI line N/10. Per-PO-line on purpose: one odd line must not disable
    # mapping for the rest. Exact matches (step 1) always win over this.
    if po_line_no and po_line_no.isdigit():
        po_num = int(po_line_no)
        if po_num % 10 == 0 and po_num >= 10:
            expected_cand_no = str(po_num // 10)
            step10_cands = [
                c
                for c in candidates
                if str(c.get("line_item_no") or "").strip() == expected_cand_no
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

    # Check conflicting descriptions on duplicate same-type candidates (FR-8.4)
    if len(dn_cands) >= 2:
        norm_descs = {_norm(d.get("description") or "") for d in dn_cands}
        if len(norm_descs) > 1:
            # Check pairwise token sort ratio; if different items -> quarantine
            descs_list = list(norm_descs)
            if fuzz.token_sort_ratio(descs_list[0], descs_list[1]) < thr:
                return {"matched": False, "quarantine": True}

    if len(si_cands) >= 2:
        norm_descs = {_norm(s.get("description") or "") for s in si_cands}
        if len(norm_descs) > 1:
            descs_list = list(norm_descs)
            if fuzz.token_sort_ratio(descs_list[0], descs_list[1]) < thr:
                return {"matched": False, "quarantine": True}

    matched = bool(dn_cands or not dn_lines) and bool(si_cands or not si_lines)
    return {"matched": matched, "quarantine": False}


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
        d_line_no = str(d.get("line_item_no") or "").strip()
        d_desc = _norm(d.get("description") or "")

        # 1. Exact match
        if d_line_no and any(
            str(p.get("line_item_no") or "").strip() == d_line_no for p in po_lines
        ):
            matched = True

        # 2. Step-10 ERP match, per line (FR-8.1a): DN line N matches PO N*10
        if not matched and d_line_no and d_line_no.isdigit():
            d_num = int(d_line_no)
            if 1 <= d_num < 10:
                expected_po_no = str(d_num * 10)
                if any(
                    str(p.get("line_item_no") or "").strip() == expected_po_no
                    for p in po_lines
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
        s_line_no = str(s.get("line_item_no") or "").strip()
        s_desc = _norm(s.get("description") or "")

        # 1. Exact match
        if s_line_no and any(
            str(p.get("line_item_no") or "").strip() == s_line_no for p in po_lines
        ):
            matched = True

        # 2. Step-10 ERP match, per line (FR-8.1a): SI line N matches PO N*10
        if not matched and s_line_no and s_line_no.isdigit():
            s_num = int(s_line_no)
            if 1 <= s_num < 10:
                expected_po_no = str(s_num * 10)
                if any(
                    str(p.get("line_item_no") or "").strip() == expected_po_no
                    for p in po_lines
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
