"""END-TO-END verification of split-delivery handling, through the real engine.

This file OVERTURNED the hypothesis it was written to confirm. The matching
layer cannot distinguish an undelivered line from a short one, but
`reconcile_po_set` can — so the defect claimed in
`test_split_delivery_defect.py` does not reach the reviewer.

The mechanism is at reconciliation.py:445-455: if every quantity flag has
`vendor_quantity == 0`, the vendor reported nothing for those lines, so the set
is held at `pending` with reason `partial_fulfillment` ("Waiting on more
deliveries or invoices") rather than quarantined. A line reported by both
sides with different quantities IS a real disagreement and becomes
`mismatched`.

So the two situations are already separated, by a different signal than the
one proposed: not by delivery-note number, but by whether any vendor quantity
was reported at all.

These tests are therefore the CORRECTED record, and the no-regression bar for
any future work. They drive the real `reconcile_po_set` against real PDFs,
real rows and a real database. Only the VLM is out of scope.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
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
from app.services.reconciliation import reconcile_po_set

S = 1000  # quantities are stored scaled x1000


# ---------------------------------------------------------------------------
# Fixtures -- real files, real rows, real database
# ---------------------------------------------------------------------------


def _cfg(tmp_path, name: str):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / name)
    cfg.paths.output_folder = str(tmp_path / "output")
    cfg.paths.quarantine_folder = str(tmp_path / "quarantine")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    for p in (
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
    ):
        Path(p).mkdir(parents=True, exist_ok=True)
    return cfg


def _pdf(path: Path) -> None:
    w = PdfWriter()
    w.add_blank_page(width=100, height=100)
    w.write(str(path))


def _build_set(cfg, po_no, docs):
    """Create a PO Set with real documents.

    `docs` is a list of (doc_type, dn_no, [(line_no, qty), ...]) where the
    per-line `dn_no` values are attached to `line_items.dn_no` — exactly as
    `extract_document` would persist them from VLM output.
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

        for i, spec in enumerate(docs):
            dtype, doc_dn_no, lines = spec
            path = Path(cfg.paths.stored_documents_folder) / f"{po_no}_{i}_{dtype}.pdf"
            _pdf(path)
            d = Document(
                sha256_hash=f"{po_no}-{i}-{dtype}-{doc_dn_no}",
                original_filename=f"{i}_{dtype}_{doc_dn_no}.pdf",
                stored_path=str(path),
                doc_type=DocType[dtype],
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps_id,
                po_no_normalized=po_no,
                dn_no=doc_dn_no,
            )
            s.add(d)
            s.commit()
            for ln, qty in lines:
                s.add(
                    LineItem(
                        document_id=d.id,
                        line_item_no=ln,
                        description=f"item {ln}",
                        quantity=qty * S,
                        unit_price=100 * S,
                        # the per-line delivery-note reference, as the VLM
                        # would have extracted it from a printed column
                        dn_no=doc_dn_no,
                    )
                )
            s.commit()
        return ps_id


def _status_and_reason(cfg, ps_id):
    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, ps_id)
        return ps.status, (ps.reconcile_reason or "")


# ---------------------------------------------------------------------------
# E2E Test 1 -- the headline failure
# ---------------------------------------------------------------------------


