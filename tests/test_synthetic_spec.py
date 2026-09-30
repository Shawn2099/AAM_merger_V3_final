"""Tests derived from `notebooklm/synthetic_test_data_spec.md`.

Scope note: this file previously carried a large test matrix for the retired
v20.5 3-step matcher — SKU rescue, reindex detection, ERP step-10 alignment,
UOM, and the `line_type` row-kind column. None of those are part of this
product, so the cases that could only be satisfied by them were removed rather
than left failing. What remains is what the product actually promises, plus
four tests that deliberately pin known limitations.

The governing safety invariant (§6.3) is unchanged and still enforced by
`test_spec_6_3_no_silent_wrong_merge`: if the engine reports `merged`, the
quantities are independently recomputed and must agree. A false quarantine is
acceptable; a wrong merge is not.

See AAM_merger_V3_PRODUCT.md for the rule and its accepted limitations.
"""

from __future__ import annotations

import re

import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import (
    DocType,
    Document,
    ExtractionStatus,
    LineItem,
    POSet,
    POSetStatus,
)
from app.models.base import Base
from app.services.grouping import normalize_po_no
from app.services.matching import normalize_line_no
from app.services.reconciliation import reconcile_po_set
from app.services.sanitizer import parse_quantity_scaled

PO, DN, SI = "PO", "DN", "SI"


def _line(no, desc, qty):
    return {"no": no, "desc": desc, "qty": qty}


