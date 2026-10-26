"""Opus core review (2026-10-24): each scenario the reviewers proved, now guarded."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_robustness_harness import make_cfg, write_pdf

from app.core.database import get_engine
from app.models import (
    AuditAction,
    AuditLog,
    DocType,
    Document,
    ExtractionStatus,
    LineItem,
    POSet,
    POSetStatus,
)
from app.models.base import Base
from app.services.grouping import PLACED_BY_NUMBER, resolve_unattached_documents
from app.services.matching import LINE_MAPPING_RULES, resolve_line_mapping
from app.services.reconciliation import reconcile_po_set


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    c = make_cfg(tmp_path, monkeypatch)
    Base.metadata.create_all(get_engine(c))
    return c


_n = [0]


def _doc(cfg, s, kind, ps_id, lines, *, dn_no=None, inv_no=None, po="4500012345", name=None):
    _n[0] += 1
    name = name or f"{kind.value}-{_n[0]}.pdf"
    stored = write_pdf(Path(cfg.paths.stored_documents_folder) / f"{_n[0]}_{name}", 200 + _n[0])
    d = Document(
        sha256_hash=f"{_n[0]:064d}",
        original_filename=name,
        stored_path=str(stored),
        doc_type=kind,
        extraction_status=ExtractionStatus.valid,
        po_set_id=ps_id,
        po_no_raw=po,
        po_no_normalized=po,
        dn_no=dn_no,
        si_no=inv_no,
        invoice_no=inv_no,
    )
    s.add(d)
    s.flush()
    for no, qty in lines:
        s.add(LineItem(document_id=d.id, line_item_no=no, description=f"item {no}", quantity=qty))
    return d


def _set(cfg, build):
    with Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized="4500012345", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        build(s, ps.id)
        s.commit()
        return ps.id


def _status(cfg, ps_id):
    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, ps_id)
        return ps.status, ps.reconcile_reason


# ------------------------------------------------------------------ CRITICAL: foreign DN
def _partial_set_with_a_foreign_dn(cfg):
    def build(s, ps_id):
        _doc(cfg, s, DocType.PO, ps_id, [("1", 10000), ("2", 4000)])
        _doc(cfg, s, DocType.DN, ps_id, [("1", 10000)], dn_no="1001")
        _doc(cfg, s, DocType.SI, ps_id, [("1", 10000), ("2", 4000)], inv_no="INV-1")
        # Another vendor's DN, also numbered 1001, printing no PO number at all.
        _doc(cfg, s, DocType.DN, None, [("2", 4000)], dn_no="1001", po=None, name="foreign-dn.pdf")

    return _set(cfg, build)


def test_a_dn_placed_by_its_number_alone_holds_the_set_instead_of_merging(cfg):
    ps_id = _partial_set_with_a_foreign_dn(cfg)
    assert reconcile_po_set(ps_id, cfg)["status"] == "pending"  # partial delivery before
    assert resolve_unattached_documents(cfg) == {ps_id}
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "pending" and res["reason"] == "placed_by_number_only"
    status, reason = _status(cfg, ps_id)
    assert status == POSetStatus.pending
    assert "foreign-dn.pdf" in reason and "Confirm" in reason
    with Session(get_engine(cfg)) as s:
        foreign = s.query(Document).filter_by(original_filename="foreign-dn.pdf").one()
        assert foreign.po_no_source == PLACED_BY_NUMBER


def test_the_operator_confirms_the_placement_and_it_is_audited(cfg, monkeypatch):
    ps_id = _partial_set_with_a_foreign_dn(cfg)
    resolve_unattached_documents(cfg)
    reconcile_po_set(ps_id, cfg)
    cfg.schedule.poll_enabled = False  # the app lifespan must not start a poller thread
    for target in ("app.api.routes.po_sets.load_config", "app.main.load_config"):
        monkeypatch.setattr(target, lambda path=None: cfg)
    from app.main import app

    with Session(get_engine(cfg)) as s:
        doc_id = s.query(Document).filter_by(original_filename="foreign-dn.pdf").one().id
    with TestClient(app) as client:
        wrong = client.post(f"/po_sets/{ps_id}/documents/999999/confirm_placement")
        assert wrong.status_code == 404
        ok = client.post(f"/po_sets/{ps_id}/documents/{doc_id}/confirm_placement")
        assert ok.status_code == 200, ok.text
        again = client.post(f"/po_sets/{ps_id}/documents/{doc_id}/confirm_placement")
        assert again.status_code == 409  # nothing left to confirm
    # Confirmed: the hold is gone and the normal gates decide (here the sums agree).
    assert ok.json()["reason"] != "placed_by_number_only"
    with Session(get_engine(cfg)) as s:
        assert s.get(Document, doc_id).po_no_source == "manual"
        rows = s.query(AuditLog).filter(AuditLog.action == AuditAction.manual_status_change).all()
        assert any(json.loads(r.detail).get("confirmed_placement") for r in rows)


# ------------------------------------------------------------------ HIGH/MEDIUM: duplicate guard
def _dups(cfg, dn_specs):
    def build(s, ps_id):
        _doc(cfg, s, DocType.PO, ps_id, [("1", 10000)])
        for dn_no, lines in dn_specs:
            _doc(cfg, s, DocType.DN, ps_id, lines, dn_no=dn_no)
        _doc(cfg, s, DocType.SI, ps_id, [("1", 10000)], inv_no="INV-1")

    ps_id = _set(cfg, build)
    return ps_id, reconcile_po_set(ps_id, cfg)


def test_a_rescan_whose_line_number_reads_01_is_still_a_duplicate(cfg):
    _ps, res = _dups(cfg, [("DN-77", [("1", 5000)]), ("DN-77", [("01", 5000)])])
    assert res["status"] == "quarantined" and res["reason"] == "duplicate_document_number"


def test_two_copies_of_a_dn_with_no_readable_number_are_a_duplicate(cfg):
    _ps, res = _dups(cfg, [(None, [("1", 5000)]), (None, [("1", 5000)])])
    assert res["status"] == "quarantined" and res["reason"] == "duplicate_document_number"
    assert "(no number)" in res["detail"]


def test_a_numbered_dn_and_an_unreadable_copy_of_it_are_a_duplicate(cfg):
    """Opus review B2: DN-77 scanned twice, one scan's number unreadable, must not both sum."""
    _ps, res = _dups(cfg, [("DN-77", [("1", 5000)]), (None, [("1", 5000)])])
    assert res["status"] == "quarantined" and res["reason"] == "duplicate_document_number"


