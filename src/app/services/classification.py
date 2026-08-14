def classify(extracted: dict) -> str:
    # real: VLM-based; this stub uses keywords for TDD
    t = (extracted.get("raw") or "").lower()
    if "purchase order" in t:
        return "PO"
    if "delivery note" in t:
        return "DN"
    if "tax invoice" in t or "sales invoice" in t:
        return "SI"
    if "customs declaration" in t:
        return "CUSTOMS"
    if "awb" in t or "shipping" in t:
        return "SHIPPING"
    if "commercial invoice" in t:
        return "COMMERCIAL_INVOICE"
    if "combined" in t:
        return "COMBINED"
    return "UNKNOWN"


def is_manual_only(t: str) -> bool:
    return t in ("CUSTOMS", "SHIPPING", "COMMERCIAL_INVOICE")
