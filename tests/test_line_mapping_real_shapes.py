"""Line-number fallback on sanitised real sets (line keys and quantities only, no names)."""

from __future__ import annotations

from _lm_helpers import rows, run

K = 1000


def _r(pairs, doc=None):
    return rows([(k, q * K) for k, q in pairs], doc=doc)


def _same(po, vendor):
    return _r(po), _r(vendor), _r(vendor)


def test_real_set39_single_line_step10_merges():
    v, dn, si, _ = run(*_same([("10", 1)], [("1", 1)]))
    assert (dn, si, v) == ("x10", "x10", "merged")


def test_real_set40_three_lines_step10_merges():
    lines = [("1", 4), ("2", 4), ("3", 6)]
    v, dn, si, _ = run(*_same([("10", 4), ("20", 4), ("30", 6)], lines))
    assert (dn, si, v) == ("x10", "x10", "merged")


def test_real_set42_step10_merges():
    v, dn, si, _ = run(*_same([("10", 12)], [("1", 12)]))
    assert (dn, si, v) == ("x10", "x10", "merged")


def test_real_set41_partial_step10_is_pending():
    v, dn, si, _ = run(*_same([("10", 3), ("20", 3), ("30", 3)], [("2", 3), ("3", 3)]))
    assert (dn, si, v) == ("x10", "x10", "pending")


def test_real_set43_step10_quantity_disagreement_is_mismatched():
    v, dn, si, cmp = run(*_same([("10", 32)], [("1", 19)]))
    assert (dn, si, v) == ("x10", "x10", "mismatched")
    qty = [f for f in cmp["flags"] if f["type"] == "quantity"]
    assert qty
    assert {f["line_item_no"] for f in qty} == {"10"}


def test_real_set48_release_suffix_merges():
    v, dn, si, _ = run(*_same([("1-1", 20)], [("1", 20)]))
    assert (dn, si, v) == ("add_rel", "add_rel", "merged")


def test_real_set50_release_suffix_merges():
    v, dn, si, _ = run(*_same([("1-1", 1)], [("1", 1)]))
    assert (dn, si, v) == ("add_rel", "add_rel", "merged")


def test_real_set47_release_suffix_quantity_disagreement_is_mismatched():
    v, dn, si, _ = run(*_same([("1-1", 50)], [("1", 46)]))
    assert (dn, si, v) == ("add_rel", "add_rel", "mismatched")


def test_real_ars_set8_exact_step10_on_all_three_is_unchanged():
    same = [("10", 50), ("20", 12)]
    v, dn, si, cmp = run(*_same(same, same))
    assert (dn, si, v) == (None, None, "merged")
    assert cmp["line_mapping"]["label"] is None


def test_real_set15_si_maps_dn_does_not_and_set_quarantines():
    po = [
        ("10", 1), ("20", 2), ("30", 2), ("40", 1), ("50", 1), ("60", 1), ("70", 2), ("80", 1),
        ("90", 1), ("100", 1), ("120", 1), ("130", 1), ("140", 1), ("150", 2), ("160", 1),
        ("170", 1), ("180", 1),
    ]  # fmt: skip
    dn = [
        ("1", 1), ("2", 2), ("3", 2), ("4", 1), ("5", 1), ("6", 1), ("7", 2), ("8", 1),
        ("9", 1), ("10", 1), ("11", 1), ("12", 1), ("13", 1), ("14", 1), ("15", 2), ("16", 1),
        ("17", 1), ("18", 1), ("19", 1),
    ]  # fmt: skip
    si_a = [("1", 1), ("2", 2), ("3", 2), ("4", 1), ("5", 1), ("6", 1), ("7", 2), ("8", 1),
            ("9", 1), ("10", 1), ("12", 1)]  # fmt: skip
    si_b = [("13", 1), ("14", 1), ("15", 2), ("16", 1), ("17", 1), ("18", 1)]
    v, dn_rule, si_rule, cmp = run(_r(po), _r(dn), _r(si_a, doc=1) + _r(si_b, doc=2))
    assert (dn_rule, si_rule, v) == (None, "x10", "quarantined")
    dn_ident = {
        f["line_item_no"]
        for f in cmp["flags"]
        if f["type"] == "identification" and f["pool"] == "DN"
    }
    assert {"11", "19"} <= dn_ident


def test_limitation_real_dts22843_shifted_numbering_is_not_mapped():
    """PO 1,3,4 against DN/SI 1,2,3 (offset +1 after line 1) is not a numbering rule."""
    v, dn, si, _ = run(*_same([("1", 2), ("3", 6), ("4", 2)], [("1", 2), ("2", 6), ("3", 2)]))
    assert (dn, si, v) == (None, None, "quarantined")