def test_dn_numbers_that_differ_only_by_o_and_zero_are_the_same_document(cfg):
    _ps, res = _dups(cfg, [("GDN-RHO-25-513", [("1", 5000)]), ("GDN-RH0-25-513", [("1", 5000)])])
    assert res["status"] == "quarantined" and res["reason"] == "duplicate_document_number"


def test_dn_numbers_that_differ_only_by_leading_zeros_are_the_same_document(cfg):
    _ps, res = _dups(cfg, [("DN-0077", [("1", 5000)]), ("DN-077", [("1", 5000)])])
    assert res["status"] == "quarantined" and res["reason"] == "duplicate_document_number"


def test_pages_of_one_dn_with_different_lines_still_sum(cfg):
    """Not over-strict: number-less pages that carry different lines are one delivery."""

    def build(s, ps_id):
        _doc(cfg, s, DocType.PO, ps_id, [("1", 5000), ("2", 5000)])
        _doc(cfg, s, DocType.DN, ps_id, [("1", 5000)], dn_no=None)
        _doc(cfg, s, DocType.DN, ps_id, [("2", 5000)], dn_no=None)
        _doc(cfg, s, DocType.SI, ps_id, [("1", 5000), ("2", 5000)], inv_no="INV-1")

    ps_id = _set(cfg, build)
    assert reconcile_po_set(ps_id, cfg)["status"] == "merged"


# ------------------------------------------------------------------ LOW: x10 and a key that equals a PO key
def test_known_limitation_x10_rekeys_a_vendor_key_that_equals_a_po_key():
    """PO 10,20,100 vs DN 1,2,10: the rule applies to EVERY key of the pool, so DN 10 pairs
    with PO 100 although a PO line 10 exists. That is the intended all-or-nothing reading of
    a DN numbered 1..10 against a PO numbered 10..100 (pinned by test_line_mapping.py);
    quantities must still agree line by line, so it cannot hide a quantity difference."""
    po = [{"line_item_no": n, "document_id": 1} for n in ("10", "20", "100")]
    dn = [{"line_item_no": n, "document_id": 2} for n in ("1", "2", "10")]
    mapping = resolve_line_mapping(po, dn, [], frozenset(LINE_MAPPING_RULES))
    assert mapping.dn.rule == "x10" and mapping.dn.map["10"] == "100"


# ------------------------------------------------------------------ pinned limitation (D18)
def test_known_limitation_one_digit_po_misread_is_not_detected(cfg):
    """A page whose PO number was misread by one digit makes a phantom PO-only set; the
    complete neighbour set still merges. Detecting it was designed (docs/SPEC_WS2_MISREAD_HOLD.md)
    and skipped by the owner (D18): the manual merger and a supervised first week cover it."""

    def build(s, ps_id):
        _doc(cfg, s, DocType.PO, ps_id, [("1", 10000)], po="4500012345")
        _doc(cfg, s, DocType.DN, ps_id, [("1", 10000)], dn_no="DN-1", po="4500012345")
        _doc(cfg, s, DocType.SI, ps_id, [("1", 10000)], inv_no="INV-1", po="4500012345")

    good = _set(cfg, build)
    with Session(get_engine(cfg)) as s:  # the phantom: one digit away, PO document only
        phantom = POSet(po_no_normalized="4500012845", status=POSetStatus.pending)
        s.add(phantom)
        s.commit()
        _doc(cfg, s, DocType.PO, phantom.id, [], po="4500012845")
        s.commit()
        phantom_id = phantom.id
    assert reconcile_po_set(phantom_id, cfg)["status"] == "pending"
    assert reconcile_po_set(good, cfg)["status"] == "merged", "nothing holds the neighbour"
