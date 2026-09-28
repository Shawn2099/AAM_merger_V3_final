"""Adversarial edge cases: hostile inputs, hostile data shapes.

These are table-driven rather than generated — each case is a specific, named
hazard we can reason about, so a failure is immediately diagnosable.
"""

from __future__ import annotations

import pytest

from app.services.matching import (
    assign_lines,
    match_vendor_line,
    normalize_line_no,
    normalize_sku,
)
from app.services.reconciliation import check_price, reconcile
from app.services.sanitizer import parse_quantity_scaled


def pl(no, desc="Widget", qty=100, price=10, part_no=None):
    d = {"line_item_no": no, "description": desc, "quantity": qty, "unit_price": price}
    if part_no is not None:
        d["part_no"] = part_no
    return d


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
    "\u2122 \u00a9 \u00ae \u65e5\u672c\u8a9e \u0939\u093f\u0928\u094d\u0926\u0940",  # multilingual
    "\u1f642\u1f643",  # emoji
    "desc\ttabbed",
    "line\nbreak",
    "zero width",  # zero-width space
    "%20%3C",  # url-encoded
    "'; DROP TABLE line_items; --",  # sql injection text
    "\u0301combining",  # combining marks
    "\U0001d56a\U0001d56f\U0001d55a",  # mathematical alphanumerics
    "\ufeffbom prefixed",  # BOM
]


@pytest.mark.parametrize("desc", HOSTILE_DESCRIPTIONS)
def test_hostile_description_never_raises(desc):
    """Fuzzy scoring must never crash, whatever text the VLM returns."""
    idx, reason = match_vendor_line(pl("1", desc), [pl("1", desc)])
    assert idx in (0, None)
    assert reason is None or isinstance(reason, str)


@pytest.mark.parametrize("desc", HOSTILE_DESCRIPTIONS)
def test_hostile_description_assign_never_raises(desc):
    assign, reason = assign_lines([pl("1", desc)], [pl("1", desc)])
    assert reason is None or reason in ("AMBIGUOUS_LINE_MATCH", "INDEX_DESCRIPTION_MISMATCH")
    if reason is None:
        assert sum(len(v) for v in assign.values()) == 1


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
    "\u0661\u0662\u0663",  # arabic-indic digits
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
def test_hostile_line_no_assign_never_raises(raw):
    _assign, reason = assign_lines([pl("1")], [pl(raw)])
    assert reason is None or isinstance(reason, str)


# ------------------------------------------------------------- hostile SKUs


HOSTILE_SKUS = [
    None,
    "",
    " ",
    "---",
    "___",
    "///",
    "..",
    "../..",
    "\x00",
    "A" * 200,
    "AB-100",
    " ab-100 ",
    "\uff21\uff22\uff0d\uff11\uff10\uff10",  # fullwidth
    "ab\u202e100",  # RTL override
    "1",
    "0",
    "-",
    "1-2-3",
]


@pytest.mark.parametrize("raw", HOSTILE_SKUS)
def test_hostile_sku_never_raises(raw):
    assert isinstance(normalize_sku(raw), str)


def test_sku_rescue_cannot_cross_match_two_po_lines():
    """Two PO lines sharing a SKU must never let a third line resolve."""
    po = [pl("1", "Alpha", part_no="AB-100"), pl("2", "Beta", part_no="AB100")]
    idx, reason = match_vendor_line(pl(None, "Reworded", part_no="ab100"), po)
    assert idx is None
    assert reason == "AMBIGUOUS_LINE_MATCH"


def test_sku_rescue_empty_sku_never_matches_everything():
    """An absent/blank SKU must not be treated as a shared identifier."""
    po = [pl("1", "Alpha"), pl("2", "Beta")]
    idx, reason = match_vendor_line(pl(None, "Totally different", part_no="  "), po)
    assert idx is None
    assert reason == "AMBIGUOUS_LINE_MATCH"


