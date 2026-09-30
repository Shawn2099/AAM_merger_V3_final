"""Temporary verification of the user's stated product rules (run, then review)."""

from pathlib import Path

from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
from app.models.base import Base
from app.services.reconciliation import reconcile_po_set


def _cfg(tmp_path, name="verify.db"):
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


def _pdf(path, width=100):
    w = PdfWriter()
    w.add_blank_page(width=width, height=100)
    w.write(str(path))


def _build(tmp_path, cfg, po_no, docs, po_no_normalized=None):
    """docs = [(doc_type, [ (line_no, qty), ... ], {extra})]"""
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no_normalized or po_no, status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id
        for i, (dtype, lines, extra) in enumerate(docs):
            p = Path(cfg.paths.stored_documents_folder) / f"{po_no}_{i}_{dtype}.pdf"
            _pdf(p)
            d = Document(
                sha256_hash=f"{po_no}-{i}-{dtype}",
                original_filename=f"{i}_{dtype}.pdf",
                stored_path=str(p),
                doc_type=DocType[dtype],
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps_id,
                po_no_normalized=po_no_normalized or po_no,
                **extra,
            )
            s.add(d)
            s.commit()
            for ln, qty in lines:
                s.add(
                    LineItem(
                        document_id=d.id,
                        line_item_no=ln,
                        description=f"item {ln}",
                        quantity=qty * 1000,
                        unit_price=1000,
                    )
                )
            s.commit()
        return ps_id


