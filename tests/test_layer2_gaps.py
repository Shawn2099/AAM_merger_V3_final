"""Layer-2 branch gaps — every meaningful uncovered line per coverage.

Found by measuring coverage over the full runnable suite and reading each
miss: ambiguous-attach refusal, prefix success/merged guards, reconcile
defensive paths, merge naming/ordering/refusal branches, quarantine folder
and report branches, manual-merge validation, customs branches, locking
direct, and po_sets route error paths. Pure or tmp-DB only: no VLM, no
Prefect server.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
from app.models.base import Base


def _cfg(tmp_path, name="gaps.db"):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / name)
    cfg.paths.input_folder = str(tmp_path / "input")
    cfg.paths.output_folder = str(tmp_path / "output")
    cfg.paths.quarantine_folder = str(tmp_path / "quarantine")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    cfg.paths.log_folder = str(tmp_path / "logs")
    for p in (
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.log_folder,
    ):
        Path(p).mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    return cfg, eng


def _tiny_pdf(path: Path, width: float = 100):
    from pypdf import PdfWriter

    path.parent.mkdir(parents=True, exist_ok=True)
    w = PdfWriter()
    w.add_blank_page(width=width, height=200)
    with open(path, "wb") as f:
        w.write(f)
    return path


def _doc(s, cfg, ps_id, dtype, name, lines=(), **extra):
    p = _tiny_pdf(Path(cfg.paths.stored_documents_folder) / name)
    d = Document(
        sha256_hash=f"gap_{name}",
        original_filename=name,
        stored_path=str(p),
        doc_type=DocType(dtype),
        extraction_status=ExtractionStatus.valid,
        po_set_id=ps_id,
        **extra,
    )
    s.add(d)
    s.commit()
    from app.models import LineItem

    for ln in lines:
        s.add(
            LineItem(
                document_id=d.id,
                line_item_no=ln.get("no"),
                description=ln.get("desc", "Widget"),
                quantity=ln["qty"],
                unit_price=ln.get("price", 1000),
            )
        )
    s.commit()
    s.refresh(d)
    return d


def _po_set(s, po_no="PO-GAP", status=POSetStatus.pending, **kw):
    ps = POSet(po_no_normalized=po_no, status=status, **kw)
    s.add(ps)
    s.commit()
    s.refresh(ps)
    return ps


@pytest.fixture()
def client(tmp_path, monkeypatch):
    cfg, _ = _cfg(tmp_path, "routes.db")
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: cfg)
    from app.main import app

    with TestClient(app) as c:
        yield c, cfg


# ---------------------------------------------------------------- grouping


def test_ambiguous_doc_never_attaches(tmp_path):
    """attach_unattached_to_open_sets skips po_reference_ambiguous docs even
    when an open set holds their key — guessing strands the other POs."""
    from app.services.grouping import attach_unattached_to_open_sets, get_or_create_po_set

    cfg, eng = _cfg(tmp_path, "amb.db")
    anchor = get_or_create_po_set("PO-AMB", cfg)
    with Session(eng) as s:
        d = Document(
            sha256_hash="h_amb",
            original_filename="amb.pdf",
            stored_path="x.pdf",
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_no_normalized="POAMB",
            po_no_raw="PO-AMB",
            po_reference_ambiguous=True,
        )
        s.add(d)
        s.commit()
        # key matches the anchor once normalised
        d.po_no_normalized = anchor.po_no_normalized
        s.commit()
    touched = attach_unattached_to_open_sets(cfg)
    assert touched == set()
    with Session(eng) as s:
        assert s.query(Document).filter_by(sha256_hash="h_amb").one().po_set_id is None


def test_prefix_match_attaches_and_inherits_po_no(tmp_path):
    """Sibling-prefix success path: no dn_no anywhere, one set shares the
    4-part prefix → attaches and inherits the set's po_no."""
    from app.services.grouping import resolve_unattached_documents

    cfg, eng = _cfg(tmp_path, "prefix_ok.db")
    with Session(eng) as s:
        ps = _po_set(s, "PO-PREF")
        s.add(
            Document(
                sha256_hash="h_sib",
                original_filename="SIV-DTS-25-477-A.pdf",
                stored_path="x.pdf",
                doc_type=DocType.SI,
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps.id,
                po_no_normalized="PO-PREF",
            )
        )
        s.add(
            Document(
                sha256_hash="h_orph",
                original_filename="SIV-DTS-25-477-B.pdf",
                stored_path="y.pdf",
                doc_type=DocType.DN,
                extraction_status=ExtractionStatus.valid,
                po_set_id=None,
                po_no_normalized=None,
            )
        )
        s.commit()
        ps_id = ps.id
    assert resolve_unattached_documents(cfg) == {ps_id}
    with Session(eng) as s:
        o = s.query(Document).filter_by(sha256_hash="h_orph").one()
        assert o.po_set_id == ps_id
        assert o.po_no_normalized == "PO-PREF"