# ------------------------------------------------------------- data shapes


def test_many_vendor_lines_into_one_po_line_sums_correctly():
    po = [pl("1", "Widget", qty=100)]
    vendors = [pl("1", "Widget", qty=1) for _ in range(100)]
    assign, reason = assign_lines(po, vendors)
    assert reason is None
    assert sum(v["quantity"] for v in assign[0]) == 100


def test_duplicate_vendor_line_numbers_all_land_together():
    po = [pl("1", "Widget", qty=100)]
    vendors = [pl("1", "Widget", qty=30), pl("1", "Widget", qty=30), pl("1", "Widget", qty=40)]
    assign, reason = assign_lines(po, vendors)
    assert reason is None
    assert sum(v["quantity"] for v in assign[0]) == 100


def test_duplicate_po_line_numbers_with_similar_text_pick_one_stably():
    """Two PO lines sharing a printed number: the winner must be stable.

    With genuinely different descriptions the sanity guard rejects the match
    outright (see the sibling test); when the text is close enough to pass, the
    tie-break is first-wins, and that choice must not vary between runs.
    """
    po = [pl("1", "Steel Widget Large"), pl("1", "Steel Widget Large")]
    results = {match_vendor_line(pl("1", "Steel Widget Large"), po)[0] for _ in range(5)}
    assert len(results) == 1, "tie-break is not deterministic"
    assert results.pop() in (0, 1)


def test_duplicate_po_line_numbers_with_conflicting_text_is_rejected():
    """The dangerous case: one printed number, two different items.

    Must NOT silently resolve to either line. It rejects, and (since one of the
    two genuinely matches) offers that line as a suggestion for a human.
    """
    po = [pl("1", "Alpha"), pl("1", "Beta")]
    idx, reason = match_vendor_line(pl("1", "Alpha"), po)
    assert idx is None
    assert reason in ("INDEX_DESCRIPTION_MISMATCH", "LINE_REINDEXED")


def test_empty_po_set_with_vendor_lines_fails_cleanly():
    assign, reason = assign_lines([], [pl("1")])
    assert assign == {}
    assert reason == "AMBIGUOUS_LINE_MATCH"


def test_very_many_po_lines_terminates():
    po = [pl(str(i), f"Item {i}") for i in range(500)]
    idx, reason = match_vendor_line(pl("250", "Item 250"), po)
    assert reason is None
    assert idx == 250


def test_step10_does_not_loop_or_recurse():
    """Step-10 mapping is single-pass; huge/odd numbers must terminate."""
    po = [pl("1000000", "Widget")]
    idx, reason = match_vendor_line(pl("100000", "Widget"), po)
    assert idx in (0, None)
    assert reason is None or isinstance(reason, str)


# ------------------------------------------------------------- reconcile math


@pytest.mark.parametrize(
    ("po_q", "dn_q", "si_q"),
    [
        (0, 0, 0),
        (1, 0, 0),
        (100, 0, 0),
        (0, 100, 100),
        (100, 100, 0),
        (100, 100, 100),
        (100, 101, 100),
        (100, 100, 99),
        (1, 1, 1),
        (-1, -1, -1),
        (100, -100, 100),
        (999999999999, 999999999999, 999999999999),
        (1, 1, 2),
        (2, 1, 1),
    ],
)
def test_reconcile_never_raises_and_agrees_only_on_exact(po_q, dn_q, si_q):
    res = reconcile(po_q, dn_q, si_q)
    assert "ok" in res and "quarantine" in res
    if po_q <= 0 or dn_q <= 0 or si_q <= 0 or po_q != dn_q or po_q != si_q:
        assert res["ok"] is False


@pytest.mark.parametrize("a", [0, 1, -1, 10**15, -(10**15)])
@pytest.mark.parametrize("b", [0, 1, -1, 10**15, -(10**15)])
def test_check_price_never_raises(a, b):
    assert check_price(a, b)["flag"] == (a != b)


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
