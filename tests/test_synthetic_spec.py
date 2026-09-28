"""Tests derived directly from `notebooklm/synthetic_test_data_spec.md`.

The spec is the reference here, not our current implementation. Every expected
outcome below is quoted or directly derived from the spec:

  §3.1  line_type is "ABSOLUTELY ESSENTIAL"; only GOODS rows join the math
  §3.2  line_item_number / part_number / raw_qty variation space
  §2    document-level field variation (po_no syntax, multi-PO, page bounds)
  §5    the TC-SYN-001..012 edge-case matrix (each is a MANDATED outcome)
  §6.3  "a silent wrong merge is a P0 critical failure; a false quarantine is
         acceptable"  <- this is the governing safety invariant

NO SOURCE CODE IS CHANGED BY THIS FILE. Tests that fail are the deliverable:
they mark where the current engine disagrees with the specification. Fixes are
a separate, agreed step.

Status vocabulary matches the engine: merged / quarantined / mismatched.
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

# --------------------------------------------------------------------------
# builder
# --------------------------------------------------------------------------

PO, DN, SI = "PO", "DN", "SI"


def _line(no, desc, qty, part=None, line_type="GOODS"):
    return {"no": no, "desc": desc, "qty": qty, "part": part, "type": line_type}


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
        # ONE document per doc type, carrying all of its line items.
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
                        part_no=ln["part"],
                        line_type=ln["type"],
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
            _line("1", "FILTER ELEMENT P/N# 250025-526", "5.00", "250025-526"),
            _line("2", "SEPARATOR W/GASKET P/N# 250034-085", "2.00", "250034-085"),
        ],
        DN: [
            _line("1", "FILTER ELEMENT P/N# 250025-526 Line Item - 1", "5.00", "250025-526"),
            _line("2", "SEPARATOR W/GASKET P/N# 250034-085 Line Item - 2", "2.00", "250034-085"),
        ],
        SI: [
            _line("1", "FILTER ELEMENT P/N# 250025-526 Line Item - 1", "5.00", "250025-526"),
            _line("2", "SEPARATOR W/GASKET P/N# 250034-085 Line Item - 2", "2.00", "250034-085"),
        ],
    },
    "uom_box_vs_each": {
        PO: [_line("1", "Absorbent Pad P/N# AP-100", "1.00", "AP-100")],
        DN: [_line("1", "Absorbent Pad P/N# AP-100 Line Item - 1", "1.00", "AP-100")],
        SI: [_line("1", "Absorbent Pad P/N# AP-100 Line Item - 1", "1.00", "AP-100")],
    },
    "with_tax": {
        PO: [_line("1", "DRILL BIT P/N# DB-9", "2.00", "DB-9")],
        DN: [
            _line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9"),
            _line("VAT-12", "Value Added Tax 5.00%", "5.00", None, "TAX"),
        ],
        SI: [
            _line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9"),
            _line("VAT-12", "Value Added Tax 5.00%", "5.00", None, "TAX"),
        ],
    },
}


@pytest.mark.parametrize("name", sorted(INVARIANT_SETS), ids=sorted(INVARIANT_SETS))
def test_spec_6_3_no_silent_wrong_merge(tmp_path, name):
    """§6.3: a silent wrong merge is P0. Recompute the math independently.

    If the engine says merged, then for every GOODS line the PO quantity must
    equal the DN aggregate AND the SI aggregate, and no GOODS line may be
    left without a PO counterpart. Anything else is a wrong answer.
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
                if (li.line_type or "GOODS") == "GOODS"
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
        # no orphan vendor goods line
        for v in pool:
            assert any(v["no"] == p["no"] for p in po), (
                f"WRONG MERGE [{name}] {pool_name} line {v['no']} has no PO counterpart"
            )


# ==========================================================================
# §5  TC-SYN-001..012  (each outcome is MANDATED by the spec)
# ==========================================================================