def test_prefix_match_to_merged_set_refuses(tmp_path):
    """A matching sibling in a MERGED set must not adopt the orphan —
    merged sets are closed."""
    from app.services.grouping import resolve_unattached_documents

    cfg, eng = _cfg(tmp_path, "prefix_merged.db")
    with Session(eng) as s:
        ps = _po_set(s, "PO-PM", status=POSetStatus.merged)
        s.add(
            Document(
                sha256_hash="h_sibm",
                original_filename="SIV-DTS-25-477-A.pdf",
                stored_path="x.pdf",
                doc_type=DocType.SI,
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps.id,
                po_no_normalized="PO-PM",
            )
        )
        s.add(
            Document(
                sha256_hash="h_orphm",
                original_filename="SIV-DTS-25-477-B.pdf",
                stored_path="y.pdf",
                doc_type=DocType.DN,
                extraction_status=ExtractionStatus.valid,
                po_set_id=None,
                po_no_normalized=None,
            )
        )
        s.commit()
    assert resolve_unattached_documents(cfg) == set()
    with Session(eng) as s:
        assert s.query(Document).filter_by(sha256_hash="h_orphm").one().po_set_id is None


# ---------------------------------------------------------- reconciliation


def test_reconcile_missing_set_raises(tmp_path):
    from app.services.reconciliation import reconcile_po_set

    cfg, _ = _cfg(tmp_path, "noset.db")
    with pytest.raises(ValueError, match="not found"):
        reconcile_po_set(99999, cfg)


def test_persist_failure_never_fails_reconcile(tmp_path):
    """A dead note-persistence layer must not fail the verdict itself."""
    from app.services import reconciliation as rec

    cfg, eng = _cfg(tmp_path, "persist.db")
    with Session(eng) as s:
        ps = _po_set(s, "PO-PN")
        _doc(s, cfg, ps.id, "PO", "po.pdf", [{"no": "1", "qty": 100}])
        _doc(s, cfg, ps.id, "DN", "dn.pdf", [{"no": "1", "qty": 100}])
        _doc(
            s,
            cfg,
            ps.id,
            "SI",
            "si.pdf",
            [{"no": "1", "qty": 100}],
            si_no="I1",
            invoice_no="I1",
        )
        ps_id = ps.id

    res = rec._reconcile_po_set_inner(ps_id, cfg)
    assert res["status"] == "merged"

    class _Boom:
        def __call__(self, *a, **k):
            raise AssertionError("Session must not be reached")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(rec, "Session", _Boom())
    try:
        rec._persist_reason(ps_id, res, cfg)  # must swallow the failure
    finally:
        monkeypatch.undo()


