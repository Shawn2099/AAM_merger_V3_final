"""Document classification tests (VLM is sole source of truth per SPEC §7.2 / §7.3)."""

from app.models import DocType
from app.services.extraction import is_manual_only


def test_customs_never_vlm():
    """FR-5.2: CUSTOMS and SHIPPING are manual-only and bypass the VLM.

    COMMERCIAL_INVOICE was removed from the product and is no longer a valid
    type, so it is absent here rather than asserted.
    """
    assert is_manual_only("CUSTOMS") is True
    assert is_manual_only("SHIPPING") is True
    assert is_manual_only("PO") is False
    assert is_manual_only("DN") is False
    assert is_manual_only("SI") is False
    assert is_manual_only("COMBINED") is False
    assert is_manual_only("UNKNOWN") is False


def test_commercial_invoice_is_gone():
    """The type is removed everywhere, not merely hidden from the UI.

    A leftover enum member would keep accepting uploads and would still be
    stamped into packets, which is exactly the silent behaviour this removal
    is meant to end.
    """
    assert "COMMERCIAL_INVOICE" not in {e.value for e in DocType}
    with __import__("pytest").raises(ValueError):
        DocType("COMMERCIAL_INVOICE")


def test_vlm_doc_types_covered_by_model():
    """FR-5.1: the 7 valid document types in the database model."""
    valid_types = {e.value for e in DocType}
    assert valid_types == {
        "PO",
        "DN",
        "SI",
        "COMBINED",
        "CUSTOMS",
        "SHIPPING",
        "UNKNOWN",
    }
