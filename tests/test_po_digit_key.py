"""The PO grouping key is the DIGITS of the PO number; letters are a safety check.

Real shapes from the vendor samples: the same PO prints as `60213576 OPG` on one
document and `60213576` on another, `LPO26/2360` on a header and `PO 26/2360` on
its DN, `P213899` against `213899`. Letters on only one side never split a PO.
Letters on BOTH sides that differ (`TOGA-PO-25-26-163` vs `ARM-PO-25-26-163`) are
two different POs that share digits: the document is held, never grouped.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
from app.models.base import Base
from app.services.grouping import (
    HOLD_REASON_TEXT,
    PoLettersConflict,
    attach_unattached_to_open_sets,
    get_or_create_po_set,
    group_document,
    held_letters_conflict,
    letters_compatible,
    normalize_po_no,
    po_letters,
)


@pytest.fixture()
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c.paths.database_path = str(tmp_path / "digits.db")
    Base.metadata.create_all(get_engine(c))
    return c


def _doc(cfg, sha, dtype, raw, *, ambiguous=False):
    eng = get_engine(cfg)
    with Session(eng) as s:
        d = Document(
            sha256_hash=sha,
            original_filename=f"{sha}.pdf",
            stored_path=f"{sha}.pdf",
            doc_type=dtype,
            extraction_status=ExtractionStatus.valid,
            po_no_raw=raw,
            po_no_normalized=normalize_po_no(raw),
            po_reference_ambiguous=ambiguous,
        )
        s.add(d)
        s.commit()
        return d.id


def _group(cfg, doc_id):
    eng = get_engine(cfg)
    with Session(eng) as s:
        d = s.get(Document, doc_id)
        ps_id, problem = group_document(d, cfg)
        if ps_id is not None and d.po_set_id is None:
            d.po_set_id = ps_id
            s.commit()
        return ps_id, problem


# ---------------------------------------------------------------- the key


@pytest.mark.parametrize(
    ("raw", "digits", "letters"),
    [
        ("60213576 OPG", "60213576", "OPG"),
        ("60213576", "60213576", ""),
        ("TOGA-PO-25-26-163", "2526163", "TOGAPO"),
        ("ARM-PO-25-26-163", "2526163", "ARMPO"),
        ("P213899", "213899", "P"),
        ("213899", "213899", ""),
        ("LPO26/2360", "262360", ""),
        ("PO 26/2360", "262360", ""),
        ("262360", "262360", ""),
        ("10006-0000079633", "100060000079633", ""),
        ("4270006645 -SNN", "4270006645", "SNN"),
        ("4270006645", "4270006645", ""),
        ("22613 , 0", "22613", ""),
        ("PO, Rev # 161538,0", "161538", ""),
        ("161538 Rev 2", "161538", ""),
        ("J255-300115876", "255300115876", "J"),
        ("d7264-po-186000-013-01", "7264186000" + "01301", "DPO"),
    ],
)
def test_the_key_is_the_digits_and_letters_are_kept_apart(raw, digits, letters):
    assert normalize_po_no(raw) == digits
    assert po_letters(raw) == letters


def test_a_po_number_with_no_digits_has_an_empty_key():
    assert normalize_po_no("OPG") == ""
    assert normalize_po_no("N/A") == ""
    assert normalize_po_no("PO") == ""
    assert po_letters(None) == ""


def test_letters_compatible_rules():
    assert letters_compatible("", "")
    assert letters_compatible(None, "OPG")
    assert letters_compatible("opg", "")
    assert letters_compatible("OPG", "opg")  # case-folded
    assert not letters_compatible("TOGAPO", "ARMPO")
    assert not letters_compatible("J", "P")


def test_the_documented_failure_stays_a_failure():
    """J255-300115876 does not match a plain 300115876: the digits differ."""
    assert normalize_po_no("J255-300115876") != normalize_po_no("300115876")


# ---------------------------------------------------------------- placing


def test_letters_on_one_side_only_attach_in_both_directions(cfg):
    po = _doc(cfg, "po1", DocType.PO, "60213576")
    dn = _doc(cfg, "dn1", DocType.DN, "60213576 OPG")
    ps_id, problem = _group(cfg, po)
    assert problem is None and ps_id
    assert _group(cfg, dn) == (ps_id, None)

    # The reverse: the PO carries the letters, the DN is bare.
    po2 = _doc(cfg, "po2", DocType.PO, "4270006645 -SNN")
    dn2 = _doc(cfg, "dn2", DocType.DN, "4270006645")
    ps2, _ = _group(cfg, po2)
    assert ps2 and ps2 != ps_id
    assert _group(cfg, dn2) == (ps2, None)


def test_p_prefix_and_lpo_label_attach(cfg):
    po = _doc(cfg, "po", DocType.PO, "LPO26/2360")
    dn = _doc(cfg, "dn", DocType.DN, "PO 26/2360")
    si = _doc(cfg, "si", DocType.SI, "262360")
    ps_id, _ = _group(cfg, po)
    assert _group(cfg, dn) == (ps_id, None)
    assert _group(cfg, si) == (ps_id, None)

    po_p = _doc(cfg, "pp", DocType.PO, "P213899")
    dn_p = _doc(cfg, "pd", DocType.DN, "213899")
    ps_p, _ = _group(cfg, po_p)
    assert _group(cfg, dn_p) == (ps_p, None)


def test_revision_counter_and_long_codes_still_group(cfg):
    po = _doc(cfg, "po", DocType.PO, "22613 , 0")
    dn = _doc(cfg, "dn", DocType.DN, "22613")
    ps_id, _ = _group(cfg, po)
    assert _group(cfg, dn) == (ps_id, None)

    po2 = _doc(cfg, "po2", DocType.PO, "10006-0000079633")
    dn2 = _doc(cfg, "dn2", DocType.DN, "10006-0000079633")
    ps2, _ = _group(cfg, po2)
    assert _group(cfg, dn2) == (ps2, None)


def test_different_letters_same_digits_are_held_not_grouped(cfg):
    toga = _doc(cfg, "toga", DocType.PO, "TOGA-PO-25-26-163")
    arm_dn = _doc(cfg, "armdn", DocType.DN, "ARM-PO-25-26-163")
    arm_po = _doc(cfg, "armpo", DocType.PO, "ARM-PO-25-26-163")
    ps_id, _ = _group(cfg, toga)
    assert ps_id

    assert _group(cfg, arm_dn) == (None, "po_letters_conflict")
    # A PO must not mint a second open set under the same digits either.
    assert _group(cfg, arm_po) == (None, "po_letters_conflict")

    eng = get_engine(cfg)
    with Session(eng) as s:
        assert s.query(POSet).count() == 1
        assert s.get(POSet, ps_id).po_no_letters == "TOGAPO"
        # the sweep must not attach them behind the back of group_document
        assert attach_unattached_to_open_sets(cfg) == set()
        assert s.query(Document).filter(Document.po_set_id.is_(None)).count() == 2
        # and the holding area can say why
        held = s.get(Document, arm_dn)
        conflict = held_letters_conflict(s, held)
        assert conflict is not None and conflict.id == ps_id
        assert "po_letters_conflict" in HOLD_REASON_TEXT


def test_a_held_document_with_no_clash_is_not_reported_as_conflicting(cfg):
    po = _doc(cfg, "po", DocType.PO, "60213576")
    _group(cfg, po)
    dn = _doc(cfg, "dn", DocType.DN, "60213576 OPG")
    eng = get_engine(cfg)
    with Session(eng) as s:
        assert held_letters_conflict(s, s.get(Document, dn)) is None


def test_the_sweep_attaches_compatible_and_skips_conflicting(cfg):
    po = _doc(cfg, "po", DocType.PO, "TOGA-PO-25-26-163")
    ok = _doc(cfg, "ok", DocType.DN, "2526163")
    bad = _doc(cfg, "bad", DocType.DN, "ARM-PO-25-26-163")
    ps_id, _ = _group(cfg, po)
    assert attach_unattached_to_open_sets(cfg) == {ps_id}
    eng = get_engine(cfg)
    with Session(eng) as s:
        assert s.get(Document, ok).po_set_id == ps_id
        assert s.get(Document, bad).po_set_id is None


def test_get_or_create_raises_on_a_letters_conflict(cfg):
    get_or_create_po_set("TOGA-PO-25-26-163", cfg)
    with pytest.raises(PoLettersConflict):
        get_or_create_po_set("ARM-PO-25-26-163", cfg)
    with pytest.raises(PoLettersConflict):
        get_or_create_po_set("ARM-PO-25-26-163", cfg, create=False)
    # compatible spellings reuse the set
    a = get_or_create_po_set("TOGA-PO-25-26-163", cfg)
    b = get_or_create_po_set("2526163", cfg)
    assert a.id == b.id


def test_a_merged_set_does_not_block_a_differently_lettered_po(cfg):
    """Only OPEN sets are compared: a delivered packet is history."""
    first = get_or_create_po_set("TOGA-PO-25-26-163", cfg)
    eng = get_engine(cfg)
    with Session(eng) as s:
        s.get(POSet, first.id).status = POSetStatus.merged
        s.commit()
    second = get_or_create_po_set("ARM-PO-25-26-163", cfg)
    assert second.id != first.id
    assert second.po_no_letters == "ARMPO"


def test_j_prefixed_number_does_not_join_the_plain_one(cfg):
    plain = _doc(cfg, "plain", DocType.PO, "300115876")
    dn = _doc(cfg, "dn", DocType.DN, "J255-300115876")
    ps_id, _ = _group(cfg, plain)
    # Not matched: the DN is simply unattached (no set with its key exists).
    assert _group(cfg, dn) == (None, None)
    eng = get_engine(cfg)
    with Session(eng) as s:
        assert s.get(Document, dn).po_set_id is None
        assert s.get(POSet, ps_id).po_no_normalized == "300115876"


def test_a_po_with_no_digits_is_held_and_never_groups(cfg):
    a = _doc(cfg, "a", DocType.PO, "OPG")
    b = _doc(cfg, "b", DocType.PO, "N/A")
    assert _group(cfg, a) == (None, "missing_po_number")
    assert _group(cfg, b) == (None, "missing_po_number")
    with pytest.raises(ValueError):
        get_or_create_po_set("OPG", cfg)
    assert get_or_create_po_set("OPG", cfg, create=False) is None
    eng = get_engine(cfg)
    with Session(eng) as s:
        assert s.query(POSet).count() == 0
        # an empty-key document is never swept into an (impossible) empty set
        assert attach_unattached_to_open_sets(cfg) == set()


def test_the_new_column_exists_in_a_migrated_database(tmp_path, monkeypatch):
    """The Alembic head carries po_sets.po_no_letters (fresh install path)."""
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "migrate_db_t", root / "scripts" / "migrate_db.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert ("po_sets.po_no_letters", "a9b0c1d2e3f4") in mod._MARKERS
    assert mod.HEAD_REVISION == "a9b0c1d2e3f4"


# ---------------------------------------------------------------- what the operator reads


def test_the_holding_area_says_why_a_lettered_document_was_held(cfg, monkeypatch):
    """Checklist for a new reason: a page-text scenario."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr("app.api.routes.dashboard.load_config", lambda path=None: cfg)
    toga = _doc(cfg, "toga-ui", DocType.PO, "TOGA-PO-25-26-163")
    arm = _doc(cfg, "arm-ui", DocType.DN, "ARM-PO-25-26-163")
    _group(cfg, toga)
    assert _group(cfg, arm) == (None, "po_letters_conflict")

    from app.main import app

    with TestClient(app) as c:
        page = c.get("/unclassified")
        assert page.status_code == 200
        assert "arm-ui.pdf" in page.text
        assert "the letters in the PO number differ" in page.text
        assert "ARM-PO-25-26-163" in page.text

        # naming a clashing PO by hand is refused with the same reason, not a 500
        r = c.post(
            f"/unclassified/{arm}/reclassify",
            data={"doc_type": "DN", "po_no": "ARM-PO-25-26-163"},
        )
        assert r.status_code == 409
        assert "different letters" in r.text
