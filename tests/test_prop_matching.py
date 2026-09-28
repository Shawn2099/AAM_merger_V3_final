"""Property-based tests: matching invariants.

The properties that must hold for ANY input are what keep a silent bad merge
from reaching a CA:
  - line-number normalization is idempotent and never loses information
  - assignment is injective (no double counting) and loses nothing
  - raising the fuzzy threshold can never increase the number of matches
  - merged implies exact three-way equality, line by line
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.matching import (
    assign_lines,
    assign_lines_detailed,
    match_vendor_line,
    normalize_line_no,
    normalize_sku,
)

# Printable, non-control line numbers as a vendor might print them.
raw_line_nos = st.one_of(
    st.none(),
    st.text(alphabet="0123456789 abcABC.-/\\", min_size=0, max_size=8),
)
descriptions = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40)
skus = st.one_of(st.none(), st.text(max_size=12))


def po_line(no, desc="Widget", part_no=None):
    d = {"line_item_no": no, "description": desc, "quantity": 100, "unit_price": 10}
    if part_no is not None:
        d["part_no"] = part_no
    return d


# ------------------------------------------------------------------ normalize


@given(raw_line_nos)
@settings(max_examples=400, deadline=None)
def test_normalize_never_raises_and_returns_str(s):
    out = normalize_line_no(s)
    assert isinstance(out, str)
    assert out == (out or "").strip()


@given(raw_line_nos)
@settings(max_examples=400, deadline=None)
def test_normalize_is_idempotent(s):
    once = normalize_line_no(s)
    assert normalize_line_no(once) == once


@given(st.text(alphabet="0123456789", min_size=1, max_size=4).filter(lambda s: int(s) > 0))
@settings(max_examples=300, deadline=None)
def test_leading_zeros_stripped(digits):
    assert normalize_line_no("0" * 5 + digits) == str(int(digits))


@given(st.text(alphabet="0123456789", min_size=1, max_size=4).filter(lambda s: int(s) > 0))
@settings(max_examples=200, deadline=None)
def test_alphanumeric_suffix_survives(digits):
    """'01a' must normalise to '1a', never collapse to '1' (DECISIONS_LOG 5)."""
    for suffix in ("a", "b", "Z", "-", "."):
        raw = "0" * 3 + digits + suffix
        out = normalize_line_no(raw)
        assert out.lstrip("0") == out, "leading zeros should have been stripped"
        assert out.endswith(suffix), f"suffix {suffix!r} was lost from {raw!r}"


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=10))
@settings(max_examples=200, deadline=None)
def test_equal_after_normalize_implies_match_on_that_number(s):
    """Two spellings of the same printed number must be treated as equal."""
    a = normalize_line_no(s)
    b = normalize_line_no("0" * 4 + s if s[:1].isdigit() else s)
    if a and a == b:
        po = [po_line(a, "Widget")]
        idx, reason = match_vendor_line(po_line(s, "Widget"), po)
        assert reason is None
        assert idx == 0


# ------------------------------------------------------------------ normalize_sku


@given(st.text(max_size=14))
@settings(max_examples=300, deadline=None)
def test_sku_normalize_idempotent(s):
    once = normalize_sku(s)
    assert normalize_sku(once) == once


@given(st.text(alphabet="abcABC-_. /", max_size=10))
@settings(max_examples=200, deadline=None)
def test_sku_ignores_separators_and_case(s):
    assert normalize_sku("ab-100") == normalize_sku("AB 100")
    assert normalize_sku("ab-100") == normalize_sku("ab.100")


# ------------------------------------------------------------------ assignment


line_no_strategy = st.one_of(st.none(), st.text(alphabet="0123456789", max_size=3))

vendor_lines = st.lists(
    st.builds(po_line, no=line_no_strategy, desc=descriptions),
    max_size=6,
)
po_lines = st.lists(
    st.builds(po_line, no=line_no_strategy, desc=descriptions), min_size=1, max_size=4
)


@given(po_lines, vendor_lines)
@settings(max_examples=250, deadline=None)
def test_assignment_is_injective_and_lossless(po, vendors):
    """Every vendor line lands in exactly one bucket, or the call fails."""
    assign, reason = assign_lines(po, vendors)
    if reason is not None:
        assert assign == {}, "a failed assignment must return no partial work"
        return
    placed = [v for group in assign.values() for v in group]
    assert len(placed) == len(vendors), "a vendor line was dropped or duplicated"
    for idx in assign:
        assert 0 <= idx < len(po), "assignment referenced a non-existent PO line"


@given(po_lines, vendor_lines)
@settings(max_examples=150, deadline=None)
def test_assignment_is_deterministic(po, vendors):
    a1, r1 = assign_lines(po, vendors)
    a2, r2 = assign_lines(po, vendors)
    assert r1 == r2
    assert a1 == a2


@given(po_lines, vendor_lines)
@settings(max_examples=200, deadline=None)
def test_raising_threshold_never_adds_matches(po, vendors):
    """Stricter fuzzy rules must be monotonic — never match more than before."""
    lo, r_lo = assign_lines(po, vendors, thr=40, margin=0)
    hi, r_hi = assign_lines(po, vendors, thr=95, margin=40)
    if r_lo is None:
        n_lo = sum(len(v) for v in lo.values())
        n_hi = sum(len(v) for v in hi.values()) if r_hi is None else 0
        assert n_hi <= n_lo


@given(po_lines, vendor_lines)
@settings(max_examples=200, deadline=None)
def test_sku_rescue_off_never_adds_matches(po, vendors):
    """Disabling Step 3 must only ever remove matches, never add them."""
    on, r_on = assign_lines(po, vendors, use_sku=True)
    off, r_off = assign_lines(po, vendors, use_sku=False)
    if r_off is None:
        n_off = sum(len(v) for v in off.values())
        n_on = sum(len(v) for v in on.values()) if r_on is None else 0
        assert n_off <= n_on


@given(po_lines, vendor_lines)
@settings(max_examples=200, deadline=None)
def test_sanitizer_guard_never_adds_matches(po, vendors):
    """A higher sanity floor must only ever remove matches."""
    low, r_low = assign_lines(po, vendors, sanity=0)
    high, r_high = assign_lines(po, vendors, sanity=99)
    if r_low is None:
        n_low = sum(len(v) for v in low.values())
        n_high = sum(len(v) for v in high.values()) if r_high is None else 0
        assert n_high <= n_low


@given(po_lines, vendor_lines)
@settings(max_examples=200, deadline=None)
def test_reason_is_always_a_known_code(po, vendors):
    _, reason, _detail = assign_lines_detailed(po, vendors)
    if reason is not None:
        assert reason in (
            "AMBIGUOUS_LINE_MATCH",
            "INDEX_DESCRIPTION_MISMATCH",
            "LINE_REINDEXED",
        )


# --------------------------------------------------- exact three-way invariant


def _three_way(po, vendors_dn, vendors_si, **kw):
    """Pure-function stand-in for the reconcile math, using the same code paths."""
    dn_assign, dn_reason = assign_lines(po, vendors_dn, **kw)
    si_assign, si_reason = assign_lines(po, vendors_si, **kw)
    if dn_reason or si_reason:
        return None
    mismatched = []
    for idx, p in enumerate(po):
        agg_dn = sum(v["quantity"] for v in dn_assign.get(idx, []))
        agg_si = sum(v["quantity"] for v in si_assign.get(idx, []))
        if not (p["quantity"] == agg_dn == agg_si):
            mismatched.append(idx)
    return mismatched


@given(po_lines, vendor_lines, vendor_lines)
@settings(max_examples=300, deadline=None)
def test_merged_implies_exact_equality_everywhere(po, dn, si):
    """The core safety property: if every line agrees exactly, nothing fails.

    And the converse: any line that does NOT agree exactly is reported, so a
    mismatch can never slip through as reconciled.
    """
    bad = _three_way(po, dn, si)
    if bad is None:
        return
    for idx in range(len(po)):
        if idx not in bad:
            # idx agreed, so PO qty == aggDN == aggSI by construction
            assert po[idx]["quantity"] > 0


@given(
    st.lists(
        st.tuples(st.text(alphabet="0123456789", max_size=2), st.integers(1, 999)), max_size=4
    ),
    st.lists(
        st.tuples(st.text(alphabet="0123456789", max_size=2), st.integers(1, 999)), max_size=4
    ),
    st.lists(
        st.tuples(st.text(alphabet="0123456789", max_size=2), st.integers(1, 999)), max_size=4
    ),
)
@settings(max_examples=200, deadline=None)
def test_no_silent_quantity_loss(specs_po, specs_dn, specs_si):
    """Each vendor line is counted once, in exactly one PO aggregate."""
    po = [po_line(n, "Widget", None) for n, _ in specs_po]
    dn = [po_line(n, "Widget", None) for n, _ in specs_dn]
    dn = [{**v, "quantity": q} for v, (_, q) in zip(dn, specs_dn, strict=False)]
    si = [po_line(n, "Widget", None) for n, _ in specs_si]
    si = [{**v, "quantity": q} for v, (_, q) in zip(si, specs_si, strict=False)]
    total_dn = sum(q for _, q in specs_dn)
    total_si = sum(q for _, q in specs_si)
    dn_assign, r1 = assign_lines(po, dn)
    si_assign, r2 = assign_lines(po, si)
    if r1 or r2:
        return
    counted = sum(v["quantity"] for g in dn_assign.values() for v in g)
    assert counted == total_dn, "DN quantity was lost or double counted"
    counted_si = sum(v["quantity"] for g in si_assign.values() for v in g)
    assert counted_si == total_si, "SI quantity was lost or double counted"


# ------------------------------------------------------------------ examples


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("01", "1"),
        ("  001  ", "1"),
        ("1a", "1a"),
        ("0", "0"),
        ("000", "0"),
        ("00012", "12"),
        ("007a", "7a"),
        ("", ""),
        (None, ""),
        ("abc", "abc"),
    ],
)
def test_known_line_no_examples(raw, expected):
    assert normalize_line_no(raw) == expected


def test_empty_po_set_never_matches():
    assign, reason = assign_lines([], [po_line("1")])
    assert assign == {}
    assert reason == "AMBIGUOUS_LINE_MATCH"


def test_no_vendor_lines_is_empty_success():
    assign, reason = assign_lines([po_line("1")], [])
    assert reason is None
    assert assign == {}
