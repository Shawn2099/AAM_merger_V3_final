"""Pinned accepted limitations from the 2026-10 code review (PRODUCT section 8).

Each test pins the CURRENT behaviour, including the behaviour the operator has
accepted as a limitation. If one of these starts failing, that is a decision to
revisit the matching rule, not a test to quietly update. Each one is documented
in AAM_merger_V3_PRODUCT.md section 8 and mitigated by a person reviewing the packet.
"""

from __future__ import annotations

from app.services.grouping import normalize_po_no, po_letters
from app.services.matching import normalize_line_no
from app.services.reconciliation import compare_po_set_lines


def _ln(no, qty, desc="Widget"):
    """A line dict with the quantity already scaled x1000, as stored."""
    return {"line_item_no": no, "description": desc, "quantity": qty * 1000}


def test_known_limitation_rescanned_dn_quantities_are_summed():
    """PRODUCT §8 'H - a re-scanned or re-issued DN or SI is summed twice'.

    A re-scan of DN-A has different bytes, so it is a second document. Its
    rows are summed into the DN pool, so 5 + 5 reconciles against PO 10.
    """
    po = [_ln("1", 10)]
    dn_doc_a = [_ln("1", 5)]
    dn_doc_a_rescan = [_ln("1", 5)]
    si = [_ln("1", 10)]

    res = compare_po_set_lines(po, dn_doc_a + dn_doc_a_rescan, si)

    assert res["dn_totals"] == {"1": 10_000}
    assert res["flags"] == []


def test_known_limitation_numberless_row_matches_a_size_variant_by_description():
    """PRODUCT §8 'M - a numberless row can match a description that differs by one token'.

    Token-sort similarity >= 85 treats 'Gate valve 4in CL150' as the PO line
    'Gate valve 2in CL150'. The link is only an advisory description_match
    flag, not a quantity or identification diff, so the set can still merge.
    """
    po = [_ln("2", 10, desc="Gate valve 2in CL150")]
    dn = [_ln(None, 10, desc="Gate valve 4in CL150")]
    si = [_ln(None, 10, desc="Gate valve 4in CL150")]

    res = compare_po_set_lines(po, dn, si)

    assert res["po_totals"] == {"2": 10_000}
    assert res["dn_totals"] == {"2": 10_000}
    assert res["si_totals"] == {"2": 10_000}
    assert res["po_fail"] is None
    assert {f["type"] for f in res["flags"]} == {"description_match"}


def test_known_limitation_zeros_after_a_dash_are_stripped():
    """PRODUCT §8 'L - zeros after a dash, underscore or slash are stripped'.

    normalize_line_no strips leading zeros after '-', '_' and '/' (documented
    in its docstring), so '1-05' and '1-5' share a key. A dotted segment keeps
    its zeros, so '1.05' and '1.5' stay distinct.
    """
    assert normalize_line_no("1-05") == normalize_line_no("1-5") == "1-5"
    assert normalize_line_no("1.05") != normalize_line_no("1.5")


def test_known_limitation_po_separators_are_flattened():
    """PRODUCT §8 'L - PO separators are flattened, so distinct POs can share a key'.

    Separators are dropped on purpose so printed variations agree, which also
    means '12-345' and '123-45' are the same key.
    """
    assert normalize_po_no("12-345") == normalize_po_no("123-45") == "12345"
    assert normalize_po_no("D7264-PO-186000-013-01") == normalize_po_no("D7264-PO186000-013-01-")


def test_known_limitation_po_prefix_strip_eats_the_letters_po():
    """PRODUCT §8 'L - a PO number starting with the letters PO loses them'.

    The leading 'PO' prefix is stripped even when it is part of the number,
    so 'POST123' loses 'PO' (letters 'ST'). The grouping key is now the digits
    only ('123'), so the lost 'PO' no longer changes the key; the letters
    check sees 'ST' rather than 'POST'.
    """
    assert normalize_po_no("POST123") == "123"
    assert po_letters("POST123") == "ST"


def test_known_limitation_letters_on_one_side_join_any_po_with_the_same_digits():
    """PRODUCT §8 'M - the PO key is the digits; letters only veto a clash'.

    Letters on one side only never split a PO, so 'P213899' and another
    vendor's plain '213899' share a set. Only letters on BOTH sides that differ
    are refused (and reconcile quarantines a set whose documents disagree).
    """
    from app.services.grouping import letters_compatible

    assert normalize_po_no("P213899") == normalize_po_no("213899") == "213899"
    assert letters_compatible(po_letters("P213899"), po_letters("213899"))
    assert not letters_compatible(po_letters("TOGA-PO-25-26-163"), po_letters("ARM-PO-25-26-163"))


def test_known_limitation_j_prefix_digits_do_not_match_the_plain_number():
    """PRODUCT §8: 'J255-300115876' keys as 255300115876, not 300115876 (held)."""
    assert normalize_po_no("J255-300115876") == "255300115876"
    assert normalize_po_no("J255-300115876") != normalize_po_no("300115876")


def test_known_limitation_a_po_number_without_digits_never_groups():
    """PRODUCT §8: a letters-only PO number has no key; the document is held."""
    assert normalize_po_no("OPG") == ""
    assert normalize_po_no("N/A") == ""
