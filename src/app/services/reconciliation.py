def aggregate(lines):
    return sum(line["quantity"] for line in lines)


def reconcile(po, dn, si):
    if po <= 0 or dn < 0 or si < 0 or dn == 0 or si == 0:  # negative/zero → quarantine (FR-10.3)
        return {"ok": False, "quarantine": True}
    ok = po == dn and po == si
    return {"ok": ok, "quarantine": False}


def check_price(po_price, agg_price):
    return {"flag": po_price != agg_price}
