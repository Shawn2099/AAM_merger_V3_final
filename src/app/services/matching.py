import re

from rapidfuzz import fuzz


def _norm(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    # split digit<->letter boundary so "10kg" -> "10 kg" (FR-8 fuzzy robustness)
    s = re.sub(r"(\d)([A-Za-z])", r"\1 \2", s)
    s = re.sub(r"([A-Za-z])(\d)", r"\1 \2", s)
    return s


def match_line(po, dn_lines, si_lines, thr=85):
    # if po has line_no, match exactly
    if po.get("line_item_no"):
        cands = [d for d in dn_lines if d.get("line_item_no") == po["line_item_no"]]
        if len(cands) >= 2 and len({c["description"] for c in cands}) > 1:
            return {"matched": False, "quarantine": True}
        if cands:
            return {"matched": True, "quarantine": False}
        return {"matched": False, "quarantine": True}
    # fallback fuzzy — rapidfuzz token_sort_ratio >= thr (FR-8.1-8.5)
    po_desc = _norm(po.get("description") or "")
    for d in dn_lines:
        if (
            not d.get("line_item_no")
            and fuzz.token_sort_ratio(po_desc, _norm(d.get("description") or "")) >= thr
        ):
            return {"matched": True, "quarantine": False}
    return {"matched": False, "quarantine": True}
