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
