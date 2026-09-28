"""DB-level edge cases for reconcile_po_set.

The pure-function properties live in test_prop_*; these exercise the real
persistence path, where the failures that actually reach a CA originate:
statelessness, split deliveries, COMBINED authority, and non-GOODS rows.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
from app.models.base import Base
from app.services.reconciliation import reconcile_po_set


def _tiny_pdf(path):
    from pypdf import PdfWriter

    path.parent.mkdir(parents=True, exist_ok=True)
    PdfWriter().write(str(path))
    return path


@pytest.fixture
def harness(tmp_path):
    """Factory that builds a PO Set with arbitrary documents/lines."""
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "edge.db")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    cfg.paths.output_folder = str(tmp_path / "out")
    cfg.paths.quarantine_folder = str(tmp_path / "quar")
    for sub in ("stored", "out", "quar"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    counter = {"n": 0}

    def build(docs, po_no="PO-EDGE", **po_set_kwargs):
        """docs: list of (doc_type, [line dicts], extra Document kwargs)."""
        with Session(eng) as s:
            ps = POSet(po_no_normalized=po_no, status=POSetStatus.pending, **po_set_kwargs)
            s.add(ps)
            s.commit()
            s.refresh(ps)
            for dtype, lines, extra in docs:
                counter["n"] += 1
                name = f"d{counter['n']}"
                p = _tiny_pdf(tmp_path / "stored" / f"{name}.pdf")
                d = Document(
                    sha256_hash=f"edge_{po_no}_{name}",
                    original_filename=f"{name}.pdf",
                    stored_path=str(p),
                    doc_type=DocType(dtype),
                    extraction_status=ExtractionStatus.valid,
                    po_set_id=ps.id,
                    po_no_normalized=po_no,
                    **extra,
                )
                s.add(d)
                s.commit()
                for ln in lines:
                    s.add(
                        LineItem(
                            document_id=d.id,
                            line_item_no=ln.get("no"),
                            description=ln.get("desc", "Widget"),
                            quantity=ln["qty"],
                            unit_price=ln.get("price", 100000),
                            part_no=ln.get("part_no"),
                            line_type=ln.get("line_type", "GOODS"),
                        )
                    )
                s.commit()
            return ps.id

    return build, eng, cfg


def _status(eng, ps_id):
    with Session(eng) as s:
        ps = s.get(POSet, ps_id)
        return ps.status.value, ps.merged_output_path, ps.reconcile_reason


# ------------------------------------------------------------------ statlessness


def test_reconciling_twice_is_idempotent(harness):
    """No hidden state: a second run must reach the same verdict, no drift."""
    build, eng, cfg = harness
    pid = build(
        [
            ("PO", [{"no": "1", "qty": 100}], {}),
            ("DN", [{"no": "1", "qty": 100}], {}),
            ("SI", [{"no": "1", "qty": 100}], {"si_no": "I1", "invoice_no": "I1"}),
        ]
    )
    first = reconcile_po_set(pid, cfg)
    second = reconcile_po_set(pid, cfg)
    assert first["status"] == second["status"] == "merged"
    assert _status(eng, pid)[0] == "merged"

    # And a third time, still merged, still the same output path.
    third = reconcile_po_set(pid, cfg)
    assert third["status"] == "merged"
    assert third.get("merged_output_path")


def test_rematch_does_not_double_deduct(harness):
    """The whole reason the matcher is stateless: a re-run must not sum twice."""
    build, _eng, cfg = harness
    pid = build(
        [
            ("PO", [{"no": "1", "qty": 100}], {}),
            ("DN", [{"no": "1", "qty": 60}], {}),
            ("SI", [{"no": "1", "qty": 60}], {"si_no": "I1", "invoice_no": "I1"}),
        ]
    )
    for _ in range(4):
        res = reconcile_po_set(pid, cfg)
    assert res["status"] == "mismatched"
    # The flagged aggregate must still be 60, never 120/180/240.
    qty = [f for f in res["flags"] if f.get("type") == "quantity"]
    assert qty, "expected a quantity flag"
    for f in qty:
        assert f["agg_dn_quantity"] == 60
        assert f["agg_si_quantity"] == 60


# ------------------------------------------------------------------ split delivery


def test_split_across_three_delivery_notes_sums_correctly(harness):
    """A PO line split over 3 DNs must aggregate to the PO quantity."""
    build, _eng, cfg = harness
    pid = build(
        [
            ("PO", [{"no": "1", "qty": 100}], {}),
            ("DN", [{"no": "1", "qty": 40}], {}),
            ("DN", [{"no": "1", "qty": 30}], {}),
            ("DN", [{"no": "1", "qty": 30}], {}),
            ("SI", [{"no": "1", "qty": 70}], {"si_no": "I1", "invoice_no": "I1"}),
            ("SI", [{"no": "1", "qty": 30}], {"si_no": "I2", "invoice_no": "I2"}),
        ]
    )
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "merged", f"{res.get('reason')} {res.get('flags')}"


def test_over_delivery_across_many_dns_is_caught(harness):
    build, eng, cfg = harness
    pid = build(
        [
            ("PO", [{"no": "1", "qty": 100}], {}),
            ("DN", [{"no": "1", "qty": 60}], {}),
            ("DN", [{"no": "1", "qty": 60}], {}),  # 120 total
            ("SI", [{"no": "1", "qty": 120}], {"si_no": "I1", "invoice_no": "I1"}),
        ]
    )
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "mismatched"
    assert _status(eng, pid)[1] is None, "must not merge on over-delivery"


# ------------------------------------------------------------------ COMBINED


def test_combined_with_separate_docs_uses_combined_only(harness):
    """COMBINED is authoritative; counting separate lines too would double."""
    build, _eng, cfg = harness
    pid = build(
        [
            ("PO", [{"no": "1", "qty": 100}], {}),
            ("DN", [{"no": "1", "qty": 100}], {}),
            ("SI", [{"no": "1", "qty": 100}], {"si_no": "I1", "invoice_no": "I1"}),
            (
                "COMBINED",
                [{"no": "1", "qty": 100}],
                {
                    "si_no": "IC",
                    "invoice_no": "IC",
                    "raw_extraction_json": json.dumps(
                        {"has_po_section": True, "has_dn_section": True, "has_si_section": True}
                    ),
                },
            ),
        ]
    )
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "merged", f"{res.get('reason')} {res.get('flags')}"


def test_combined_mismatch_blocks_merge(harness):
    build, eng, cfg = harness
    pid = build(
        [
            ("PO", [{"no": "1", "qty": 100}], {}),
            (
                "COMBINED",
                [{"no": "1", "qty": 90}],
                {
                    "si_no": "IC",
                    "invoice_no": "IC",
                    "raw_extraction_json": json.dumps(
                        {"has_po_section": True, "has_dn_section": True, "has_si_section": True}
                    ),
                },
            ),
        ]
    )
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "mismatched"
    assert _status(eng, pid)[1] is None


# ------------------------------------------------------------------ non-GOODS


def test_non_goods_rows_are_fully_excluded(harness):
    """Tax/freight rows on every document must not perturb the math at all."""
    build, _eng, cfg = harness
    pid = build(
        [
            ("PO", [{"no": "1", "qty": 100}], {}),
            (
                "DN",
                [
                    {"no": "1", "qty": 100},
                    {"no": "2", "qty": 500, "line_type": "TAX"},
                    {"no": "3", "qty": 900, "line_type": "FREIGHT"},
                ],
                {},
            ),
            (
                "SI",
                [
                    {"no": "1", "qty": 100},
                    {"no": "2", "qty": 500, "line_type": "TAX"},
                ],
                {"si_no": "I1", "invoice_no": "I1"},
            ),
        ]
    )
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "merged", f"{res.get('reason')} {res.get('flags')}"


# ------------------------------------------------------------------ degenerate


def test_po_with_no_lines_stays_pending(harness):
    build, eng, cfg = harness
    pid = build([("PO", [], {}), ("DN", [], {}), ("SI", [], {"si_no": "I", "invoice_no": "I"})])
    res = reconcile_po_set(pid, cfg)
    assert res["status"] in ("pending", "quarantined")
    assert _status(eng, pid)[1] is None


def test_missing_si_stays_pending(harness):
    build, _eng, cfg = harness
    pid = build([("PO", [{"no": "1", "qty": 100}], {}), ("DN", [{"no": "1", "qty": 100}], {})])
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "pending"


def test_unknown_line_type_falls_back_to_goods(harness):
    """An unrecognised line_type must not silently drop a real line."""
    build, _eng, cfg = harness
    pid = build(
        [
            ("PO", [{"no": "1", "qty": 100, "line_type": "MYSTERY"}], {}),
            ("DN", [{"no": "1", "qty": 100, "line_type": "MYSTERY"}], {}),
            (
                "SI",
                [{"no": "1", "qty": 100, "line_type": "MYSTERY"}],
                {"si_no": "I", "invoice_no": "I"},
            ),
        ]
    )
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "merged", "unknown line_type must be treated as GOODS"


def test_zero_quantity_line_quarantines_whole_set(harness):
    build, eng, cfg = harness
    pid = build(
        [
            ("PO", [{"no": "1", "qty": 100}, {"no": "2", "qty": 0}], {}),
            ("DN", [{"no": "1", "qty": 100}, {"no": "2", "qty": 0}], {}),
            (
                "SI",
                [{"no": "1", "qty": 100}, {"no": "2", "qty": 0}],
                {"si_no": "I", "invoice_no": "I"},
            ),
        ]
    )
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "quarantined"
    assert _status(eng, pid)[1] is None


def test_reason_is_persisted_for_dashboard(harness):
    """Every terminal state leaves a human-readable reason behind."""
    build, eng, cfg = harness
    pid = build([("PO", [{"no": "1", "qty": 100}], {}), ("DN", [{"no": "1", "qty": 50}], {})])
    reconcile_po_set(pid, cfg)
    reason = _status(eng, pid)[2]
    assert reason, "reason must be persisted so the dashboard can explain the state"
    assert isinstance(reason, str) and len(reason) > 5
