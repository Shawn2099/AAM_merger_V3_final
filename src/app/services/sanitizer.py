"""Quantity sanitizer — babel strict locale parsing → scaled int x1000 (DECISIONS_LOG §4).

- Default locale en_IN, strict=True, locale comes from config (matching.locale).
- Inner spaces / bad grouping fail loudly (NumberFormatError → ValueError).
- Strict 3-decimal ceiling; zero/negative rejected.
"""

from __future__ import annotations

from decimal import Decimal

from babel.numbers import NumberFormatError, parse_decimal


def parse_quantity_scaled(raw_qty_str: str, locale: str = "en_IN") -> int:
    """Parse a vendor quantity string to scaled integer (x1000).

    Raises ValueError (INVALID_QUANTITY) for: empty/whitespace, unparseable
    (bad grouping, inner spaces like "1 000"), >3 decimals ("1.0005"),
    zero or negative.
    """
    if raw_qty_str is None or not str(raw_qty_str).strip():
        raise ValueError("Quantity string is empty.")
    cleaned = str(raw_qty_str).strip()
    try:
        val = parse_decimal(cleaned, locale=locale, strict=True)
    except (NumberFormatError, ValueError) as e:
        raise ValueError(f"Invalid numeric format: {raw_qty_str!r}") from e
    # babel's strict parse still accepts INF / NaN / -INF. They are not
    # quantities: `val <= 0` is False for NaN and the exponent tuple for
    # Infinity/NaN is a *string*, which would raise TypeError below and escape
    # the clean quarantine path. Reject non-finite values explicitly.
    if not val.is_finite():
        raise ValueError(f"Quantity is not a finite number: {raw_qty_str!r}")
    if val <= 0:
        raise ValueError(f"Quantity must be strictly positive: {raw_qty_str!r}")
    # Magnitude guard. babel accepts exponent forms like "1e999999999", and
    # int(val * 1000) on that would try to materialise a billion-digit integer
    # — a memory bomb from a single mis-read field. The ceiling is 1e12
    # actual units (adjusted() > 12 means >= 1e13), which is ~10 billion times
    # larger than any plausible single line item, so real documents are
    # unaffected while garbage is rejected before it is scaled.
    if val.adjusted() > 12:
        raise ValueError(f"Quantity is implausibly large: {raw_qty_str!r}")
    d = Decimal(str(val))
    if d.as_tuple().exponent < -3:
        raise ValueError(f"Quantity exceeds 3 decimal places: {raw_qty_str!r}")
    return int(val * 1000)
