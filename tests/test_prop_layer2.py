"""Property tests for Layer 2 (matching + comparison + explain).

Scope: the pure layer — `group_by_line_no`, `compare_aggregates`,
`compare_po_set_lines`, `normalize_line_no`, `explain`. No DB, no VLM, no
Prefect, so these run anywhere in seconds.

Each property pins a load-bearing rule from AAM_merger_V3_PRODUCT.md §2-§3:
sums conserve, normalisation is spelling-independent, orphans are complete
(never silently dropped), descriptions never override numbers, pools are
independent, comparisons are exact integers, the reason vocabulary is closed,
and `explain` is total. They assert current-correct behaviour; a failure is
a real regression, not a known gap.
"""

from __future__ import annotations

import random

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services.matching import (
    compare_aggregates,
    group_by_line_no,
    normalize_line_no,
)
from app.services.reconciliation import REASON_TEXT, compare_po_set_lines, explain

S = 1000  # quantities are stored scaled x1000

SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

KNOWN_REASONS = {
    "no_po_line_with_this_number",
    "no_line_number_and_no_description_match",
    "quantity_mismatch",
}


def _row(no, desc="WIDGET PART", qty=10 * S):
    return {"line_item_no": no, "description": desc, "quantity": qty}


@st.composite
def spelling(draw, n: str):
    """Random printed spellings of one canonical line number."""
    return draw(
        st.sampled_from([n, n.zfill(2), n.zfill(3), f" {n} ", f"  {n.zfill(2)} ", f"\t{n}\t"])
    )


canon_nos = st.integers(min_value=1, max_value=20).map(str)
pos_qty = st.integers(min_value=1, max_value=500).map(lambda q: q * S)


def _flags_key(flags):
    return sorted(
        (
            f.get("priority"),
            f.get("type"),
            f.get("pool"),
            f.get("line_item_no"),
            f.get("po_quantity"),
            f.get("vendor_quantity"),
            f.get("reason"),
        )
        for f in flags
    )


# ---------------------------------------------------------------------------
# 1. Sum conservation across split deliveries and spelling variants
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_split_quantities_sum_exactly(data):
    n_lines = data.draw(st.integers(min_value=1, max_value=6))
    nos = [str(i + 1) for i in range(n_lines)]
    po_lines, vendor_lines, expected = [], [], {}
    for i, no in enumerate(nos):
        parts = data.draw(st.lists(pos_qty, min_size=1, max_size=4))
        po_lines.append(_row(no, f"WIDGET PART {i}", sum(parts)))
        expected[no] = sum(parts)
        for p in parts:
            vendor_lines.append(_row(data.draw(spelling(no)), f"WIDGET PART {i}", p))
    po_t, v_t, orphans, po_fail = group_by_line_no(po_lines, vendor_lines)
    assert po_fail is None
    assert orphans == []
    assert po_t == expected
    assert v_t == expected
    assert compare_aggregates(po_t, v_t, orphans) == []


# ---------------------------------------------------------------------------
# 2. Normalisation is spelling-independent
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_spelling_variants_change_nothing(data):
    nos = data.draw(st.lists(canon_nos, min_size=1, max_size=5, unique=True))
    qtys = data.draw(st.lists(pos_qty, min_size=len(nos), max_size=len(nos)))
    po = [_row(no, f"WIDGET PART {i}", q) for i, (no, q) in enumerate(zip(nos, qtys, strict=True))]
    vendor = [
        _row(no, f"WIDGET PART {i}", q) for i, (no, q) in enumerate(zip(nos, qtys, strict=True))
    ]

    def run(render):
        rp = [{**r, "line_item_no": data.draw(spelling(r["line_item_no"]))} for r in po]
        rv = [{**r, "line_item_no": data.draw(spelling(r["line_item_no"]))} for r in vendor]
        return render(rp, rv)

    a = run(lambda p, v: group_by_line_no(p, v))
    b = run(lambda p, v: group_by_line_no(p, v))
    assert a[0] == b[0] and a[1] == b[1] and a[3] == b[3]

    def proj(o):
        return sorted(
            (x["why"], normalize_line_no(x.get("line_item_no")), x["quantity"]) for x in o
        )

    assert proj(a[2]) == proj(b[2])
    # Canonical keys: zero-padded spellings collapse.
    assert set(a[0]) == set(nos)


