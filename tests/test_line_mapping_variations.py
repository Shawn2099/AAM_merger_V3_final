"""Line-number fallback against table-driven ERP numbering families.

Each case renders the TRUE line numbers of a real set in a PO style, a DN style
and an SI style, runs the comparison with every rule on, and pins the rule each
pool used and the engine verdict. Cases marked `held` are the limits: they stay
quarantined today and each is pinned by a `test_limitation_*` function.
"""

from __future__ import annotations

import pytest
from _lm_helpers import L, rows, run

K = 1000
B3 = [4 * K, 4 * K, 6 * K]  # real set 40: true lines 1..3
B1 = [1 * K]  # real set 39: one line


def render(style: str, i: int) -> str:
    """The printed form of true line number `i` in an ERP style."""
    n10 = i * 10
    table = {
        "plain": str(i),
        "pad2": f"{i:02d}",
        "spaces": f" {i} ",
        "step10": str(n10),
        "sap3": f"{n10:03d}",
        "sap4": f"{n10:04d}",
        "sap5": f"{n10:05d}",
        "sap6": f"{n10:06d}",
        "spaces10": f"{n10} ",
        "rel": f"{i}-1",
        "rel_pad": f"{i:02d}-01",
        "sap5_sched": f"{n10:05d}-0001",
        "sap_sched_dash": f"{n10}-1",
        "sap_sched_slash": f"{n10:05d}/1",
        "rel_us": f"{i}_1",
        "rel_slash": f"{i}/1",
        "rel_endash": f"{i}–1",  # noqa: RUF001
        "rel_minus": f"{i}−1",  # noqa: RUF001
        "dot1": f"{i}.1",
        "dot0": f"{i}.0",
        "dot11": f"{i}.1.1",
        "line_lbl": f"Line {i}",
        "hash_lbl": f"#{i}",
        "item_lbl": f"Item {i}",
        "trail_dot": f"{i}.",
        "float10": f"{n10}.0",
        "step5": str(i * 5),
        "step20": str(i * 20),
        "step100": str(i * 100),
        "offset1": str(i + 1),
    }
    return table[style]


def _case(cid, po, dn, si, delivery, expected, *, base="B3", pin=None):
    return {
        "id": cid,
        "po": po,
        "dn": dn,
        "si": si,
        "delivery": delivery,
        "expected": expected,
        "base": base,
        "pin": pin,
    }


def _each(cid, styles, build):
    return [build(f"{cid}[{s}]", s) for s in styles]


