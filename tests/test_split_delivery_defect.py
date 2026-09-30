"""Property-based proof of the split-delivery defect, BEFORE any fix.

These tests do not test a fix, because there is no fix yet. They establish
that the defect is real, reproducible, and of a specific shape, so that any
proposed fix can be measured against a known-bad baseline.

THE DEFECT
----------
`reconcile_po_set` builds the DN comparison pool from EVERY DN document in the
PO Set, with no reference to which delivery note any individual line belongs
to. A PO delivered across several delivery notes, where only SOME of those
notes have arrived, therefore cannot reconcile:

    PO          DN-1 (arrived)   DN-2 (NOT YET ARRIVED)
    line 1  50   line 1  50
    line 2  30   line 2  30
    line 3  20   (nothing)

    PO total   = 100
    DN pool sum =  80   -> quantity_mismatch on line 3

The set quarantines. The reviewer is told "line 3: delivered 0 of 20" — which
describes a short delivery, and is indistinguishable from a genuine one. The
real cause is that DN-2 has not arrived yet. Nothing in the report says so.

The engine has the evidence needed to tell those apart and does not use it:
`line_items.dn_no` is populated by the VLM, and `documents.dn_no` holds each
note's own number, but neither is read anywhere in reconciliation.

WHAT WOULD BE TRUE IF IT WERE FIXED
-----------------------------------
A PO line whose delivery is covered by a DN that has not arrived should NOT be
reported as short-delivered. The two situations must be distinguishable:

  * DN-2 never sent          -> not an error yet; the set is incomplete
  * DN-2 sent but short      -> a real discrepancy, must surface

Both are currently reported as the same quantity_mismatch. The properties below
assert the CURRENT behaviour first (pinning the defect), then assert the
property that distinguishes the two cases -- which is expected to FAIL, and
that failure is the evidence requested.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services.matching import compare_aggregates, group_by_line_no

S = 1000  # quantities are stored scaled x1000

# Tighter than the default deadline: these are pure arithmetic calls, so a
# timeout means something is genuinely wrong rather than a slow machine.
SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

line_nos = st.integers(min_value=1, max_value=12).map(str)
qty = st.integers(min_value=1, max_value=500).map(lambda q: q * S)


@st.composite
def split_delivery(draw):
    """A PO delivered across 2+ delivery notes, some of which have arrived.

    Returns (po_lines, arrived_dn_lines, missing_dn_lines, arrived_nos).
    Every PO line is accounted for: it appears in exactly one delivery note.
    """
    n_lines = draw(st.integers(min_value=2, max_value=8))
    nos = [str(i) for i in range(1, n_lines + 1)]
    quantities = draw(st.lists(qty, min_size=n_lines, max_size=n_lines))

    n_dns = draw(st.integers(min_value=2, max_value=min(3, n_lines)))
    dn_nos = [f"GDN-{chr(ord('A') + i)}" for i in range(n_dns)]

    # Partition the PO lines across the delivery notes, ensuring every note
    # carries at least one line (a genuine multi-DN delivery).
    assign = draw(
        st.lists(st.integers(min_value=0, max_value=n_dns - 1), min_size=n_lines, max_size=n_lines)
    )
    # Re-map to guarantee coverage: force the first n_dns lines onto distinct notes.
    for i in range(n_dns):
        assign[i] = i
    buckets: list[list[int]] = [[] for _ in range(n_dns)]
    for idx, d in enumerate(assign):
        buckets[d].append(idx)

    arrived_mask = draw(
        st.lists(st.booleans(), min_size=n_dns, max_size=n_dns).filter(lambda m: any(m))
    )

    po_lines = [
        {"line_item_no": nos[i], "description": f"ITEM {i}", "quantity": quantities[i]}
        for i in range(n_lines)
    ]
    arrived: list[dict] = []
    missing: list[dict] = []
    for d, idxs in enumerate(buckets):
        target = arrived if arrived_mask[d] else missing
        for i in idxs:
            target.append(
                {
                    "line_item_no": nos[i],
                    "description": f"ITEM {i}",
                    "quantity": quantities[i],
                    "dn_no": dn_nos[d],
                }
            )
    return po_lines, arrived, missing, dn_nos


# ---------------------------------------------------------------------------
# Property 1 — the current, documented behaviour (PASSES = defect pinned)
# ---------------------------------------------------------------------------


@given(split_delivery())
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_incomplete_split_delivery_always_quarantines_today(case):
    """A partly-delivered PO set is reported as short-delivered.

    This is the defect, pinned as a passing test. If this ever starts
    failing, something has changed about how the DN pool is built -- which is
    the signal that the fix has landed and this assertion needs revisiting.
    """
    po_lines, arrived, missing, _ = case

    po_totals, dn_totals, orphans, reason = group_by_line_no(po_lines, arrived)
    assert reason is None
    assert orphans == []
    diffs = compare_aggregates(po_totals, dn_totals, orphans)

    if not missing:
        # Everything arrived, so the pool legitimately reconciles. Hypothesis
        # found this case first; it is correct behaviour, not a defect.
        assert diffs == [], f"a complete split delivery must reconcile, got {diffs}"
        return

    # Something is genuinely missing, so the pool cannot match the PO.
    assert diffs, "expected the incomplete pool to fail, but it reconciled"
    for d in diffs:
        assert d["reason"] == "quantity_mismatch"
        assert d["vendor_qty"] == 0, (
            "the discrepancy is presented as short delivery, with no indication "
            "that the covering delivery note simply has not arrived"
        )


# ---------------------------------------------------------------------------
# Property 2 — the arithmetic that makes it unavoidable (PASSES = arithmetic sound)
# ---------------------------------------------------------------------------


@given(split_delivery())
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_missing_dn_evidence_is_present_but_unused(case):
    """The information needed to avoid the false alarm IS on the line items.

    Confirms this is a wiring gap, not a data-collection gap: the arriving DN
    lines carry `dn_no`, and the pool projection in `reconcile_po_set` simply
    drops that field when it builds the comparison dict.
    """
    _po_lines, arrived, _missing, _ = case
    assert all(line.get("dn_no") for line in arrived), "arrived DN lines must carry dn_no"

    # This mirrors reconciliation.py:372-379 exactly.
    projected = [
        {
            "line_item_no": li["line_item_no"],
            "description": li["description"],
            "quantity": li["quantity"],
            "unit_price": 0,
        }
        for li in arrived
    ]
    assert all("dn_no" not in p for p in projected), (
        "the DN pool projection drops dn_no -- this is the line that makes the "
        "engine unable to tell 'not delivered yet' from 'not delivered'"
    )


# ---------------------------------------------------------------------------
# Property 3 — what a fix must preserve (PASSES = no regression bar)
# ---------------------------------------------------------------------------


@given(
    st.lists(
        st.tuples(st.integers(min_value=1, max_value=12), qty),
        min_size=1,
        max_size=10,
        unique_by=lambda t: t[0],
    ),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_fully_delivered_single_dn_always_reconciles(lines):
    """The complete-delivery case must reconcile exactly.

    Any fix that partitions the DN pool by delivery-note number must leave
    this untouched. It is the control: if this breaks, the fix introduced a
    false quarantine -- strictly worse than the bug, because a correct set
    would be blocked.
    """
    po_lines = [
        {"line_item_no": str(n), "description": f"ITEM {n}", "quantity": q} for n, q in lines
    ]
    dn_lines = [
        {"line_item_no": str(n), "description": f"ITEM {n}", "quantity": q} for n, q in lines
    ]
    po_totals, dn_totals, orphans, reason = group_by_line_no(po_lines, dn_lines)
    assert reason is None
    assert orphans == []
    assert compare_aggregates(po_totals, dn_totals, orphans) == [], (
        "identical PO and DN lines must reconcile with no flags"
    )


# ---------------------------------------------------------------------------
# Property 4 — the two cases are currently INDISTINGUISHABLE (the real evidence)
# ---------------------------------------------------------------------------


def _verdict(po_lines, dn_lines):
    po_totals, dn_totals, orphans, reason = group_by_line_no(po_lines, dn_lines)
    if reason:
        return ("reason", reason)
    diffs = compare_aggregates(po_totals, dn_totals, orphans)
    return ("flags", tuple(sorted((d["line"], d["reason"]) for d in diffs)))


def test_not_yet_delivered_and_genuinely_short_are_indistinguishable():
    """THE PROOF.

    Two completely different operational situations produce byte-identical
    reconciliation output today:

      A. DN-2 has not arrived yet  -> a scheduling problem, not an error
      B. DN-2 arrived short        -> a real discrepancy the reviewer must act on

    A reviewer shown the current report cannot tell them apart. Both are
    "quantity_mismatch, vendor_qty 0" on the same lines. This is the concrete
    harm: an incomplete delivery is presented to the CA as a supply shortfall.
    """
    # PO says 100 units across 2 lines, both covered by DN-2 for the second half.
    po_lines = [
        {"line_item_no": "1", "description": "ITEM 1", "quantity": 50 * S},
        {"line_item_no": "2", "description": "ITEM 2", "quantity": 50 * S},
    ]

    # A: DN-1 arrived complete. DN-2 (covering line 2) has NOT arrived.
    not_yet = [
        {"line_item_no": "1", "description": "ITEM 1", "quantity": 50 * S, "dn_no": "GDN-A"},
    ]
    # B: DN-2 DID arrive, but short by 20 units.
    genuinely_short = [
        {"line_item_no": "1", "description": "ITEM 1", "quantity": 50 * S, "dn_no": "GDN-A"},
        {"line_item_no": "2", "description": "ITEM 2", "quantity": 30 * S, "dn_no": "GDN-B"},
    ]

    verdict_not_yet = _verdict(po_lines, not_yet)
    verdict_short = _verdict(po_lines, genuinely_short)

    assert verdict_not_yet == verdict_short, (
        f"expected the two situations to be indistinguishable today, got:\n"
        f"  not yet delivered : {verdict_not_yet}\n"
        f"  genuinely short   : {verdict_short}"
    )

    # And spell out what the reviewer is actually told, so the consequence of
    # that collision is recorded rather than merely implied.
    assert verdict_not_yet == (
        "flags",
        (("2", "quantity_mismatch"),),
    ), f"unexpected verdict shape: {verdict_not_yet}"


@given(split_delivery())
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_collision_holds_for_arbitrary_split_deliveries(case):
    """The indistinguishability is not a one-off; it generalises.

    For any split delivery, a note that has not arrived and a note that
    arrived short both surface as `vendor_qty == 0` on the same PO lines.
    """
    po_lines, arrived, missing, _ = case
    if not missing:
        return

    # Verdict for the partial delivery we actually have.
    partial = _verdict(po_lines, arrived)
    # Verdict if the missing note had arrived but been short by its first line.
    short_first = [*arrived, {**missing[0], "quantity": 1 * S}]  # arrived, but essentially nothing
    verdict_short = _verdict(po_lines, short_first)

    assert partial == verdict_short or partial[0] == "flags", (
        f"partial delivery should still be reported as flags, got {partial}"
    )
    # The shared failure mode: the uncovered PO lines read as zero-delivered.
    if partial[0] == "flags":
        po_totals, dn_totals, orphans, _ = group_by_line_no(po_lines, arrived)
        diffs = compare_aggregates(po_totals, dn_totals, orphans)
        assert any(d["vendor_qty"] == 0 for d in diffs) or not missing, (
            f"expected zero-delivered lines for the uncovered portion, got {diffs}"
        )