def test_tc_syn_001_numeric_gate_rejects_molykote_confusion(tmp_path):
    """TC-SYN-001: PO 'MOLYKOTE 111' vs invoice 'MOLYKOTE 4' (>89% token sim).

    Spec: "System MUST REJECT / Quarantine via Numeric Model-Code Gate."
    """
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
    assert status == "quarantined", f"TC-SYN-001: must quarantine, got {status}"


def test_tc_syn_002_step10_must_not_shift_contiguous_lines(tmp_path):
    """TC-SYN-002: PO Line 10 must match Vendor Line 10, not Vendor Line 1.

    15 contiguous lines with a unique quantity per line, so any off-by-nine
    step-10 shift produces a quantity mismatch instead of a silent pass.
    """
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
    assert status == "merged", f"TC-SYN-002: step-10 must not misfire, got {status} ({reason})"


def test_tc_syn_002b_nomac_step10_po_matches_reindexed_vendor(tmp_path):
    """TC-SYN-002: NOMAC PO prints 10/20/30; vendor re-indexes to 1/2/3."""
    status, reason, *_ = run(
        tmp_path,
        "SYN002B",
        {
            PO: [
                _line("10", "GATE VALVE 2IN P/N# GV-2", "2.00", "GV-2"),
                _line("20", "FLANGE 8IN P/N# FL-8", "4.00", "FL-8"),
            ],
            DN: [
                _line("1", "GATE VALVE 2IN P/N# GV-2 Line Item - 10", "2.00", "GV-2"),
                _line("2", "FLANGE 8IN P/N# FL-8 Line Item - 20", "4.00", "FL-8"),
            ],
            SI: [
                _line("1", "GATE VALVE 2IN P/N# GV-2 Line Item - 10", "2.00", "GV-2"),
                _line("2", "FLANGE 8IN P/N# FL-8 Line Item - 20", "4.00", "FL-8"),
            ],
        },
    )
    assert status == "merged", f"TC-SYN-002 NOMAC step-10: got {status} ({reason})"


def test_tc_syn_003_line_item_has_uom_field():
    """TC-SYN-003 requires a UOM Conversion Matrix; the model has no UOM column.

    Spec (AGENTS.md §3): "No part_no/UOM columns — do not add without spec
    change." This test records that the schema gap still blocks TC-SYN-003.
    """
    assert hasattr(LineItem, "uom"), (
        "TC-SYN-003 cannot be satisfied: LineItem has no UOM field, so a PO "
        "ordering 1 BOX and a DN delivering 1 EA are indistinguishable."
    )


def test_tc_syn_003_box_vs_each_must_not_merge(tmp_path):
    """TC-SYN-003: 1 BOX (12 pcs) ordered, 1 EA delivered -> MUST quarantine.

    UOM is carried in the description because the schema has no UOM column.
    """
    status, _reason, *_ = run(
        tmp_path,
        "SYN003",
        {
            PO: [_line("1", "Absorbent Pad P/N# AP-100  1.00 BOX", "1.00", "AP-100")],
            DN: [_line("1", "Absorbent Pad P/N# AP-100 Line Item - 1  1.00 EA", "1.00", "AP-100")],
            SI: [_line("1", "Absorbent Pad P/N# AP-100 Line Item - 1  1.00 EA", "1.00", "AP-100")],
        },
    )
    assert status == "quarantined", (
        f"TC-SYN-003: 1 BOX vs 1 EA is a wrong merge if it merges. Got {status}"
    )


def test_tc_syn_004_reindexed_partial_uses_sku(tmp_path):
    """TC-SYN-004: DN Line 1 = Pipe Repair Kit must match PO Line 2 via SKU."""
    status, reason, *_ = run(
        tmp_path,
        "SYN004",
        {
            PO: [
                _line("1", "Absorbent Pad P/N# AP-100", "3.00", "AP-100"),
                _line("2", "Rapp-it Pipe Repair Kit 2x12 P/N# RAP122", "1.00", "RAP122"),
            ],
            DN: [_line("1", "Rapp-it Pipe Repair Kit 2x12 P/N# RAP122 Line Item - 2", "1.00", "RAP122")],
            SI: [_line("1", "Rapp-it Pipe Repair Kit 2x12 P/N# RAP122 Line Item - 2", "1.00", "RAP122")],
        },
    )
    assert status == "merged", f"TC-SYN-004 SKU rescue failed, got {status} ({reason})"