def test_merge_refusal_restores_prior_status(tmp_path):
    """Docs but zero line-item evidence: merge refuses (None) and the set
    keeps its prior status instead of sliding to pending."""
    from app.services.reconciliation import reconcile_po_set

    cfg, eng = _cfg(tmp_path, "refuse.db")
    with Session(eng) as s:
        ps = _po_set(s, "PO-RF", status=POSetStatus.mismatched)
        _doc(s, cfg, ps.id, "PO", "po.pdf", [])
        _doc(s, cfg, ps.id, "DN", "dn.pdf", [])
        _doc(s, cfg, ps.id, "SI", "si.pdf", [], si_no="I1", invoice_no="I1")
        ps_id = ps.id
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "mismatched"
    with Session(eng) as s:
        assert s.get(POSet, ps_id).status == POSetStatus.mismatched
        assert s.get(POSet, ps_id).merged_output_path is None


# ------------------------------------------------------------------ merge


def test_ordered_docs_defaults_and_unknowns_last():
    from app.services.merge import _ordered_docs

    def stub(dtype):
        return SimpleNamespace(doc_type=dtype, si_no=None, invoice_no=None)

    docs = [stub("PO"), stub("UNKNOWN"), stub("SI"), stub("DN")]
    ps = SimpleNamespace(documents=docs)
    assert [_doc_type(d) for d in _ordered_docs(ps, None)] == ["SI", "DN", "PO", "UNKNOWN"]

    class _NoMerge:
        pass

    assert [_doc_type(d) for d in _ordered_docs(ps, SimpleNamespace())] == [
        "SI",
        "DN",
        "PO",
        "UNKNOWN",
    ]


def _doc_type(d):
    dt = d.doc_type
    return dt.value if hasattr(dt, "value") else str(dt)


def test_packet_name_unsafe_si_falls_back_and_empty_is_none():
    from app.services.merge import _packet_name

    si = SimpleNamespace(doc_type="SI", si_no="!!!", invoice_no=None)
    ps = SimpleNamespace(documents=[si], po_no_normalized="PO-1")
    assert _packet_name(ps) == ("PO-1", True)
    ps2 = SimpleNamespace(documents=[si], po_no_normalized="")
    assert _packet_name(ps2) == (None, True)


def test_resolve_own_file_returns(tmp_path):
    from app.services.merge import _resolve_output_path

    out = _tiny_pdf(tmp_path / "out" / "A.pdf")
    assert _resolve_output_path("A", 1, tmp_path / "out", str(out)) == out


def test_write_merged_missing_refuses_and_allows(tmp_path):
    import pytest

    from app.services.merge import _write_merged

    good = _tiny_pdf(tmp_path / "good.pdf")
    ghost = SimpleNamespace(
        stored_path=str(tmp_path / "ghost.pdf"), id=9, original_filename="ghost.pdf"
    )
    docs = [SimpleNamespace(stored_path=str(good), id=1, original_filename="good.pdf"), ghost]
    with pytest.raises(FileNotFoundError, match=r"ghost\.pdf"):
        _write_merged(docs, tmp_path / "o1.pdf", allow_missing=False)
    out = _write_merged(docs, tmp_path / "o2.pdf", allow_missing=True)
    assert out.exists()


def test_merge_missing_set_raises_and_empty_set_refuses(tmp_path):
    import pytest

    from app.services.merge import force_merge, merge_po_set

    cfg, eng = _cfg(tmp_path, "mempty.db")
    with pytest.raises(ValueError, match="not found"):
        merge_po_set(99999, cfg)
    with pytest.raises(ValueError, match="not found"):
        force_merge(99999, cfg)
    with Session(eng) as s:
        ps = _po_set(s, "PO-ME")
        ps_id = ps.id
    assert merge_po_set(ps_id, cfg) is None
    with Session(eng) as s:
        assert s.get(POSet, ps_id).status == POSetStatus.pending


