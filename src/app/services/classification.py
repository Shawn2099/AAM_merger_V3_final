def classify(extracted: dict) -> str:
    """Keyword stub for local dev — prod VLM uses PDF text via instructor."""
    t = (extracted.get("raw") or "").lower()
    # Filename prefixes for local samples — prod VLM uses PDF text
    if t.startswith("do_") or t.startswith("do-") or t.startswith("do "):
        return "DN"
    if "purchase order" in t:
        return "PO"
    if "delivery note" in t or "delivery" in t:
        return "DN"
    if "siv" in t:
        # SIV samples are SI (SIV-ARS-..., SIV-RAK, SIV-DTS)
        return "SI"
    if "tax invoice" in t or "sales invoice" in t or "invoice" in t:
        return "SI"
    if "customs declaration" in t or t.strip() == "customs":
        return "CUSTOMS"
    if "customs" in t:
        return "CUSTOMS"
    if "awb" in t or "shipping" in t:
        return "SHIPPING"
    if "commercial invoice" in t:
        return "COMMERCIAL_INVOICE"
    if "combined" in t:
        return "COMBINED"
    # filename fallbacks for samples without PDF text (data/input/*.pdf)
    if t.startswith("do_") or t.startswith("do-") or "do_siv" in t:
        return "DN"
    if "(2 dns)" in t or "(3 dns)" in t:
        return "DN"
    if "po1234" in t or "po999" in t or t.startswith("po"):
        return "PO"
    return "UNKNOWN"


def is_manual_only(t: str) -> bool:
    return t in ("CUSTOMS", "SHIPPING", "COMMERCIAL_INVOICE")