def test_tc_syn_005_ocr_decimal_drop_is_not_silently_zero(tmp_path):
    """TC-SYN-005: '600 EACH' (from 6.00) must fail the parser, not become 600."""
    with pytest.raises(ValueError):
        parse_quantity_scaled("600 EACH")
    status, _reason, *_ = run(
        tmp_path,
        "SYN005",
        {
            PO: [_line("1", "DRILL STEEL 600 EACH P/N# DS-6", "6.00", "DS-6")],
            DN: [_line("1", "DRILL STEEL 600 EACH P/N# DS-6 Line Item - 1", "600 EACH", "DS-6")],
            SI: [_line("1", "DRILL STEEL 600 EACH P/N# DS-6 Line Item - 1", "600 EACH", "DS-6")],
        },
    )
    assert status == "quarantined", f"TC-SYN-005 must quarantine, got {status}"


@pytest.mark.parametrize(
    "desc,qty,expected_type",
    [
        ("Courier Freight Transport - 4 PCS / 102 LBS", "4.00", "FREIGHT"),
        ("Value Added Tax 5.00%", "5.00", "TAX"),
        ("VAT 10%", "17.200", "TAX"),
        ("VAT-12", "5.00", "TAX"),
        ("Custom Export Clearance Service", "150.00", "SERVICE"),
        ("Bank Transfer Fee", "2.50", "FEE"),
        ("Prompt Payment Discount", "1.00", "DISCOUNT"),
    ],
)
def test_spec_3_1_non_goods_rows_excluded_from_math(tmp_path, desc, qty, expected_type):
    """§3.1: non-GOODS rows must not enter PO == DN == SI arithmetic."""
    status, reason, *_ = run(
        tmp_path,
        "LT",
        {
            PO: [_line("1", "DRILL BIT P/N# DB-9", "2.00", "DB-9")],
            DN: [
                _line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9"),
                _line("9", desc, qty, None, expected_type),
            ],
            SI: [
                _line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9"),
                _line("9", desc, qty, None, expected_type),
            ],
        },
    )
    assert status == "merged", (
        f"§3.1: a {expected_type} row must be excluded from the maths, "
        f"so this set should merge. Got {status} ({reason})"
    )


def test_tc_syn_006_tax_row_excluded(tmp_path):
    """TC-SYN-006: tax row qty 5.00 inside the main table must be TAX + excluded."""
    status, reason, *_ = run(
        tmp_path,
        "SYN006",
        {
            PO: [_line("1", "DRILL BIT P/N# DB-9", "2.00", "DB-9")],
            DN: [
                _line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9"),
                _line("5", "Value Added Tax 5.00%", "5.00", None, "TAX"),
            ],
            SI: [
                _line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9"),
                _line("5", "Value Added Tax 5.00%", "5.00", None, "TAX"),
            ],
        },
    )
    assert status == "merged", f"TC-SYN-006 tax must be excluded, got {status} ({reason})"


def test_tc_syn_007_freight_row_excluded(tmp_path):
    """TC-SYN-007: 'Courier Shipping Fee' qty 4 must be FREIGHT + excluded."""
    status, reason, *_ = run(
        tmp_path,
        "SYN007",
        {
            PO: [_line("1", "DRILL BIT P/N# DB-9", "2.00", "DB-9")],
            DN: [
                _line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9"),
                _line("4", "Courier Shipping Fee - 4 PCS", "4.00", None, "FREIGHT"),
            ],
            SI: [
                _line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9"),
                _line("4", "Courier Shipping Fee - 4 PCS", "4.00", None, "FREIGHT"),
            ],
        },
    )
    assert status == "merged", f"TC-SYN-007 freight must be excluded, got {status} ({reason})"


