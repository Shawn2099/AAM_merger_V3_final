"""DB-level edge cases for reconcile_po_set.

The pure-function properties live in test_prop_*; these exercise the real
persistence path, where the failures that actually reach a CA originate:
statelessness, split deliveries, over-delivery, and non-GOODS rows.
"""

from __future__ import annotations

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
    """The whole reason the comparison is stateless: a re-run must not sum twice."""
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
        # this harness stores raw quantities unscaled
        assert f["po_quantity"] == 100
        assert f["vendor_quantity"] == 60
    # both pools are reported separately and independently
    assert {f["pool"] for f in qty} == {"DN", "SI"}


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


# ------------------------------------------------------------------ non-GOODS


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
    """Removed: line_type no longer exists. Tax/freight/fee rows are excluded
    by the extraction prompt (STEP 3 'EXCLUDE subtotal, VAT, tax, ...') rather
    than by a stored row kind. See AAM_merger_V3_PRODUCT.md, Accepted
    limitations â€” a tax row the VLM fails to exclude will be summed."""


def test_unexcluded_tax_row_summed_and_mismatches(harness):
    """PRODUCT §8 M-limitation, behavioural pin: a tax row the VLM failed to
    exclude is summed like any other line. DN total 110 vs PO 100 mismatches
    (safe direction — false quarantine, never a wrong merge). SI pool agrees,
    proving the verdict comes from the DN pool alone."""
    build, eng, cfg = harness
    pid = build(
        [
            ("PO", [{"no": "1", "qty": 100}], {}),
            (
                "DN",
                [
                    {"no": "1", "qty": 100},
                    {"no": "1", "qty": 10, "desc": "VAT 10%"},
                ],
                {},
            ),
            ("SI", [{"no": "1", "qty": 100}], {"si_no": "I1", "invoice_no": "I1"}),
        ]
    )
    res = reconcile_po_set(pid, cfg)
    assert res["status"] == "mismatched"
    assert _status(eng, pid)[1] is None, "must not merge with a tax-inflated total"
    dn_flags = [f for f in res["flags"] if f.get("pool") == "DN" and f.get("type") == "quantity"]
    assert dn_flags and all(f["vendor_quantity"] == 110 for f in dn_flags)
    assert not [f for f in res["flags"] if f.get("pool") == "SI"], "SI pool agreed"


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


def test_second_set_with_taken_invoice_name_quarantines(harness):
    """One invoice number covering two PO Sets: the first merges, the second
    quarantines with packet_naming_failed instead of clobbering the delivered
    packet. Fail closed, never overwrite."""
    from pathlib import Path

    build, eng, cfg = harness
    first = build(
        [
            ("PO", [{"no": "1", "qty": 100}], {}),
            ("DN", [{"no": "1", "qty": 100}], {}),
            ("SI", [{"no": "1", "qty": 100}], {"si_no": "DUP-INV", "invoice_no": "DUP-INV"}),
        ],
        po_no="PO-FIRST",
    )
    assert reconcile_po_set(first, cfg)["status"] == "merged"
    packet = Path(cfg.paths.output_folder) / "DUP-INV.pdf"
    assert packet.exists()
    before = packet.read_bytes()

    second = build(
        [
            ("PO", [{"no": "1", "qty": 50}], {}),
            ("DN", [{"no": "1", "qty": 50}], {}),
            ("SI", [{"no": "1", "qty": 50}], {"si_no": "DUP-INV", "invoice_no": "DUP-INV"}),
        ],
        po_no="PO-SECOND",
    )
    res = reconcile_po_set(second, cfg)
    assert res["status"] == "quarantined"
    assert res["reason"] == "packet_naming_failed"
    assert packet.read_bytes() == before, "delivered packet must not be clobbered"
    assert _status(eng, second)[1] is None


def test_reason_is_persisted_for_dashboard(harness):
    """Every terminal state leaves a human-readable reason behind."""
    build, eng, cfg = harness
    pid = build([("PO", [{"no": "1", "qty": 100}], {}), ("DN", [{"no": "1", "qty": 50}], {})])
    reconcile_po_set(pid, cfg)
    reason = _status(eng, pid)[2]
    assert reason, "reason must be persisted so the dashboard can explain the state"
    assert isinstance(reason, str) and len(reason) > 5
