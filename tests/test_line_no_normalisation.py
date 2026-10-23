"""Line-number normalisation: dotted sub-lines are distinct identifiers.

Leading zeros are stripped only at the start of a number and after '-', '_' or
'/'. A '.' segment is kept verbatim, so PO sub-lines '1.01' and '1.1' never
share a key and a single vendor row cannot satisfy both.
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


def test_leading_zeros_still_stripped_at_start_and_after_separators():
    assert normalize_line_no("01") == "1"
    assert normalize_line_no(" 001 ") == "1"
    assert normalize_line_no("01-01") == "1-1"
    assert normalize_line_no("1-1") == "1-1"


def test_dotted_sub_line_zeros_are_not_stripped():
    assert normalize_line_no("1.05") == "1.05"
    assert normalize_line_no("1.01") == "1.01"
    assert normalize_line_no("1.1") == "1.1"
    assert normalize_line_no("1.05") != normalize_line_no("1.5")
    assert normalize_line_no("1.01") != normalize_line_no("1.1")


def test_missing_line_number_is_empty_key():
    assert normalize_line_no("") == ""
    assert normalize_line_no(None) == ""


def test_distinct_dotted_sub_lines_do_not_merge_into_one_vendor_row():
    """PO 1.01 (qty 2) and 1.1 (qty 3); vendor row 1.1 (qty 5).

    The old normalisation mapped 1.01 -> 1.1, summing both PO lines to 5 and
    passing the single vendor row. Each PO line must stand on its own, so the
    set cannot reconcile.
    """
    po_totals, vendor_totals, orphans, fail = group_by_line_no(
        [_ln("1.01", qty=2), _ln("1.1", qty=3)],
        [_ln("1.1", qty=5)],
    )
    assert fail is None
    assert orphans == []
    assert po_totals == {"1.01": 2_000, "1.1": 3_000}
    assert vendor_totals == {"1.1": 5_000}
    diffs = compare_aggregates(po_totals, vendor_totals, orphans)
    assert {d["line"] for d in diffs} == {"1.01", "1.1"}