M = "merged"
Q = "quarantined"
P = "pending"
CASES: list[dict] = [
    _case("exact_plain", "plain", "plain", "plain", "full", (None, None, M)),
    _case("exact_zero_pad_po", "pad2", "plain", "plain", "full", (None, None, M)),
    _case("exact_sap_both", "sap5", "step10", "sap4", "full", (None, None, M)),
    _case("exact_spaces", "spaces", "plain", "plain", "full", (None, None, M)),
    *_each(
        "sap_po_vendor_serial",
        ["sap3", "sap4", "sap5", "sap6", "step10"],
        lambda cid, s: _case(cid, s, "plain", "plain", "full", ("x10", "x10", M)),
    ),
    _case("sap_po_vendor_serial_partial", "sap5", "plain", "plain", "partial", ("x10", "x10", P)),
    _case("vendor_sap_po_serial", "plain", "sap5", "sap5", "full", ("div10", "div10", M)),
    _case("vendor_step10_spaces", "plain", "spaces10", "spaces10", "full", ("div10", "div10", M)),
    _case("po_release_suffix", "rel", "plain", "plain", "full", ("add_rel", "add_rel", M)),
    _case(
        "po_release_suffix_padded", "rel_pad", "plain", "plain", "full", ("add_rel", "add_rel", M)
    ),
    _case("vendor_release_suffix", "plain", "rel", "rel", "full", ("strip_rel", "strip_rel", M)),
    _case(
        "sap_schedule_vs_sap_po",
        "step10",
        "sap5_sched",
        "sap5_sched",
        "full",
        ("strip_rel", "strip_rel", M),
    ),
    _case("dn_exact_si_step10", "plain", "plain", "step10", "full", (None, "div10", M)),
    _case("dn_step10_si_suffix", "plain", "step10", "rel", "full", ("div10", "strip_rel", M)),
    _case(
        "held_sap_schedule_vs_plain_po",
        "plain",
        "sap_sched_dash",
        "sap_sched_dash",
        "full",
        (None, None, Q),
        pin="test_limitation_two_rules_never_compose",
    ),
    _case(
        "held_sap_schedule_slash",
        "step10",
        "sap_sched_slash",
        "sap_sched_slash",
        "full",
        (None, None, Q),
        pin="test_limitation_slash_schedule_is_not_mapped",
    ),
    *_each(
        "held_unicode_dash_po",
        ["rel_endash", "rel_minus"],
        lambda cid, s: _case(
            cid,
            s,
            "plain",
            "plain",
            "full",
            (None, None, Q),
            pin="test_limitation_unicode_dash_is_not_normalised",
        ),
    ),
    *_each(
        "held_other_separators",
        ["rel_us", "rel_slash", "dot1", "dot0", "dot11"],
        lambda cid, s: _case(
            cid,
            s,
            "plain",
            "plain",
            "full",
            (None, None, Q),
            pin="test_limitation_other_separators_are_not_mapped",
        ),
    ),
    *_each(
        "held_labels",
        ["line_lbl", "hash_lbl", "item_lbl", "trail_dot", "float10"],
        lambda cid, s: _case(
            cid,
            s,
            "plain",
            "plain",
            "full",
            (None, None, Q),
            pin="test_limitation_labelled_keys_are_not_mapped",
        ),
    ),
    *_each(
        "held_other_steps",
        ["step5", "step20", "step100"],
        lambda cid, s: _case(
            cid,
            s,
            "plain",
            "plain",
            "full",
            (None, None, Q),
            pin="test_limitation_steps_other_than_10_are_not_mapped",
        ),
    ),
    _case(
        "held_offset_vendor",
        "plain",
        "offset1",
        "offset1",
        "full",
        (None, None, Q),
        pin="test_limitation_shifted_numbering_is_not_mapped",
    ),
    _case(
        "held_mixed_docs_plain_step10",
        "plain",
        ("mixed", "plain", "step10"),
        "plain",
        "full",
        (None, None, Q),
        pin="test_limitation_mixed_pool_is_not_mapped",
    ),
    _case(
        "vendor_serial_renumbered_partial",
        "step10",
        "renumbered",
        "renumbered",
        "partial",
        ("x10", "x10", "mismatched"),
        pin="test_known_limitation_vendor_serial_renumbering_pairs_wrongly_but_never_merges",
    ),
    _case(
        "single_line_step10",
        "step10",
        "plain",
        "plain",
        "full",
        ("x10", "x10", M),
        base="B1",
        pin="test_known_limitation_single_line_step10_maps_on_quantity_alone",
    ),
    _case(
        "single_line_reverse[step10]",
        "plain",
        "step10",
        "step10",
        "full",
        ("div10", "div10", M),
        base="B1",
        pin="test_known_limitation_reverse_rules_map_on_structure_alone",
    ),
    _case(
        "single_line_reverse[rel]",
        "plain",
        "rel",
        "rel",
        "full",
        ("strip_rel", "strip_rel", M),
        base="B1",
        pin="test_known_limitation_reverse_rules_map_on_structure_alone",
    ),
]