def test_e2e_partly_delivered_po_is_held_pending_not_quarantined(tmp_path):
    """END-TO-END: a PO awaiting a second delivery note waits, correctly.

    A real PO for 100 units across two lines, split across two delivery notes.
    Only the first note has arrived. The set is held at `pending` with reason
    `partial_fulfillment` — "Waiting on more deliveries or invoices".

    This is CORRECT behaviour and the engine gets it right. An undelivered
    line is an outstanding delivery, not a supplier dispute, so it must not
    quarantine the set and must not be shown to the CA as a shortfall. The
    reviewer is told the set is waiting.

    The reason is the zero-quantity signal at reconciliation.py:445-455, not
    the delivery-note number, which nothing in this path reads.
    """
    cfg = _cfg(tmp_path, "split_e2e.db")
    ps_id = _build_set(
        cfg,
        "PO-SPLIT-1",
        [
            ("PO", "PO-SPLIT-1", [("1", 50), ("2", 50)]),
            ("DN", "GDN-A", [("1", 50)]),  # covers line 1 only
            # GDN-B, which would have covered line 2, has NOT arrived
            ("SI", "SI-SPLIT-1", [("1", 50), ("2", 50)]),
        ],
    )

    res = reconcile_po_set(ps_id, cfg)

    assert res["status"] == "pending", (
        f"an outstanding delivery must WAIT, not quarantine. Got {res}"
    )
    assert res["reason"] == "partial_fulfillment", res

    status, reason = _status_and_reason(cfg, ps_id)
    assert status == POSetStatus.pending
    assert "waiting" in reason.lower(), reason

    flags = res.get("flags", [])
    line2 = [f for f in flags if str(f.get("line_item_no")) == "2"]
    assert line2, f"line 2 should still be listed as outstanding: {flags}"
    assert line2[0]["vendor_quantity"] == 0, line2[0]

    # The reviewer is told the set is waiting, not that the vendor under-
    # delivered. This wording is the whole point of the branch.
    assert "mismatch" not in reason.lower(), (
        f"an outstanding delivery must not be reported as a disagreement: {reason}"
    )


# ---------------------------------------------------------------------------
# E2E Test 2 -- the collision, proven end to end
# ---------------------------------------------------------------------------


def test_e2e_not_yet_delivered_and_genuinely_short_are_distinguished(tmp_path):
    """END-TO-END: the two situations ARE separated, and correctly.

    Case A: the covering delivery note has NOT been sent  -> `pending`,
            reason `partial_fulfillment` (an outstanding delivery).
    Case B: it WAS sent and arrived short                 -> `mismatched`
            (a genuine disagreement the reviewer must act on).

    This is the corrected result. The matching layer cannot tell them apart
    (see test_split_delivery_defect.py), but the reconciliation layer can, by
    checking whether any vendor quantity was reported at all. The distinction
    a reviewer sees is therefore sound today.

    It is coarse, though: it separates the two cases without naming WHICH
    delivery note is outstanding. That is the only genuine gap the
    delivery-note number would close.
    """
    # A -- GDN-B (covering line 2) has not arrived.
    cfg_a = _cfg(tmp_path, "case_a.db")
    id_a = _build_set(
        cfg_a,
        "PO-CASE-A",
        [
            ("PO", "PO-CASE-A", [("1", 50), ("2", 50)]),
            ("DN", "GDN-A", [("1", 50)]),
            ("SI", "SI-A", [("1", 50), ("2", 50)]),
        ],
    )
    res_a = reconcile_po_set(id_a, cfg_a)

    # B -- GDN-B arrived, but short by 20.
    cfg_b = _cfg(tmp_path, "case_b.db")
    id_b = _build_set(
        cfg_b,
        "PO-CASE-B",
        [
            ("PO", "PO-CASE-B", [("1", 50), ("2", 50)]),
            ("DN", "GDN-A", [("1", 50)]),
            ("DN", "GDN-B", [("2", 30)]),  # arrived, short
            ("SI", "SI-B", [("1", 50), ("2", 50)]),
        ],
    )
    res_b = reconcile_po_set(id_b, cfg_b)

    assert res_a["status"] == "pending", res_a
    assert res_a["reason"] == "partial_fulfillment", res_a
    assert res_b["status"] == "mismatched", res_b

    assert res_a["status"] != res_b["status"], (
        "these must differ; if they ever converge, a partial delivery is being "
        "presented as a supplier dispute"
    )

    # The remaining, real gap: the outstanding note is never named.
    assert "gdn-b" not in repr(res_a).lower(), (
        f"GDN-B is named in the waiting result, so the gap is already closed: {res_a}"
    )


