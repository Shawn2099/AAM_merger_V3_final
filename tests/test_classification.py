def test_classify_unknown_holding():
    from app.services.classification import classify

    assert classify({"raw": "random memo"}) == "UNKNOWN"


def test_customs_never_vlm():
    from app.services.classification import is_manual_only

    assert is_manual_only("CUSTOMS") is True
    assert is_manual_only("SHIPPING") is True
    assert is_manual_only("COMMERCIAL_INVOICE") is True
    assert is_manual_only("PO") is False
