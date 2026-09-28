"""F2 — DN re-indexing detection (FINDINGS_AND_FIXES_R1.md).

Real case from the vendor samples: PO 15676 has Line 1 = PAD, ABSORBENT and
Line 2 = BANDAGE: PIPE REPAIR. DN GDN-RAK-26-1549 fulfils only the pipe repair
kit and re-indexes it as its own Line 1.

Before F2: the printed number exists on the PO, Step 1 fires, the description
guard fails, and the set quarantines with a reason that does not explain what
happened. The fuzzy path could already resolve it correctly but was never
reached.

After F2: the set still quarantines (nothing merges itself), but the reason is
LINE_REINDEXED and carries the suggested PO line for a human to confirm.
"""

from __future__ import annotations

import pytest

from app.services.matching import assign_lines_detailed, match_vendor_line_detailed

# The real PO 15676 shape.
PO_15676 = [
    {"line_item_no": "1", "description": "PAD, ABSORBENT", "quantity": 10000, "unit_price": 1},
    {
        "line_item_no": "2",
        "description": "BANDAGE: PIPE REPAIR",
        "quantity": 5000,
        "unit_price": 1,
    },
]


def vendor(no, desc, qty=100, part_no=None):
    d = {"line_item_no": no, "description": desc, "quantity": qty, "unit_price": 1}
    if part_no is not None:
        d["part_no"] = part_no
    return d


def test_reindexed_line_reports_the_right_reason():
    idx, reason, detail = match_vendor_line_detailed(vendor("1", "BANDAGE: PIPE REPAIR"), PO_15676)
    assert idx is None, "must never auto-resolve a re-indexed line"
    assert reason == "LINE_REINDEXED"
    assert detail is not None
    assert detail["suggested_po_index"] == 1
    assert detail["suggested_po_line_no"] == "2"
    assert detail["printed_line_no"] == "1"
    assert "BANDAGE" in detail["suggested_po_description"]


def test_reindex_suggestion_is_never_self_referential():
    """The suggestion must point at a DIFFERENT line than the printed number."""
    _idx, reason, detail = match_vendor_line_detailed(vendor("1", "BANDAGE: PIPE REPAIR"), PO_15676)
    assert reason == "LINE_REINDEXED"
    assert detail["suggested_po_line_no"] != detail["matched_po_line_no"]


def test_unnumbered_version_still_resolves_automatically():
    """Unchanged good behaviour: no printed number, fuzzy finds the right line."""
    idx, reason, _detail = match_vendor_line_detailed(
        vendor(None, "BANDAGE: PIPE REPAIR"), PO_15676
    )
    assert reason is None
    assert idx == 1


def test_genuine_wrong_item_still_plain_mismatch():
    """If NO PO line explains the description, do not invent a suggestion."""
    po = [
        {"line_item_no": "1", "description": "PAD, ABSORBENT", "quantity": 1, "unit_price": 1},
        {"line_item_no": "2", "description": "WIRE ROPE 12MM", "quantity": 1, "unit_price": 1},
    ]
    idx, reason, detail = match_vendor_line_detailed(
        vendor("1", "COMPLETELY UNRELATED ZZZ THING"), po
    )
    assert idx is None
    assert reason == "INDEX_DESCRIPTION_MISMATCH"
    assert detail is None


def test_sku_can_supply_the_reindex_suggestion():
    """With rewording, a unique SKU is enough to suggest the right line."""
    po = [
        {
            "line_item_no": "1",
            "description": "PAD, ABSORBENT",
            "quantity": 1,
            "unit_price": 1,
            "part_no": "PAD-1",
        },
        {
            "line_item_no": "2",
            "description": "BANDAGE: PIPE REPAIR",
            "quantity": 1,
            "unit_price": 1,
            "part_no": "BR-9",
        },
    ]
    # Number matches line 1, description is nothing like it, but the SKU is
    # unique to line 2.
    idx, reason, detail = match_vendor_line_detailed(
        vendor("1", "TOTALLY REWORDED ITEM", part_no="br9"), po
    )
    assert idx is None
    assert reason == "LINE_REINDEXED"
    assert detail["suggested_po_index"] == 1
    assert detail["suggested_by"] == "sku"


def test_ambiguous_alternative_produces_no_suggestion():
    """Two PO lines explain it equally well -> no confident suggestion.

    Needs three PO lines: the printed-number line (which fails the sanity
    guard) plus two near-identical candidates, so the runner-up is inside the
    margin and Step 2 cannot pick a winner.
    """
    po = [
        {"line_item_no": "1", "description": "GASKET RUBBER", "quantity": 1, "unit_price": 1},
        {"line_item_no": "2", "description": "STEEL BRACKET 90MM", "quantity": 1, "unit_price": 1},
        {"line_item_no": "3", "description": "STEEL BRACKET 90MM", "quantity": 1, "unit_price": 1},
    ]
    idx, reason, detail = match_vendor_line_detailed(vendor("1", "STEEL BRACKET 90MM"), po)
    assert idx is None
    assert detail is None, "two identical candidates must not produce a suggestion"
    assert reason in ("INDEX_DESCRIPTION_MISMATCH", "AMBIGUOUS_LINE_MATCH")


def test_assign_lines_detailed_propagates_suggestion():
    assign, reason, detail = assign_lines_detailed(PO_15676, [vendor("1", "BANDAGE: PIPE REPAIR")])
    assert assign == {}, "a failed assignment must not commit partial work"
    assert reason == "LINE_REINDEXED"
    assert detail and detail["suggested_po_line_no"] == "2"


def test_sku_rescue_disabled_still_yields_description_suggestion():
    """Turning off Step 3 must not disable the description-based suggestion."""
    _idx, reason, detail = match_vendor_line_detailed(
        vendor("1", "BANDAGE: PIPE REPAIR"), PO_15676, use_sku=False
    )
    assert reason == "LINE_REINDEXED"
    assert detail["suggested_by"] == "description"


def test_correct_match_is_never_flagged_as_reindexed():
    """The happy path must stay silent — no false LINE_REINDEXED noise."""
    idx, reason, detail = match_vendor_line_detailed(vendor("1", "PAD, ABSORBENT"), PO_15676)
    assert idx == 0
    assert reason is None
    assert detail is None


@pytest.mark.parametrize(
    ("printed", "expected_po_line"),
    [("01", "2"), ("001", "2"), (" 1 ", "2")],
)
def test_reindex_detection_survives_leading_zeros(printed, expected_po_line):
    """'001' on the DN must still be recognised as printed line 1."""
    _, reason, detail = match_vendor_line_detailed(
        vendor(printed, "BANDAGE: PIPE REPAIR"), PO_15676
    )
    assert reason == "LINE_REINDEXED"
    assert detail["suggested_po_line_no"] == expected_po_line