def _build(case: dict):
    qty = B1 if case["base"] == "B1" else B3
    true_lines = list(range(1, len(qty) + 1))
    partial = case["delivery"] == "partial"
    vendor_lines = true_lines[1:] if partial else true_lines

    def po_rows():
        return rows([(render(case["po"], i), qty[i - 1]) for i in true_lines])

    def vendor(style, desc="ITEM"):
        if isinstance(style, tuple):  # ("mixed", style of true line 1, style of the rest)
            _, first, rest = style
            out = []
            for i in vendor_lines:
                s = first if i == 1 else rest
                out += rows([(render(s, i), qty[i - 1])], doc=1 if i == 1 else 2, desc=desc)
            return out
        if style == "renumbered":  # the vendor numbers what it ships as its own 1..m
            return rows([(str(n), qty[i - 1]) for n, i in enumerate(vendor_lines, 1)])
        return rows([(render(style, i), qty[i - 1]) for i in vendor_lines], desc=desc)

    dn_desc = "DIFFERENT" if case["id"] == "single_line_step10" else "ITEM"
    return po_rows(), vendor(case["dn"], dn_desc), vendor(case["si"])


def _check(case: dict) -> None:
    po, dn, si = _build(case)
    got_verdict, dn_rule, si_rule, _ = run(po, dn, si)
    assert (dn_rule, si_rule, got_verdict) == case["expected"], case["id"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_variation_family(case):
    _check(case)


def _pinned(name: str) -> list[dict]:
    found = [c for c in CASES if c["pin"] == name]
    assert found, name
    return found


def test_limitation_two_rules_never_compose():
    """PO `1` against vendor `10-1` needs two rules; each rule is tried alone."""
    for case in _pinned("test_limitation_two_rules_never_compose"):
        _check(case)


def test_limitation_slash_schedule_is_not_mapped():
    """`00010/1` normalises to `10/1`; the slash form is not a release suffix."""
    for case in _pinned("test_limitation_slash_schedule_is_not_mapped"):
        _check(case)


def test_limitation_unicode_dash_is_not_normalised():
    """En dash and minus-sign variants of `1-1` do not equal it; the key stays foreign and quarantines."""
    for case in _pinned("test_limitation_unicode_dash_is_not_normalised"):
        _check(case)


def test_limitation_other_separators_are_not_mapped():
    for case in _pinned("test_limitation_other_separators_are_not_mapped"):
        _check(case)


def test_limitation_labelled_keys_are_not_mapped():
    for case in _pinned("test_limitation_labelled_keys_are_not_mapped"):
        _check(case)


def test_limitation_steps_other_than_10_are_not_mapped():
    for case in _pinned("test_limitation_steps_other_than_10_are_not_mapped"):
        _check(case)


def test_limitation_shifted_numbering_is_not_mapped():
    for case in _pinned("test_limitation_shifted_numbering_is_not_mapped"):
        _check(case)


def test_limitation_mixed_pool_is_not_mapped():
    for case in _pinned("test_limitation_mixed_pool_is_not_mapped"):
        _check(case)
    # explicit keys: DN doc A prints PO keys as-is, doc B prints serials
    po = L("10", "20", "30", "100")
    dn = L("10", doc=1) + L("2", "3", doc=2)
    si = L("1", "2", "3", "10")
    got_verdict, dn_rule, si_rule, cmp = run(po, dn, si)
    assert (dn_rule, si_rule, got_verdict) == (None, "x10", Q)
    assert [f["reason"] for f in cmp["flags"] if f["type"] == "line_mapping_rejected"] == [
        "document_fits_exact"
    ]


def test_known_limitation_vendor_serial_renumbering_pairs_wrongly_but_never_merges():
    """A vendor that renumbers a partial delivery 1..m is paired by position under x10.

    The exact path pairs such a delivery wrongly too. The pairing is wrong but the
    quantities then disagree, so it is a mismatch and never a merge.
    """
    for case in _pinned(
        "test_known_limitation_vendor_serial_renumbering_pairs_wrongly_but_never_merges"
    ):
        _check(case)


def test_known_limitation_single_line_step10_maps_on_quantity_alone():
    """With one PO line there is nothing but the quantity to tell a right pairing."""
    for case in _pinned("test_known_limitation_single_line_step10_maps_on_quantity_alone"):
        _check(case)


def test_known_limitation_reverse_rules_map_on_structure_alone():
    for case in _pinned("test_known_limitation_reverse_rules_map_on_structure_alone"):
        _check(case)
