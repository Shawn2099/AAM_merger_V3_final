"""Quantity sanitizer - babel strict locale parsing -> exact integer scaled x1000.

Contract (PRODUCT doc section 5):
  - Default locale en_IN, strict=True, locale comes from config (matching.locale).
  - Trailing zeros are NOT precision. "12.45000000" and "5.000" are 12.45 and 5,
    so they are normalised before the precision check rather than rejected.
    Vendors emit these constantly (Excel/OCR round-trips), and rejecting them
    would quarantine documents that are perfectly legible.
  - After normalisation, at most 2 decimal places. Real quantities are whole
    units or occasional .50; a third significant decimal means the printed
    string is something else - most often a European thousands separator
    ("1.234" meaning 1234), which would otherwise be mis-scaled by 1000x.
  - Inner spaces / bad grouping fail loudly (NumberFormatError -> ValueError).
    A space is never guessed: it could be a separator or a typo.
  - Zero or negative rejected.

Storage stays scaled x1000 so existing databases and the column semantics are
unchanged. With a 2dp input contract the low two digits are always zero, which
is harmless - an exact integer is still what every comparison operates on.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from babel.numbers import NumberFormatError, parse_decimal

#: Decimal places permitted after trailing zeros are stripped. See module
#: docstring. Changing this is a product decision, not a tuning knob.
MAX_DECIMAL_PLACES = 2


def parse_quantity_scaled(raw_qty_str: str, locale: str = "en_IN") -> int:
    """Parse a vendor quantity string to a scaled integer (x1000).

    Raises ValueError for: empty/whitespace, unparseable (bad grouping, inner
    spaces like "1 000"), more than MAX_DECIMAL_PLACES significant decimals
    ("1.2345", "1.234"), zero or negative, non-finite, or implausible magnitude.
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
    # - a memory bomb from a single mis-read field. PRODUCT §5 rejects anything
    # above 1e12 actual units; the old `adjusted() > 12` only rejected >= 1e13,
    # ten times looser than the contract.
    if val > Decimal("1e12"):
        raise ValueError(f"Quantity is implausibly large: {raw_qty_str!r}")
    d = Decimal(str(val))
    # Trailing zeros are formatting, not precision: "12.45000000" is 12.45 and
    # "5.000" is 5. Normalise first, then judge what is left.
    normalised = d.normalize()
    exponent = normalised.as_tuple().exponent
    decimals = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    if decimals > MAX_DECIMAL_PLACES:
        raise ValueError(
            f"Quantity has {decimals} significant decimals (max "
            f"{MAX_DECIMAL_PLACES}): {raw_qty_str!r}"
        )
    return int(val * 1000)


def parse_price_scaled(raw_price_str: str | None) -> int:
    """Parse a printed unit price to a scaled integer (x1000).

    Prices are documentation only (they never decide a verdict), so this is
    deliberately more permissive than the quantity parser: signed values,
    more than two decimals, and zero are all real in vendor documents
    (per-metre rates, credit lines, free-of-charge lines). The old path reused
    `parse_quantity_scaled`, which turned "0.125" and "-250.00" into 0 — a
    silent wrong price in the packet.
    """
    if raw_price_str is None or not str(raw_price_str).strip():
        return 0
    cleaned = str(raw_price_str).strip().replace(",", "")
    try:
        val = Decimal(cleaned)
    except Exception:
        return 0
    if not val.is_finite():
        return 0
    # Keep the column's x1000 contract; half-up is the least surprising rule
    # for a price with more precision than the column can hold.
    return int((val * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
