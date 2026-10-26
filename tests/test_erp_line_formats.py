"""ERP line-item numbering matrix.

Covers the common conventions that map cleanly, the ones that are deliberately
not mapped (SAP step-10, JDE 1000-increment, NetSuite gaps), and the labels and
punctuation a model may return even though the prompt asks for a number only.
"""

from __future__ import annotations

import pytest

from app.services.matching import normalize_line_no
from app.services.reconciliation import compare_po_set_lines

# ---------------------------------------------------------------------------
# Forms that work today.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", "1"),
        ("01", "1"),
        ("0001", "1"),
        ("00010", "10"),  # SAP EBELN item 00010
        ("010", "10"),
        ("1-1", "1-1"),
        ("01-01", "1-1"),
        ("1-01", "1-1"),
        ("1.05", "1.05"),  # dotted sub-line is intentionally kept
        ("001A", "1A"),
        ("1a", "1a"),
        (None, ""),
        ("", ""),
    ],
)
def test_line_forms_that_work(raw, expected):
    assert normalize_line_no(raw) == expected


# ---------------------------------------------------------------------------
# Conventions that currently produce different keys.
# ---------------------------------------------------------------------------


CONVENTION_PAIRS = [
    ("sap step-10 vs serial", "10", "1"),
    ("sap change-order 11 vs serial", "11", "1"),
    ("jde 1000 increment", "1000", "1"),
    ("netsuite line id gap", "3", "2"),
    ("oracle line/shipment", "1.1", "1"),
    ("decimal trailing zero", "1.000", "1"),
    ("decimal one place", "1.0", "1"),
    ("hash label", "#1", "1"),
    ("trailing dot", "1.", "1"),
    ("trailing paren", "1)", "1"),
    ("item label", "Item 1", "1"),
    ("sl label", "SL 1", "1"),
    ("line label", "Line 1", "1"),
    ("slash separator", "1/1", "1-1"),
    ("space separator", "1 1", "1-1"),
    ("en dash", "1\u20131", "1-1"),
    ("letter case", "1A", "1a"),
]


@pytest.mark.parametrize(
    ("label", "left", "right"), CONVENTION_PAIRS, ids=[c[0] for c in CONVENTION_PAIRS]
)
def test_conventions_are_currently_distinct(label, left, right):
    """Document the current, fail-safe behaviour: no silent renumbering."""
    assert normalize_line_no(left) != normalize_line_no(right)


@pytest.mark.parametrize(
    ("label", "left", "right"), CONVENTION_PAIRS, ids=[c[0] for c in CONVENTION_PAIRS]
)
@pytest.mark.xfail(
    strict=True,
    reason="common ERP line convention is not mapped to the other side's key",
)
def test_conventions_should_normalise_to_one_key(label, left, right):
    assert normalize_line_no(left) == normalize_line_no(right)


# ---------------------------------------------------------------------------
# Description fallback is only for a blank number, never for a wrong number.
# ---------------------------------------------------------------------------


def test_wrong_nonempty_line_number_is_not_rescued_by_description():
    po = [
        {
            "line_item_no": "10",
            "description": "FILTER ELEMENT P/N 250025-526",
            "quantity": 5000,
        }
    ]
    vendor = [
        {
            "line_item_no": "1",  # SAP item 10, but the invoice printed its own line 1
            "description": "FILTER ELEMENT P/N 250025-526",
            "quantity": 5000,
        }
    ]
    result = compare_po_set_lines(po, vendor, vendor)
    assert [f["type"] for f in result["flags"]] == ["identification", "identification"]
    assert result["dn_totals"] == {}
    assert result["si_totals"] == {}
    assert all(f.get("type") != "description_match" for f in result["flags"])


def test_blank_line_number_does_use_description_fallback():
    po = [
        {
            "line_item_no": "10",
            "description": "FILTER ELEMENT P/N 250025-526",
            "quantity": 5000,
        }
    ]
    vendor = [
        {
            "line_item_no": None,
            "description": "FILTER ELEMENT P/N 250025-526",
            "quantity": 5000,
        }
    ]
    result = compare_po_set_lines(po, vendor, vendor)
    assert result["dn_totals"] == {"10": 5000}
    assert result["si_totals"] == {"10": 5000}
    assert [f["type"] for f in result["flags"]] == ["description_match", "description_match"]


# ---------------------------------------------------------------------------
# A line collision can attach a row to the wrong PO line.
# ---------------------------------------------------------------------------


def test_sap_10_and_vendor_1_attach_to_po_line_1_if_it_exists():
    """The dangerous collision: a PO that contains both line 1 and line 10."""
    po = [
        {"line_item_no": "1", "description": "BOLT", "quantity": 5000},
        {"line_item_no": "10", "description": "NUT", "quantity": 5000},
    ]
    vendor = [
        {"line_item_no": "1", "description": "NUT", "quantity": 5000},
    ]
    result = compare_po_set_lines(po, vendor, vendor)
    # The vendor row attaches to PO line 1 by number; line 10 has nothing.
    assert result["dn_totals"].get("1") == 5000
    assert "10" not in result["dn_totals"]
    # This is the collision the product cannot detect: the descriptions differ,
    # but a printed number is authoritative.
    assert any(f.get("type") == "quantity" for f in result["flags"])
