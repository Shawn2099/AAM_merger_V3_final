"""ERP PO-number format matrix.

The cases are split into:

* forms the current digits-only key already handles (passing);
* forms common in SAP, Oracle, D365, Tally and JD Edwards that currently split
  one real PO into several keys (strict xfail — a later fix turns the test into
  XPASS, which is the reminder to remove the mark);
* collision risks where one-sided letters or a missing composite key can join
  two different POs (strict xfail for the safe expectation).

No product code is changed by these tests.
"""

from __future__ import annotations

import pytest

from app.core.config import load_config
from app.core.database import get_engine
from app.models import Base
from app.services.grouping import (
    PoLettersConflict,
    get_or_create_po_set,
    normalize_po_no,
    po_letters,
)

# ---------------------------------------------------------------------------
# Forms that work today.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4500017623", "4500017623"),  # SAP EBELN, no padding
        ("PO 161538", "161538"),
        ("P.O. 161538", "161538"),
        ("PO, Rev # 161538,0", "161538"),
        ("161538 Rev 2", "161538"),
        ("12,345", "12345"),
        ("1,00,000", "100000"),
        ("NPO123456", "123456"),  # Coupa, one-sided NPO
    ],
)
def test_po_forms_that_work(raw, expected):
    assert normalize_po_no(raw) == expected


def test_po_letters_are_kept_apart():
    assert po_letters("NPO123456") == "NPO"
    assert po_letters("4500017623") == ""
    assert po_letters("TOGA-PO-25-26-163") == "TOGAPO"


# ---------------------------------------------------------------------------
# Leading zeros: SAP/Oracle/D365/Tally commonly zero-pad.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("0004500012", "4500012"),  # SAP EBELN zero padding
        ("004500017623", "4500017623"),  # manufacturer/vendor zero padding
        ("PO-000123", "123"),  # D365 number sequence
        ("000123", "123"),  # plain zero-padded sequence
        ("PO/001", "PO/1"),  # Tally / SMB voucher number
        ("00312546", "312546"),  # Oracle 8-digit PO with leading zeros
    ],
)
@pytest.mark.xfail(
    strict=True,
    reason="leading-zero spellings of one ERP PO currently split into different keys",
)
def test_leading_zero_spellings_share_the_key(left, right):
    assert normalize_po_no(left) == normalize_po_no(right)


# ---------------------------------------------------------------------------
# Version / release suffixes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("210851-0", "210851"),  # SAP PO version / "same number plus -0"
        ("PO 210851-0", "PO 210851"),
        ("4368-000", "4368"),  # JDE suffix-like tail
    ],
)
@pytest.mark.xfail(
    strict=True,
    reason="version/suffix tails are not stripped; the same PO splits",
)
def test_version_suffixes_are_not_part_of_the_key(left, right):
    assert normalize_po_no(left) == normalize_po_no(right)


def test_oracle_release_is_a_separate_key_by_design():
    """A release is a separate document; do not silently merge it with the base.

    Oracle identifies a PO by PO number + release + line + shipment.  The
    current behaviour is safe (different keys); the cost is a vendor that
    prints only the base number does not attach to the release set.
    """
    assert normalize_po_no("00312546-REL0100") != normalize_po_no("00312546")


# ---------------------------------------------------------------------------
# Indian financial-year notations.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("PO/2025-26/001", "PO/2025-2026/001"),
        ("PO/25-26/001", "PO/2025-26/001"),
    ],
)
@pytest.mark.xfail(
    strict=True,
    reason="Tally/Indian FY spellings of one PO currently produce different digit keys",
)
def test_indian_financial_year_spellings_share_the_key(left, right):
    assert normalize_po_no(left) == normalize_po_no(right)


# ---------------------------------------------------------------------------
# Letters: one-sided attachment and composite-key risk.
# ---------------------------------------------------------------------------


def test_different_prefixed_pos_with_the_same_digits_are_held(cfg):
    """If both sides print letters, they are treated as different POs."""
    get_or_create_po_set("AB-12345", cfg)
    with pytest.raises(PoLettersConflict):
        get_or_create_po_set("CD-12345", cfg)


@pytest.mark.xfail(
    strict=True,
    reason="one-sided prefix rule can attach a plain vendor number to a different prefixed PO",
)
def test_a_plain_number_does_not_join_a_prefixed_po(cfg):
    get_or_create_po_set("AB-12345", cfg)
    assert get_or_create_po_set("12345", cfg, create=False) is None


@pytest.mark.xfail(
    strict=True,
    reason="JDE company/type/suffix are not represented; a plain PO number joins the first prefixed set",
)
def test_jde_composite_key_is_not_collapsed_to_a_plain_number(cfg):
    """JDE key = PONUMBER + COMPANY + POTYPE + POSUFFIX (F4311)."""
    get_or_create_po_set("OP-4368", cfg)
    assert get_or_create_po_set("4368", cfg, create=False) is None


def test_a_prefixed_po_and_its_plain_spelling_share_a_set(cfg):
    """The current, deliberate one-sided rule; documented as a risk above."""
    op = get_or_create_po_set("OP-4368", cfg)
    plain = get_or_create_po_set("4368", cfg, create=False)
    assert plain is not None and plain.id == op.id


def test_jde_type_letters_conflict_when_both_sides_print_them(cfg):
    get_or_create_po_set("OP-4368", cfg)
    with pytest.raises(PoLettersConflict):
        get_or_create_po_set("SO-4368", cfg)


@pytest.fixture()
def cfg(tmp_path):
    config = load_config("config.example.yaml")
    config.paths.database_path = str(tmp_path / "erp_po.db")
    config.paths.input_folder = str(tmp_path / "input")
    config.paths.output_folder = str(tmp_path / "output")
    config.paths.quarantine_folder = str(tmp_path / "quarantine")
    config.paths.stored_documents_folder = str(tmp_path / "stored")
    config.paths.log_folder = str(tmp_path / "logs")
    for name in ("input", "output", "quarantine", "stored", "logs"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(config))
    return config