def test_multi_doc_sum_merges_named_invoice(tmp_path):
    """PO vs 2 DNs and 2 SIs: sums must match per line and output = <invoice_no>.pdf."""
    cfg = _cfg(tmp_path, "multi.db")
    ps_id = _build(
        tmp_path,
        cfg,
        "PO-A",
        [
            ("PO", [("1", 100), ("2", 40)], {}),
            ("DN", [("1", 60)], {}),
            ("DN", [("1", 40), ("2", 40)], {}),
            ("SI", [("1", 100)], {}),
            ("SI", [("2", 40)], {}),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "merged", res
    out = Path(cfg.paths.output_folder)
    files = [f.name for f in out.glob("*.pdf")]
    assert files == ["PO-A.pdf"], files


def test_comparisons_are_scoped_to_one_po_set(tmp_path):
    """A second set's lines never leak into the first set's verdict."""
    cfg = _cfg(tmp_path, "scoped.db")
    ok = _build(
        tmp_path,
        cfg,
        "PO-OK",
        [
            ("PO", [("1", 100)], {}),
            ("DN", [("1", 100)], {}),
            ("SI", [("1", 100)], {}),
        ],
    )
    bad = _build(
        tmp_path,
        cfg,
        "PO-BAD",
        [
            ("PO", [("1", 100)], {}),
            ("DN", [("1", 70)], {}),
            ("SI", [("1", 100)], {}),
        ],
    )
    r1 = reconcile_po_set(ok, cfg)
    r2 = reconcile_po_set(bad, cfg)
    assert r1["status"] == "merged", r1
    assert r2["status"] == "mismatched", r2
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, ok).status == POSetStatus.merged
        assert s.get(POSet, bad).status == POSetStatus.mismatched


def test_mismatch_never_merges_and_carries_reason(tmp_path):
    cfg = _cfg(tmp_path, "mis.db")
    ps_id = _build(
        tmp_path,
        cfg,
        "PO-MIS",
        [
            ("PO", [("1", 100)], {}),
            ("DN", [("1", 90)], {}),
            ("SI", [("1", 100)], {}),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "mismatched", res
    assert not list(Path(cfg.paths.output_folder).glob("*.pdf"))
    with Session(get_engine(cfg)) as s:
        reason = s.get(POSet, ps_id).reconcile_reason
    assert reason and "90 of 100" in reason, reason


def test_orphan_vendor_line_quarantines_with_reason(tmp_path):
    cfg = _cfg(tmp_path, "orph.db")
    ps_id = _build(
        tmp_path,
        cfg,
        "PO-ORPH",
        [
            ("PO", [("1", 100)], {}),
            ("DN", [("1", 100), ("2", 5)], {}),
            ("SI", [("1", 100)], {}),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "quarantined", res
    assert res["reason"] == "unmatched_vendor_line", res
    with Session(get_engine(cfg)) as s:
        reason = s.get(POSet, ps_id).reconcile_reason
    assert reason and "could not be matched" in reason, reason


def test_po_line_without_line_number_quarantines(tmp_path):
    cfg = _cfg(tmp_path, "noline.db")
    ps_id = _build(
        tmp_path,
        cfg,
        "PO-NOLINE",
        [
            ("PO", [(None, 100)], {}),
            ("DN", [("1", 100)], {}),
            ("SI", [("1", 100)], {}),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "quarantined", res
    assert res["reason"] == "po_line_missing_line_item_no", res


def test_po_with_si_but_no_dn_does_not_merge(tmp_path):
    cfg = _cfg(tmp_path, "nodn.db")
    ps_id = _build(
        tmp_path,
        cfg,
        "PO-NODN",
        [
            ("PO", [("1", 100)], {}),
            ("SI", [("1", 100)], {}),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] != "merged", res
    assert not list(Path(cfg.paths.output_folder).glob("*.pdf"))


def test_customs_toggle_blocks_then_manual_upload_and_merge(tmp_path, monkeypatch):
    """Toggle on -> waits after 3-way match; 2 manual uploads (no AI) clear it;
    the Merge button produces <invoice_no>.pdf."""
    from fastapi.testclient import TestClient

    cfg = _cfg(tmp_path, "customs.db")
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: cfg)
    monkeypatch.setattr("app.api.routes.dashboard.load_config", lambda path=None: cfg)

    ps_id = _build(
        tmp_path,
        cfg,
        "PO-CUS",
        [
            ("PO", [("1", 100)], {}),
            ("DN", [("1", 100)], {}),
            ("SI", [("1", 100)], {"si_no": "INV-777", "invoice_no": "INV-777"}),
        ],
    )

    # toggle ON via the real route
    from app.main import app

    client = TestClient(app)
    r = client.post(f"/po_sets/{ps_id}/toggle_customs")
    assert r.status_code == 200, r.text
    assert r.json()["has_customs_toggle"] is True

    # still all-matched, but must wait
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "blocked_customs", res
    assert not list(Path(cfg.paths.output_folder).glob("*.pdf"))

    # manual upload of the two required docs (no VLM call involved)
    for i, dtype in enumerate(("CUSTOMS", "SHIPPING")):
        p = tmp_path / f"{dtype}.pdf"
        _pdf(p, width=200 + i)
        with p.open("rb") as fh:
            up = client.post(
                f"/po_sets/{ps_id}/upload",
                files={"file": (f"{dtype}.pdf", fh, "application/pdf")},
                data={"doc_type": dtype},
            )
        assert up.status_code in (200, 302), up.text

    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, ps_id)
        types = {d.doc_type.value for d in ps.documents}
        assert {"CUSTOMS", "SHIPPING"} <= types
        # manual docs were never sent to a VLM
        assert all(d.extraction_status == ExtractionStatus.valid for d in ps.documents)

    # operator presses Merge
    r2 = client.post(f"/po_sets/{ps_id}/merge")
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "merged", r2.json()
    files = [f.name for f in Path(cfg.paths.output_folder).glob("*.pdf")]
    assert files == ["INV-777.pdf"], files


def test_no_invoice_number_falls_back_to_po_name(tmp_path):
    """Deviation probe: without an invoice number the packet is named from the
    PO number (reported, not quarantined)."""
    cfg = _cfg(tmp_path, "noinv.db")
    ps_id = _build(
        tmp_path,
        cfg,
        "PO-NOINV",
        [
            ("PO", [("1", 100)], {}),
            ("DN", [("1", 100)], {}),
            ("SI", [("1", 100)], {}),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "merged", res
    files = [f.name for f in Path(cfg.paths.output_folder).glob("*.pdf")]
    assert files == ["PO-NOINV.pdf"], files
    with Session(get_engine(cfg)) as s:
        reason = s.get(POSet, ps_id).reconcile_reason
    assert reason and "No invoice number" in reason, reason


def test_two_pos_in_one_set_quarantine(tmp_path):
    cfg = _cfg(tmp_path, "twopo.db")
    ps_id = _build(
        tmp_path,
        cfg,
        "PO-TWO",
        [
            ("PO", [("1", 100)], {}),
            ("PO", [("1", 100)], {}),
            ("DN", [("1", 200)], {}),
            ("SI", [("1", 200)], {}),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "quarantined", res
    assert res["reason"] == "multiple_po_documents", res


def test_duplicate_hash_manual_upload_is_reported_not_silent(tmp_path, monkeypatch):
    """Uploading bytes already owned by another PO Set is now reported.

    This started as a probe that documented the bug: identical bytes offered
    to a second set fell through the dedup branch untouched, so the set got
    nothing, the customs gate could never clear, and the operator saw a plain
    302 as though the upload had worked. It is now a 409 naming the owning
    PO Set, and the document is not silently attached anywhere new.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    cfg = _cfg(tmp_path, "dup.db")
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: cfg)
    monkeypatch.setattr("app.api.routes.dashboard.load_config", lambda path=None: cfg)

    a = _build(
        tmp_path,
        cfg,
        "PO-DUPA",
        [
            ("PO", [("1", 100)], {}),
            ("DN", [("1", 100)], {}),
            ("SI", [("1", 100)], {"si_no": "INV-A", "invoice_no": "INV-A"}),
        ],
    )
    b = _build(
        tmp_path,
        cfg,
        "PO-DUPB",
        [
            ("PO", [("1", 100)], {}),
            ("DN", [("1", 100)], {}),
            ("SI", [("1", 100)], {"si_no": "INV-B", "invoice_no": "INV-B"}),
        ],
    )

    shared = tmp_path / "shared_customs.pdf"
    _pdf(shared, width=333)
    client = TestClient(app)

    with shared.open("rb") as fh:
        first = client.post(
            f"/po_sets/{a}/upload",
            files={"file": ("shared.pdf", fh, "application/pdf")},
            data={"doc_type": "CUSTOMS"},
        )
    assert first.status_code in (200, 302), first.text

    with shared.open("rb") as fh:
        second = client.post(
            f"/po_sets/{b}/upload",
            files={"file": ("shared.pdf", fh, "application/pdf")},
            data={"doc_type": "CUSTOMS"},
        )
    assert second.status_code == 409, f"expected a visible conflict, got {second.status_code}"
    assert "PO-DUPA" in second.text, "the conflict must name the PO Set that already holds it"

    with Session(get_engine(cfg)) as s:
        types_a = {d.doc_type.value for d in s.get(POSet, a).documents}
        types_b = {d.doc_type.value for d in s.get(POSet, b).documents}
    assert "CUSTOMS" in types_a
    assert "CUSTOMS" not in types_b, types_b


def test_two_si_docs_different_invoice_numbers(tmp_path):
    """Probe: with two SIs carrying different invoice numbers, which name wins."""
    cfg = _cfg(tmp_path, "twosi.db")
    ps_id = _build(
        tmp_path,
        cfg,
        "PO-2SI",
        [
            ("PO", [("1", 100)], {}),
            ("DN", [("1", 100)], {}),
            ("SI", [("1", 60)], {"si_no": "INV-FIRST", "invoice_no": "INV-FIRST"}),
            ("SI", [("1", 40)], {"si_no": "INV-SECOND", "invoice_no": "INV-SECOND"}),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "merged", res
    files = sorted(f.name for f in Path(cfg.paths.output_folder).glob("*.pdf"))
    assert len(files) == 1, files
    print("MULTI-SI NAME:", files)


def test_missing_dn_document_stays_pending_with_reason(tmp_path):
    """DN gate: PO + SI but no DN row waits visibly instead of merging."""
    cfg = _cfg(tmp_path, "missdn.db")
    ps_id = _build(
        tmp_path,
        cfg,
        "PO-MISSDN",
        [
            ("PO", [("1", 100)], {}),
            ("SI", [("1", 100)], {"si_no": "INV-MD", "invoice_no": "INV-MD"}),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "pending", res
    assert res["reason"] == "missing_dn_document", res
    assert not list(Path(cfg.paths.output_folder).glob("*.pdf"))
    with Session(get_engine(cfg)) as s:
        reason = s.get(POSet, ps_id).reconcile_reason
    assert reason and "delivery note" in reason.lower(), reason


def test_missing_si_document_stays_pending_with_reason(tmp_path):
    """DN gate: PO + DN but no SI row waits visibly instead of merging."""
    cfg = _cfg(tmp_path, "misssi.db")
    ps_id = _build(
        tmp_path,
        cfg,
        "PO-MISSSI",
        [
            ("PO", [("1", 100)], {}),
            ("DN", [("1", 100)], {}),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "pending", res
    assert res["reason"] == "missing_si_document", res
    assert not list(Path(cfg.paths.output_folder).glob("*.pdf"))
    with Session(get_engine(cfg)) as s:
        reason = s.get(POSet, ps_id).reconcile_reason
    assert reason and "invoice" in reason.lower(), reason


def test_dn_doc_with_no_lines_is_partial_not_mismatch(tmp_path):
    """A DN row that delivered nothing is outstanding delivery, not disagreement."""
    cfg = _cfg(tmp_path, "dnempty.db")
    ps_id = _build(
        tmp_path,
        cfg,
        "PO-DNEMPTY",
        [
            ("PO", [("1", 100)], {}),
            ("DN", [], {}),
            ("SI", [("1", 100)], {"si_no": "INV-E", "invoice_no": "INV-E"}),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "pending", res
    assert res["reason"] == "partial_fulfillment", res
    dn_flags = [f for f in res["flags"] if f.get("pool") == "DN"]
    assert dn_flags, res
    assert all((f.get("vendor_quantity") or 0) == 0 for f in dn_flags), res
    assert not list(Path(cfg.paths.output_folder).glob("*.pdf"))


def test_dn_over_delivery_mismatches(tmp_path):
    """Delivered more than ordered is disagreement, not fulfillment."""
    cfg = _cfg(tmp_path, "dnover.db")
    ps_id = _build(
        tmp_path,
        cfg,
        "PO-DNOVER",
        [
            ("PO", [("1", 100)], {}),
            ("DN", [("1", 120)], {}),
            ("SI", [("1", 100)], {"si_no": "INV-O", "invoice_no": "INV-O"}),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "mismatched", res
    assert not list(Path(cfg.paths.output_folder).glob("*.pdf"))