# ---------------------------------------------------------------------------
# 3. Orphan completeness — nothing silently dropped
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_every_unknown_vendor_number_is_an_orphan(data):
    po_nos = data.draw(st.lists(canon_nos, min_size=1, max_size=5, unique=True))
    po = [_row(no, f"WIDGET PART {i}", data.draw(pos_qty)) for i, no in enumerate(po_nos)]
    vendor, alien_nos = [], set()
    for _ in range(data.draw(st.integers(min_value=1, max_value=6))):
        if data.draw(st.booleans()) and po_nos:
            no = data.draw(st.sampled_from(po_nos))
        else:
            no = str(data.draw(st.integers(min_value=101, max_value=999)))
            alien_nos.add(normalize_line_no(no))
        vendor.append(_row(no, "WIDGET PART", data.draw(pos_qty)))
    _, v_t, orphans, po_fail = group_by_line_no(po, vendor)
    assert po_fail is None
    reported = {
        normalize_line_no(o.get("line_item_no"))
        for o in orphans
        if o.get("why") == "no_po_line_with_this_number"
    }
    assert alien_nos <= reported
    # Conservation: matched sums + orphaned quantities == total vendor input.
    assert sum(v_t.values()) + sum(o["quantity"] for o in orphans) == sum(
        r["quantity"] for r in vendor
    )


# ---------------------------------------------------------------------------
# 4. Unusable vendor rows never join a group
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_numberless_gibberish_rows_always_orphan(data):
    n = data.draw(st.integers(min_value=1, max_value=4))
    po = [_row(str(i + 1), f"WIDGET PART {i}", data.draw(pos_qty)) for i in range(n)]
    vendor = [
        {
            "line_item_no": data.draw(st.sampled_from([None, "", "   "])),
            "description": f"UNMATCHABLE ZXQ {i} {data.draw(st.integers(0, 10**6))}",
            "quantity": data.draw(pos_qty),
        }
        for i in range(data.draw(st.integers(min_value=1, max_value=4)))
    ]
    _, v_t, orphans, po_fail = group_by_line_no(po, vendor)
    assert po_fail is None
    assert v_t == {}
    assert len(orphans) == len(vendor)
    assert {o["why"] for o in orphans} == {"no_line_number_and_no_description_match"}


# ---------------------------------------------------------------------------
# 5. A real line number beats any description
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_number_never_loses_to_description(data):
    po = [_row("1", "WIDGET PART ONE", data.draw(pos_qty))]
    # Wrong number, byte-identical description: still an orphan, never merged.
    vendor = [
        {"line_item_no": "999", "description": "WIDGET PART ONE", "quantity": data.draw(pos_qty)}
    ]
    _, v_t, orphans, _ = group_by_line_no(po, vendor)
    assert v_t.get("1", 0) == 0
    assert len(orphans) == 1
    assert orphans[0]["why"] == "no_po_line_with_this_number"


# ---------------------------------------------------------------------------
# 6. Pool symmetry — DN and SI are evaluated independently
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_dn_si_pools_are_symmetric(data):
    nos = data.draw(st.lists(canon_nos, min_size=1, max_size=4, unique=True))
    po = [_row(no, f"WIDGET PART {i}", data.draw(pos_qty)) for i, no in enumerate(nos)]
    dn = [_row(no, f"WIDGET PART {i}", data.draw(pos_qty)) for i, no in enumerate(nos)]
    si = [_row(no, f"WIDGET PART {i}", data.draw(pos_qty)) for i, no in enumerate(nos)]
    a = compare_po_set_lines(po, dn, si)
    b = compare_po_set_lines(po, si, dn)
    assert a["dn_totals"] == b["si_totals"] and a["si_totals"] == b["dn_totals"]

    def swap(f):
        return {**f, "pool": "DN" if f["pool"] == "SI" else "SI"}

    assert _flags_key(a["flags"]) == _flags_key([swap(f) for f in b["flags"]])


# ---------------------------------------------------------------------------
# 7. Order independence
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_row_order_changes_nothing(data):
    seed = data.draw(st.integers(min_value=0, max_value=2**31))
    nos = data.draw(st.lists(canon_nos, min_size=1, max_size=5, unique=True))
    po = [_row(no, f"WIDGET PART {i}", data.draw(pos_qty)) for i, no in enumerate(nos)]
    dn = [
        _row(data.draw(spelling(no)), f"WIDGET PART {i}", data.draw(pos_qty))
        for i, no in enumerate(nos)
    ]
    si = [
        _row(data.draw(spelling(no)), f"WIDGET PART {i}", data.draw(pos_qty))
        for i, no in enumerate(nos)
    ]
    rng = random.Random(seed)
    po2, dn2, si2 = list(po), list(dn), list(si)
    rng.shuffle(po2)
    rng.shuffle(dn2)
    rng.shuffle(si2)
    a = compare_po_set_lines(po, dn, si)
    b = compare_po_set_lines(po2, dn2, si2)
    assert a["po_totals"] == b["po_totals"]
    assert a["dn_totals"] == b["dn_totals"]
    assert a["si_totals"] == b["si_totals"]
    assert a["po_fail"] == b["po_fail"]
    assert _flags_key(a["flags"]) == _flags_key(b["flags"])


