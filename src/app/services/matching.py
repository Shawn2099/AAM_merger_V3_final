"""Line-item matching — primary line_item_no, fuzzy fallback (rapidfuzz), conflict & reverse checks (FR-8.1-8.5)."""

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


def match_line(po: dict, dn_lines: list[dict], si_lines: list[dict], thr: int = 85) -> dict:
    """Match a PO line item against DN and SI lines (FR-8.1-8.4).

    - If PO has line_item_no:
      - Matches DN lines with same line_item_no.
      - Matches SI lines with same line_item_no.
      - Checks conflicting descriptions on duplicate line_item_no (FR-8.4).
    - If PO has no line_item_no:
      - Fuzzy fallback via token_sort_ratio >= thr against DN/SI lines without line_item_no (FR-8.2).
    """
    po_line_no = po.get("line_item_no")
    po_desc = _norm(po.get("description") or "")

    dn_matched = True
    si_matched = True

    if po_line_no:
        # Check DN lines
        if dn_lines:
            dn_cands = [d for d in dn_lines if d.get("line_item_no") == po_line_no]
            if not dn_cands:
                return {"matched": False, "quarantine": True}
            # Check conflicting descriptions on same line_item_no (FR-8.4)
            if len(dn_cands) >= 2:
                norm_descs = {_norm(d.get("description") or "") for d in dn_cands}
                if len(norm_descs) > 1:
                    return {"matched": False, "quarantine": True}

        # Check SI lines
        if si_lines:
            si_cands = [s for s in si_lines if s.get("line_item_no") == po_line_no]
            if not si_cands:
                return {"matched": False, "quarantine": True}
            # Check conflicting descriptions on same line_item_no (FR-8.4)
            if len(si_cands) >= 2:
                norm_descs = {_norm(s.get("description") or "") for s in si_cands}
                if len(norm_descs) > 1:
                    return {"matched": False, "quarantine": True}

        if not dn_lines and not si_lines:
            return {"matched": False, "quarantine": False}

        return {"matched": True, "quarantine": False}

    # Fuzzy fallback when PO has no line_item_no (FR-8.2)
    if dn_lines:
        dn_match = False
        for d in dn_lines:
            if not d.get("line_item_no"):
                score = fuzz.token_sort_ratio(po_desc, _norm(d.get("description") or ""))
                if score >= thr:
                    dn_match = True
                    break
        if not dn_match:
            dn_matched = False

    if si_lines:
        si_match = False
        for s in si_lines:
            if not s.get("line_item_no"):
                score = fuzz.token_sort_ratio(po_desc, _norm(s.get("description") or ""))
                if score >= thr:
                    si_match = True
                    break
        if not si_match:
            si_matched = False

    if not dn_lines and not si_lines:
        return {"matched": False, "quarantine": False}

    matched = dn_matched and si_matched
    return {"matched": matched, "quarantine": not matched}


def find_unmatched(
    po_lines: list[dict],
    dn_lines: list[dict],
    si_lines: list[dict],
    thr: int = 85,
) -> list[dict]:
    """Reverse check (FR-8.5): Verify every DN and SI line matches a PO line.

    Returns list of unmatched DN / SI line dicts.
    """
    unmatched: list[dict] = []

    for d in dn_lines:
        matched = False
        d_line_no = d.get("line_item_no")
        d_desc = _norm(d.get("description") or "")

        if d_line_no:
            for p in po_lines:
                if p.get("line_item_no") == d_line_no:
                    matched = True
                    break
        else:
            for p in po_lines:
                if not p.get("line_item_no"):
                    if fuzz.token_sort_ratio(d_desc, _norm(p.get("description") or "")) >= thr:
                        matched = True
                        break
        if not matched:
            unmatched.append(d)

    for s in si_lines:
        matched = False
        s_line_no = s.get("line_item_no")
        s_desc = _norm(s.get("description") or "")

        if s_line_no:
            for p in po_lines:
                if p.get("line_item_no") == s_line_no:
                    matched = True
                    break
        else:
            for p in po_lines:
                if not p.get("line_item_no"):
                    if fuzz.token_sort_ratio(s_desc, _norm(p.get("description") or "")) >= thr:
                        matched = True
                        break
        if not matched:
            unmatched.append(s)

    return unmatched
