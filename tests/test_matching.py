def test_match_by_line_no():
    from app.services.matching import match_line

    po = {"line_item_no": "5", "description": "Widget A"}
    dn = [{"line_item_no": "5", "description": "Widget A", "qty": 10}]
    assert match_line(po, dn, [], thr=85)["matched"] is True


def test_match_si_by_line_no():
    from app.services.matching import match_line

    po = {"line_item_no": "5", "description": "Widget A"}
    si = [{"line_item_no": "5", "description": "Widget A", "qty": 10}]
    assert match_line(po, [], si, thr=85)["matched"] is True


def test_fuzzy_fallback():
    from app.services.matching import match_line

    po = {"line_item_no": None, "description": "Widget A 10kg"}
    dn = [{"line_item_no": None, "description": "Widget A 10 KG", "qty": 10}]
    assert match_line(po, dn, [], thr=85)["matched"] is True


def test_fuzzy_si_fallback():
    from app.services.matching import match_line

    po = {"line_item_no": None, "description": "Widget A 10kg"}
    si = [{"line_item_no": None, "description": "Widget A 10 KG", "qty": 10}]
    assert match_line(po, [], si, thr=85)["matched"] is True


def test_conflict_quarantine():
    from app.services.matching import match_line

    po = {"line_item_no": "5", "description": "Widget A"}
    dn = [
        {"line_item_no": "5", "description": "Conflict", "qty": 10},
        {"line_item_no": "5", "description": "Widget A", "qty": 10},
    ]
    assert match_line(po, dn, [], thr=85)["quarantine"] is True


def test_si_conflict_quarantine():
    from app.services.matching import match_line

    po = {"line_item_no": "5", "description": "Widget A"}
    si = [
        {"line_item_no": "5", "description": "Conflict Desc", "qty": 10},
        {"line_item_no": "5", "description": "Widget A", "qty": 10},
    ]
    assert match_line(po, [], si, thr=85)["quarantine"] is True


def test_find_unmatched_all_clean():
    from app.services.matching import find_unmatched

    po_lines = [{"line_item_no": "1", "description": "Widget A"}]
    dn_lines = [{"line_item_no": "1", "description": "Widget A"}]
    si_lines = [{"line_item_no": "1", "description": "Widget A"}]
    assert find_unmatched(po_lines, dn_lines, si_lines, thr=85) == []


def test_find_unmatched_extra_dn_line():
    from app.services.matching import find_unmatched

    po_lines = [{"line_item_no": "1", "description": "Widget A"}]
    dn_lines = [
        {"line_item_no": "1", "description": "Widget A"},
        {"line_item_no": "2", "description": "Extra Unmatched DN Line"},
    ]
    si_lines = [{"line_item_no": "1", "description": "Widget A"}]
    unmatched = find_unmatched(po_lines, dn_lines, si_lines, thr=85)
    assert len(unmatched) == 1
    assert unmatched[0]["line_item_no"] == "2"


def test_find_unmatched_extra_si_line():
    from app.services.matching import find_unmatched

    po_lines = [{"line_item_no": "1", "description": "Widget A"}]
    dn_lines = [{"line_item_no": "1", "description": "Widget A"}]
    si_lines = [
        {"line_item_no": "1", "description": "Widget A"},
        {"line_item_no": "99", "description": "Rogue SI item"},
    ]
    unmatched = find_unmatched(po_lines, dn_lines, si_lines, thr=85)
    assert len(unmatched) == 1
    assert unmatched[0]["line_item_no"] == "99"


def test_step_10_matching_alignment():
    from app.services.matching import find_unmatched, get_matching_candidates, match_line

    po_lines = [
        {"line_item_no": "10", "description": "Item 1"},
        {"line_item_no": "20", "description": "Item 2"},
        {"line_item_no": "30", "description": "Item 3"},
    ]
    dn_lines = [
        {"line_item_no": "1", "description": "Item 1"},
        {"line_item_no": "2", "description": "Item 2"},
        {"line_item_no": "3", "description": "Item 3"},
    ]
    si_lines = [
        {"line_item_no": "1", "description": "Item 1"},
        {"line_item_no": "2", "description": "Item 2"},
        {"line_item_no": "3", "description": "Item 3"},
    ]

    # Check match_line on line 10
    res = match_line(po_lines[0], dn_lines, si_lines, all_po_lines=po_lines)
    assert res["matched"] is True
    assert res["quarantine"] is False

    # Check get_matching_candidates returns line 1
    cands = get_matching_candidates(po_lines[0], dn_lines, all_po_lines=po_lines)
    assert len(cands) == 1
    assert cands[0]["line_item_no"] == "1"

    # Reverse check passes with 0 unmatched
    unmatched = find_unmatched(po_lines, dn_lines, si_lines, thr=85)
    assert unmatched == []


def test_step_10_per_line_despite_mixed_po():
    """FR-8.1a: one unit-numbered PO line must not disable step-10 mapping
    for the remaining step-10 lines."""
    from app.services.matching import get_matching_candidates

    po_lines = [
        {"line_item_no": "10", "description": "Hexagon head bolt M12 x 50mm grade 8.8"},
        {"line_item_no": "5", "description": "Item 5"},
    ]
    dn_lines = [
        {"line_item_no": "1", "description": "Bolt"},
        {"line_item_no": "5", "description": "Item 5"},
    ]
    cands = get_matching_candidates(po_lines[0], dn_lines, all_po_lines=po_lines)
    assert len(cands) == 1
    assert cands[0]["line_item_no"] == "1"
    # the unit-numbered line still exact-matches, unaffected
    cands5 = get_matching_candidates(po_lines[1], dn_lines, all_po_lines=po_lines)
    assert len(cands5) == 1
    assert cands5[0]["line_item_no"] == "5"


def test_step_10_reverse_per_line():
    """FR-8.1a: reverse check maps DN line 1 to PO line 10 per line."""
    from app.services.matching import find_unmatched

    po_lines = [
        {"line_item_no": "10", "description": "Hexagon head bolt M12 x 50mm grade 8.8"},
        {"line_item_no": "5", "description": "Flat Washer SAE"},
    ]
    dn_lines = [{"line_item_no": "1", "description": "Bolt"}]
    assert find_unmatched(po_lines, dn_lines, [], thr=85) == []
