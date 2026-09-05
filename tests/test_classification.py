"""Document classification tests (VLM is sole source of truth per SPEC §7.2 / §7.3)."""

from app.models import DocType
from app.services.extraction import is_manual_only


def test_customs_never_vlm():
    """FR-5.2: CUSTOMS, SHIPPING, COMMERCIAL_INVOICE are manual-only and bypass VLM."""
    assert is_manual_only("CUSTOMS") is True
    assert is_manual_only("SHIPPING") is True
    assert is_manual_only("COMMERCIAL_INVOICE") is True
    assert is_manual_only("PO") is False
    assert is_manual_only("DN") is False
    assert is_manual_only("SI") is False
    assert is_manual_only("COMBINED") is False
    assert is_manual_only("UNKNOWN") is False


def test_vlm_doc_types_covered_by_model():
    """FR-5.1: 8 valid document types in database model."""
    valid_types = {e.value for e in DocType}
    assert valid_types == {
        "PO",
        "DN",
        "SI",
        "COMBINED",
        "CUSTOMS",
        "SHIPPING",
        "COMMERCIAL_INVOICE",
        "UNKNOWN",
    }
