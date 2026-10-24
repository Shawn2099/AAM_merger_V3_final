"""Line-number fallback: the pure resolver and its integration into the comparison."""

from __future__ import annotations

import copy
import itertools

import pytest
from _lm_helpers import A, L, R, rows, run

from app.services.matching import (
    LineMapping,
    PoolMapping,
    _eligible_rules,
    group_by_line_no,
    normalize_line_no,
    resolve_line_mapping,
)
from app.services.reconciliation import compare_po_set_lines

X10_MSG = "PO lines in steps of 10, DN/SI lines in steps of 1"


def _x10():
    return L("10", "20", "30"), L("1", "2", "3"), L("1", "2", "3")


# SET 15 shape (real, sanitised): PO steps of 10 with gaps; SI maps x10, DN has 11 and 19.
_S15_PO = [
    ("10", 1), ("20", 2), ("30", 2), ("40", 1), ("50", 1), ("60", 1), ("70", 2), ("80", 1),
    ("90", 1), ("100", 1), ("120", 1), ("130", 1), ("140", 1), ("150", 2), ("160", 1),
    ("170", 1), ("180", 1),
]  # fmt: skip
_S15_DN = [
    ("1", 1), ("2", 2), ("3", 2), ("4", 1), ("5", 1), ("6", 1), ("7", 2), ("8", 1), ("9", 1),
    ("10", 1), ("11", 1), ("12", 1), ("13", 1), ("14", 1), ("15", 2), ("16", 1), ("17", 1),
    ("18", 1), ("19", 1),
]  # fmt: skip


def _s15():
    k = 1000
    po = rows([(a, b * k) for a, b in _S15_PO])
    dn = rows([(a, b * k) for a, b in _S15_DN])
    si_a = rows([(a, b * k) for a, b in _S15_DN if int(a) <= 10 or a == "12"], doc=1)
    si_b = rows(
        [(a, b * k) for a, b in _S15_DN if a in ("13", "14", "15", "16", "17", "18")], doc=2
    )
    return po, dn, si_a + si_b


def test_exact_match_needs_no_mapping():
    assert resolve_line_mapping(L("10", "20"), L("10", "20"), L("10", "20"), R) == LineMapping()


def test_x10_maps_vendor_steps_of_one():
    lm = resolve_line_mapping(*_x10(), R)
    expect = PoolMapping("x10", {"1": "10", "2": "20", "3": "30"})
    assert lm.dn == lm.si == expect
    assert lm.label == "x10"


def test_div10_maps_vendor_steps_of_ten():
    lm = resolve_line_mapping(L("1", "2"), L("10", "20"), L("10", "20"), R)
    expect = PoolMapping("div10", {"10": "1", "20": "2"})
    assert lm.dn == lm.si == expect


def test_add_rel_maps_po_release_suffix():
    lm = resolve_line_mapping(L("1-1", "2-1"), L("1", "2"), L("1", "2"), R)
    expect = PoolMapping("add_rel", {"1": "1-1", "2": "2-1"})
    assert lm.dn == lm.si == expect


def test_strip_rel_maps_vendor_release_suffix():
    lm = resolve_line_mapping(L("1", "2"), L("01-01", "2-1"), L("1-1", "2-1"), R)
    assert lm.dn == PoolMapping("strip_rel", {"1-1": "1", "2-1": "2"})
    assert lm.si == PoolMapping("strip_rel", {"1-1": "1", "2-1": "2"})


def test_partial_vendor_pool_still_maps():
    lm = resolve_line_mapping(L("10", "20", "30"), L("2", "3"), L("2", "3"), R)
    assert lm.dn.rule == lm.si.rule == "x10"
    assert lm.dn.map == {"2": "20", "3": "30"}


def test_x10_with_ten_or_more_po_lines_maps_all_or_nothing():
    po = L(*[str(i * 10) for i in range(1, 13)])
    vendor = L(*[str(i) for i in range(1, 13)])
    lm = resolve_line_mapping(po, vendor, vendor, R)
    assert lm.dn.rule == "x10"
    assert lm.dn.map["10"] == "100"


def test_dn_exact_si_mapped_is_allowed():
    lm = resolve_line_mapping(L("10", "20"), L("10", "20"), L("1", "2"), R)
    assert lm.dn == PoolMapping()
    assert lm.si.rule == "x10"
    assert lm.label == "DN as printed, SI x10"
    assert lm.rejections == ()


def test_dn_and_si_may_use_different_rules():
    lm = resolve_line_mapping(L("1", "2"), L("10", "20"), L("1-1", "2-1"), R)
    assert lm.dn.rule == "div10"
    assert lm.si.rule == "strip_rel"
    assert lm.label == "DN div10, SI strip_rel"


def test_set15_shape_maps_si_but_not_dn():
    lm = resolve_line_mapping(*_s15(), R)
    assert lm.dn == PoolMapping()  # DN 11 and 19 have no target
    assert lm.si.rule == "x10"
    assert lm.rejections == ()


def test_mixed_pool_is_not_mapped():
    po = L("10", "20", "30", "100")
    dn = L("10", doc=1) + L("2", "3", doc=2)
    si = L("1", "2", "3", "10")
    lm = resolve_line_mapping(po, dn, si, R)
    assert lm.dn == PoolMapping()
    assert lm.rejections == (
        (
            "DN",
            "document_fits_exact",
            f"DN line numbers fit rule x10 ({X10_MSG}), but a DN document already "
            "matches the PO line numbers as printed; line numbers were not mapped",
        ),
    )
    assert lm.si.rule == "x10"


def test_numberless_row_disables_mapping():
    po, si = L("10", "20"), L("1", "2")
    assert resolve_line_mapping(po, L("1", None), si, R) == LineMapping()