# ---------------------------------------------------------------------------
# E2E Test 3 -- property: complete split delivery still merges (no-regression bar)
# ---------------------------------------------------------------------------


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    n_dns=st.integers(min_value=2, max_value=3),
    per_dn=st.integers(min_value=1, max_value=3),
    qty=st.integers(min_value=1, max_value=90),
)
def test_e2e_complete_split_delivery_always_merges(tmp_path_factory, n_dns, per_dn, qty):
    """CONTROL: when every delivery note arrives, the set must merge.

    Any fix that partitions the DN pool by delivery-note number must leave
    this intact. Breaking it means quarantining a fully-delivered, fully-
    invoiced set — a worse failure than the defect being fixed, because it
    blocks correct work.
    """
    tmp_path = tmp_path_factory.mktemp("complete")
    cfg = _cfg(tmp_path, f"complete_{n_dns}_{per_dn}_{qty}.db")

    docs = []
    # PO covers every line of every note.
    total_lines = n_dns * per_dn
    po_lines = [(str(i + 1), qty) for i in range(total_lines)]
    docs.append(("PO", "PO-COMPLETE", po_lines))

    # Each DN arrives complete, covering its own slice of PO lines.
    for d in range(n_dns):
        start = d * per_dn
        dn_lines = [(str(i + 1), qty) for i in range(start, start + per_dn)]
        docs.append(("DN", f"GDN-{d}", dn_lines))

    # SI covers the whole PO.
    docs.append(("SI", "SI-COMPLETE", po_lines))

    ps_id = _build_set(cfg, "PO-COMPLETE", docs)
    res = reconcile_po_set(cfg=cfg, po_set_id=ps_id)

    assert res["status"] == "merged", (
        f"a fully-delivered split PO must merge. Got {res['status']}: "
        f"{res.get('reason')} flags={res.get('flags')}"
    )


# ---------------------------------------------------------------------------
# E2E Test 4 -- property: dropping any one note breaks it, always
# ---------------------------------------------------------------------------


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    n_dns=st.integers(min_value=2, max_value=3),
    per_dn=st.integers(min_value=1, max_value=2),
    qty=st.integers(min_value=5, max_value=60),
    drop=st.integers(min_value=0, max_value=1),  # index of the note withheld
)
def test_e2e_withholding_any_note_always_blocks(tmp_path_factory, n_dns, per_dn, qty, drop):
    """PROPERTY: withholding any single delivery note blocks the set.

    Which note is missing makes no difference to the outcome today, and
    nothing in the result identifies the note. After a fix, the result should
    name the outstanding delivery note rather than presenting the shortfall
    as a supply problem.
    """
    tmp_path = tmp_path_factory.mktemp("withheld")
    cfg = _cfg(tmp_path, f"withheld_{n_dns}_{per_dn}_{qty}_{drop}.db")

    total_lines = n_dns * per_dn
    po_lines = [(str(i + 1), qty) for i in range(total_lines)]
    docs = [("PO", "PO-WITHHELD", po_lines)]

    missing_nos = []
    for d in range(n_dns):
        start = d * per_dn
        dn_lines = [(str(i + 1), qty) for i in range(start, start + per_dn)]
        if d == drop:
            missing_nos.append(f"GDN-{d}")
            continue
        docs.append(("DN", f"GDN-{d}", dn_lines))

    docs.append(("SI", "SI-WITHHELD", po_lines))

    ps_id = _build_set(cfg, "PO-WITHHELD", docs)
    res = reconcile_po_set(ps_id, cfg)

    # A withheld note leaves those lines wholly unreported, so the set waits
    # rather than quarantining. It must never merge and never be called a
    # mismatch.
    assert res["status"] == "pending", (
        f"withholding {missing_nos} must leave the set waiting, got {res['status']}"
    )
    assert res["reason"] == "partial_fulfillment", res
    assert res["status"] != "merged", "an incomplete delivery must never merge"

    # THE REMAINING GAP: the set is correctly held, but the outstanding
    # delivery note is never named. The reviewer knows to wait, not what for.
    blob = repr(res).lower()
    for no in missing_nos:
        assert no.lower() not in blob, (
            f"the outstanding delivery note {no} IS named in the result, so the "
            f"delivery-note-number work is already done and this test should be "
            f"inverted.\n{res}"
        )
