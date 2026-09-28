"""Sanitizer tests — en_IN strict, space fails loudly (DECISIONS_LOG §4)."""

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


def test_rejects_four_decimals():
    with pytest.raises(ValueError):
        parse_quantity_scaled("1.0005")


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