def test_merge_refuses_blocked_and_missing_file(tmp_path):
    from app.services.merge import merge_po_set

    cfg, eng = _cfg(tmp_path, "mref.db")
    with Session(eng) as s:
        ps = _po_set(s, "PO-MB", has_customs_toggle=True)
        _doc(s, cfg, ps.id, "PO", "po.pdf", [{"no": "1", "qty": 100}])
        _doc(s, cfg, ps.id, "DN", "dn.pdf", [{"no": "1", "qty": 100}])
        _doc(
            s,
            cfg,
            ps.id,
            "SI",
            "si.pdf",
            [{"no": "1", "qty": 100}],
            si_no="I1",
            invoice_no="I1",
        )
        blocked_id = ps.id
        ps2 = _po_set(s, "PO-MF")
        ghost = Document(
            sha256_hash="h_ghost",
            original_filename="ghost.pdf",
            stored_path=str(tmp_path / "nope.pdf"),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps2.id,
        )
        s.add(ghost)
        s.commit()
        ghost_id = ps2.id
    assert merge_po_set(blocked_id, cfg) is None  # customs gate unsatisfied
    assert merge_po_set(ghost_id, cfg) is None  # stored file gone → refuse
    with Session(eng) as s:
        assert s.get(POSet, ghost_id).merged_output_path is None


def test_merge_unnameable_raises(tmp_path):
    import pytest

    from app.services.merge import MergeNamingError, merge_po_set

    cfg, eng = _cfg(tmp_path, "mname.db")
    with Session(eng) as s:
        ps = _po_set(s, "")
        _doc(s, cfg, ps.id, "PO", "po.pdf", [{"no": "1", "qty": 100}])
        _doc(s, cfg, ps.id, "DN", "dn.pdf", [{"no": "1", "qty": 100}])
        _doc(s, cfg, ps.id, "SI", "si.pdf", [{"no": "1", "qty": 100}])
        ps_id = ps.id
    with pytest.raises(MergeNamingError, match="cannot be named"):
        merge_po_set(ps_id, cfg)


def test_force_empty_and_unnameable_raise(tmp_path):
    import pytest

    from app.services.merge import MergeNamingError, force_merge

    cfg, eng = _cfg(tmp_path, "fempty.db")
    with Session(eng) as s:
        ps = _po_set(s, "PO-FE")
        empty_id = ps.id
        ps2 = _po_set(s, "")
        _doc(s, cfg, ps2.id, "PO", "po.pdf", [{"no": "1", "qty": 5}])
        nameless_id = ps2.id
    with pytest.raises(MergeNamingError, match="no documents"):
        force_merge(empty_id, cfg)
    with pytest.raises(MergeNamingError, match="cannot be named"):
        force_merge(nameless_id, cfg)


def test_force_twice_returns_same_packet_and_audits_both(tmp_path):
    """PRODUCT §6: every Force Merge press writes an audit row — including a
    press on an already-merged set. The second press is an idempotent no-op
    returning the same packet, but the intent is recorded with
    already_merged set so the log stays honest."""
    import json

    from app.models import AuditAction, AuditLog
    from app.services.merge import force_merge

    cfg, eng = _cfg(tmp_path, "ftwice.db")
    with Session(eng) as s:
        ps = _po_set(s, "PO-FT", status=POSetStatus.mismatched)
        _doc(s, cfg, ps.id, "SI", "si.pdf", [], si_no="INV-TW", invoice_no="INV-TW")
        _doc(s, cfg, ps.id, "PO", "po.pdf", [])
        ps_id = ps.id
    first = force_merge(ps_id, cfg, justification="Operator confirmed by phone call today.")
    second = force_merge(ps_id, cfg, justification="Operator confirmed by phone call today.")
    assert first == second
    with Session(eng) as s:
        rows = (
            s.query(AuditLog)
            .filter(AuditLog.action == AuditAction.force_merge, AuditLog.po_set_id == ps_id)
            .order_by(AuditLog.id.asc())
            .all()
        )
        assert len(rows) == 2
        assert json.loads(rows[1].detail).get("already_merged") is True
        assert rows[1].justification == "Operator confirmed by phone call today."