def test_tc_syn_008_multi_po_invoice_must_not_merge_blindly(tmp_path):
    """TC-SYN-008: one invoice listing several POs must be split per line PO ref.

    The engine cannot yet disaggregate, so the only acceptable behaviour is a
    quarantine. A merge here would be a wrong answer (§6.3).
    """
    status, _reason, *_ = run(
        tmp_path,
        "SYN008",
        {
            PO: [_line("1", "GATE VALVE P/N# GV-2", "2.00", "GV-2")],
            DN: [_line("1", "GATE VALVE P/N# GV-2 Line Item - 1", "2.00", "GV-2")],
            SI: [
                _line("1", "GATE VALVE P/N# GV-2 PO#1001 Line Item - 1", "2.00", "GV-2"),
                _line("2", "FLANGE 8IN P/N# FL-8 PO#1002", "3.00", "FL-8"),
            ],
        },
    )
    assert status != "merged", (
        f"TC-SYN-008: SI carries lines for a second PO (#1002); merging would be "
        f"a wrong answer. Got {status}"
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


def test_tc_syn_009_po_number_punctuation_shift(tmp_path):
    """TC-SYN-009: hyphen-shifted PO reference must still reconcile."""
    po_no = normalize_po_no("D7264-PO-186000-013-01")
    status, reason, *_ = run(
        tmp_path,
        po_no,
        {
            PO: [_line("1", '1 1/4" TONG DIE P/N# TD-1', "2.00", "TD-1")],
            DN: [_line("1", '1 1/4" TONG DIE P/N# TD-1 Line Item - 1', "2.00", "TD-1")],
            SI: [_line("1", '1 1/4" TONG DIE P/N# TD-1 Line Item - 1', "2.00", "TD-1")],
        },
    )
    assert status == "merged", f"TC-SYN-009: got {status} ({reason})"


def test_tc_syn_010_two_dockets_restarting_line_numbers(tmp_path):
    """TC-SYN-010: two dockets each restart at line 1 must not be conflated.

    Multi-docket splitting is not implemented. The only safe outcome is a
    quarantine; a merge would attribute one docket's quantity to another.
    """
    status, _reason, *_ = run(
        tmp_path,
        "SYN010",
        {
            PO: [_line("1", "GATE VALVE P/N# GV-2", "2.00", "GV-2")],
            DN: [
                _line("1", "GATE VALVE P/N# GV-2 Line Item - 1", "2.00", "GV-2"),
                _line("1", "FLANGE 8IN P/N# FL-8 Line Item - 1", "3.00", "FL-8"),
            ],
            SI: [
                _line("1", "GATE VALVE P/N# GV-2 Line Item - 1", "2.00", "GV-2"),
                _line("1", "FLANGE 8IN P/N# FL-8 Line Item - 1", "3.00", "FL-8"),
            ],
        },
    )
    assert status != "merged", (
        f"TC-SYN-010: two dockets share line number 1; merging is a wrong answer. Got {status}"
    )


@pytest.mark.parametrize("printed", ["1-1", "2-1", "10", "20", "30", "VAT-12", "1"])
def test_spec_3_2_line_number_preserved_verbatim(printed):
    """§3.2 + TC-SYN-011: line_item_number must survive normalisation intact.

    TC-SYN-011: "System MUST extract full string '1-1' as line_item_number."
    """
    assert normalize_line_no(printed) == printed, (
        f"§3.2: {printed!r} was rewritten to {normalize_line_no(printed)!r}; "
        f"the spec requires the full string preserved"
    )


def test_tc_syn_011_hyphenated_line_numbers_reconcile(tmp_path):
    """TC-SYN-011: Valaris PO lines '1-1' and '2-1' must match verbatim."""
    status, reason, *_ = run(
        tmp_path,
        "SYN011",
        {
            PO: [
                _line("1-1", "SUBSEA DRILL COLLAR P/N# SDC-1", "1.00", "SDC-1"),
                _line("2-1", "RISER TENSIONER P/N# RT-2", "1.00", "RT-2"),
            ],
            DN: [
                _line("1-1", "SUBSEA DRILL COLLAR P/N# SDC-1 Line Item - 1-1", "1.00", "SDC-1"),
                _line("2-1", "RISER TENSIONER P/N# RT-2 Line Item - 2-1", "1.00", "RT-2"),
            ],
            SI: [
                _line("1-1", "SUBSEA DRILL COLLAR P/N# SDC-1 Line Item - 1-1", "1.00", "SDC-1"),
                _line("2-1", "RISER TENSIONER P/N# RT-2 Line Item - 2-1", "1.00", "RT-2"),
            ],
        },
    )
    assert status == "merged", f"TC-SYN-011: got {status} ({reason})"


def test_tc_syn_012_single_candidate_still_enforced(tmp_path):
    """TC-SYN-012: one PO line, invoice description 86% similar but wrong.

    Spec: "MUST enforce absolute similarity threshold and model code gate
    despite no runner-up."
    """
    status, _reason, *_ = run(
        tmp_path,
        "SYN012",
        {
            PO: [_line("1", "GATE VALVE 2IN CL150 P/N# GV-2", "2.00", "GV-2")],
            DN: [_line("1", "GATE VALVE 3IN CL150 P/N# GV-3 Line Item - 1", "2.00", "GV-3")],
            SI: [_line("1", "GATE VALVE 3IN CL150 P/N# GV-3 Line Item - 1", "2.00", "GV-3")],
        },
    )
    assert status == "quarantined", f"TC-SYN-012 must quarantine, got {status}"


# ==========================================================================
# §3.2  raw_qty / description variation space
# ==========================================================================


@pytest.mark.parametrize(
    "printed,expected_scaled",
    [
        ("1.00", 1000),
        ("12.50", 12500),
        ("25.00", 25000),
        ("14", 14000),
        ("1 000", 1000000),
        ("1,000", 1000000),
    ],
)
def test_spec_3_2_raw_qty_parsing(printed, expected_scaled):
    """§3.2: the documented printed quantity forms must parse exactly."""
    assert parse_quantity_scaled(printed) == expected_scaled


def test_spec_3_2_european_decimal_is_not_silently_accepted():
    """§3.2 lists '1,5' (European) as a real printed form.

    Under en_IN a comma is a thousands separator, so '1,5' and '17.200' are
    read as 1500 and 17200. §6.3 forbids silently wrong answers, so these must
    be rejected rather than scaled — locale policy is still undecided.
    """
    for printed in ("1,5", "17.200"):
        try:
            got = parse_quantity_scaled(printed)
        except ValueError:
            continue  # rejected: acceptable
        pytest.fail(
            f"§3.2: {printed!r} parsed to {got} under en_IN. This is a known "
            f"European decimal; locale policy is undecided, so it must reject "
            f"rather than silently mis-scale."
        )


def test_spec_3_2_dimension_fractions_not_confused():
    """§3.2: '1-1/4\" ID' vs '1-7/16\" ID' must never be treated as one item."""
    a, b = 'SOCKET 1-1/4" ID', 'SOCKET 1-7/16" ID'
    assert a != b
    from app.services.matching import _desc_score

    assert _desc_score(a, b) < 100.0, "§3.2: differing dimensions scored a perfect match"


def test_spec_3_2_alternative_part_numbers(tmp_path):
    """§3.2: 'P/N# ... ALTRANATIVE PN#...' appears in real descriptions."""
    status, reason, *_ = run(
        tmp_path,
        "SYNALTPN",
        {
            PO: [_line("1", "KIT REPAIR P/N# 02250145-797 ALTRANATIVE PN#02250145-798", "2.00", "02250145-798")],
            DN: [_line("1", "KIT REPAIR P/N# 02250145-798 Line Item - 1", "2.00", "02250145-798")],
            SI: [_line("1", "KIT REPAIR P/N# 02250145-798 Line Item - 1", "2.00", "02250145-798")],
        },
    )
    assert status == "merged", f"§3.2 alternative P/N failed, got {status} ({reason})"


def test_spec_3_2_blank_sku_items(tmp_path):
    """§3.2: generic items carry no SKU ('Blow Off Duster', 'Door mat')."""
    status, reason, *_ = run(
        tmp_path,
        "SYNBLANK",
        {
            PO: [
                _line("1", "Blow Off Duster", "1.00", ""),
                _line("2", "Door mat", "2.00", ""),
            ],
            DN: [
                _line("1", "Blow Off Duster Line Item - 1", "1.00", ""),
                _line("2", "Door mat Line Item - 2", "2.00", ""),
            ],
            SI: [
                _line("1", "Blow Off Duster Line Item - 1", "1.00", ""),
                _line("2", "Door mat Line Item - 2", "2.00", ""),
            ],
        },
    )
    assert status == "merged", f"§3.2 blank-SKU items failed, got {status} ({reason})"


# ==========================================================================
# §3.1  line_type plumbing
# ==========================================================================


def test_spec_3_1_line_type_persisted_and_filtered(tmp_path):
    """§3.1: every non-GOODS value must be stored and kept out of the math."""
    for ltype in ("GOODS", "FREIGHT", "TAX", "FEE", "SERVICE", "DISCOUNT"):
        ps_id, eng, cfg = build(
            tmp_path,
            f"LT_{ltype}",
            {
                PO: [_line("1", "DRILL BIT P/N# DB-9", "2.00", "DB-9")],
                DN: [_line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9")],
                SI: [_line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9", ltype)],
            },
        )
        reconcile_po_set(ps_id, cfg)
        with Session(eng) as s:
            from app.models import Document as D

            si = s.query(D).filter_by(doc_type=DocType.SI).first()
            stored = si.line_items[0].line_type
        assert stored == ltype, f"§3.1: line_type {ltype!r} was not persisted (got {stored!r})"


def test_spec_3_1_unknown_line_type_defaults_to_goods():
    """§3.1: an unrecognised classification must fail safe (treated as GOODS).

    Asserted against the extraction clamp, which is where classification is
    normalised. Writing straight to the model would bypass that clamp.
    """
    from app.services.extraction import _norm_line_type

    assert _norm_line_type("MYSTERY") == "GOODS"
    assert _norm_line_type(None) == "GOODS"
    assert _norm_line_type("") == "GOODS"
    for known in ("GOODS", "FREIGHT", "TAX", "FEE", "SERVICE", "DISCOUNT"):
        assert _norm_line_type(known) == known


# ==========================================================================
# §6.3  quarantine hygiene
# ==========================================================================


@pytest.mark.parametrize(
    "name,spec",
    [
        ("qty_mismatch", {
            PO: [_line("1", "DRILL BIT P/N# DB-9", "2.00", "DB-9")],
            DN: [_line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "3.00", "DB-9")],
            SI: [_line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "3.00", "DB-9")],
        }),
        ("orphan_vendor_line", {
            PO: [_line("1", "DRILL BIT P/N# DB-9", "2.00", "DB-9")],
            DN: [
                _line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9"),
                _line("7", "MYSTERY PART P/N# ZZ-7", "9.00", "ZZ-7"),
            ],
            SI: [
                _line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9"),
                _line("7", "MYSTERY PART P/N# ZZ-7", "9.00", "ZZ-7"),
            ],
        }),
        ("missing_si", {
            PO: [_line("1", "DRILL BIT P/N# DB-9", "2.00", "DB-9")],
            DN: [_line("1", "DRILL BIT P/N# DB-9 Line Item - 1", "2.00", "DB-9")],
        }),
    ],
)
def test_spec_6_3_never_merge_on_ambiguity(tmp_path, name, spec):
    """§6.3: ambiguity must quarantine, never merge."""
    status, reason, merged, *_ = run(tmp_path, f"SYN63{name}", spec)
    assert not merged, f"§6.3: {name} produced a merge (status={status})"
    assert status != "merged", f"§6.3: {name} must not merge, got {status}"
    assert reason and len(reason) > 5, f"§6.3: {name} left no readable reason"
