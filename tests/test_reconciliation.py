def test_reconcile_exact():
    from app.services.reconciliation import reconcile

    assert reconcile(100000, 100000, 100000)["ok"] is True  # 100*1000
    assert reconcile(100000, 90000, 100000)["ok"] is False


def test_negative_quarantine():
    from app.services.reconciliation import reconcile

    assert reconcile(100000, -10000, 100000)["quarantine"] is True


def test_price_flag_priority():
    from app.services.reconciliation import check_price

    assert check_price(10000, 10000)["flag"] is False
    assert check_price(10000, 9000)["flag"] is True  # price-only not block, but flagged