# -------------------------------------------------------------- quarantine


def test_safe_po_folder_variants():
    from app.services.quarantine import _safe_po_folder

    assert _safe_po_folder("PO-1", 5, "fp") == "PO-1"
    assert _safe_po_folder("", 7, "abc").startswith("UNIDENTIFIED_PO_")
    assert _safe_po_folder("...", None, None) == "UNIDENTIFIED_PO"
    assert _safe_po_folder("CON", 1, "x") == "_CON"
    assert _safe_po_folder("NUL", 1, "x") == "_NUL"
    long = _safe_po_folder("P" * 500, 1, "x")
    assert len(long) <= 100


def test_quarantine_report_ident_without_pool(tmp_path):
    """Identification flags with no pool still render their note; quantity
    flags without pool do not crash the report."""
    from app.services.quarantine import _write_quarantine_report

    folder = tmp_path / "rep"
    folder.mkdir()
    _write_quarantine_report(
        folder,
        "PO-1",
        "quarantined",
        "unmatched_vendor_line",
        None,
        [
            {
                "line_item_no": "9",
                "type": "identification",
                "pool": None,
                "reason": "no_po_line_with_this_number",
                "vendor_quantity": 5,
            },
            {
                "line_item_no": "1",
                "type": "quantity",
                "pool": None,
                "reason": "quantity_mismatch",
                "vendor_quantity": 4,
            },
        ],
        {"1": 100},
    )
    text = (folder / "QUARANTINE.txt").read_text()
    assert "no_po_line_with_this_number" in text
    assert "PO number   : PO-1" in text


def test_quarantine_copy_skips_missing_stored(tmp_path):
    from app.services.quarantine import quarantine_copy

    cfg, eng = _cfg(tmp_path, "qskip.db")
    with Session(eng) as s:
        ps = _po_set(s, "PO-QS", status=POSetStatus.quarantined)
        s.add(
            Document(
                sha256_hash="h_qs",
                original_filename="gone.pdf",
                stored_path=str(tmp_path / "gone.pdf"),
                doc_type=DocType.PO,
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps.id,
            )
        )
        s.commit()
        ps_id = ps.id
    folder = quarantine_copy(ps_id, cfg, reason="non_positive_quantity")
    assert (folder / "QUARANTINE.txt").exists()
    assert not (folder / "gone.pdf").exists()


def test_quarantine_missing_ids_raise(tmp_path):
    import pytest

    from app.services.quarantine import quarantine_copy, quarantine_document

    cfg, _ = _cfg(tmp_path, "qmiss.db")
    with pytest.raises(ValueError, match="not found"):
        quarantine_copy(99999, cfg)
    with pytest.raises(ValueError, match="not found"):
        quarantine_document(99999, cfg, reason="x")


def test_quarantine_document_without_stored_still_reports(tmp_path):
    from app.services.quarantine import quarantine_document

    cfg, eng = _cfg(tmp_path, "qdoc.db")
    with Session(eng) as s:
        d = Document(
            sha256_hash="h_qd",
            original_filename="lost.pdf",
            stored_path=str(tmp_path / "lost.pdf"),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.failed,
        )
        s.add(d)
        s.commit()
        doc_id = d.id
    folder = quarantine_document(doc_id, cfg, reason="split_failed:pages_unaccounted")
    assert (folder / "QUARANTINE.txt").exists()
    assert "split_failed" in (folder / "QUARANTINE.txt").read_text()


def test_manual_merge_validation(tmp_path):
    import pytest

    from app.services.quarantine import manual_merge

    a = _tiny_pdf(tmp_path / "a.pdf")
    with pytest.raises(ValueError, match="No files"):
        manual_merge([], [])
    with pytest.raises(ValueError, match="order length"):
        manual_merge([a], [0, 1])
    with pytest.raises(ValueError, match="permutation"):
        manual_merge([a], [5])
    with pytest.raises(FileNotFoundError, match="not found"):
        manual_merge([tmp_path / "nope.pdf"], [0])
    out = manual_merge([a], None)
    assert out.exists()