# ---------------------------------------------------------------------------
# 8. Exact integers — one scaled unit still mismatches
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_one_scaled_unit_is_a_mismatch(data):
    q = data.draw(st.integers(min_value=2, max_value=10**6)) * S
    po = [_row("1", "WIDGET", q)]
    assert (
        compare_po_set_lines(po, [_row("1", "WIDGET", q)], [_row("1", "WIDGET", q)])["flags"] == []
    )
    for off in (1, -1):
        res = compare_po_set_lines(
            po, [_row("1", "WIDGET", q + off)], [_row("1", "WIDGET", q + off)]
        )
        assert any(
            f["reason"] == "quantity_mismatch" and f["type"] == "quantity" for f in res["flags"]
        ), f"offset {off} must mismatch (no tolerance)"


# ---------------------------------------------------------------------------
# 9. Closed reason vocabulary (PRODUCT §3.2: exactly two types, three reasons)
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_reason_vocabulary_is_closed(data):
    po_nos = data.draw(st.lists(canon_nos, min_size=1, max_size=4, unique=True))
    po = [_row(no, f"WIDGET PART {i}", data.draw(pos_qty)) for i, no in enumerate(po_nos)]

    def rand_vendor():
        kind = data.draw(st.integers(min_value=0, max_value=3))
        if kind == 0 and po_nos:
            return _row(data.draw(st.sampled_from(po_nos)), "WIDGET", data.draw(pos_qty))
        if kind == 1:
            return _row(str(data.draw(st.integers(101, 999))), "WIDGET", data.draw(pos_qty))
        if kind == 2:
            return {
                "line_item_no": None,
                "description": f"ZXQ {data.draw(st.integers(0, 10**6))}",
                "quantity": data.draw(pos_qty),
            }
        return _row(
            data.draw(st.sampled_from(po_nos)) if po_nos else "1",
            "WIDGET",
            data.draw(pos_qty),
        )

    dn = [rand_vendor() for _ in range(data.draw(st.integers(0, 5)))]
    si = [rand_vendor() for _ in range(data.draw(st.integers(0, 5)))]
    res = compare_po_set_lines(po, dn, si)
    assert set(res.keys()) == {"po_totals", "dn_totals", "si_totals", "po_fail", "flags"}
    for f in res["flags"]:
        assert f["reason"] in KNOWN_REASONS, f["reason"]
        if f["type"] == "identification":
            assert f["priority"] == 1 and f["po_quantity"] is None
        else:
            assert f["type"] == "quantity" and f["priority"] == 2


# ---------------------------------------------------------------------------
# 10. explain() is total — never raises, never empty
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_explain_never_raises_nor_empty(data):
    reasons = [*REASON_TEXT, None, "", "not_a_reason", "COMBINED"]
    reason = data.draw(st.sampled_from(reasons))

    def rand_flag():
        if data.draw(st.booleans()):
            return {
                "priority": 2,
                "type": "quantity",
                "pool": data.draw(st.sampled_from(["DN", "SI"])),
                "line_item_no": "1",
                "po_quantity": data.draw(pos_qty),
                "vendor_quantity": data.draw(st.integers(min_value=0, max_value=500)) * S,
                "reason": "quantity_mismatch",
            }
        return {
            "priority": 1,
            "type": "identification",
            "pool": data.draw(st.sampled_from(["DN", "SI"])),
            "line_item_no": "9",
            "po_quantity": None,
            "vendor_quantity": data.draw(pos_qty),
            "reason": "no_po_line_with_this_number",
        }

    flags = [rand_flag() for _ in range(data.draw(st.integers(0, 3)))]
    text = explain(reason, flags)
    assert isinstance(text, str) and len(text) > 0


# ---------------------------------------------------------------------------
# 11. Empty vendor pool reports every PO line short, never clean
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_empty_vendor_pool_is_all_short(data):
    nos = data.draw(st.lists(canon_nos, min_size=1, max_size=5, unique=True))
    po = [_row(no, f"WIDGET PART {i}", data.draw(pos_qty)) for i, no in enumerate(nos)]
    res = compare_po_set_lines(po, [], [])
    assert res["po_fail"] is None
    short = {f["line_item_no"] for f in res["flags"] if f["vendor_quantity"] == 0}
    assert short == set(nos)


# ---------------------------------------------------------------------------
# 13. normalize_line_no is total and idempotent over hostile strings
# ---------------------------------------------------------------------------

nasty_text = st.one_of(
    st.just(None),
    st.just(""),
    st.text(max_size=12),
    st.sampled_from(["0", "000", "  000  ", "\n1\n", "\t", "0.5", "-3", "１２", "①", "1\x002"]),  # noqa: RUF001 — hostile fuzz strings are the point
)


