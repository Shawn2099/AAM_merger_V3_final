def test_normalize():
    from app.services.grouping import normalize_po_no

    assert normalize_po_no("PO-1234") == "PO1234"
    assert normalize_po_no("po 1234") == "PO1234"
    assert normalize_po_no("PO/1234") == "PO1234"


def test_grouping_same_set():
    from app.core.config import load_config
    from app.services.grouping import get_or_create_po_set

    cfg = load_config("config.example.yaml")
    a = get_or_create_po_set("PO-1234", cfg)
    b = get_or_create_po_set("po 1234", cfg)
    assert a.id == b.id
