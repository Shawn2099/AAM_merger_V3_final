"""Property-based tests: quantity sanitizer (DECISIONS_LOG §4).

Properties, not examples — these must hold for *any* generated input, which is
how we catch the silent-drift and false-accept cases a table test misses.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.services.sanitizer import parse_quantity_scaled

# A decimal with at most 2 significant decimals, always strictly positive.
# Real quantities are whole units or occasional .50; 2dp is the product
# contract (PRODUCT doc section 5).
quantities = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("1000000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


def as_plain(d: Decimal) -> str:
    """Plain (non-exponent) string form, which is what a printed document has."""
    return format(d, "f")


def indian_group(plain: str) -> str:
    """Format a plain decimal with Indian digit grouping (1,00,000.500).

    Python's format(d, ',') uses Western grouping (100,000.500), which en_IN
    strict correctly rejects — that is the point, so the test must use real
    Indian grouping.
    """
    neg = plain.startswith("-")
    if neg:
        plain = plain[1:]
    int_part, _, frac = plain.partition(".")
    if len(int_part) <= 3:
        grouped = int_part
    else:
        head, tail = int_part[:-3], int_part[-3:]
        chunks = []
        while len(head) > 2:
            chunks.insert(0, head[-2:])
            head = head[:-2]
        if head:
            chunks.insert(0, head)
        grouped = ",".join(chunks) + "," + tail
    out = grouped + ("." + frac if frac else "")
    return ("-" + out) if neg else out


@given(quantities)
@settings(max_examples=300, deadline=None)
def test_valid_quantity_returns_positive_int(d):
    assert parse_quantity_scaled(as_plain(d)) == int(d * 1000)


@given(quantities)
@settings(max_examples=200, deadline=None)
def test_always_int_and_positive(d):
    out = parse_quantity_scaled(as_plain(d))
    assert isinstance(out, int)
    assert not isinstance(out, bool)
    assert out > 0


@given(quantities, st.integers(min_value=0, max_value=6))
@settings(max_examples=200, deadline=None)
def test_outer_whitespace_irrelevant(d, pad):
    plain = as_plain(d)
    padded = (" " * pad) + plain + ("\t" * pad)
    assert parse_quantity_scaled(padded) == parse_quantity_scaled(plain)


@given(quantities)
@settings(max_examples=300, deadline=None)
def test_monotonic_in_value(d):
    """A larger printed quantity must never parse to a smaller scaled int."""
    bigger = d + Decimal("0.01")
    assume(bigger <= Decimal("1000000"))
    assert parse_quantity_scaled(as_plain(bigger)) > parse_quantity_scaled(as_plain(d))


@given(quantities)
@settings(max_examples=200, deadline=None)
def test_indian_grouping_equivalence(d):
    """1,00,000 and 100000 must agree — grouping is presentation, not value."""
    plain = as_plain(d)
    grouped = indian_group(plain)
    assert parse_quantity_scaled(grouped) == parse_quantity_scaled(plain)


@given(quantities)
@settings(max_examples=200, deadline=None)
def test_round_trip_through_scaled_int(d):
    """parse -> scaled int -> decimal must be lossless at 2dp precision."""
    scaled = parse_quantity_scaled(as_plain(d))
    assert Decimal(scaled) / 1000 == d


# --------------------------------------------------------------------------- rejects


@given(quantities)
@settings(max_examples=200, deadline=None)
def test_rejects_more_than_two_decimals(d):
    """A third significant decimal must be rejected, not silently rounded."""
    bad = as_plain(d) + "5"
    assume("." in bad)
    with pytest.raises(ValueError):
        parse_quantity_scaled(bad)


@given(quantities, st.integers(min_value=0, max_value=8))
@settings(max_examples=200, deadline=None)
def test_trailing_zeros_are_never_rejected(d, extra):
    """Padding a value with trailing zeros must not change accept/reject.

    "12.45", "12.450" and "12.45000000" are one number. A precision check that
    counts characters rather than significant decimals would quarantine
    documents that are perfectly legible, which vendors emit constantly.
    """
    plain = as_plain(d)
    padded = plain + ("0" * extra)
    assert parse_quantity_scaled(padded) == parse_quantity_scaled(plain)


@given(st.integers(min_value=1, max_value=10000))
@settings(max_examples=100, deadline=None)
def test_rejects_zero_and_negative(n):
    for bad in ("0", "0.00", "-1", f"-{n}", "-0.01"):
        with pytest.raises(ValueError):
            parse_quantity_scaled(bad)


@given(st.text())
@settings(max_examples=400, deadline=None)
def test_garbage_only_ever_raises_valueerror(s):
    """Never leak babel/Decimal/TypeError to the caller — always ValueError."""
    try:
        out = parse_quantity_scaled(s)
    except ValueError:
        return
    except Exception as e:  # pragma: no cover - this is the assertion
        raise AssertionError(f"non-ValueError for {s!r}: {type(e).__name__}") from e
    # If it parsed at all, it must be a clean positive int scaled x1000.
    assert isinstance(out, int) and not isinstance(out, bool) and out > 0


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=24))
@settings(max_examples=300, deadline=None)
def test_never_raises_anything_but_valueerror(s):
    try:
        parse_quantity_scaled(s)
    except ValueError:
        pass
    except Exception as e:  # pragma: no cover
        raise AssertionError(f"non-ValueError for {s!r}: {type(e).__name__!r}") from e


@given(st.integers(min_value=3, max_value=12))
@settings(max_examples=60, deadline=None)
def test_inner_space_separator_never_guessed(n):
    """'1 000' style separators must fail loudly, never be silently stripped."""
    n0 = 10**n  # always has a grouping comma, so the space is really a separator
    spaced = f"{n0:,}".replace(",", " ")
    with pytest.raises(ValueError):
        parse_quantity_scaled(spaced)


@pytest.mark.parametrize(
    "s",
    [
        "1 000",  # space as a thousands separator — never guessed
        "1,5",  # ambiguous grouping
        "1.0005",  # four significant decimals
        "1.234",  # three significant decimals (European thousands separator)
        "0.001",  # three significant decimals
        "",
        "   ",
        "INF",
        "inf",
        "NaN",
        "nan",
        "-INF",
        "1e999",  # implausible magnitude
        "1e999999999",  # would be a billion-digit int
        "0x10",
        "1,00,000,000",  # over Indian grouping width
        "12..5",
        "--3",
        "1-2",
        "abc",
    ],
)
def test_known_rejects(s):
    with pytest.raises(ValueError):
        parse_quantity_scaled(s)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # A leading + is unambiguous, so it is accepted and yields the right value.
        ("+5", 5000),
        ("5", 5000),
        (" 5 ", 5000),
        ("5.0", 5000),
        ("5.000", 5000),
        # An explicit underscore separator is unambiguous (unlike a space),
        # so it is accepted rather than rejected.
        ("1_000", 1000000),
        # Unicode decimal digits normalise to the same value.
        ("\u0665", 5000),
        ("1,00,000", 100000000),
    ],
)
def test_accepted_variants(raw, expected):
    """Unambiguous formatting variants must all yield the identical value.

    Note the deliberate asymmetry: a SPACE separator is rejected (it could be
    grouping or a typo, so it is never guessed), while an explicit underscore
    or a Unicode digit is accepted because the value is unambiguous.
    """
    assert parse_quantity_scaled(raw) == expected


@pytest.mark.parametrize("raw", ["100,000", "1,234,567"])
def test_western_grouping_is_rejected_under_en_in(raw):
    """DECIDED 2026-09-29 — accepted limit, not a bug. Do not "fix" unasked.

    en_IN strict demands Indian grouping: 1,00,000, never 100,000. The client
    base is English-language UAE and USA, which print Western grouping, so a
    six-digit quantity would quarantine.

    It fails SAFE (INVALID_QUANTITY -> 0 -> `non_positive_quantity` ->
    quarantine, never a bad merge), and it has never been observed: real
    quantities are whole units or occasional .50. A dual-locale parse (accept
    when en_IN and en_US agree, reject when they disagree) is the fix if it ever
    bites; until then the one-line remedy is `matching.locale: "en_US"` in
    config.yaml, no code change. See AAM_merger_V3_PRODUCT.md section 8.
    """
    with pytest.raises(ValueError):
        parse_quantity_scaled(raw)


@given(quantities)
@settings(max_examples=100, deadline=None)
def test_never_returns_float(d):
    out = parse_quantity_scaled(as_plain(d))
    assert re.fullmatch(r"\d+", str(out)), f"scaled value must be a plain int, got {out!r}"


@given(quantities)
@settings(max_examples=150, deadline=None)
def test_scaled_is_multiple_of_one(d):
    """Values with <=2dp scale by exactly 1000 — no rounding surprises."""
    scaled = parse_quantity_scaled(as_plain(d))
    assert scaled == int(d * 1000)
    assert scaled % 1 == 0
