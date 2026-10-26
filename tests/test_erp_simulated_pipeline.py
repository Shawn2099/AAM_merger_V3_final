"""A simulated extraction -> normalisation -> matching run.

The simulation starts where the VLM stops: a list of structured documents
(``doc_type``, ``po_no_raw``, ``invoice_no``, and line items with
``line_item_no`` / ``description`` / ``quantity``).  It then uses the real
production helpers for PO normalisation, line normalisation, description
fallback and quantity comparison, and mirrors the reconciliation gate that
turns the flags into ``matched`` / ``mismatched`` / ``pending`` / ``quarantined``.

No PDF is read or written and no API call is made.  A small set of tests at the
bottom drives the real ``reconcile_po_set`` engine with the *merge step faked*
and quarantine copying stubbed, so the simulation is checked against the
product's verdict rather than only against another copy of the same logic.

Hypothesis mutates the real ERP spellings (SAP, Oracle, Tally/GST, JDE, Coupa)
and asserts that every mutation still produces a known outcome and never a
silent partial match.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import Base, DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
from app.services.grouping import letters_compatible, normalize_po_no, po_letters
from app.services.matching import normalize_line_no
from app.services.merge import _safe_stem
from app.services.reconciliation import compare_po_set_lines, reconcile_po_set

SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# ---------------------------------------------------------------------------
# A tiny extraction snapshot and a simulator over the production primitives.
# ---------------------------------------------------------------------------


def _line(no, description, quantity):
    return {
        "line_item_no": no,
        "description": description,
        "quantity": quantity,
    }


def _doc(doc_type, po_no_raw, lines, invoice_no=None):
    return {
        "doc_type": doc_type,
        "po_no_raw": po_no_raw,
        "invoice_no": invoice_no,
        "lines": lines,
    }


def _verdict(status, reason, flags, **extra):
    return {"status": status, "reason": reason, "flags": flags, **extra}


def simulate(documents: list[dict], threshold: int = 85) -> dict:
    """Run the post-extraction half of the pipeline over structured documents.

    The documents are assumed to be an extraction snapshot for one PO Set.  A
    PO-number drift between them is treated the way the engine treats it at
    reconciliation time: quarantine, never sum.  Grouping letters are checked
    with the same ``letters_compatible`` rule; two different prefixed POs remain
    held apart rather than merged.

    The returned verdict is the engine's *matching* verdict.  It deliberately
    stops before packet naming/writing: ``matched`` means "the quantities
    proved it and the real engine would proceed to auto-merge" — no file is
    produced here.
    """
    po_docs = [d for d in documents if d["doc_type"] == "PO"]
    dn_docs = [d for d in documents if d["doc_type"] == "DN"]
    si_docs = [d for d in documents if d["doc_type"] == "SI"]

    refs = [normalize_po_no(d.get("po_no_raw")) for d in documents]
    nonempty = {r for r in refs if r}
    if not nonempty:
        return _verdict("quarantined", "missing_po_reference", [])
    if len(nonempty) > 1:
        return _verdict("quarantined", "po_reference_mismatch", [])

    po_letters_seen = [po_letters(d.get("po_no_raw")) for d in documents]
    for left in po_letters_seen:
        for right in po_letters_seen:
            if not letters_compatible(left, right):
                return _verdict("held", "po_letters_conflict", [])

    if not po_docs:
        return _verdict("pending", "missing_po_document", [])
    if not dn_docs:
        return _verdict("pending", "missing_dn_document", [])
    if not si_docs:
        return _verdict("pending", "missing_si_document", [])

    po_lines = [line for d in po_docs for line in d["lines"]]
    dn_lines = [line for d in dn_docs for line in d["lines"]]
    si_lines = [line for d in si_docs for line in d["lines"]]
    if not po_lines:
        return _verdict("quarantined", "po_document_has_no_line_items", [])
    for _label, vendor_docs in (("DN", dn_docs), ("SI", si_docs)):
        if any(not d["lines"] for d in vendor_docs):
            return _verdict("quarantined", "vendor_document_has_no_line_items", [])
    all_lines = po_lines + dn_lines + si_lines
    if any(int(line.get("quantity") or 0) <= 0 for line in all_lines):
        return _verdict("quarantined", "non_positive_quantity", [])

    comparison = compare_po_set_lines(po_lines, dn_lines, si_lines, threshold)
    flags = comparison["flags"]
    if comparison["po_fail"]:
        return _verdict("quarantined", comparison["po_fail"], flags)

    failure_flags = [f for f in flags if f.get("type") in ("identification", "quantity")]
    if failure_flags:
        if any(f.get("type") == "identification" for f in failure_flags):
            return _verdict("quarantined", "unmatched_vendor_line", flags)
        if any((f.get("vendor_quantity") or 0) > 0 for f in failure_flags):
            return _verdict("mismatched", "quantity_mismatch", flags)
        return _verdict("pending", "partial_fulfillment", flags)

    invoices = [str(d["invoice_no"]) for d in si_docs if d.get("invoice_no")]
    return _verdict(
        "matched",
        None,
        flags,
        po_key=next(iter(nonempty)),
        po_letters=po_letters(po_docs[0].get("po_no_raw")),
        invoice_numbers=invoices,
        invoice_stems=[_safe_stem(n) for n in invoices],
    )


# ---------------------------------------------------------------------------
# Strategies built from the field spellings the ERP research found.
# ---------------------------------------------------------------------------

# Real ERP PO numbers are longer than three digits.  The revision regex is
# allowed to consume a 1-3 digit string immediately after "Rev", so a 1-3
# digit generated number would be testing the label parser's ambiguity rather
# than the separator/label rule this property is about.
_DIGITS = st.text("0123456789", min_size=4, max_size=10)

_PO_SPELLINGS = st.sampled_from(
    [
        "{}",
        "PO {}",
        "P.O. {}",
        "PO No. {}",
        "PO, Rev # {},0",
        "LPO{}",
        "PO-{}",
        "  {}  ",
    ]
)

_LINE_KEYS = st.one_of(
    st.integers(1, 999).map(str),
    st.tuples(st.integers(1, 9), st.integers(1, 99)).map(lambda t: f"{t[0]}-{t[1]}"),
    st.tuples(st.integers(1, 9), st.integers(0, 99)).map(lambda t: f"{t[0]}.{t[1]:02d}"),
)

_QUANTITIES = st.integers(1, 5000).map(lambda n: n * 1000)

_DESCRIPTIONS = st.sampled_from(
    [
        "FILTER ELEMENT P/N 250025-526",
        "FILTER, ELEMENT (REPLACEMENT) - MFG: SUL - P/N# 250025-526",
        "GATE VALVE 2IN CL150",
        "WRENCH 3/4 DRIVE AIR IMPACT",
        "NUT, HEX 9/16 IN-12 UNC",
        "BOLT M8 X 40 ZINC",
        "PLATE, STEEL, 10MM",
        "BEARING 6204 2RS",
    ]
)

_INVOICES = st.one_of(
    st.tuples(st.integers(2020, 2030), st.integers(1, 99)).map(
        lambda t: f"INV/{t[0]}-{str(t[1]).zfill(2)}/001"
    ),
    st.tuples(st.integers(2020, 2030), st.integers(1, 999)).map(lambda t: f"GST/{t[0]}/{t[1]:03d}"),
    st.tuples(st.integers(1, 9999), st.integers(1, 9)).map(lambda t: f"INV-{t[0]}-{t[1]}"),
)


def _po_raw(spelling: str, digits: str) -> str:
    return spelling.format(digits)


# ---------------------------------------------------------------------------
# Pure normalisation properties.
# ---------------------------------------------------------------------------


@SETTINGS
@given(digits=_DIGITS, spelling=_PO_SPELLINGS)
def test_po_labels_revisions_and_separators_do_not_change_the_key(digits, spelling):
    raw = _po_raw(spelling, digits)
    assert normalize_po_no(raw) == normalize_po_no(digits)
    assert po_letters(raw) == po_letters(digits)


@SETTINGS
@given(raw=st.text(alphabet="0123456789PO. ,#/_-RevLPO", max_size=30))
def test_po_normalisation_is_idempotent(raw):
    once = normalize_po_no(raw)
    assert normalize_po_no(once) == once
    assert once == "".join(c for c in once if c.isdigit())


@SETTINGS
@given(key=_LINE_KEYS)
def test_line_leading_zeros_and_surrounding_space_do_not_change_the_key(key):
    assert normalize_line_no("0" * 3 + key) == normalize_line_no(key)
    assert normalize_line_no(f"  {key}  ") == normalize_line_no(key)


@SETTINGS
@given(raw=st.text(alphabet="0123456789-#./ _ABCitemSL", max_size=20))
def test_line_normalisation_is_idempotent(raw):
    once = normalize_line_no(raw)
    assert normalize_line_no(once) == once


@SETTINGS
@given(left=st.text(alphabet="ABPO", max_size=6), right=st.text(alphabet="ABPO", max_size=6))
def test_letters_compatibility_is_symmetric_and_reflexive(left, right):
    assert letters_compatible(left, right) == letters_compatible(right, left)
    assert letters_compatible(left, left)


# ---------------------------------------------------------------------------
# End-to-end matching properties over structured extraction output.
# ---------------------------------------------------------------------------


@SETTINGS
@given(
    digits=_DIGITS,
    key=_LINE_KEYS,
    quantity=_QUANTITIES,
    po_description=_DESCRIPTIONS,
    vendor_description=_DESCRIPTIONS,
    invoice=_INVOICES,
)
def test_balanced_set_matches_even_when_every_spelling_differs(
    digits, key, quantity, po_description, vendor_description, invoice
):
    documents = [
        _doc("PO", f"PO {digits}", [_line(key, po_description, quantity)]),
        _doc("DN", digits, [_line("0" * 3 + key, vendor_description, quantity)]),
        _doc("SI", f"PO-{digits}", [_line("0" + key, vendor_description, quantity)], invoice),
    ]
    result = simulate(documents)

    assert result["status"] == "matched", result
    assert result["flags"] == []
    assert result["po_key"] == normalize_po_no(digits)
    assert result["invoice_numbers"] == [invoice]


@SETTINGS
@given(
    digits=_DIGITS,
    key=_LINE_KEYS,
    po_quantity=_QUANTITIES,
    delta=st.integers(1, 1000).map(lambda n: n * 1000),
    po_description=_DESCRIPTIONS,
    vendor_description=_DESCRIPTIONS,
)
def test_quantity_mutation_is_a_mismatch_not_a_silent_match(
    digits, key, po_quantity, delta, po_description, vendor_description
):
    vendor_quantity = po_quantity + delta
    documents = [
        _doc("PO", digits, [_line(key, po_description, po_quantity)]),
        _doc("DN", digits, [_line(key, vendor_description, vendor_quantity)]),
        _doc("SI", digits, [_line(key, vendor_description, vendor_quantity)], "INV-1"),
    ]
    result = simulate(documents)

    assert result["status"] == "mismatched", result
    assert {f["type"] for f in result["flags"]} == {"quantity"}
    assert all(f["vendor_quantity"] > 0 for f in result["flags"])


@SETTINGS
@given(
    digits=_DIGITS,
    key=_LINE_KEYS,
    quantity=_QUANTITIES,
    first_invoice=_INVOICES,
    second_invoice=_INVOICES,
)
def test_invoice_number_changes_naming_only_not_the_match(
    digits, key, quantity, first_invoice, second_invoice
):
    def run(invoice):
        return simulate(
            [
                _doc("PO", digits, [_line(key, "ITEM", quantity)]),
                _doc("DN", digits, [_line(key, "ITEM", quantity)]),
                _doc("SI", digits, [_line(key, "ITEM", quantity)], invoice),
            ]
        )

    first = run(first_invoice)
    second = run(second_invoice)

    assert first["status"] == second["status"] == "matched"
    assert first["flags"] == second["flags"] == []
    assert first["invoice_stems"] == [_safe_stem(first_invoice)]
    assert second["invoice_stems"] == [_safe_stem(second_invoice)]


@SETTINGS
@given(
    digits=_DIGITS,
    key=_LINE_KEYS,
    quantity=_QUANTITIES,
    po_description=_DESCRIPTIONS,
    vendor_description=_DESCRIPTIONS,
)
def test_a_printed_line_number_is_authoritative_over_description(
    digits, key, quantity, po_description, vendor_description
):
    documents = [
        _doc("PO", digits, [_line(key, po_description, quantity)]),
        _doc("DN", digits, [_line(key, vendor_description, quantity)]),
        _doc("SI", digits, [_line(key, vendor_description, quantity)], "INV-1"),
    ]
    result = simulate(documents)
    assert result["status"] == "matched"
    assert all(f["type"] != "description_match" for f in result["flags"])


# ---------------------------------------------------------------------------
# Golden cases using the actual field spellings from the ERP matrix.
# ---------------------------------------------------------------------------

SAP_DESCRIPTION = "FILTER, ELEMENT (REPLACEMENT) - MFG: SUL - P/N# 250025-526"
VENDOR_DESCRIPTION = (
    "FILTER, ELEMENT (REPLACEMENT) - MFG: SUL - P/N# 250025-526 Item No: 5061800.01.11.00362"
)


def _base_documents() -> list[dict]:
    return [
        _doc("PO", "PO, Rev # 161538,0", [_line("00010", SAP_DESCRIPTION, 5000)]),
        _doc("DN", "161538", [_line("10", VENDOR_DESCRIPTION, 5000)]),
        _doc(
            "SI",
            "PO 161538",
            [_line("010", VENDOR_DESCRIPTION, 5000)],
            "INV/2025-26/001",
        ),
    ]


@pytest.mark.parametrize(
    ("label", "mutate", "status", "reason"),
    [
        ("baseline", lambda docs: docs, "matched", None),
        (
            "PO labels and revision",
            lambda docs: [
                dict(d, po_no_raw=f"P.O. No. {d['po_no_raw'].split()[-1].split(',')[0]}")
                for d in docs
            ],
            "matched",
            None,
        ),
        (
            "line zero padding",
            lambda docs: [
                dict(d, lines=[dict(line, line_item_no="00010") for line in d["lines"]])
                for d in docs
            ],
            "matched",
            None,
        ),
        (
            "description metadata",
            lambda docs: [
                dict(d, lines=[dict(line, description=VENDOR_DESCRIPTION) for line in d["lines"]])
                for d in docs
            ],
            "matched",
            None,
        ),
        (
            "invoice separator variant",
            lambda docs: [
                dict(d, invoice_no="INV 2025-26 001") if d["doc_type"] == "SI" else d for d in docs
            ],
            "matched",
            None,
        ),
        (
            "vendor line 1 instead of SAP 10",
            lambda docs: [
                dict(d, lines=[dict(line, line_item_no="1") for line in d["lines"]])
                if d["doc_type"] != "PO"
                else d
                for d in docs
            ],
            "quarantined",
            "unmatched_vendor_line",
        ),
        (
            "short delivery",
            lambda docs: [
                dict(d, lines=[dict(line, quantity=4000) for line in d["lines"]])
                if d["doc_type"] == "DN"
                else d
                for d in docs
            ],
            "mismatched",
            "quantity_mismatch",
        ),
        (
            "empty vendor documents",
            lambda docs: [dict(d, lines=[]) if d["doc_type"] != "PO" else d for d in docs],
            "quarantined",
            "vendor_document_has_no_line_items",
        ),
        (
            "PO digit drift on the DN",
            lambda docs: [
                dict(d, po_no_raw="161539") if d["doc_type"] == "DN" else d for d in docs
            ],
            "quarantined",
            "po_reference_mismatch",
        ),
        (
            "blank vendor line with the same description",
            lambda docs: [
                dict(
                    d,
                    lines=[
                        dict(line, line_item_no=None, description=SAP_DESCRIPTION)
                        for line in d["lines"]
                    ],
                )
                if d["doc_type"] != "PO"
                else d
                for d in docs
            ],
            "matched",
            None,
        ),
        (
            "blank vendor line with an unrelated description",
            lambda docs: [
                dict(
                    d,
                    lines=[
                        dict(line, line_item_no=None, description="UNRELATED ITEM")
                        for line in d["lines"]
                    ],
                )
                if d["doc_type"] != "PO"
                else d
                for d in docs
            ],
            "quarantined",
            "unmatched_vendor_line",
        ),
    ],
)
def test_golden_sap_mutations_reach_a_known_verdict(label, mutate, status, reason):
    documents = mutate(copy.deepcopy(_base_documents()))
    result = simulate(documents)
    assert result["status"] == status, (label, result)
    if reason is None:
        assert result["reason"] is None
    else:
        assert result["reason"] == reason


def test_blank_line_numbers_record_the_description_link():
    documents = [
        _doc("PO", "161538", [_line("10", SAP_DESCRIPTION, 5000)]),
        _doc("DN", "161538", [_line(None, SAP_DESCRIPTION + " 2 EA", 5000)]),
        _doc("SI", "161538", [_line(None, SAP_DESCRIPTION + " 2 EA", 5000)], "INV/2025-26/001"),
    ]
    result = simulate(documents)
    assert result["status"] == "matched"
    assert [f["type"] for f in result["flags"]] == ["description_match", "description_match"]


def test_a_short_delivery_is_pending_not_a_mismatch():
    """A PO line the vendor has not mentioned is unfulfilled, not disputed."""
    documents = [
        _doc(
            "PO",
            "161538",
            [
                _line("10", SAP_DESCRIPTION, 5000),
                _line("20", "GATE VALVE 2IN CL150", 3000),
            ],
        ),
        _doc("DN", "161538", [_line("10", SAP_DESCRIPTION, 5000)]),
        _doc("SI", "161538", [_line("10", SAP_DESCRIPTION, 5000)], "INV/2025-26/001"),
    ]
    result = simulate(documents)
    assert result["status"] == "pending"
    assert result["reason"] == "partial_fulfillment"
    assert {f["line_item_no"] for f in result["flags"]} == {"20"}
    assert all(f["vendor_quantity"] == 0 for f in result["flags"])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PO, Rev # 12,0", "12"),
        ("PO, Rev # 123,0", "123"),
    ],
)
def test_short_po_number_survives_a_revision_label(raw, expected):
    assert normalize_po_no(raw) == expected


def test_two_short_pos_with_revisions_do_not_collapse_to_one_key():
    assert normalize_po_no("PO, Rev # 12,0") != normalize_po_no("PO, Rev # 34,0")


def test_oracle_release_and_plain_number_do_not_silently_join():
    documents = [
        _doc("PO", "00312546-REL0100", [_line("1", "ITEM", 5000)]),
        _doc("DN", "312546", [_line("1", "ITEM", 5000)]),
        _doc("SI", "312546", [_line("1", "ITEM", 5000)], "INV-1"),
    ]
    result = simulate(documents)
    assert result["status"] == "quarantined"
    assert result["reason"] == "po_reference_mismatch"


def test_jde_plain_number_joins_the_first_prefixed_set_by_the_one_sided_rule():
    documents = [
        _doc("PO", "OP-4368", [_line("1", "ITEM", 5000)]),
        _doc("DN", "4368", [_line("1", "ITEM", 5000)]),
        _doc("SI", "4368", [_line("1", "ITEM", 5000)], "INV-1"),
    ]
    result = simulate(documents)
    assert result["status"] == "matched"


def test_different_po_letters_are_held_apart_not_summed():
    documents = [
        _doc("PO", "AB-12345", [_line("1", "ITEM", 5000)]),
        _doc("DN", "CD-12345", [_line("1", "ITEM", 5000)]),
        _doc("SI", "CD-12345", [_line("1", "ITEM", 5000)], "INV-1"),
    ]
    result = simulate(documents)
    assert result["status"] == "held"
    assert result["reason"] == "po_letters_conflict"


# ---------------------------------------------------------------------------
# Differential check: the real reconcile engine, with merge faked.
#
# The engine's own decision gate is exercised for every outcome that does not
# need a packet.  For a clean set, ``merge_po_set`` is replaced by a stub that
# marks the set merged without touching a PDF, so the matching half is still
# the production code.  Quarantine copying is stubbed for the same reason:
# this file is about identity/normalisation, not about filesystem side effects.
# ---------------------------------------------------------------------------


def _persist_documents(cfg, documents: list[dict]) -> int:
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    anchor = normalize_po_no(documents[0]["po_no_raw"])
    with Session(eng) as s:
        ps = POSet(
            po_no_normalized=anchor,
            po_no_letters=po_letters(documents[0]["po_no_raw"]),
            status=POSetStatus.pending,
        )
        s.add(ps)
        s.commit()
        s.refresh(ps)
        po_set_id = ps.id

        for index, document in enumerate(documents):
            doc = Document(
                sha256_hash=f"sim-{index}-{po_set_id}",
                original_filename=f"{document['doc_type']}-{index}.pdf",
                stored_path="",
                doc_type=DocType[document["doc_type"]],
                extraction_status=ExtractionStatus.valid,
                po_set_id=po_set_id,
                po_no_normalized=normalize_po_no(document["po_no_raw"]),
            )
            if document["doc_type"] == "SI":
                doc.si_no = document["invoice_no"]
                doc.invoice_no = document["invoice_no"]
            s.add(doc)
            s.commit()
            for line in document["lines"]:
                s.add(
                    LineItem(
                        document_id=doc.id,
                        line_item_no=line["line_item_no"],
                        description=line["description"],
                        quantity=line["quantity"],
                    )
                )
            s.commit()
    return po_set_id


def _fake_merge(monkeypatch, cfg):
    calls: list[int] = []

    def fake_merge(po_set_id, cfg, *, info=None, expected_doc_ids=None, **kwargs):
        calls.append(po_set_id)
        with Session(get_engine(cfg)) as s:
            ps = s.get(POSet, po_set_id)
            ps.status = POSetStatus.merged
            ps.merged_output_path = str(Path(cfg.paths.output_folder) / "SIMULATED.pdf")
            s.commit()
        if info is not None:
            info["output_name"] = "SIMULATED.pdf"
        return Path(cfg.paths.output_folder) / "SIMULATED.pdf"

    monkeypatch.setattr("app.services.merge.merge_po_set", fake_merge)
    return calls


def _fake_quarantine(monkeypatch):
    reasons: list[str] = []

    def fake_quarantine(po_set_id, cfg, **kwargs):
        reasons.append(kwargs.get("reason"))

    monkeypatch.setattr("app.services.reconciliation.quarantine_copy", fake_quarantine)
    return reasons


@pytest.fixture()
def sim_cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "simulated.db")
    cfg.paths.output_folder = str(tmp_path / "output")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    cfg.paths.quarantine_folder = str(tmp_path / "quarantine")
    for name in ("output", "stored", "quarantine"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(cfg))
    return cfg


def test_engine_reaches_merged_for_a_clean_simulated_set(sim_cfg, monkeypatch):
    calls = _fake_merge(monkeypatch, sim_cfg)
    _fake_quarantine(monkeypatch)
    po_set_id = _persist_documents(sim_cfg, _base_documents())

    result = reconcile_po_set(po_set_id, sim_cfg)

    assert result["status"] == "merged"
    assert calls == [po_set_id]
    assert not (Path(sim_cfg.paths.output_folder) / "SIMULATED.pdf").exists()


def test_engine_reaches_mismatched_for_a_quantity_mutation(sim_cfg, monkeypatch):
    calls = _fake_merge(monkeypatch, sim_cfg)
    _fake_quarantine(monkeypatch)
    documents = copy.deepcopy(_base_documents())
    documents[1]["lines"][0]["quantity"] = 4000
    po_set_id = _persist_documents(sim_cfg, documents)

    result = reconcile_po_set(po_set_id, sim_cfg)

    assert result["status"] == "mismatched"
    assert result["flags"]
    assert calls == []


def test_engine_reaches_quarantined_for_an_orphan(sim_cfg, monkeypatch):
    calls = _fake_merge(monkeypatch, sim_cfg)
    reasons = _fake_quarantine(monkeypatch)
    documents = copy.deepcopy(_base_documents())
    # "1" against PO "00010" is the step-10 fallback case and merges now; 7 fits no rule
    documents[1]["lines"][0]["line_item_no"] = "7"
    documents[2]["lines"][0]["line_item_no"] = "7"
    po_set_id = _persist_documents(sim_cfg, documents)

    result = reconcile_po_set(po_set_id, sim_cfg)

    assert result["status"] == "quarantined"
    assert reasons == ["unmatched_vendor_line"]
    assert calls == []