def test_zero_key_never_maps():
    lm = resolve_line_mapping(L("10", "20"), L("0", "1"), L("1", "2"), R)
    assert lm.dn == PoolMapping()
    assert lm.si.rule == "x10"


def test_no_po_keys_no_mapping():
    assert resolve_line_mapping([], L("1"), L("1"), R) == LineMapping()


def test_rules_off_equals_exact():
    assert resolve_line_mapping(*_x10(), frozenset()) == LineMapping()


def test_single_rule_off_disables_only_that_rule():
    assert resolve_line_mapping(*_x10(), R - {"x10"}) == LineMapping()
    lm = resolve_line_mapping(L("1-1"), L("1"), L("1"), R - {"x10"})
    assert lm.dn.rule == "add_rel"


_PO_SETS = [{"10", "20"}, {"1", "2"}, {"1", "2", "10"}, {"1-1", "2-1"}, {"10"}, {"1"}]
_VENDOR_SETS = [{"1", "2"}, {"10", "20"}, {"1-1", "2-1"}, {"10-1"}, {"1"}]


@pytest.mark.parametrize(("po", "vendor"), list(itertools.product(_PO_SETS, _VENDOR_SETS)), ids=str)
def test_preconditions_are_mutually_exclusive(po, vendor):
    assert len(_eligible_rules(po, vendor)) <= 1


# ---- integration with compare_po_set_lines -------------------------------------------------


def test_compare_x10_merges_without_failure_flags():
    po = rows([("10", 5000), ("20", 3000)])
    v = rows([("1", 5000), ("2", 3000)])
    cmp = compare_po_set_lines(po, v, v, line_fallback=A)
    assert [f["type"] for f in cmp["flags"]] == ["line_mapping", "line_mapping"]
    assert cmp["line_mapping"] == {
        "label": "x10",
        "DN": {"rule": "x10", "map": {"1": "10", "2": "20"}},
        "SI": {"rule": "x10", "map": {"1": "10", "2": "20"}},
    }
    dn_flag = next(f for f in cmp["flags"] if f["pool"] == "DN")
    assert dn_flag["message"] == (
        f"DN line numbers mapped to the PO by rule x10 ({X10_MSG}): 1 -> 10, 2 -> 20"
    )


def test_compare_po_keys_are_never_rewritten():
    po = rows([("10", 5000), ("20", 3000)])
    v = rows([("1", 5000), ("2", 3000)])
    exact = rows([("10", 5000), ("20", 3000)])
    for dn, si in ((v, v), (exact, v)):
        before = copy.deepcopy((po, dn, si))
        cmp = compare_po_set_lines(po, dn, si, line_fallback=A)
        keys = {normalize_line_no(r["line_item_no"]) for r in po}
        assert set(cmp["po_totals"]) == set(cmp["dn_totals"]) == set(cmp["si_totals"]) == keys
        assert (po, dn, si) == before  # the input rows are untouched too


def test_compare_mapped_quantity_mismatch_reports_po_line():
    cmp = compare_po_set_lines(
        rows([("10", 32000)]), rows([("1", 19000)]), rows([("1", 19000)]), line_fallback=A
    )
    qty = [f for f in cmp["flags"] if f["type"] == "quantity"]
    assert sorted(f["pool"] for f in qty) == ["DN", "SI"]
    for f in qty:
        assert (f["line_item_no"], f["po_quantity"], f["vendor_quantity"]) == ("10", 32000, 19000)


def test_compare_without_config_is_exact():
    po, dn, si = _x10()
    cmp = compare_po_set_lines(po, dn, si)
    assert [f["type"] for f in cmp["flags"]] == ["identification"] * len(cmp["flags"])
    assert {f["pool"] for f in cmp["flags"]} == {"DN", "SI"}
    assert cmp["line_mapping"]["label"] is None


def test_compare_rejection_flag_is_advisory():
    po = L("10", "20", "30", "100")
    dn = L("10", doc=1) + L("2", "3", doc=2)
    si = L("1", "2", "3", "10")
    cmp = compare_po_set_lines(po, dn, si, line_fallback=A)
    rej = [f for f in cmp["flags"] if f["type"] == "line_mapping_rejected"]
    assert len(rej) == 1
    assert rej[0]["priority"] == 1
    assert rej[0]["pool"] == "DN"
    assert any(f["type"] == "identification" and f["pool"] == "DN" for f in cmp["flags"])
    assert not [
        f for f in cmp["flags"] if f["pool"] == "SI" and f["type"] in ("identification", "quantity")
    ]


def test_group_by_line_no_is_untouched_by_the_fallback():
    """Exact matching still knows nothing about numbering rules."""
    po, dn, _ = _x10()
    _, totals, orphans, _ = group_by_line_no(po, dn)
    assert totals == {}
    assert len(orphans) == 3


def test_known_limitation_step10_pool_split_across_files_quarantines():
    """A DN split by page into files with different key sets is a mixed pool: never mapped.

    File 1 carries vendor lines 1-9, 11, 12; file 2 carries only vendor line 10, which is also a
    PO key as printed. The guard that protects mixed numbering rejects the whole pool, so the set
    quarantines (safe direction) even though the vendor numbers every line the same way.
    """
    po = L(*[str(n) for n in range(10, 130, 10)], doc=100)
    file_1 = L("1", "2", "3", "4", "5", "6", "7", "8", "9", "11", "12", doc=1)
    file_2 = L("10", doc=2)

    v, dn_rule, _si_rule, _cmp = run(po, file_1 + file_2, [])

    assert dn_rule is None
    assert v == "quarantined"
