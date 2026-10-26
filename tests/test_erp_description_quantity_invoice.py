"""ERP description, quantity and invoice-format matrix.

The checks use the exact production helpers:

* `matching._norm` + `rapidfuzz.token_sort_ratio` (description fallback);
* `sanitizer.parse_quantity_scaled` (the only quantity signal);
* `merge._safe_stem` and `merge._resolve_output_path` (packet naming).

Description and quantity gaps are strict xfail: the current behaviour is
fail-safe/quarantine or a wrong match, but the ERP format is common.
"""

from __future__ import annotations

import pytest
from rapidfuzz import fuzz

from app.services.matching import _norm
from app.services.merge import MergeNamingError, _resolve_output_path, _safe_stem
from app.services.sanitizer import parse_quantity_scaled

# ---------------------------------------------------------------------------
# Description fallback.
# ---------------------------------------------------------------------------


def _score(left: str, right: str) -> float:
    return fuzz.token_sort_ratio(_norm(left), _norm(right))


def test_description_with_uom_appended_still_matches():
    assert _score("GATE VALVE 2IN CL150", "GATE VALVE 2IN CL150 2 EA") >= 85


def test_description_punctuation_and_case_still_matches():
    assert _score("FILTER, ELEMENT", "filter element") >= 85


def test_description_bilingual_text_still_matches():
    assert _score("GATE VALVE 2IN CL150", "GATE VALVE 2IN CL150 بوابة") >= 85


@pytest.mark.xfail(
    strict=True,
    reason="SAP MAKTX is 40 chars; truncation vs full vendor text scores below 85",
)
def test_sap_40_char_description_truncation_matches():
    sap = "FILTER, ELEMENT (REPLACEMENT) - MFG: SUL - P/N# 250025-526"
    vendor = (
        "FILTER, ELEMENT (REPLACEMENT) - MFG: SUL - P/N# 250025-526 Item No: 5061800.01.11.00362"
    )
    assert _score(sap, vendor) >= 85


@pytest.mark.xfail(
    strict=True,
    reason="a vendor 'Line Item - N' marker can push a numberless row below 85",
)
def test_vendor_line_item_marker_still_matches():
    assert (
        _score(
            "FILTER ELEMENT P/N 250025-526",
            "FILTER ELEMENT P/N 250025-526 Line Item - 1",
        )
        >= 85
    )


@pytest.mark.xfail(
    strict=True,
    reason="ERP metadata appended to the description drops similarity below 85",
)
def test_erp_metadata_in_description_matches():
    assert (
        _score(
            "WRENCH 3/4 DRIVE AIR IMPACT",
            'WRENCH 3/4" DRIVE AIR IMPACT LineRel No: 11 Part Main Group: CONS',
        )
        >= 85
    )


@pytest.mark.xfail(
    strict=True,
    reason="common abbreviation/spacing form scores just below the 85 threshold",
)
def test_abbreviated_description_matches():
    assert _score("NUT, HEX 9/16 IN-12 UNC", "NUT HEX 9/16IN 12UNC") >= 85


@pytest.mark.xfail(
    strict=True,
    reason="same family, different size scores 96.9 and can attach the wrong item",
)
def test_same_family_different_size_does_not_match():
    assert (
        _score(
            "FILTER, OIL, SPIN ON, 4-11/16 OD",
            "FILTER, OIL, SPIN ON, 3-11/16 OD",
        )
        < 85
    )


# ---------------------------------------------------------------------------
# Quantity parser (SAP QUAN 13,3 and UOM variants).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", 1000),
        ("1.0", 1000),
        ("0.5", 500),
        ("0.50", 500),
        ("0.500", 500),
        ("12.45000000", 12450),
        ("1,200.00", 1_200_000),
        ("1,00,000", 100_000_000),
    ],
)
def test_quantity_formats_that_work(raw, expected):
    assert parse_quantity_scaled(raw) == expected


@pytest.mark.parametrize("raw", ["1e3", "1_000"])
def test_exponent_and_underscore_are_accepted_by_current_parser(raw):
    """Documented limitation: a vendor using these forms is not quarantined."""
    assert parse_quantity_scaled(raw) == 1_000_000


@pytest.mark.parametrize("raw", ["0", "-1", "1 000", "1.234,50", "", "2 EA"])
def test_quantity_forms_that_are_safely_rejected(raw):
    with pytest.raises(ValueError):
        parse_quantity_scaled(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.125", 125),  # SAP QUAN 13,3
        ("1.005", 1005),
        ("1.234", 1234),
    ],
)
@pytest.mark.xfail(
    strict=True,
    reason="SAP QUAN is 13,3 but the sanitizer allows only 2 significant decimals",
)
def test_three_decimal_quantity_is_accepted(raw, expected):
    assert parse_quantity_scaled(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [("2 EA", 2000), ("12.5 EA", 12_500)])
@pytest.mark.xfail(
    strict=True,
    reason="a UOM left in the quantity string is rejected instead of stripped",
)
def test_uom_in_quantity_is_stripped(raw, expected):
    assert parse_quantity_scaled(raw) == expected


def test_three_decimal_quantity_reaches_the_extraction_path_as_zero():
    from app.services.extraction import _parse_scaled_int

    assert _parse_scaled_int("0.125") == 0


# ---------------------------------------------------------------------------
# Invoice numbers / packet naming.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("INV/2025-26/001", "INV2025-26001"),
        ("INV 2025-26 001", "INV2025-26001"),
        ("INV#2025-26#001", "INV2025-26001"),
        ("INV-2025-26-001", "INV-2025-26-001"),
        ("INV.2025-26.001", "INV.2025-26.001"),
        ("INV_2025-26_001", "INV_2025-26_001"),
        ("GST/2025-26/0001", "GST2025-260001"),
    ],
)
def test_gst_and_erp_invoice_stems(raw, expected):
    assert _safe_stem(raw) == expected


def test_separator_variants_of_one_invoice_share_a_stem():
    assert _safe_stem("INV/2025/001") == _safe_stem("INV 2025 001")
    assert _safe_stem("INV/2025/001") == _safe_stem("INV#2025#001")


def test_long_invoice_number_is_bounded():
    assert len(_safe_stem("A" * 150)) == 100


def test_unicode_invoice_number_falls_back_to_alphanumerics():
    # `é` is alphanumeric and is kept; a symbol such as € is not.
    assert _safe_stem("INVé001") == "INVé001"
    assert _safe_stem("INV€001") == "INV001"


def test_one_invoice_for_two_pos_refuses_the_second_packet(tmp_path):
    """Oracle/D365 allow one invoice on multiple POs; the second must not clobber."""
    output = tmp_path / "output"
    output.mkdir()
    existing = output / "INV-001.pdf"
    existing.write_bytes(b"%PDF-existing")

    with pytest.raises(MergeNamingError):
        _resolve_output_path("INV-001", 2, output, current_path=None)

    assert existing.read_bytes() == b"%PDF-existing"


def test_a_different_invoice_name_does_not_collide(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "INV-001.pdf").write_bytes(b"%PDF-existing")

    path = _resolve_output_path("INV-002", 2, output, current_path=None)
    assert path.name == "INV-002.pdf"


def test_credit_note_negative_quantity_is_quarantined():
    with pytest.raises(ValueError):
        parse_quantity_scaled("-5")
