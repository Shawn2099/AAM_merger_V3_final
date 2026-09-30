"""Sanitizer tests - en_IN strict, 2 significant decimals, space fails loudly."""

import pytest

from app.services.sanitizer import parse_quantity_scaled


def test_indian_grouping():
    assert parse_quantity_scaled("1,00,000") == 100000000
    assert parse_quantity_scaled("10,00,000") == 1000000000
    assert parse_quantity_scaled("1,000") == 1000000


def test_plain_and_decimal():
    assert parse_quantity_scaled("50") == 50000
    assert parse_quantity_scaled("12.5") == 12500
    assert parse_quantity_scaled("350.00") == 350000
    assert parse_quantity_scaled("  50  ") == 50000  # outer trim ok


def test_whole_and_half_are_the_real_shapes():
    """Real quantities are whole units or occasionally .50. Both are fine."""
    assert parse_quantity_scaled("1") == 1000
    assert parse_quantity_scaled("1.50") == 1500
    assert parse_quantity_scaled("12.45") == 12450
    assert parse_quantity_scaled("0.01") == 10


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Trailing zeros are formatting, not precision. Vendors emit these
        # constantly (Excel / OCR round-trips) and they are all the same value.
        ("12.45000000", 12450),
        ("12.4500000", 12450),
        ("12.45000", 12450),
        ("12.450", 12450),
        ("5.000", 5000),
        ("5.00000", 5000),
        ("0.500", 500),
        ("100.10", 100100),
        ("100.100", 100100),
    ],
)
def test_trailing_zeros_are_normalised_not_rejected(raw, expected):
    """A naive 'reject more than 2 decimals' would reject all of these.

    They are not imprecise - they are the same number written out. Rejecting
    them would quarantine perfectly legible documents.
    """
    assert parse_quantity_scaled(raw) == expected


@pytest.mark.parametrize("raw", ["1.234", "1.2345", "0.001", "12.345678", "1.00000001"])
def test_three_or_more_significant_decimals_are_rejected(raw):
    """After trailing zeros are stripped, a third significant decimal means
    the printed string is something else.

    The realistic case is a European thousands separator: "1.234" printed by a
    vendor who means 1234 would otherwise be read as 1.234 - a 1000x error.
    Rejecting routes the set to quarantine, which is the safe direction.
    """
    with pytest.raises(ValueError):
        parse_quantity_scaled(raw)


def test_space_separator_fails_loudly():
    with pytest.raises(ValueError):
        parse_quantity_scaled("1 000")


def test_bad_grouping_fails_loudly():
    with pytest.raises(ValueError):
        parse_quantity_scaled("1,5")


def test_rejects_non_positive_and_empty():
    for bad in ["0", "0.00", "-5", "", "   ", None]:
        with pytest.raises(ValueError):
            parse_quantity_scaled(bad)


def test_rejects_garbage():
    for bad in ["abc", "12..5", "--3"]:
        with pytest.raises(ValueError):
            parse_quantity_scaled(bad)
