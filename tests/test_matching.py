"""Tests for the reconciliation rule: group by line number, sum, compare.

These are the only matching tests in the project. The v20.5 3-step matcher
(SKU rescue, reindex detection, ERP step-10 alignment, description sanity
guard) was retired — see AAM_merger_V3_PRODUCT.md — and the tests that covered
it were removed with it rather than left failing.
"""

from __future__ import annotations

from app.services.matching import (
    compare_aggregates,
    group_by_line_no,
    normalize_line_no,
)


def _ln(no, desc="Widget", qty=100):
    """A line dict with the quantity already scaled x1000, as stored."""
    return {"line_item_no": no, "description": desc, "quantity": qty * 1000}


# --- normalize_line_no -----------------------------------------------------


def test_normalize_line_no_strips_whitespace_and_leading_zeros():
    assert normalize_line_no("01") == "1"
    assert normalize_line_no(" 001 ") == "1"
    assert normalize_line_no("1") == "1"
    assert normalize_line_no("0") == "0"


def test_normalize_line_no_preserves_alphanumeric_forms():
    """Vendors print '1a', '1-1', and '01-01'; these must normalize correctly."""
    assert normalize_line_no("1a") == "1a"
    assert normalize_line_no("01a") == "1a"
    assert normalize_line_no("1-1") == "1-1"
    assert normalize_line_no("01-01") == "1-1"


def test_normalize_line_no_missing_is_empty_key():
    assert normalize_line_no("") == ""
    assert normalize_line_no(None) == ""


# --- group_by_line_no ------------------------------------------------------


def test_split_delivery_sums_into_one_po_line():
    """One PO line delivered across two DN rows reconciles as a single line."""
    po_totals, vendor_totals, orphans, fail = group_by_line_no(
        [_ln("1", qty=100)], [_ln("1", qty=40), _ln("1", qty=60)]
    )
    assert fail is None
    assert orphans == []
    assert po_totals == {"1": 100_000}
    assert vendor_totals == {"1": 100_000}
    assert compare_aggregates(po_totals, vendor_totals, orphans) == []


def test_leading_zero_line_numbers_reconcile_across_documents():
    po_totals, vendor_totals, orphans, fail = group_by_line_no(
        [_ln("1", qty=100)], [_ln("01", qty=100)]
    )
    assert fail is None and orphans == []
    assert compare_aggregates(po_totals, vendor_totals, orphans) == []


def test_shortfall_is_reported_as_a_quantity_mismatch():
    po_totals, vendor_totals, orphans, fail = group_by_line_no(
        [_ln("1", qty=100)], [_ln("1", qty=40)]
    )
    assert fail is None and orphans == []
    diffs = compare_aggregates(po_totals, vendor_totals, orphans)
    assert len(diffs) == 1
    assert diffs[0] == {
        "line": "1",
        "po_qty": 100_000,
        "vendor_qty": 40_000,
        "reason": "quantity_mismatch",
    }


def test_over_delivery_is_reported_as_a_quantity_mismatch():
    po_totals, vendor_totals, orphans, _ = group_by_line_no(
        [_ln("1", qty=100)], [_ln("1", qty=250)]
    )
    diffs = compare_aggregates(po_totals, vendor_totals, orphans)
    assert diffs[0]["reason"] == "quantity_mismatch"
    assert diffs[0]["vendor_qty"] == 250_000


def test_vendor_line_with_no_po_counterpart_is_an_orphan():
    """A delivered line that resolves nowhere is an identity failure."""
    po_totals, vendor_totals, orphans, _ = group_by_line_no(
        [_ln("1", qty=100)], [_ln("1", qty=100), _ln("99", desc="Rogue item", qty=5)]
    )
    assert len(orphans) == 1
    assert orphans[0]["why"] == "no_po_line_with_this_number"
    # orphans are reported in place of quantity diffs
    diffs = compare_aggregates(po_totals, vendor_totals, orphans)
    assert diffs[0]["po_qty"] is None
    assert diffs[0]["reason"] == "no_po_line_with_this_number"


def test_unnumbered_vendor_row_falls_back_to_description():
    """No usable line number -> try the description, and only then."""
    po_totals, vendor_totals, orphans, _ = group_by_line_no(
        [_ln("1", desc="Hexagon head bolt M12", qty=100)],
        [{"line_item_no": None, "description": "hexagon head bolt m 12", "quantity": 100_000}],
    )
    assert orphans == []
    assert vendor_totals == {"1": 100_000}
    assert compare_aggregates(po_totals, vendor_totals, orphans) == []


def test_unnumbered_row_with_no_similar_description_is_an_orphan():
    _po, _vt, orphans, _ = group_by_line_no(
        [_ln("1", desc="Hexagon head bolt M12")],
        [{"line_item_no": None, "description": "Copper pipe fitting", "quantity": 5_000}],
    )
    assert len(orphans) == 1
    assert orphans[0]["why"] == "no_line_number_and_no_description_match"


def test_description_never_overrides_a_real_line_number():
    """A printed number is authoritative even when the text reads like another
    line. This is the rule that stops a wrong-item aggregate shipping."""
    po_totals, vendor_totals, orphans, _ = group_by_line_no(
        [_ln("1", desc="Widget Alpha"), _ln("2", desc="Widget Beta")],
        [_ln("1", desc="Widget Beta", qty=100)],
    )
    assert orphans == []
    # it landed on line 1, not line 2
    assert vendor_totals == {"1": 100_000}
    assert compare_aggregates(po_totals, vendor_totals, orphans)[0]["line"] == "2"


def test_po_line_without_a_line_number_fails_the_set():
    """An unaddressable PO line is unresolvable by definition."""
    _pt, _vt, _orphans, fail = group_by_line_no([_ln("1"), _ln(None)], [_ln("1")])
    assert fail == "po_line_missing_line_item_no"


def test_missing_line_number_fails_even_when_other_lines_are_fine():
    _pt, _vt, _orphans, fail = group_by_line_no([_ln("1"), _ln("2"), _ln("")], [_ln("1"), _ln("2")])
    assert fail == "po_line_missing_line_item_no"