# ---------------------------------------------------------------- customs


def test_toggle_missing_set_raises(tmp_path):
    import pytest

    from app.services.customs import toggle_customs

    cfg, _ = _cfg(tmp_path, "ct.db")
    with pytest.raises(ValueError, match="not found"):
        toggle_customs(99999, cfg)


def test_toggle_counts_only_customs_shipping(tmp_path):
    from app.services.customs import is_blocked, toggle_customs

    cfg, eng = _cfg(tmp_path, "ct2.db")
    with Session(eng) as s:
        ps = _po_set(s, "PO-CT")
        _doc(s, cfg, ps.id, "PO", "po.pdf", [{"no": "1", "qty": 1}])
        ps_id = ps.id
    out = toggle_customs(ps_id, cfg)
    assert out.customs_doc_count == 0
    assert out.status == POSetStatus.blocked_customs
    with Session(eng) as s:
        assert is_blocked(s.get(POSet, ps_id)) is True


def test_toggle_heals_inconsistent_blocked_state(tmp_path):
    """status==blocked_customs with toggle OFF and docs present is
    inconsistent (upload path never clears it); toggling ON heals to
    pending instead of sticking."""
    from app.services.customs import is_blocked, toggle_customs

    cfg, eng = _cfg(tmp_path, "ct3.db")
    with Session(eng) as s:
        ps = _po_set(s, "PO-CH", status=POSetStatus.blocked_customs, has_customs_toggle=False)
        _doc(s, cfg, ps.id, "CUSTOMS", "c.pdf", [])
        _doc(s, cfg, ps.id, "SHIPPING", "s.pdf", [])
        ps_id = ps.id
    out = toggle_customs(ps_id, cfg)
    assert out.has_customs_toggle is True
    assert out.status == POSetStatus.pending
    with Session(eng) as s:
        assert is_blocked(s.get(POSet, ps_id)) is False


# ---------------------------------------------------------------- locking


def test_is_locked_variants():
    from app.services.locking import is_locked

    cfg = load_config("config.example.yaml")
    assert is_locked(SimpleNamespace(locked_by_action=None), cfg) is False
    now = datetime.now(UTC)
    assert is_locked(SimpleNamespace(locked_by_action="x", locked_at=now), cfg) is True
    old = now - timedelta(seconds=10**6)
    assert is_locked(SimpleNamespace(locked_by_action="x", locked_at=old), cfg) is False
    # locked_at missing falls back to updated_at; both missing stays locked
    assert (
        is_locked(SimpleNamespace(locked_by_action="x", locked_at=None, updated_at=now), cfg)
        is True
    )
    assert (
        is_locked(SimpleNamespace(locked_by_action="x", locked_at=None, updated_at=None), cfg)
        is True
    )
    # naive datetimes do not crash
    assert is_locked(SimpleNamespace(locked_by_action="x", locked_at=datetime.now()), cfg) is True


def test_acquire_release_and_mismatch(tmp_path):
    from app.services.locking import acquire_lock, is_locked, release_lock

    cfg, eng = _cfg(tmp_path, "lock.db")
    with Session(eng) as s:
        ps = _po_set(s, "PO-LK")
        assert acquire_lock(ps, "a", s, cfg) is True
        assert acquire_lock(ps, "b", s, cfg) is False  # held by a
        release_lock(ps, s, "b")  # wrong action: must not clear a's lock
        s.refresh(ps)
        assert is_locked(ps, cfg) is True
        release_lock(ps, s)  # unscoped release clears
        s.refresh(ps)
        assert is_locked(ps, cfg) is False
        assert acquire_lock(ps, "b", s, cfg) is True


# ---------------------------------------------------------------- routes


