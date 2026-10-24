"""Robustness: PO-number normalisation and grouping under hostile spellings (2026-10-24).

`normalize_po_no` keys a set on the DIGITS of the PO number (CORE_LOGIC §2).
These tests pin what that rule does with spellings a VLM or a vendor really
produces, and mark the cases where the digit key silently becomes a DIFFERENT
plausible number instead of no key at all.
"""

# ruff: noqa: RUF001  (look-alike characters are the subject under test)
from __future__ import annotations

from pathlib import Path

import pytest
from test_robustness_harness import (
    HttpVLM,
    doc_by_name,
    extraction,
    line,
    make_cfg,
    run_sync,
    sets,
    write_pdf,
)

from app.models import POSetStatus
from app.services.grouping import letters_compatible, normalize_po_no, po_letters


@pytest.fixture()
def rcfg(tmp_path, monkeypatch):
    return make_cfg(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("4500123", "4500123"),
        ("PO 4500123", "4500123"),
        ("P.O.No.4500123", "4500123"),
        ("PO#4500123", "4500123"),
        ("Order No: 4500123", "4500123"),
        ("4500123 Rev 2", "4500123"),
        ("PO 4500123, 0", "4500123"),
        ("  4500 123  ", "4500123"),
        ("4500 123", "4500123"),  # no-break space
        ("4500123​", "4500123"),  # zero-width space
        ("4500–123", "4500123"),  # en dash
        ("4500—123", "4500123"),  # em dash
        ("4.500.123", "4500123"),
        ("4,500,123", "4500123"),
        ("1" * 300, "1" * 300),  # extremely long: kept, never truncated
    ],
)
def test_spellings_that_must_share_a_key(raw, key):
    assert normalize_po_no(raw) == key


@pytest.mark.parametrize(
    "raw",
    ["N/A", "OPG", "", "   ", "4500123 REV", "4500123 Rev 2 Rev 3", "PO: 4500123 (Rev.1)"],
)
def test_spellings_that_get_no_key_and_are_held(raw):
    """No key -> missing_po_number -> held for the operator. Safe direction.
    Note 'PO: 4500123 (Rev.1)' is a false hold (recall loss, not a wrong merge)."""
    assert normalize_po_no(raw) == ""


def test_leading_zeros_are_significant_so_padded_and_unpadded_never_meet():
    """Observed: '0045001234' and '45001234' are different sets; the DN waits
    unattached. Safe (no wrong merge), costs an operator action."""
    assert normalize_po_no("0045001234") != normalize_po_no("45001234")


def test_hyphen_revision_is_not_stripped():
    """Observed: '4500123-1' keys as 45001231 (a different PO). Safe: held."""
    assert normalize_po_no("4500123-1") == "45001231"


def test_digit_only_key_collides_for_differently_punctuated_numbers():
    """By design (CORE_LOGIC §2): '12/345' and '123/45' are the same key and,
    with no letters on either side, the same set. Reconciliation is then the
    only guard (a second PO document holds the set; a foreign DN usually
    mismatches)."""
    assert normalize_po_no("12/345") == normalize_po_no("123/45") == "12345"
    assert letters_compatible(po_letters("12/345"), po_letters("123/45"))


def test_letters_on_both_sides_that_differ_are_a_conflict():
    assert normalize_po_no("TOGA-PO-25-26-163") == normalize_po_no("ARM-PO-25-26-163")
    assert not letters_compatible(po_letters("TOGA-PO-25-26-163"), po_letters("ARM-PO-25-26-163"))


@pytest.mark.parametrize(
    "raw",
    [
        "PO 45OO123",  # letter O read for zero
        "PO-4500l23",  # lower-case L read for one
        "PO ４５00 12345",  # full-width digits
    ],
)
def test_misread_characters_inside_the_number_give_no_key(raw):
    """R-14: a confusable letter (or non-ASCII digit) INSIDE the digit run is a
    misread, not a shorter PO. No key -> the document is held for the operator."""
    assert normalize_po_no(raw) == ""


def test_dn_with_misread_po_number_does_not_complete_an_unrelated_set(rcfg, monkeypatch):
    HttpVLM(
        monkeypatch,
        {
            111.0: extraction("PO", "45123", None, [line("1", "10", "BOLT")]),
            333.0: extraction("SI", "INV-45123", "45123", [line("1", "10", "BOLT")]),
            # The DN belongs to PO 4500123; the model misread two zeros as letter O.
            222.0: extraction("DN", "DN-4500123", "PO 45OO123", [line("1", "10", "BOLT")]),
        },
    )
    inp = Path(rcfg.paths.input_folder)
    write_pdf(inp / "PO_45123.pdf", 111.0)
    write_pdf(inp / "SI_45123.pdf", 333.0)
    write_pdf(inp / "DN_for_4500123.pdf", 222.0)
    run_sync()
    (ps,) = sets(rcfg)
    dn = doc_by_name(rcfg, "DN_for_4500123.pdf")
    assert dn.po_no_raw == "PO 45OO123"  # precondition
    assert ps.status != POSetStatus.merged, "merged with a foreign DN"


def test_po_number_with_two_pos_in_one_field_is_held_not_split(rcfg, monkeypatch):
    """'4500012345/46' (shorthand for two POs) without the ambiguity flag keys as
    one long number that matches no set: held, never attached. Safe."""
    HttpVLM(
        monkeypatch,
        {
            111.0: extraction("PO", "4500012345", None, [line("1", "10")]),
            222.0: extraction("DN", "DN-1", "4500012345/46", [line("1", "10")]),
        },
    )
    inp = Path(rcfg.paths.input_folder)
    write_pdf(inp / "PO.pdf", 111.0)
    write_pdf(inp / "DN.pdf", 222.0)
    run_sync()
    dn = doc_by_name(rcfg, "DN.pdf")
    assert dn.po_set_id is None


def test_extremely_long_po_number_groups_and_merges_with_a_bounded_filename(rcfg, monkeypatch):
    long_po = "9" * 400
    HttpVLM(
        monkeypatch,
        {
            111.0: extraction("PO", long_po, None, [line("1", "10")]),
            222.0: extraction("DN", "DN-1", long_po, [line("1", "10")]),
            333.0: extraction("SI", None, long_po, [line("1", "10")]),  # no invoice no
        },
    )
    inp = Path(rcfg.paths.input_folder)
    for name, w in (("PO.pdf", 111.0), ("DN.pdf", 222.0), ("SI.pdf", 333.0)):
        write_pdf(inp / name, w)
    run_sync()
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.merged, ps.reconcile_reason
    # named from the PO number, truncated to 100 chars by _safe_stem
    assert Path(ps.merged_output_path).name == "9" * 100 + ".pdf"
    assert "from the PO number" in (ps.reconcile_reason or "")
