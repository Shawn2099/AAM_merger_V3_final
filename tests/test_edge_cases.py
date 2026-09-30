"""Adversarial edge cases: hostile inputs, hostile data shapes.

Table-driven rather than generated — each case is a specific, named hazard we
can reason about, so a failure is immediately diagnosable.

Scope note: the hostile-input coverage below used to be aimed at the retired
v20.5 3-step matcher. It is now aimed at the reconciliation rule that actually
runs. See AAM_merger_V3_PRODUCT.md.
"""

from __future__ import annotations

import pytest

from app.services.matching import compare_aggregates, group_by_line_no, normalize_line_no
from app.services.sanitizer import parse_quantity_scaled


def pl(no, desc="Widget", qty=100):
    """A line dict with quantity already scaled x1000, as stored."""
    return {"line_item_no": no, "description": desc, "quantity": qty * 1000}


# --------------------------------------------------------- hostile descriptions

HOSTILE_DESCRIPTIONS = [
    "",  # empty
    " ",  # whitespace only
    "   \t\n  ",  # mixed whitespace
    "\x00null byte",
    "<script>alert(1)</script>",  # HTML injection attempt
    "{{7*7}}",  # template injection attempt
    "../../etc/passwd",  # path traversal text
    "a" * 5000,  # very long
    "™ © ® 日本語 हिन्दी",  # multilingual
    "\U0001f642\U0001f643",  # emoji
    "desc\ttabbed",
    "line\nbreak",
    "zero width",  # zero-width space
    "%20%3C",  # url-encoded
    "'; DROP TABLE line_items; --",  # sql injection text
    "́combining",  # combining marks
    "\U0001d56a\U0001d56f\U0001d55a",  # mathematical alphanumerics
    "﻿bom prefixed",  # BOM
]


@pytest.mark.parametrize("desc", HOSTILE_DESCRIPTIONS)
def test_hostile_description_never_raises(desc):
    """Fuzzy description fallback must never crash, whatever text the VLM returns."""
    _pt, _vt, orphans, fail = group_by_line_no(
        [pl("1", desc)], [{"line_item_no": None, "description": desc, "quantity": 100_000}]
    )
    assert fail is None
    assert isinstance(orphans, list)


@pytest.mark.parametrize("desc", HOSTILE_DESCRIPTIONS)
def test_hostile_description_on_numbered_rows_never_raises(desc):
    """A printed number is used directly; description is not even consulted."""
    po_totals, vendor_totals, orphans, fail = group_by_line_no([pl("1", desc)], [pl("1", desc)])
    assert fail is None
    assert orphans == []
    assert compare_aggregates(po_totals, vendor_totals, orphans) == []


# ------------------------------------------------------------- hostile line nos

HOSTILE_LINE_NOS = [
    None,
    "",
    " ",
    "\t",
    "\n",
    "0",
    "00",
    "0000000",
    "-1",
    "-0",
    "1.0",
    "1,0",
    "1/2",
    "1\\2",
    "١٢٣",  # arabic-indic digits
    "1e5",
    "0x1",
    "1 ",
    " 1",
    "1_0",
    "1a",
    "A1",
    "1A",
    "999999999999",
    "-" * 20,
    "1" * 50,
    "None",
    "null",
    "NULL",
    "undefined",
    "NaN",
    "True",
    "\u0661",  # arabic-indic digit one
    "\u06f1\u06f2",  # extended arabic-indic
    "\u00b2",  # superscript two
    "\u00bd",  # vulgar fraction one half
]


@pytest.mark.parametrize("raw", HOSTILE_LINE_NOS)
def test_hostile_line_no_never_raises(raw):
    out = normalize_line_no(raw)
    assert isinstance(out, str)


@pytest.mark.parametrize("raw", HOSTILE_LINE_NOS)
def test_hostile_line_no_grouping_never_raises(raw):
    """Grouping must return a well-formed result for any printed number."""
    po_totals, vendor_totals, orphans, fail = group_by_line_no([pl("1")], [pl(raw)])
    assert fail is None
    assert isinstance(po_totals, dict)
    assert isinstance(vendor_totals, dict)
    assert isinstance(orphans, list)
    assert isinstance(compare_aggregates(po_totals, vendor_totals, orphans), list)


# ------------------------------------------------------------- data shapes


def test_many_vendor_lines_into_one_po_line_sums_correctly():
    po_totals, vendor_totals, orphans, fail = group_by_line_no(
        [pl("1", qty=100)], [pl("1", qty=1) for _ in range(100)]
    )
    assert fail is None and orphans == []
    assert vendor_totals["1"] == 100_000
    assert compare_aggregates(po_totals, vendor_totals, orphans) == []


