"""A pass/fail matrix for the uncommitted digits-only PO key and letters guard.

The matrix is deliberately split into three layers:

* pure-function cases (labels, revisions, structured codes, grouping);
* property tests with Hypothesis (the key is digits-only, idempotent, and
  insensitive to labels/separators in the ways the contract claims);
* database cases for `get_or_create_po_set` / `group_document` / the letters
  safety check.

Known defects found in the current working tree are pinned with
`pytest.mark.xfail(strict=True)`.  Strict xfail means a later fix makes the
test fail (XPASS), which is the reminder to remove the xfail and record the
behaviour as passing.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.core.config import AppConfig, load_config
from app.core.database import get_engine
from app.models import Base, DocType, Document, ExtractionStatus, POSet, POSetStatus
from app.services.grouping import (
    PoLettersConflict,
    get_or_create_po_set,
    group_document,
    held_letters_conflict,
    letters_compatible,
    normalize_po_no,
    po_letters,
)

# ---------------------------------------------------------------------------
# Pure-function matrix: every row states the key and the letters separately.
# ---------------------------------------------------------------------------

NORMALISATION_MATRIX = [
    # plain numbers and labels
    ("plain", "161538", "161538", ""),
    ("PO prefix", "PO 161538", "161538", ""),
    ("P.O. prefix", "P.O. 161538", "161538", ""),
    ("P/0 prefix", "P/0 161538", "161538", ""),
    ("hash prefix", "PO#161538", "161538", ""),
    ("padded", "   PO 161538  ", "161538", ""),
    ("lowercase label", "po 161538", "161538", ""),
    # labels that are part of the real number
    ("LPO slash form", "LPO26/2360", "262360", ""),
    ("PO slash form", "PO 26/2360", "262360", ""),
    ("purchase order label", "PURCHASE ORDER NO: 8300023893", "8300023893", ""),
    ("PO No dot form", "PO No. 161538", "161538", ""),
    # revision forms
    ("SAP comma revision", "PO, Rev # 161538,0", "161538", ""),
    ("SAP revision two digits", "PO, Rev # 161538, 12", "161538", ""),
    ("bare comma revision", "161538,0", "161538", ""),
    ("trailing Rev word", "161538 Rev 2", "161538", ""),
    ("trailing Rev dot", "PO No. 161538 Rev. 0", "161538", ""),
    # grouping separators
    ("western thousands", "12,345", "12345", ""),
    ("indian thousands", "1,00,000", "100000", ""),
    ("grouped after label", "PO 12,345", "12345", ""),
    # structured codes: digits are the key, letters are the safety check
    ("structured code", "D7264-PO-186000-013-01", "726418600001301", "DPO"),
    ("structured code trailing dash", "D7264-PO186000-013-01-", "726418600001301", "DPO"),
    ("P-prefixed code", "P106420232", "106420232", "P"),
    ("BH-prefixed code", "BH0011409-2", "00114092", "BH"),
    ("site suffix", "60213576 OPG", "60213576", "OPG"),
    ("J-prefixed code", "J255-300115876", "255300115876", "J"),
    ("TOGA code", "TOGA-PO-25-26-163", "2526163", "TOGAPO"),
    ("ARM code", "ARM-PO-25-26-163", "2526163", "ARMPO"),
    ("slash code", "AB12/3456", "123456", "AB"),
    ("leading-zero code", "025/09-2026-27", "02509202627", ""),
    ("PO leading-zero code", "PO-025-09-2026-27", "02509202627", ""),
    ("long structured code", "10006-0000080913", "100060000080913", ""),
    ("BST token", "BSTSPO241200611", "241200611", "BSTSPO"),
    ("PO.No structured token", "PO.No: BSTSPO241200611", "241200611", "BSTSPO"),
    # Accepted limitation: a three-digit tail is treated as grouping, not revision.
    ("three digit tail is kept", "161538,100", "161538100", ""),
    # no digits: no key
    ("letters only", "OPG", "", "OPG"),
    ("not applicable", "N/A", "", "NA"),
    ("empty", "", "", ""),
    ("None", None, "", ""),
]


@pytest.mark.parametrize(
    ("label", "raw", "expected_key", "expected_letters"),
    NORMALISATION_MATRIX,
    ids=[row[0] for row in NORMALISATION_MATRIX],
)
def test_normalisation_matrix(label, raw, expected_key, expected_letters):
    assert normalize_po_no(raw) == expected_key
    assert po_letters(raw) == expected_letters


# ---------------------------------------------------------------------------
# Equivalence and non-collision matrix.
# ---------------------------------------------------------------------------

EQUIVALENT_SPELLINGS = [
    ("161538", "PO 161538", "P.O. 161538", "P/0 161538", "PO, Rev # 161538,0", "161538 Rev 2"),
    ("262360", "LPO26/2360", "PO 26/2360"),
    ("726418600001301", "D7264-PO-186000-013-01", "D7264-PO186000-013-01-"),
    ("2526163", "TOGA-PO-25-26-163", "TOGA-PO-25-26-163 Rev 0"),
    ("12345", "12,345", "PO 12,345"),
    ("100000", "1,00,000"),
    ("100060000080913", "10006-0000080913"),
]


@pytest.mark.parametrize(
    "spellings", EQUIVALENT_SPELLINGS, ids=[s[1] for s in EQUIVALENT_SPELLINGS]
)
def test_spellings_of_one_po_share_the_key(spellings):
    keys = {normalize_po_no(value) for value in spellings}
    assert len(keys) == 1, keys


DISTINCT_PAIRS = [
    ("161538", "1615382"),
    ("12,345", "12"),
    ("1,000", "1"),
    ("162538", "161538"),
    ("300115876", "J255-300115876"),
    ("TOGA-PO-25-26-163", "TOGA-PO-25-26-164"),
    ("262360", "2623600"),
]


@pytest.mark.parametrize("left,right", DISTINCT_PAIRS)
def test_distinct_pos_do_not_collide(left, right):
    assert normalize_po_no(left) != normalize_po_no(right)


LETTER_COMPATIBILITY = [
    ("", ""),
    (None, "OPG"),
    ("opg", ""),
    ("OPG", "opg"),
    ("TOGAPO", "TOGAPO"),
    ("", "ARMPO"),
    ("TOGAPO", ""),
]


@pytest.mark.parametrize("left,right", LETTER_COMPATIBILITY)
def test_letters_compatibility_true(left, right):
    assert letters_compatible(left, right) is True
    assert letters_compatible(right, left) is True


@pytest.mark.parametrize(
    "left,right",
    [
        ("TOGAPO", "ARMPO"),
        ("J", "P"),
        ("AB", "CD"),
    ],
)
def test_letters_compatibility_false(left, right):
    assert letters_compatible(left, right) is False
    assert letters_compatible(right, left) is False


# ---------------------------------------------------------------------------
# Property tests: the contract survives arbitrary text.
# ---------------------------------------------------------------------------

DIGITS = st.text(alphabet="0123456789", min_size=1, max_size=30)
SEPARATORS = st.text(alphabet="-_/ .", max_size=6)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=120))
def test_key_contains_only_digits(value):
    assert set(normalize_po_no(value)) <= set("0123456789")


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=120))
def test_key_is_idempotent(value):
    once = normalize_po_no(value)
    assert normalize_po_no(once) == once


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=120))
def test_letters_are_uppercase_ascii(value):
    assert set(po_letters(value)) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=120))
def test_letters_never_invent_characters(value):
    # Not idempotent by design: "0NO" has letters "NO", and "NO" alone is a
    # label word with no letters. Re-reading letters may only drop characters.
    once = po_letters(value)
    assert set(po_letters(once)) <= set(once)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=30), st.text(max_size=30))
def test_letters_compatibility_is_symmetric(left, right):
    assert letters_compatible(left, right) == letters_compatible(right, left)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(DIGITS)
def test_leading_labels_do_not_change_a_digit_key(digits):
    assert normalize_po_no(f"PO {digits}") == digits
    assert normalize_po_no(f"P.O. {digits}") == digits
    assert normalize_po_no(f"LPO{digits}") == digits


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(DIGITS, SEPARATORS)
def test_separators_do_not_change_a_digit_key(digits, separator):
    assert normalize_po_no(f"{separator}{digits}{separator}") == digits


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(DIGITS, st.integers(min_value=0, max_value=99))
def test_short_revision_suffix_is_removed(digits, revision):
    assert normalize_po_no(f"{digits}, {revision}") == digits


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.one_of(
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        st.lists(st.text()),
    )
)
def test_non_strings_are_refused_not_coerced(value):
    with pytest.raises(TypeError):
        normalize_po_no(value)


# ---------------------------------------------------------------------------
# Database matrix: grouping, letters conflict, no-digit behaviour.
# ---------------------------------------------------------------------------


@pytest.fixture()
def cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "matrix.db")
    cfg.paths.input_folder = str(tmp_path / "input")
    cfg.paths.output_folder = str(tmp_path / "output")
    cfg.paths.quarantine_folder = str(tmp_path / "quarantine")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    cfg.paths.combined_folder = str(tmp_path / "combined")
    cfg.paths.archive_folder = str(tmp_path / "archive")
    cfg.paths.log_folder = str(tmp_path / "logs")
    for name in ("input", "output", "quarantine", "stored", "combined", "archive", "logs"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(cfg))
    return cfg


def _doc(
    cfg,
    *,
    doc_type=DocType.DN,
    po_raw=None,
    po_key=None,
    ambiguous=False,
    sha="a" * 64,
):
    eng = get_engine(cfg)
    with Session(eng) as session:
        doc = Document(
            sha256_hash=sha,
            original_filename=f"{doc_type.value.lower()}-{sha[:4]}.pdf",
            stored_path=f"{cfg.paths.stored_documents_folder}/{sha}.pdf",
            doc_type=doc_type,
            extraction_status=ExtractionStatus.valid,
            po_no_raw=po_raw,
            po_no_normalized=po_key,
            po_reference_ambiguous=ambiguous,
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        return doc


def test_get_or_create_uses_the_digit_key(cfg):
    first = get_or_create_po_set("PO 161538", cfg)
    assert first.po_no_normalized == "161538"
    assert first.po_no_letters is None
    second = get_or_create_po_set("161538", cfg)
    assert second.id == first.id


def test_one_sided_letters_attach(cfg):
    toga = get_or_create_po_set("TOGA-PO-25-26-163", cfg)
    assert toga.po_no_normalized == "2526163"
    assert toga.po_no_letters == "TOGAPO"

    plain = get_or_create_po_set("25-26-163", cfg, create=False)
    assert plain is not None and plain.id == toga.id


def test_conflicting_letters_never_mint_or_attach(cfg):
    toga = get_or_create_po_set("TOGA-PO-25-26-163", cfg)
    with pytest.raises(PoLettersConflict):
        get_or_create_po_set("ARM-PO-25-26-163", cfg)
    with pytest.raises(PoLettersConflict):
        get_or_create_po_set("ARM-PO-25-26-163", cfg, create=False)
    with Session(get_engine(cfg)) as session:
        assert session.query(POSet).filter(POSet.po_no_normalized == "2526163").count() == 1
        assert session.get(POSet, toga.id).po_no_letters == "TOGAPO"


def test_different_digits_with_different_letters_are_a_different_set(cfg):
    toga = get_or_create_po_set("TOGA-PO-25-26-163", cfg)
    arm = get_or_create_po_set("ARM-PO-25-26-029", cfg)
    assert arm.id != toga.id
    assert arm.po_no_normalized == "2526029"
    assert arm.po_no_letters == "ARMPO"


def test_group_document_refuses_a_letters_clash(cfg):
    get_or_create_po_set("TOGA-PO-25-26-163", cfg)
    doc = _doc(cfg, po_raw="ARM-PO-25-26-163", po_key="2526163", sha="b" * 64)
    assert group_document(doc, cfg) == (None, "po_letters_conflict")


def test_group_document_attaches_a_numberless_letter_side(cfg):
    toga = get_or_create_po_set("TOGA-PO-25-26-163", cfg)
    doc = _doc(cfg, po_raw="25-26-163", po_key="2526163", sha="c" * 64)
    assert group_document(doc, cfg) == (toga.id, None)


def test_held_letters_conflict_explains_why(cfg):
    toga = get_or_create_po_set("TOGA-PO-25-26-163", cfg)
    doc = _doc(cfg, po_raw="ARM-PO-25-26-163", po_key="2526163", sha="d" * 64)
    with Session(get_engine(cfg)) as session:
        assert held_letters_conflict(session, doc).id == toga.id
        compatible = _doc(cfg, po_raw="25-26-163", po_key="2526163", sha="e" * 64)
        assert held_letters_conflict(session, compatible) is None


def test_no_digit_po_is_held_and_never_mints(cfg):
    assert normalize_po_no("OPG") == ""
    assert get_or_create_po_set("OPG", cfg, create=False) is None
    with pytest.raises(ValueError, match="no digits"):
        get_or_create_po_set("OPG", cfg, create=True)

    doc = _doc(cfg, doc_type=DocType.PO, po_raw="OPG", po_key=None, sha="f" * 64)
    assert group_document(doc, cfg) == (None, "missing_po_number")
    with Session(get_engine(cfg)) as session:
        assert session.query(POSet).count() == 0


def test_ambiguous_document_is_never_attached(cfg):
    get_or_create_po_set("TOGA-PO-25-26-163", cfg)
    doc = _doc(
        cfg,
        po_raw="TOGA-PO-25-26-163",
        po_key="2526163",
        ambiguous=True,
        sha="9" * 64,
    )
    assert group_document(doc, cfg) == (None, "ambiguous_po_number")


# ---------------------------------------------------------------------------
# Known gaps in the current tree.  Strict xfail: fix them and remove the mark.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PO, Rev # 12,345", "12345"),
        ("PO, Rev # 1,000,000", "1000000"),
        ("PO, Rev # 25-26-163", "2526163"),
        ("PO, Rev # 25-26-163,0", "2526163"),
        ("PO 25-26-163", "2526163"),
        ("22613 , 0", "22613"),
        ("PO, Rev # 22613 , 0", "22613"),
    ],
)
def test_grouped_number_after_rev_prefix_is_not_truncated(raw, expected):
    assert normalize_po_no(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "PO, Rev 25-26-163",  # label without '#': cannot tell label from revision
        "Rev 5",
        "161538 Rev 2 7",  # junk after the revision
        "161538 Rev 2024",  # revision longer than 3 digits
        "161538 Rev ABC",
        "PO, Rev # 12 Rev 3",  # two markers
        "161538 Rev 2 Rev 3",
        "Rev # ",
    ],
)
def test_ambiguous_revision_shapes_give_no_key(raw):
    assert normalize_po_no(raw) == ""
    assert po_letters(raw) == ""


_REV_BASES = ["161538", "25-26-163", "PO 161538", "TOGA-PO-161538"]
_REV_WORDS = ["Rev", "REV", "rev", "Rev.", "Revision", "REV#", "Rev #", "Rev-", "Rev:"]
_REV_SEPS = ["", " ", "-", "/", "_", ", ", " (", "."]


@pytest.mark.parametrize("base", _REV_BASES)
@pytest.mark.parametrize("sep", _REV_SEPS)
@pytest.mark.parametrize("word", _REV_WORDS)
@pytest.mark.parametrize("gap", ["", " ", "#", " # "])
@pytest.mark.parametrize("rev", ["0", "2", "12", "345"])
def test_a_revision_never_becomes_part_of_the_key(base, sep, word, gap, rev):
    """A revision is stripped or the key is empty; it is never kept as digits.

    `\\b` treats a digit or "_" next to "Rev" as no boundary, so "161538Rev2"
    and "161538_Rev_2" used to key as 1615382, a different PO."""
    key = normalize_po_no(f"{base}{sep}{word}{gap}{rev}")
    assert key in ("", normalize_po_no(base))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("161538Rev2", "161538"),
        ("161538-Rev2", "161538"),
        ("161538 Rev2", "161538"),
        ("161538_Rev_2", ""),
        ("PREV 161538", "161538"),
        ("161538 REVISED", "161538"),
    ],
)
def test_glued_revision_word_is_recognised(raw, expected):
    assert normalize_po_no(raw) == expected


def test_known_limitation_a_bare_r_revision_stays_in_the_key():
    """A bare R is not read as a revision: "161538 R2" keys as 1615382, letter R.

    One-sided letters are compatible with anything, so only the digits keep it
    apart from PO 161538 (it waits unattached) and quantities guard a genuine
    1615382. Documented in PRODUCT section 8."""
    assert normalize_po_no("161538 R2") == "1615382"
    assert po_letters("161538 R2") == "R"


@pytest.mark.xfail(
    strict=True,
    reason="migration a9b0 adds po_no_letters but does not backfill legacy sets",
)
def test_legacy_set_without_letters_still_guards(cfg):
    """A legacy open set must not accept a conflicting lettered PO."""
    with Session(get_engine(cfg)) as session:
        session.add(
            POSet(
                po_no_normalized="2526163",
                po_no_letters=None,
                status=POSetStatus.pending,
            )
        )
        session.commit()
    with pytest.raises(PoLettersConflict):
        get_or_create_po_set("TOGA-PO-25-26-163", cfg)


@pytest.mark.xfail(
    strict=True,
    reason="no key-rewrite migration: legacy alphanumeric keys are not found by the new digit key",
)
def test_legacy_alphanumeric_key_is_found_by_digits(cfg):
    """The documented upgrade path must keep an existing LPO set reachable."""
    with Session(get_engine(cfg)) as session:
        session.add(
            POSet(
                po_no_normalized="LPO262360",
                po_no_letters=None,
                status=POSetStatus.pending,
            )
        )
        session.commit()
    assert get_or_create_po_set("LPO26/2360", cfg, create=False) is not None


def test_documented_vlm_raster_settings_are_accepted(tmp_path):
    data = {
        "paths": {
            "input_folder": str(tmp_path / "input"),
            "output_folder": str(tmp_path / "output"),
            "quarantine_folder": str(tmp_path / "quarantine"),
            "stored_documents_folder": str(tmp_path / "stored"),
            "database_path": str(tmp_path / "aam.db"),
            "log_folder": str(tmp_path / "logs"),
        },
        "vlm": {"raster_dpi": 150, "reasoning_effort": "high", "max_pages": 40},
    }
    AppConfig.model_validate(data)