def _cfg(tmp_path, name):
    cfg = load_config()
    cfg.paths.database_path = str(tmp_path / f"{name}.db")
    for sub in ("stored", "output", "quarantine"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    cfg.paths.output_folder = str(tmp_path / "output")
    cfg.paths.quarantine_folder = str(tmp_path / "quarantine")
    return cfg


def build(tmp_path, po_no, spec, name=None):
    """Create one PO Set with a PO/DN/SI document carrying the given lines."""
    cfg = _cfg(tmp_path, name or re.sub(r"\W+", "_", po_no)[:40])
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    n = 0
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id
        for dtype in (PO, DN, SI):
            lines = spec.get(dtype, [])
            if not lines:
                continue
            n += 1
            p = tmp_path / "stored" / f"doc{n}.pdf"
            PdfWriter().write(str(p))
            extra = {}
            if dtype == SI:
                extra = {"si_no": f"SI-{n}", "invoice_no": f"SI-{n}"}
            d = Document(
                sha256_hash=f"syn_{po_no}_{n}",
                original_filename=f"doc{n}.pdf",
                stored_path=str(p),
                doc_type=DocType[dtype],
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps_id,
                po_no_normalized=po_no,
                **extra,
            )
            s.add(d)
            s.commit()
            for ln in lines:
                try:
                    q = parse_quantity_scaled(ln["qty"])
                except ValueError:
                    # Mirrors the real ingestion path: an unparseable printed
                    # quantity must not be invented as a number.
                    q = 0
                s.add(
                    LineItem(
                        document_id=d.id,
                        line_item_no=ln["no"],
                        description=ln["desc"],
                        quantity=q,
                        unit_price=100000,
                    )
                )
            s.commit()
    return ps_id, eng, cfg


def run(tmp_path, po_no, spec):
    ps_id, eng, cfg = build(tmp_path, po_no, spec)
    res = reconcile_po_set(ps_id, cfg)
    with Session(eng) as s:
        ps = s.get(POSet, ps_id)
        merged = bool(ps.merged_output_path)
        reason = ps.reconcile_reason
    return res["status"], reason, merged, eng, ps_id


# ==========================================================================
# §6.3  GOVERNING SAFETY INVARIANT — a wrong merge is P0
# ==========================================================================

INVARIANT_SETS = {
    "molykote_a": {
        PO: [_line("1", "MOLYKOTE 111 100 GMS", "10.00")],
        DN: [_line("1", "MOLYKOTE 111 100 GMS", "10.00")],
        SI: [_line("1", "MOLYKOTE 111 100 GMS", "10.00")],
    },
    "molykote_wrong": {
        PO: [_line("1", "MOLYKOTE 111 100 GMS", "10.00")],
        DN: [_line("1", "MOLYKOTE 4 100 GMS", "10.00")],
        SI: [_line("1", "MOLYKOTE 4 100 GMS", "10.00")],
    },
    "two_lines": {
        PO: [
            _line("1", "FILTER ELEMENT P/N# 250025-526", "5.00"),
            _line("2", "SEPARATOR W/GASKET P/N# 250034-085", "2.00"),
        ],
        DN: [
            _line("1", "FILTER ELEMENT P/N# 250025-526 Line Item - 1", "5.00"),
            _line("2", "SEPARATOR W/GASKET P/N# 250034-085 Line Item - 2", "2.00"),
        ],
        SI: [
            _line("1", "FILTER ELEMENT P/N# 250025-526 Line Item - 1", "5.00"),
            _line("2", "SEPARATOR W/GASKET P/N# 250034-085 Line Item - 2", "2.00"),
        ],
    },
    "uom_box_vs_each": {
        PO: [_line("1", "Absorbent Pad P/N# AP-100", "1.00")],
        DN: [_line("1", "Absorbent Pad P/N# AP-100 Line Item - 1", "1.00")],
        SI: [_line("1", "Absorbent Pad P/N# AP-100 Line Item - 1", "1.00")],
    },
}


@pytest.mark.parametrize("name", sorted(INVARIANT_SETS), ids=sorted(INVARIANT_SETS))
def test_spec_6_3_no_silent_wrong_merge(tmp_path, name):
    """§6.3: a silent wrong merge is P0. Recompute the math independently.

    If the engine says merged, then for every line the PO quantity must equal
    the DN aggregate AND the SI aggregate, and no line may be left without a PO
    counterpart. Anything else is a wrong answer.
    """
    spec = INVARIANT_SETS[name]
    status, reason, _merged, eng, ps_id = run(tmp_path, name, spec)
    if status != "merged":
        pytest.skip(f"quarantined, not a wrong merge (acceptable per §6.3) — {reason}")

    with Session(eng) as s:
        rows = {}
        for doc in s.query(Document).filter_by(po_set_id=ps_id).all():
            rows[doc.doc_type.name] = [
                {
                    "no": normalize_line_no(li.line_item_no),
                    "desc": li.description or "",
                    "qty": li.quantity,
                }
                for li in doc.line_items
            ]

    po = rows.get(PO, [])
    for pool_name in (DN, SI):
        pool = rows.get(pool_name, [])
        agg: dict[str, int] = {}
        for v in pool:
            agg[v["no"]] = agg.get(v["no"], 0) + v["qty"]
        for p in po:
            assert agg.get(p["no"], 0) == p["qty"], (
                f"WRONG MERGE [{name}] {pool_name} line {p['no']}: "
                f"PO {p['qty']} != {pool_name} {agg.get(p['no'], 0)}"
            )
        for v in pool:
            assert any(v["no"] == p["no"] for p in po), (
                f"WRONG MERGE [{name}] {pool_name} line {v['no']} has no PO counterpart"
            )


# ==========================================================================
# §5  TC-SYN edge cases the product still handles
# ==========================================================================


def test_contiguous_line_numbers_reconcile(tmp_path):
    """15 contiguous lines with a unique quantity each, so any off-by-N mapping
    produces a quantity mismatch rather than a silent pass."""
    n = 15
    po = [_line(str(i + 1), f"ITEM-{i + 1} MISC PART", f"{(i + 1) * 100}.00") for i in range(n)]
    dn = [
        _line(str(i + 1), f"ITEM-{i + 1} MISC PART Line Item - {i + 1}", f"{(i + 1) * 100}.00")
        for i in range(n)
    ]
    si = [
        _line(str(i + 1), f"ITEM-{i + 1} MISC PART Line Item - {i + 1}", f"{(i + 1) * 100}.00")
        for i in range(n)
    ]
    status, reason, *_ = run(tmp_path, "SYN002", {PO: po, DN: dn, SI: si})
    assert status == "merged", f"contiguous numbering must reconcile, got {status} ({reason})"


def test_molykote_confusion_never_merges(tmp_path):
    """PO 'MOLYKOTE 111' vs vendor 'MOLYKOTE 4' — PO line 2 is never delivered,
    so the set cannot be a full match."""
    status, _reason, *_ = run(
        tmp_path,
        "SYN001",
        {
            PO: [
                _line("1", "MOLYKOTE 111 100 GMS", "10.00"),
                _line("2", "MOLYKOTE 4 100 GMS", "10.00"),
            ],
            DN: [_line("1", "MOLYKOTE 4 100 GMS Line Item - 1", "10.00")],
            SI: [_line("1", "MOLYKOTE 4 100 GMS Line Item - 1", "10.00")],
        },
    )
    assert status != "merged", "an undelivered PO line must not merge"


def test_ocr_decimal_drop_is_not_silently_zero(tmp_path):
    """'600 EACH' (a mis-read of 6.00) must fail the parser, not become 600."""
    with pytest.raises(ValueError):
        parse_quantity_scaled("600 EACH")
    status, _reason, *_ = run(
        tmp_path,
        "SYN005",
        {
            PO: [_line("1", "DRILL STEEL 600 EACH P/N# DS-6", "6.00")],
            DN: [_line("1", "DRILL STEEL 600 EACH P/N# DS-6 Line Item - 1", "600 EACH")],
            SI: [_line("1", "DRILL STEEL 600 EACH P/N# DS-6 Line Item - 1", "600 EACH")],
        },
    )
    assert status == "quarantined", f"an unparseable quantity must quarantine, got {status}"


def test_multi_po_invoice_must_not_merge_blindly(tmp_path):
    """One invoice listing several POs must not be merged against one PO.

    The engine cannot disaggregate, so the only acceptable outcome is no merge.
    """
    status, _reason, *_ = run(
        tmp_path,
        "SYN008",
        {
            PO: [_line("1", "GATE VALVE P/N# GV-2", "2.00")],
            DN: [_line("1", "GATE VALVE P/N# GV-2 Line Item - 1", "2.00")],
            SI: [
                _line("1", "GATE VALVE P/N# GV-2 PO#1001 Line Item - 1", "2.00"),
                _line("2", "FLANGE 8IN P/N# FL-8 PO#1002", "3.00"),
            ],
        },
    )
    assert status != "merged", (
        f"SI carries lines for a second PO (#1002); merging would be a wrong answer. Got {status}"
    )


@pytest.mark.parametrize(
    "printed_po,referenced_po",
    [
        ("D7264-PO-186000-013-01", "D7264-PO186000-013-01-"),
        ("161538", "161538"),
        ("025/09-2026-27", "PO-025-09-2026-27"),
        ("BH0011409-2", "BH0011409-2"),
        ("GDNRHO--25-513", "GDNRHO-25-513"),
    ],
)
def test_spec_2_po_number_normalisation(printed_po, referenced_po):
    """§2: po_no must normalise across the documented syntax variants."""
    assert normalize_po_no(printed_po) == normalize_po_no(referenced_po), (
        f"§2: {printed_po!r} and {referenced_po!r} must normalise to the same PO"
    )


def test_po_number_punctuation_shift_reconciles(tmp_path):
    po_no = normalize_po_no("D7264-PO-186000-013-01")
    status, reason, *_ = run(
        tmp_path,
        po_no,
        {
            PO: [_line("1", '1 1/4" TONG DIE P/N# TD-1', "2.00")],
            DN: [_line("1", '1 1/4" TONG DIE P/N# TD-1 Line Item - 1', "2.00")],
            SI: [_line("1", '1 1/4" TONG DIE P/N# TD-1 Line Item - 1', "2.00")],
        },
    )
    assert status == "merged", f"got {status} ({reason})"


def test_two_dockets_restarting_line_numbers_never_merge(tmp_path):
    """Two dockets each restart at line 1. Summing them is the only behaviour
    available, so this must NOT reconcile — a merge would attribute one
    docket's quantity to another."""
    status, _reason, *_ = run(
        tmp_path,
        "SYN010",
        {
            PO: [_line("1", "GATE VALVE P/N# GV-2", "2.00")],
            DN: [
                _line("1", "GATE VALVE P/N# GV-2 Line Item - 1", "2.00"),
                _line("1", "FLANGE 8IN P/N# FL-8 Line Item - 1", "3.00"),
            ],
            SI: [
                _line("1", "GATE VALVE P/N# GV-2 Line Item - 1", "2.00"),
                _line("1", "FLANGE 8IN P/N# FL-8 Line Item - 1", "3.00"),
            ],
        },
    )
    assert status != "merged", f"two dockets share line 1; merging is a wrong answer. Got {status}"


@pytest.mark.parametrize("printed", ["1-1", "2-1", "10", "20", "30", "VAT-12", "1"])
def test_spec_3_2_line_number_preserved_verbatim(printed):
    """§3.2: line_item_number must survive normalisation intact."""
    assert normalize_line_no(printed) == printed, (
        f"§3.2: {printed!r} was rewritten to {normalize_line_no(printed)!r}; "
        f"the spec requires the full string preserved"
    )


def test_hyphenated_line_numbers_reconcile(tmp_path):
    """Valaris PO lines '1-1' and '2-1' must match verbatim."""
    status, reason, *_ = run(
        tmp_path,
        "SYN011",
        {
            PO: [
                _line("1-1", "SUBSEA DRILL COLLAR P/N# SDC-1", "1.00"),
                _line("2-1", "RISER TENSIONER P/N# RT-2", "1.00"),
            ],
            DN: [
                _line("1-1", "SUBSEA DRILL COLLAR P/N# SDC-1 Line Item - 1-1", "1.00"),
                _line("2-1", "RISER TENSIONER P/N# RT-2 Line Item - 2-1", "1.00"),
            ],
            SI: [
                _line("1-1", "SUBSEA DRILL COLLAR P/N# SDC-1 Line Item - 1-1", "1.00"),
                _line("2-1", "RISER TENSIONER P/N# RT-2 Line Item - 2-1", "1.00"),
            ],
        },
    )
    assert status == "merged", f"got {status} ({reason})"


def test_alternative_part_numbers_in_description_reconcile(tmp_path):
    """'ALTRANATIVE PN#' text in a real description must not break matching."""
    status, reason, *_ = run(
        tmp_path,
        "SYNALTPN",
        {
            PO: [_line("1", "KIT REPAIR P/N# 02250145-797 ALTRANATIVE PN#02250145-798", "2.00")],
            DN: [_line("1", "KIT REPAIR P/N# 02250145-798 Line Item - 1", "2.00")],
            SI: [_line("1", "KIT REPAIR P/N# 02250145-798 Line Item - 1", "2.00")],
        },
    )
    assert status == "merged", f"got {status} ({reason})"


def test_items_with_no_part_number_reconcile(tmp_path):
    """Generic items carry no part number in their description."""
    status, reason, *_ = run(
        tmp_path,
        "SYNBLANK",
        {
            PO: [
                _line("1", "Blow Off Duster", "1.00"),
                _line("2", "Door mat", "2.00"),
            ],
            DN: [
                _line("1", "Blow Off Duster Line Item - 1", "1.00"),
                _line("2", "Door mat Line Item - 2", "2.00"),
            ],
            SI: [
                _line("1", "Blow Off Duster Line Item - 1", "1.00"),
                _line("2", "Door mat Line Item - 2", "2.00"),
            ],
        },
    )
    assert status == "merged", f"got {status} ({reason})"


# ==========================================================================
# §3.2  raw_qty parsing
# ==========================================================================


@pytest.mark.parametrize(
    "printed,expected_scaled",
    [
        ("1.00", 1000),
        ("12.50", 12500),
        ("25.00", 25000),
        ("14", 14000),
        ("1,000", 1000000),
        ("1,00,000", 100000000),  # one lakh, en_IN grouping
    ],
)
def test_spec_3_2_raw_qty_parsing(printed, expected_scaled):
    """The documented printed quantity forms must parse exactly."""
    assert parse_quantity_scaled(printed) == expected_scaled


@pytest.mark.parametrize("printed", ["1 000", "1,5", "6 00", "1 00"])
def test_spec_3_2_inner_space_forms_are_rejected(printed):
    """PRODUCT CONTRACT: an inner-space quantity is rejected, not guessed.

    "1 000" is rejected rather than read as one thousand. Rejection routes the
    set to quarantine, which is the safe direction. Note the trade-off: a
    vendor printing an inner-space thousands separator gets a quarantine rather
    than a merge.

    See AAM_merger_V3_PRODUCT.md, "Quantity parsing".
    """
    with pytest.raises(ValueError):
        parse_quantity_scaled(printed)


def test_spec_3_2_locale_assumption_is_documented():
    """PINS A KNOWN LIMITATION — the locale is an assumption, not a detection.

    `matching.locale` is a fixed value (default en_IN), not inferred from the
    document. Under en_IN a dot is a decimal separator, so a European-printed
    "17.200" is read as 17.2 where its author meant 17200.

    The 2-significant-decimal rule closes the *common* form of this: a printed
    "1.234" (European 1234) is now rejected outright, so the set quarantines
    rather than reconciling on a 1000x error. What remains is the trailing-zero
    form, where the European reading is indistinguishable from a legitimate
    value ("17.200" == 17.2). Since real quantities are whole units or
    occasional .50, a European-printed value with three trailing zeros is not a
    shape we expect, and this is accepted as an open risk rather than solved.

    Mitigation is operational: set `matching.locale` to the vendor's convention
    (PRODUCT doc section 5). A mixed-vendor estate cannot be served correctly by
    one locale and would need per-document locale detection, which this product
    does not do.
    """
    from app.core.config import load_config
    from app.services.sanitizer import parse_quantity_scaled

    assert load_config("config.example.yaml").matching.locale == "en_IN"

    # the common European thousands form IS caught
    with pytest.raises(ValueError):
        parse_quantity_scaled("1.234")

    # the trailing-zero form is not distinguishable from a real value
    assert parse_quantity_scaled("17.200") == 17_200  # 17.2 scaled, NOT 1_720_000


def test_spec_3_2_dimension_fractions_not_confused():
    """'1-1/4" ID' and '1-7/16" ID' are different items and stay different text."""
    a, b = 'SOCKET 1-1/4" ID', 'SOCKET 1-7/16" ID'
    assert a != b
    # they differ only in the numeric token, which is exactly why there is no
    # description-based gate in this product: differing numbers on the same
    # line number are summed, not rejected.
    from app.services.matching import _norm

    assert _norm(a) != _norm(b)


# ==========================================================================
# Known limitations — pinned deliberately, do not "fix" without a decision
# ==========================================================================


def test_limitation_uom_box_vs_each_merges(tmp_path):
    """PINS A KNOWN LIMITATION. 1 BOX ordered vs 1 EA delivered merges.

    There is no UOM column and no UOM conversion, so a PO ordering 1 BOX and a
    DN delivering 1 EA are the number 1 and are indistinguishable. The set
    reconciles. A human reviewer is the last line of defence here.

    See AAM_merger_V3_PRODUCT.md, Accepted limitations.
    """
    status, _reason, *_ = run(
        tmp_path,
        "SYN003",
        {
            PO: [_line("1", "Absorbent Pad P/N# AP-100  1.00 BOX", "1.00")],
            DN: [_line("1", "Absorbent Pad P/N# AP-100 Line Item - 1  1.00 EA", "1.00")],
            SI: [_line("1", "Absorbent Pad P/N# AP-100 Line Item - 1  1.00 EA", "1.00")],
        },
    )
    assert status == "merged", (
        f"documents the UOM limitation: expected the set to merge. Got {status}"
    )


def test_limitation_no_line_type_column():
    """PINS A KNOWN LIMITATION. There is no `line_type` column.

    Tax, freight, fee and discount rows are excluded by the extraction prompt
    (STEP 3: "EXCLUDE subtotal, VAT, tax, total, ...") rather than by a stored
    row kind. If the VLM fails to exclude one it is summed like any other line
    and the set mismatches — the safe direction, but a false quarantine.

    See AAM_merger_V3_PRODUCT.md, Accepted limitations.
    """
    assert not hasattr(LineItem, "line_type")
    assert not hasattr(LineItem, "part_no")
    assert not hasattr(LineItem, "uom")


# ==========================================================================
# Documentation guard: the PRODUCT doc's flag taxonomy must match the code
# ==========================================================================


def _product_doc() -> str:
    import pathlib

    return pathlib.Path("AAM_merger_V3_PRODUCT.md").read_text(encoding="utf-8")


def test_product_doc_lists_every_reason_code_the_reconciler_emits():
    """PRODUCT 3.1 must cover every reason `reconcile_po_set` can return.

    The dashboard renders `reconcile_reason` from REASON_TEXT. A reason the
    reconciler emits but the doc does not list is a reason nobody reviewed, and
    an unmapped code silently degrades to "Awaiting further processing" —
    understating a quarantine as a wait.
    """
    import pathlib
    import re

    from app.services.reconciliation import REASON_TEXT

    src = pathlib.Path("src/app/services/reconciliation.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'reason="(\w+)"', src))
    emitted |= {"po_line_missing_line_item_no"}  # returned via po_fail

    assert emitted, "expected to find reason codes in the reconciler"
    missing = emitted - set(REASON_TEXT)
    assert not missing, f"reason codes emitted but absent from REASON_TEXT: {missing}"

    doc = _product_doc()
    undocumented = {r for r in emitted if f"`{r}`" not in doc}
    assert not undocumented, f"reason codes emitted but not in PRODUCT 3.1: {undocumented}"


def test_product_doc_lists_every_per_line_flag_type():
    """PRODUCT 3.2 must list every flag `type` the comparison can emit.

    Guards against a flag type being added without the doc, and against a
    retired type being documented as if it still existed.
    """
    from app.services.reconciliation import compare_po_set_lines

    def L(no, desc, q):
        return {"line_item_no": no, "description": desc, "quantity": q * 1000}

    scenarios = [
        ([L("1", "A", 100)], [L("1", "A", 40)], [L("1", "A", 100)]),  # DN quantity
        ([L("1", "A", 100)], [L("1", "A", 100)], [L("1", "A", 100), L("9", "Z", 5)]),  # SI orphan
        (
            [L("1", "A", 100)],
            [L("1", "A", 100), L("9", "Z", 5)],
            [{"line_item_no": None, "description": "nothing alike", "quantity": 100_000}],
        ),  # both pools, different identification reasons
    ]
    seen_types, seen_reasons, seen_pools = set(), set(), set()
    for po, dn, si in scenarios:
        for f in compare_po_set_lines(po, dn, si)["flags"]:
            seen_types.add(f["type"])
            seen_reasons.add(f["reason"])
            seen_pools.add(f["pool"])
            assert f["priority"] in (1, 2)

    assert seen_types == {"identification", "quantity"}, seen_types
    assert seen_reasons == {
        "quantity_mismatch",
        "no_po_line_with_this_number",
        "no_line_number_and_no_description_match",
    }, seen_reasons
    assert seen_pools == {"DN", "SI"}, seen_pools

    doc = _product_doc()
    for t in seen_types | seen_reasons:
        assert f"`{t}`" in doc, f"flag value {t!r} is not documented in PRODUCT 3.2"

    # the naming flag is raised by reconcile_po_set, not the comparison
    assert "`naming`" in doc, "the naming flag is not documented in PRODUCT 3.2"


def test_multiple_po_documents_quarantines_and_is_configurable():
    """PRODUCT 3.1.1: two POs in one set quarantine rather than being summed.

    Summing them would double the baseline every DN/SI quantity is compared
    against, and the set could never reconcile for an obvious reason.
    """
    import itertools
    from pathlib import Path as _P

    from pypdf import PdfWriter

    from app.services.reconciliation import reconcile_po_set

    seq = itertools.count()

    def _real_pdf(path, width=100):
        _P(path).parent.mkdir(parents=True, exist_ok=True)
        w = PdfWriter()
        w.add_blank_page(width=width, height=72)
        w.write(str(path))

    def build(cfg, n_po_docs):
        from sqlalchemy.orm import Session

        from app.core.database import get_engine
        from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus

        eng = get_engine(cfg)
        run = next(seq)
        store = cfg.paths.stored_documents_folder
        with Session(eng) as s:
            ps = POSet(po_no_normalized="MULTI", status=POSetStatus.pending)
            s.add(ps)
            s.commit()
            s.refresh(ps)
            pid = ps.id
            for i in range(n_po_docs):
                stored = f"{store}/po{run}_{i}.pdf"
                _real_pdf(stored, 100 + i)
                d = Document(
                    sha256_hash=f"mp{run}_po_{i}",
                    original_filename=f"po{i}.pdf",
                    stored_path=stored,
                    doc_type=DocType.PO,
                    extraction_status=ExtractionStatus.valid,
                    po_set_id=pid,
                    po_no_normalized="MULTI",
                )
                s.add(d)
                s.commit()
                s.add(
                    LineItem(
                        document_id=d.id,
                        line_item_no="1",
                        description="W",
                        quantity=100_000,
                        unit_price=1000,
                    )
                )
                s.commit()
            for dt, extra in (
                ("DN", {}),
                ("SI", {"si_no": f"INV{run}", "invoice_no": f"INV{run}"}),
            ):
                stored = f"{store}/{dt.lower()}{run}.pdf"
                _real_pdf(stored, 140)
                d = Document(
                    sha256_hash=f"mp{run}_{dt}",
                    original_filename=f"{dt.lower()}.pdf",
                    stored_path=stored,
                    doc_type=DocType[dt],
                    extraction_status=ExtractionStatus.valid,
                    po_set_id=pid,
                    po_no_normalized="MULTI",
                    **extra,
                )
                s.add(d)
                s.commit()
                s.add(
                    LineItem(
                        document_id=d.id,
                        line_item_no="1",
                        description="W",
                        quantity=100_000,
                        unit_price=1000,
                    )
                )
                s.commit()
        return pid

    import pathlib
    import tempfile

    from app.core.config import load_config
    from app.models.base import Base

    tmp = pathlib.Path(tempfile.mkdtemp())
    base = tmp.as_posix()
    for sub in ("out", "stored", "q"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = f"{base}/mp.db"
    cfg.paths.output_folder = f"{base}/out"
    cfg.paths.stored_documents_folder = f"{base}/stored"
    cfg.paths.quarantine_folder = f"{base}/q"
    Base.metadata.create_all(get_engine(cfg))

    assert cfg.reconciliation.single_po_document is True, "the gate must default ON"

    pid = build(cfg, 2)
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "quarantined"
    assert res["reason"] == "multiple_po_documents"
    assert "2 PO documents" in res["detail"]
    assert res["flags"][0]["type"] == "identification"

    # one PO is fine
    pid1 = build(cfg, 1)
    assert reconcile_po_set(pid1, cfg)["status"] == "merged"

    # and the gate is genuinely a switch
    cfg.reconciliation.single_po_document = False
    pid2 = build(cfg, 2)
    res = reconcile_po_set(pid2, cfg)
    assert res.get("reason") != "multiple_po_documents"
    assert res["status"] == "mismatched", "two summed POs double the baseline and cannot reconcile"


def test_price_is_never_compared():
    """Quantities are the only reconciling signal. Prices ride along, unused.

    If a price comparison is ever reintroduced this fails, which is the point:
    it would have to be a product decision, and a flag type with it, documented
    in PRODUCT 3.2.
    """
    import app.services.reconciliation as rec
    from app.services.matching import group_by_line_no

    assert not hasattr(rec, "check_price")

    # unit_price is carried through the comparison untouched and never read
    import inspect

    src = inspect.getsource(group_by_line_no) + inspect.getsource(rec.compare_po_set_lines)
    assert "unit_price" not in src

    # and a price-only difference cannot change the outcome
    def L(no, q, price):
        return {"line_item_no": no, "description": "A", "quantity": q * 1000, "unit_price": price}

    cheap = group_by_line_no([L("1", 100, 10_000)], [L("1", 100, 99_000)])
    dear = group_by_line_no([L("1", 100, 99_000)], [L("1", 100, 10_000)])
    assert cheap[0] == dear[0] and cheap[1] == dear[1] and cheap[2] == dear[2]


@pytest.mark.parametrize(
    "name,spec",
    [
        (
            "qty_mismatch",
            {
                PO: [_line("1", "DRILL BIT P/N# DB-9", "2.00")],
                DN: [_line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "3.00")],
                SI: [_line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "3.00")],
            },
        ),
        (
            "orphan_vendor_line",
            {
                PO: [_line("1", "DRILL BIT P/N# DB-9", "2.00")],
                DN: [
                    _line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00"),
                    _line("7", "MYSTERY PART P/N# ZZ-7", "9.00"),
                ],
                SI: [
                    _line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00"),
                    _line("7", "MYSTERY PART P/N# ZZ-7", "9.00"),
                ],
            },
        ),
        (
            "missing_si",
            {
                PO: [_line("1", "DRILL BIT P/N# DB-9", "2.00")],
                DN: [_line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00")],
            },
        ),
    ],
)
def test_spec_6_3_never_merge_on_ambiguity(tmp_path, name, spec):
    """§6.3: ambiguity must never produce a merge."""
    status, reason, merged, *_ = run(tmp_path, f"SYN63{name}", spec)
    assert not merged, f"§6.3: {name} produced a merge (status={status})"
    assert status != "merged", f"§6.3: {name} must not merge, got {status}"
    assert reason and len(reason) > 5, f"§6.3: {name} left no readable reason"