def test_duplicate_vendor_line_numbers_all_land_together():
    po_totals, vendor_totals, orphans, _ = group_by_line_no(
        [pl("1", qty=100)], [pl("1", qty=30), pl("1", qty=30), pl("1", qty=40)]
    )
    assert vendor_totals["1"] == 100_000
    assert compare_aggregates(po_totals, vendor_totals, orphans) == []


def test_duplicate_po_line_numbers_are_summed_on_the_po_side():
    """Two PO rows printed as line 1 become one line 1 group of their sum."""
    po_totals, vendor_totals, orphans, fail = group_by_line_no(
        [pl("1", qty=40), pl("1", qty=60)], [pl("1", qty=100)]
    )
    assert fail is None and orphans == []
    assert po_totals == {"1": 100_000}
    assert compare_aggregates(po_totals, vendor_totals, orphans) == []


def test_known_limitation_conflicting_text_on_one_number_is_summed():
    """PINS A KNOWN LIMITATION — do not "fix" this without a product decision.

    The retired matcher quarantined a set where two rows shared a line_item_no
    but described different items (old FR-8.4). That check is deliberately not
    part of this product: quantities are the only signal. The consequence is
    that conflicting rows are summed, so a wrong-item aggregate can in
    principle reconcile. See AAM_merger_V3_PRODUCT.md, "Accepted limitations".
    """
    po_totals, vendor_totals, orphans, _ = group_by_line_no(
        [pl("1", "Alpha", qty=100)],
        [pl("1", "Alpha", qty=50), pl("1", "Beta", qty=50)],
    )
    assert orphans == []
    # both rows land on line 1 and the set reconciles
    assert vendor_totals == {"1": 100_000}
    assert compare_aggregates(po_totals, vendor_totals, orphans) == []


def test_empty_po_set_with_vendor_lines_fails_cleanly():
    po_totals, vendor_totals, orphans, fail = group_by_line_no([], [pl("1")])
    assert fail is None
    assert po_totals == {}
    assert len(orphans) == 1
    assert orphans[0]["why"] == "no_po_line_with_this_number"
    assert compare_aggregates(po_totals, vendor_totals, orphans)[0]["po_qty"] is None


def test_very_many_po_lines_terminates():
    """No quadratic blow-up or recursion on a large PO.

    500 PO lines with one delivered: 499 come back as quantity mismatches,
    which is the correct answer, not a hang.
    """
    po = [pl(str(i), f"Item {i}") for i in range(500)]
    po_totals, vendor_totals, orphans, fail = group_by_line_no(po, [pl("250", "Item 250")])
    assert fail is None
    assert orphans == []
    diffs = compare_aggregates(po_totals, vendor_totals, orphans)
    assert len(diffs) == 499
    assert all(d["reason"] == "quantity_mismatch" for d in diffs)


def test_huge_line_numbers_terminate():
    _pt, _vt, _orphans, fail = group_by_line_no([pl("1000000")], [pl("100000")])
    assert fail is None


# ------------------------------------------------------------- sanitizer bombs


@pytest.mark.parametrize(
    "raw",
    [
        "9" * 400,  # huge but finite digit string
        "0." + "0" * 500 + "1",  # extreme precision
        "1e308",  # near float overflow
        "1e-308",
        "0.000",  # zero with decimals
        "00.00",
        "00000",
    ],
)
def test_sanitizer_never_hangs_or_crashes(raw):
    try:
        out = parse_quantity_scaled(raw)
        assert isinstance(out, int) and out > 0
    except ValueError:
        pass
    except Exception as e:  # pragma: no cover
        raise AssertionError(f"unexpected {type(e).__name__} for {raw!r}") from e


@pytest.mark.parametrize(
    ("raw", "actual"),
    [
        ("1,00,000", 100000),  # 1 lakh
        ("1,00,00,000", 10000000),  # 1 crore
        ("10,00,00,000", 100000000),  # 10 crore
        ("99,99,99,999", 999999999),  # just under the 1e12 guard
    ],
)
def test_sanitizer_large_but_plausible_is_accepted(raw, actual):
    assert parse_quantity_scaled(raw) == actual * 1000


@pytest.mark.parametrize(
    "raw",
    [
        "1,00,00,00,00,00,000",  # 1e13 actual
        "1,00,00,00,00,00,00,000",  # 1e15 actual
        "1e13",
        "9" * 30,
    ],
)
def test_sanitizer_rejects_beyond_the_guard(raw):
    """The guard sits above 1e12; anything larger is treated as a mis-read.

    This is what stops a single hallucinated field from being scaled into a
    number that is expensive to hold or nonsense to reconcile.
    """
    with pytest.raises(ValueError):
        parse_quantity_scaled(raw)
