"""v20.5 3-step matcher rules adopted into the set-level flow.

Covers the logical issues found in review:
  - injective assignment (a vendor line can never inflate two PO aggregates)
  - description fuzzy ONLY when the line number is absent or unlisted (v20.5)
  - sanity >= 40 guard on an exact line_no hit (v20.5)
  - fuzzy margin >= 5 over runner-up (v20.5)
  - Step 3 unique normalized-SKU rescue (v20.5)
"""

from __future__ import annotations


def _po(no, desc, part_no=None):
    d = {"line_item_no": no, "description": desc, "quantity": 100, "unit_price": 10}
    if part_no:
        d["part_no"] = part_no
    return d


def _v(no, desc, qty=100, part_no=None):
    d = {"line_item_no": no, "description": desc, "quantity": qty, "unit_price": 10}
    if part_no:
        d["part_no"] = part_no
    return d


def test_assignment_is_injective_no_double_count():
    """One unnumbered DN line similar to two PO lines must not count in both."""
    from app.services.matching import assign_lines

    po = [_po("1", "Widget Alpha"), _po("2", "Widget Beta")]
    vendor = [_v(None, "Widget Alpha", qty=30)]
    assign, reason = assign_lines(po, vendor)
    assert reason is None
    total = sum(len(v) for v in assign.values())
    assert total == 1, "vendor line assigned to more than one PO line"


def test_multiple_vendor_lines_still_sum_into_one_po_line():
    """Split delivery across two DNs must aggregate into a single PO line."""
    from app.services.matching import assign_lines

    po = [_po("1", "Widget Alpha")]
    vendor = [_v("1", "Widget Alpha", qty=40), _v("1", "Widget Alpha", qty=60)]
    assign, reason = assign_lines(po, vendor)
    assert reason is None
    assert len(assign) == 1
    assert sum(v["quantity"] for v in assign[0]) == 100


def test_fuzzy_never_overrides_a_listed_number():
    """v20.5: a number that IS on the PO is authoritative — description must
    not redirect it to a different PO line that happens to read similarly."""
    from app.services.matching import match_vendor_line

    po = [_po("1", "Widget Alpha"), _po("2", "Widget Beta")]
    # line 1 present, but the text reads like line 2 -> must stay on line 1
    idx, reason = match_vendor_line(_v("1", "Widget Beta"), po)
    assert reason is None
    assert idx == 0
    idx2, reason2 = match_vendor_line(_v("2", "Widget Alpha"), po)
    assert reason2 is None
    assert idx2 == 1


def test_fuzzy_allowed_when_number_unlisted_on_po():
    """Number present but absent from the PO -> fuzzy fallback is allowed."""
    from app.services.matching import match_vendor_line

    po = [_po("1", "Widget Alpha"), _po("2", "Widget Beta")]
    idx, reason = match_vendor_line(_v("7", "Widget Beta"), po)
    assert reason is None
    assert idx == 1


def test_sanity_guard_rejects_wrong_index_row():
    """Exact line_no hit but unrelated description -> INDEX_DESCRIPTION_MISMATCH."""
    from app.services.matching import match_vendor_line

    po = [_po("5", "Stainless Hex Nut M10")]
    idx, reason = match_vendor_line(_v("5", "Copper Pipe Fitting"), po)
    assert idx is None
    assert reason == "INDEX_DESCRIPTION_MISMATCH"


def test_fuzzy_margin_rejects_near_ties():
    """Two near-identical PO lines and no number -> ambiguous, not a coin flip."""
    from app.services.matching import match_vendor_line

    po = [_po("1", "Widget Alpha Large"), _po("2", "Widget Alpha Large")]
    idx, reason = match_vendor_line(_v(None, "Widget Alpha Large"), po)
    assert idx is None
    assert reason == "AMBIGUOUS_LINE_MATCH"


def test_fuzzy_margin_accepts_clear_winner():
    from app.services.matching import match_vendor_line

    po = [_po("1", "Widget Alpha"), _po("2", "Bolt Assembly")]
    idx, reason = match_vendor_line(_v(None, "Bolt Assembly"), po)
    assert reason is None
    assert idx == 1


def test_sku_rescue_matches_reworded_description():
    """v20.5 Step 3: SKU rescues a line whose description was reworded."""
    from app.services.matching import match_vendor_line

    po = [_po("1", "Widget Alpha", part_no="AB-100")]
    idx, reason = match_vendor_line(_v(None, "Totally Different Words", part_no="ab100"), po)
    assert reason is None
    assert idx == 0


def test_sku_rescue_refuses_when_ambiguous():
    from app.services.matching import match_vendor_line

    po = [_po("1", "Widget Alpha", part_no="AB-100"), _po("2", "Widget Beta", part_no="AB100")]
    idx, reason = match_vendor_line(_v(None, "Totally Different", part_no="AB-100"), po)
    assert idx is None
    assert reason == "AMBIGUOUS_LINE_MATCH"


def test_step10_still_works_under_new_flow():
    from app.services.matching import match_vendor_line

    po = [_po("20", "Widget Alpha")]
    idx, reason = match_vendor_line(_v("2", "Widget Alpha"), po)
    assert reason is None
    assert idx == 0


def test_line_number_normalization_applies():
    from app.services.matching import match_vendor_line

    po = [_po("1", "Widget Alpha")]
    idx, reason = match_vendor_line(_v("01", "Widget Alpha"), po)
    assert reason is None
    assert idx == 0
