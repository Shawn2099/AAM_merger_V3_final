def test_match_by_line_no():
    from app.services.matching import match_line

    po = {"line_item_no": "5", "description": "Widget A"}
    dn = [{"line_item_no": "5", "description": "Widget A", "qty": 10}]
    assert match_line(po, dn, [], thr=85)["matched"] is True


def test_fuzzy_fallback():
    from app.services.matching import match_line

    po = {"line_item_no": None, "description": "Widget A 10kg"}
    dn = [{"line_item_no": None, "description": "Widget A 10 KG", "qty": 10}]
    assert match_line(po, dn, [], thr=85)["matched"] is True


def test_conflict_quarantine():
    from app.services.matching import match_line

    po = {"line_item_no": "5", "description": "Widget A"}
    dn = [
        {"line_item_no": "5", "description": "Conflict", "qty": 10},
        {"line_item_no": "5", "description": "Widget A", "qty": 10},
    ]
    assert match_line(po, dn, [], thr=85)["quarantine"] is True