def test_doc_badges_all_states():
    from app.api.routes.po_sets import _doc_status_badge
    from app.models import ExtractionStatus

    assert "Read" in _doc_status_badge(ExtractionStatus.valid)
    assert "Failed" in _doc_status_badge(ExtractionStatus.failed)
    assert "Reading" in _doc_status_badge(ExtractionStatus.processing)
    assert "Not read" in _doc_status_badge(ExtractionStatus.pending)
    assert "Not read" in _doc_status_badge("weird-string")


def test_route_404s(client):
    c, _ = client
    assert c.get("/po_sets/99999").status_code == 404
    assert c.get("/po_sets/99999/detail").status_code == 404
    assert c.post("/po_sets/99999/toggle_customs").status_code == 404


def test_force_route_422_and_releases_lock(client):
    c, cfg = client
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = _po_set(s, "PO-F422")
        ps_id = ps.id
    # empty set: service raises MergeNamingError → route maps to 422
    r = c.post(f"/po_sets/{ps_id}/force_merge", data={"justification": ""})
    assert r.status_code == 422
    # short justification → validation error → 422, and the lock was released
    r2 = c.post(f"/po_sets/{ps_id}/force_merge", data={"justification": "too short"})
    assert r2.status_code == 422
    with Session(eng) as s:
        assert s.get(POSet, ps_id).locked_by_action is None


def test_redo_skips_manual_docs_and_reports_errors(client, monkeypatch):
    from app.models import ExtractionStatus

    c, cfg = client
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = _po_set(s, "PO-RD")
        po = _doc(s, cfg, ps.id, "PO", "po.pdf", [{"no": "1", "qty": 100}])
        custom = _doc(s, cfg, ps.id, "CUSTOMS", "cu.pdf", [])
        custom_id, po_id = custom.id, po.id
        attempts_before = custom.extraction_attempt_count
        ps_id = ps.id

    def boom(doc_id, cfg):
        raise RuntimeError("VLM down")

    monkeypatch.setattr("app.services.extraction.extract_document", boom)
    r = c.post(f"/po_sets/{ps_id}/redo_extract")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "redo_extract_complete"
    by_id = {e["doc_id"]: e for e in body["extractions"]}
    assert "error" in by_id[po_id]  # failure recorded per-doc, not raised
    assert custom_id not in by_id  # manual docs are never re-sent
    with Session(eng) as s:
        cu = s.get(Document, custom_id)
        assert cu.extraction_attempt_count == attempts_before
        assert cu.extraction_status == ExtractionStatus.valid


def test_delete_route_409_and_stale_release(client):
    from datetime import UTC as _UTC
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    c, cfg = client
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = _po_set(s, "PO-D409")  # pending, NOT quarantined
        ps_id = ps.id
    r = c.request("DELETE", f"/po_sets/{ps_id}/quarantine", data={"justification": ""})
    assert r.status_code == 409  # "not quarantined" maps to 409
    # stale lock is auto-released by the detail view
    with Session(eng) as s:
        ps = s.get(POSet, ps_id)
        ps.locked_by_action = "force_merge"
        ps.locked_at = _dt.now(_UTC) - _td(seconds=10**6)
        s.commit()
    d = c.get(f"/po_sets/{ps_id}/detail")
    assert d.status_code == 200
    assert "Locked by" not in d.text
    with Session(eng) as s:
        assert s.get(POSet, ps_id).locked_by_action is None


def test_detail_no_docs_and_locked_msg(client):
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    c, cfg = client
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = _po_set(s, "PO-DN")
        empty_id = ps.id
        ps2 = _po_set(s, "PO-DL")
        ps2.locked_by_action = "force_merge"
        ps2.locked_at = _dt.now(_UTC)
        s.commit()
        locked_id = ps2.id
    d = c.get(f"/po_sets/{empty_id}/detail")
    assert "No documents are attached" in d.text
    d2 = c.get(f"/po_sets/{locked_id}/detail")
    assert "Locked by force_merge" in d2.text
    assert "disabled" in d2.text