@SETTINGS
@given(st.data())
def test_prop_normalize_never_raises_and_idempotent(data):
    s = data.draw(nasty_text)
    first = normalize_line_no(s if s is None or isinstance(s, str) else str(s))
    assert isinstance(first, str)
    assert normalize_line_no(first) == first  # idempotent
    if s is None or (isinstance(s, str) and not s.strip()):
        assert first == ""


# ---------------------------------------------------------------------------
# 14. Every group-produced orphan carries a known identification reason
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_orphans_always_carry_known_why(data):
    po_nos = data.draw(st.lists(canon_nos, min_size=1, max_size=4, unique=True))
    po = [_row(no, f"WIDGET PART {i}", data.draw(pos_qty)) for i, no in enumerate(po_nos)]
    vendor = []
    for _ in range(data.draw(st.integers(0, 6))):
        kind = data.draw(st.integers(0, 2))
        if kind == 0:
            vendor.append(_row(str(data.draw(st.integers(101, 999))), "WIDGET", data.draw(pos_qty)))
        elif kind == 1:
            vendor.append(
                {
                    "line_item_no": None,
                    "description": f"ZXQ {data.draw(st.integers(0, 10**6))}",
                    "quantity": data.draw(pos_qty),
                }
            )
        else:
            vendor.append(
                _row(
                    data.draw(st.sampled_from(po_nos)),
                    "WIDGET",
                    data.draw(pos_qty),
                )
            )
    _, _, orphans, _ = group_by_line_no(po, vendor)
    for o in orphans:
        assert o["why"] in {
            "no_po_line_with_this_number",
            "no_line_number_and_no_description_match",
        }


# ---------------------------------------------------------------------------
# 15. explain() survives hostile flag shapes
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_explain_survives_hostile_flags(data):
    hostile = data.draw(
        st.lists(
            st.one_of(
                st.just({}),
                st.just({"type": None}),
                st.just({"type": "quantity"}),
                st.just({"type": "quantity", "pool": None, "po_quantity": None}),
                st.just({"type": "quantity", "pool": "DN"}),
                st.fixed_dictionaries(
                    {},
                    optional={
                        "priority": st.integers(),
                        "type": st.text(max_size=8),
                        "pool": st.text(max_size=4),
                        "line_item_no": st.text(max_size=6),
                        "po_quantity": st.one_of(st.none(), st.integers()),
                        "vendor_quantity": st.one_of(st.none(), st.integers()),
                        "reason": st.text(max_size=12),
                    },
                ),
            ),
            max_size=4,
        )
    )
    text = explain(data.draw(st.one_of(st.none(), st.text(max_size=10))), hostile)
    assert isinstance(text, str) and len(text) > 0


# ---------------------------------------------------------------------------
# 16. Threshold 0 disables the description fallback entirely
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_threshold_zero_disables_fallback(data):
    po = [_row("1", "WIDGET PART ONE", data.draw(pos_qty))]
    vendor = [
        {"line_item_no": None, "description": "WIDGET PART ONE", "quantity": data.draw(pos_qty)}
    ]
    _, v_t, orphans, _ = group_by_line_no(po, vendor, desc_threshold=0)
    assert v_t == {}
    assert len(orphans) == 1


# ---------------------------------------------------------------------------
# 17. Duplicate PO lines sum into one group under the first description
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_duplicate_po_lines_sum(data):
    q1, q2 = data.draw(pos_qty), data.draw(pos_qty)
    po = [
        _row("1", "WIDGET A", q1),
        _row(data.draw(spelling("1")), "WIDGET B", q2),
    ]
    vendor = [_row("1", "WIDGET A", q1 + q2)]
    po_t, v_t, orphans, po_fail = group_by_line_no(po, vendor)
    assert po_fail is None and orphans == []
    assert po_t == {"1": q1 + q2} and v_t == {"1": q1 + q2}
    assert compare_aggregates(po_t, v_t, orphans) == []


# ---------------------------------------------------------------------------
# 12. One numberless PO row poisons the set regardless of the vendor side
# ---------------------------------------------------------------------------


@SETTINGS
@given(st.data())
def test_prop_numberless_po_row_always_quarantines(data):
    po = [
        _row("1", "WIDGET", data.draw(pos_qty)),
        {
            "line_item_no": data.draw(st.sampled_from([None, "", "   "])),
            "description": "MYSTERY",
            "quantity": data.draw(pos_qty),
        },
    ]
    vendor = [_row("1", "WIDGET", data.draw(pos_qty)) for _ in range(data.draw(st.integers(0, 3)))]
    po_t, v_t, orphans, po_fail = group_by_line_no(po, vendor)
    assert po_fail == "po_line_missing_line_item_no"
    assert po_t == {} and v_t == {} and orphans == []
    res = compare_po_set_lines(po, vendor, vendor)
    assert res["po_fail"] == "po_line_missing_line_item_no"
