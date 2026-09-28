"""F1 — PO number normalisation, built from the real vendor sample inventory.

Source: NotebookLM Section 2, 87 real documents. The same PO is printed
differently on the PO header than on its DN/SI, which is what splits one real
PO into several un-reconcilable sets under naive normalisation.

Two properties matter and both are asserted here:
  1. different spellings of ONE PO collapse to one key
  2. genuinely different POs never collide
"""

from __future__ import annotations

import pytest

from app.services.grouping import normalize_po_no

# (label, spellings on the PO, spellings on the DN/SI) — all from real samples.
REAL_SETS = [
    ("ADES 161538", ["161538", "PO 161538", "PO, Rev # 161538,0"], ["161538"]),
    ("RAK 15676", ["15676", "PO 15676"], ["15676"]),
    ("ADES 15884", ["15884", "PO 15884", "PO, Rev # 15884,0"], ["15884"]),
    ("ADES 15808", ["15808", "PO 15808"], ["15808"]),
    ("Tubestar 025/09-2026-27", ["025/09-2026-27", "PO-025-09-2026-27"], ["025/09-2026-27"]),
    ("McDermott D7264", ["D7264-PO-186000-013-01"], ["D7264-PO186000-013-01-"]),
    ("Valaris 10006-913", ["10006-0000080913"], ["10006-0000080913"]),
    ("Valaris 10006-820", ["10006-0000080820"], ["10006-0000080820"]),
    ("Valaris 10006-880", ["10006-0000080880"], ["10006-0000080880"]),
    ("McDermott P106420232", ["P106420232"], ["P106420232"]),
    ("McDermott P106420244", ["P106420244"], ["P106420244"]),
    ("Ensign BH0011409-2", ["BH0011409-2"], ["BH0011409-2"]),
    ("BSTS BSTSPO241200611", ["PO.No: BSTSPO241200611"], ["BSTSPO241200611"]),
    ("IRE 63615", ["PO No.: 63615"], ["63615"]),
    ("NOMAC 8300023893", ["Purchase Order: 8300023893"], ["8300023893"]),
    ("McDermott D7519-581", ["D7519-PO-581100-007-01"], ["D7519-PO-581100-007-01"]),
    ("McDermott D7519-233", ["D7519-PO-233130-055-01"], ["D7519-PO-233130-055-01"]),
    ("Halliburton 4518897682", ["4518897682"], ["4518897682"]),
    ("Nabors 37028240", ["37028240"], ["37028240"]),
    ("H&P 540012562", ["540012562"], ["540012562"]),
]


@pytest.mark.parametrize(
    ("label", "po_forms", "dn_forms"), REAL_SETS, ids=[r[0] for r in REAL_SETS]
)
def test_one_po_spelled_differently_yields_one_key(label, po_forms, dn_forms):
    """The core F1 fix: a PO and its DN/SI must group together."""
    keys = {normalize_po_no(f) for f in po_forms + dn_forms}
    assert len(keys) == 1, f"{label} split into {sorted(keys)}"


def test_distinct_pos_never_collide():
    """The dangerous direction: over-stripping must not merge two real POs."""
    canonical = {}
    for label, _po_forms, dn_forms in REAL_SETS:
        canonical[label] = normalize_po_no(dn_forms[0])
    labels = list(canonical)
    collisions = [
        (a, b, canonical[a])
        for i, a in enumerate(labels)
        for b in labels[i + 1 :]
        if canonical[a] == canonical[b]
    ]
    assert not collisions, f"distinct POs collided: {collisions}"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Label words and prefixes
        ("PO 161538", "161538"),
        ("PO161538", "161538"),
        ("po 1234", "1234"),
        ("PO-1234", "1234"),
        ("PO/1234", "1234"),
        ("PO1234", "1234"),
        ("PO No.: 63615", "63615"),
        ("PO.No: BSTSPO241200611", "BSTSPO241200611"),
        ("Purchase Order: 8300023893", "8300023893"),
        # SAP revision counter
        ("PO, Rev # 161538,0", "161538"),
        ("161538,0", "161538"),
        # Structured codes must survive intact
        ("D7264-PO-186000-013-01", "D7264PO18600001301"),
        ("D7264-PO186000-013-01-", "D7264PO18600001301"),
        ("D7519-PO-581100-007-01", "D7519PO58110000701"),
        ("BH0011409-2", "BH00114092"),
        ("P106420232", "P106420232"),
        ("10006-0000080913", "100060000080913"),
        ("025/09-2026-27", "02509202627"),
        ("PO-025-09-2026-27", "02509202627"),
        ("BSTSPO241200611", "BSTSPO241200611"),
    ],
)
def test_known_variants(raw, expected):
    assert normalize_po_no(raw) == expected


def test_sap_internal_id_stays_distinct_from_the_po_number():
    """'PO_2112_15676_0_US' is a different identifier, not another spelling.

    The VLM must pick the header PO number; the normaliser must NOT collapse
    the SAP internal reference onto the PO, or two unrelated keys would merge.
    """
    assert normalize_po_no("PO_2112_15676_0_US") != normalize_po_no("15676")


def test_blank_and_none_are_safe():
    assert normalize_po_no("") == ""
    assert normalize_po_no("   ") == ""
    assert normalize_po_no(None) == ""


def test_non_string_is_refused_rather_than_stringified():
    """An int/Decimal must not become a plausible-looking but wrong key."""
    for bad in (161538, 161538.0):
        with pytest.raises(TypeError):
            normalize_po_no(bad)


def test_normalization_is_idempotent():
    """Normalising an already-normalised key must not change it again."""
    for _label, po_forms, dn_forms in REAL_SETS:
        for f in po_forms + dn_forms:
            once = normalize_po_no(f)
            assert normalize_po_no(once) == once, f"not idempotent for {f!r}"
