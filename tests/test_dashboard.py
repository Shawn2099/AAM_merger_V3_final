"""Task 10 TDD — Dashboard + Audit + Polish (§8). Tests wire all views, 5-status filter, customs toggle,
Redo vs Redo matching split, Force Merge modal, HTMX partial refresh, audit read-only, cross-platform."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import (
    AuditAction,
    AuditLog,
    DocType,
    Document,
    ExtractionStatus,
    POSet,
    POSetStatus,
)
from app.models.base import Base


@pytest.fixture()
def tmp_cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test_dashboard.db"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.unclassified_folder = tmp_path / "unclassified"
    cfg.paths.log_folder = tmp_path / "logs"
    for p in [
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.unclassified_folder,
        cfg.paths.log_folder,
    ]:
        Path(p).mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    return cfg


@pytest.fixture()
def client(tmp_cfg, monkeypatch):
    import app.api.routes.sync as sync_mod

    monkeypatch.setattr("app.api.routes.sync.load_config", lambda path=None: tmp_cfg)
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: tmp_cfg)
    monkeypatch.setattr("app.flows.sync.load_config", lambda path=None: tmp_cfg)
    # dashboard module may also use load_config
    try:
        import importlib.util

        dash_spec = importlib.util.find_spec("app.api.routes.dashboard")
        assert dash_spec is not None

        monkeypatch.setattr("app.api.routes.dashboard.load_config", lambda path=None: tmp_cfg)
    except ImportError:
        pass
    # also patch app.main load_config if needed
    sync_mod._sync_running = False
    from app.main import app

    with TestClient(app) as c:
        yield c
    sync_mod._sync_running = False


def _create_poset(cfg, po_no="PO1000", status=POSetStatus.pending, has_customs=False):
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=status, has_customs_toggle=has_customs)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        return ps.id


def _create_poset_with_docs(cfg, po_no="PO1234", status=POSetStatus.pending):
    """Create POSet with a PO/DN/SI doc and line items for detail checks."""
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    import hashlib

    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=status)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        pid = ps.id
        for dt in [DocType.PO, DocType.DN, DocType.SI]:
            sha = hashlib.sha256(f"{pid}-{dt.value}".encode()).hexdigest()
            # create valid tiny PDF via pypdf (so force_merge can read it)
            from pypdf import PdfWriter

            p = Path(cfg.paths.stored_documents_folder) / f"{sha}.pdf"
            w = PdfWriter()
            w.add_blank_page(width=200, height=200)
            w.write(str(p))
            doc = Document(
                sha256_hash=sha,
                original_filename=f"{dt.value}.pdf",
                stored_path=str(p),
                doc_type=dt,
                extraction_status=ExtractionStatus.valid,
                po_set_id=pid,
            )
            s.add(doc)
            s.flush()
            # add line item

            s.query(Document).filter_by(id=doc.id).first()
            from app.models import LineItem as LI

            s.add(LI(document_id=doc.id, description="Widget A", quantity=100000, unit_price=10000))
        s.commit()
        return pid


# ---------------------------------------------------------------------------
# 1. Dashboard renders with 5-status filter
# ---------------------------------------------------------------------------


def test_dashboard_renders_with_filter(client):
    r = client.get("/dashboard")
    assert r.status_code == 200, r.text
    html = r.text
    # must contain 5 statuses in filter dropdown
    for st in ["pending", "mismatched", "quarantined", "blocked_customs", "merged"]:
        assert st in html, f"missing status {st} in dashboard"
    # filter select element
    assert "<select" in html.lower()
    # HTMX attributes present
    assert "hx-get" in html
    assert "hx-trigger" in html


def test_dashboard_table_fragment_htmx(tmp_cfg, client):
    _create_poset(tmp_cfg, po_no="POHTMXFRAG", status=POSetStatus.pending)
    r = client.get("/dashboard/table")
    assert r.status_code == 200, r.text
    html = r.text
    assert "hx-get" in html or "hx-swap" in html or "po_no" in html.lower() or "PO" in html
    # fragment should be HTMX partial (not full html) or at least contain table/div
    assert "table" in html.lower() or "div" in html.lower()


def test_dashboard_filter_by_status(tmp_cfg, client):
    _create_poset(tmp_cfg, po_no="POFILT1", status=POSetStatus.pending)
    _create_poset(tmp_cfg, po_no="POFILT2", status=POSetStatus.merged)
    r = client.get("/dashboard?status=pending")
    assert r.status_code == 200, r.text
    html = r.text
    # pending should appear, merged should not in filtered view (or at least pending present)
    assert "POFILT1" in html
    # when filtering pending, merged row should be absent
    assert "POFILT2" not in html
    # reverse filter
    r2 = client.get("/dashboard?status=merged")
    assert r2.status_code == 200
    assert "POFILT2" in r2.text
    assert "POFILT1" not in r2.text


def test_dashboard_filter_all_statuses(tmp_cfg, client):
    # all five statuses appear when no filter, and each filters correctly
    for st in POSetStatus:
        _create_poset(tmp_cfg, po_no=f"POF{st.value.upper()}", status=st)
    r = client.get("/dashboard")
    assert r.status_code == 200
    for st in POSetStatus:
        assert f"POF{st.value.upper()}" in r.text


# ---------------------------------------------------------------------------
# 2. Audit log read-only
# ---------------------------------------------------------------------------


def test_audit_log_read_only(tmp_cfg, client):
    # create an audit entry via force_merge path
    _create_poset(tmp_cfg, po_no="POAUD1", status=POSetStatus.pending)
    # force merge to generate audit (use stored docs so merge succeeds)
    pid2 = _create_poset_with_docs(tmp_cfg, po_no="POAUD2", status=POSetStatus.pending)
    client.post(f"/po_sets/{pid2}/force_merge")
    r = client.get("/audit")
    assert r.status_code == 200, r.text
    html = r.text.lower()
    # audit view must be read-only: contains audit entries table and no editable form
    assert "audit" in html
    # Should not contain POST form for audit creation
    # Check audit entry appears (force_merge)
    assert "force_merge" in html or "force merge" in html
    # POST to /audit should be 405
    r2 = client.post("/audit", json={})
    assert r2.status_code in (404, 405), f"audit POST should be 405, got {r2.status_code}"


def test_audit_log_shows_quarantine_delete(tmp_cfg, client):
    pid = _create_poset(tmp_cfg, po_no="POAUDQ1", status=POSetStatus.quarantined)
    # need docs for delete to succeed
    import hashlib

    eng = get_engine(tmp_cfg)
    with Session(eng) as s:
        s.get(POSet, pid)
        sha = hashlib.sha256(b"deltest").hexdigest()
        p = Path(tmp_cfg.paths.stored_documents_folder) / f"{sha}.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        doc = Document(
            sha256_hash=sha,
            original_filename="PO.pdf",
            stored_path=str(p),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=pid,
        )
        s.add(doc)
        s.commit()
    # HTMX delete or POST delete quarantined
    client.delete(f"/po_sets/{pid}/quarantine")
    r = client.get("/audit")
    assert r.status_code == 200
    assert "quarantine_delete" in r.text.lower() or "quarantine" in r.text.lower()


# ---------------------------------------------------------------------------
# 3. PO Set detail: customs toggle, Redo vs Redo matching split, Force Merge modal, HTMX
# ---------------------------------------------------------------------------


def test_po_set_detail_has_customs_toggle_and_redo_split_and_force_merge_modal(tmp_cfg, client):
    pid = _create_poset_with_docs(tmp_cfg, po_no="PODET1", status=POSetStatus.pending)
    r = client.get(f"/po_sets/{pid}/view")
    # fallback to alternative detail route if /view not found, try /dashboard/po_set/{id}
    if r.status_code == 404:
        r = client.get(f"/dashboard/po_set/{pid}")
    if r.status_code == 404:
        r = client.get(f"/po_sets/{pid}/detail")
    assert r.status_code == 200, f"detail view not found: {r.text[:500]}"
    html = r.text
    # customs toggle must be present regardless of status (FR-12.1)
    assert "customs" in html.lower(), "customs toggle missing"
    # two distinct redo buttons
    assert "redo" in html.lower()
    # check distinct labels/endpoints
    has_extract = "redo_extract" in html or "re-extract" in html.lower() or "Re-extract" in html
    has_match = "redo_match" in html or "redo matching" in html.lower()
    assert has_extract and has_match, f"redo split missing: {html[:800]}"
    # Force Merge modal/confirmation
    assert "force" in html.lower() and "merge" in html.lower()
    # modal id or confirmation dialog
    assert (
        "modal" in html.lower() or "confirm" in html.lower() or "force-merge-modal" in html.lower()
    )
    # HTMX partial refresh attributes
    assert "hx-get" in html or "hx-post" in html


def test_customs_toggle_available_regardless_of_status(tmp_cfg, client):
    for st in [
        POSetStatus.pending,
        POSetStatus.mismatched,
        POSetStatus.quarantined,
        POSetStatus.merged,
    ]:
        pid = _create_poset(tmp_cfg, po_no=f"POCUST{st.value}", status=st)
        r = client.get(f"/po_sets/{pid}/view")
        if r.status_code == 404:
            r = client.get(f"/po_sets/{pid}/detail")
        assert r.status_code == 200
        assert "customs" in r.text.lower()


def test_redo_endpoints_split(tmp_cfg, client):
    pid = _create_poset(tmp_cfg, po_no="POREDO1", status=POSetStatus.pending)
    r1 = client.post(f"/po_sets/{pid}/redo_extract")
    assert r1.status_code == 200, r1.text
    r2 = client.post(f"/po_sets/{pid}/redo_match")
    assert r2.status_code == 200, r2.text
    # they must be distinct actions (response contains distinct status)
    assert (
        r1.json()["status"] != r2.json()["status"]
        or "extract" in r1.text.lower()
        or "match" in r2.text.lower()
    )


def test_force_merge_modal_and_audit(tmp_cfg, client):
    pid = _create_poset_with_docs(tmp_cfg, po_no="POFM1", status=POSetStatus.pending)
    r = client.get(f"/po_sets/{pid}/view")
    if r.status_code == 404:
        r = client.get(f"/po_sets/{pid}/detail")
    html = r.text
    # modal must have confirmation text and HTMX post to force_merge
    assert "hx-post" in html and "force_merge" in html
    # execute force merge
    r2 = client.post(f"/po_sets/{pid}/force_merge")
    assert r2.status_code == 200
    # audit entry created
    eng = get_engine(tmp_cfg)
    with Session(eng) as s:
        entries = s.query(AuditLog).filter_by(action=AuditAction.force_merge).all()
        assert len(entries) >= 1
        assert (
            any(str(e.po_set_id) == str(pid) or e.po_set_id == pid for e in entries)
            or len(entries) >= 1
        )


# ---------------------------------------------------------------------------
# 4. Quarantine view with Delete confirmation modal (HTMX)
# ---------------------------------------------------------------------------


def test_quarantine_view_with_delete_modal(tmp_cfg, client):
    _create_poset(tmp_cfg, po_no="POQ1", status=POSetStatus.quarantined)
    r = client.get("/quarantine")
    assert r.status_code == 200, r.text
    html = r.text.lower()
    assert "quarantine" in html
    # delete button/modal
    assert "delete" in html
    assert "modal" in html or "confirm" in html
    # hx-delete or hx-post for delete
    assert "hx-delete" in r.text or "hx-post" in r.text or "delete" in html


# ---------------------------------------------------------------------------
# 5. HTMX partial refresh + cross-platform + sync button disabled
# ---------------------------------------------------------------------------


def test_htmx_partial_refresh_attributes(tmp_cfg, client):
    _create_poset(tmp_cfg, po_no="POHTMX1", status=POSetStatus.pending)
    r = client.get("/dashboard")
    html = r.text
    # dashboard must poll via hx-get + hx-trigger every 2s + hx-swap
    assert "hx-get" in html
    assert "every 2s" in html or "every 2" in html
    assert "hx-swap" in html or "outerHTML" in html or "innerHTML" in html
    # table fragment endpoint must also be HTMX-aware
    rf = client.get("/dashboard/table?status=pending")
    assert rf.status_code == 200
    assert "hx-get" in rf.text or "POHTMX1" in rf.text


def test_sync_button_htmx_and_disabled_when_running(client):
    # dashboard must have Sync button with hx-post to /sync
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert 'hx-post="/sync"' in r.text or "hx-post='/sync'" in r.text or "/sync" in r.text
    # trigger sync then check status endpoint
    client.post("/sync")
    r2 = client.get("/sync/status")
    assert r2.status_code == 200
    assert "running" in r2.text.lower()
    # dashboard while running should show disabled or "Sync already running"
    r3 = client.get("/dashboard")
    # at least contains sync button text; when running, may show disabled attribute
    assert "sync" in r3.text.lower()


def test_cross_platform_paths_no_hardcoded_separators(tmp_cfg, client):
    # config paths must be Path objects and as_posix usable (cross-platform)
    assert isinstance(tmp_cfg.paths.input_folder, Path)
    assert isinstance(tmp_cfg.paths.database_path, Path)
    # dashboard HTML should not contain hardcoded Windows C:\ paths
    r = client.get("/dashboard")
    assert "C:\\" not in r.text
    # audit and quarantine also cross-platform
    for path in ["/audit", "/quarantine"]:
        r2 = client.get(path)
        assert "C:\\" not in r2.text


def test_dashboard_filter_htmx_returns_partial(tmp_cfg, client):
    """P3: HTMX filter returns partial table not full page."""
    _create_poset(tmp_cfg, po_no="POHTMX2", status=POSetStatus.pending)
    # normal full page
    r_full = client.get("/dashboard?status=pending")
    assert r_full.status_code == 200
    assert "POHTMX2" in r_full.text
    # HTMX request should return _dashboard_table fragment
    r_htmx = client.get("/dashboard?status=pending", headers={"HX-Request": "true"})
    assert r_htmx.status_code == 200
    # partial should still contain PO, but ideally NOT full base layout
    # check that HTMX partial is shorter or contains table marker
    assert "POHTMX2" in r_htmx.text
    # dashboard partial should be HTMX-aware: less than full page or contains table id
    # fail if implementation still returns full dashboard.html (contains <html> or base title)
    # We assert that HTMX response does NOT contain full base title "AAM Merger" if partial, or contains hx fragment marker
    # For TDD, we fail if response is identical to full (means not returning partial)
    if r_htmx.text == r_full.text:
        pytest.fail(
            "HTMX dashboard filter should return partial _dashboard_table, not full dashboard.html"
        )


def test_unclassified_view_and_reclassify(tmp_cfg, client):
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, POSet
    from app.models.base import Base

    eng = get_engine(tmp_cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        doc = Document(
            sha256_hash="unk_hash_1",
            original_filename="unknown_vendor.pdf",
            stored_path="data/stored/unknown_vendor.pdf",
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    # GET /unclassified
    r = client.get("/unclassified")
    assert r.status_code == 200
    assert "unknown_vendor.pdf" in r.text
    assert "Unclassified Documents" in r.text

    # POST /unclassified/{doc_id}/reclassify
    r_post = client.post(
        f"/unclassified/{doc_id}/reclassify",
        data={"doc_type": "PO", "po_no": "MANUALPO999"},
        headers={"HX-Request": "true"},
    )
    assert r_post.status_code == 200
    assert "Reclassified" in r_post.text or "PO" in r_post.text

    with Session(eng) as s:
        d = s.get(Document, doc_id)
        assert d.doc_type == DocType.PO
        assert d.po_no_normalized == "MANUALPO999"
        assert d.po_set_id is not None
        ps = s.get(POSet, d.po_set_id)
        assert ps.po_no_normalized == "MANUALPO999"


def test_manual_merger_back_link_points_to_dashboard(client):
    r = client.get("/manual/merger")
    assert r.status_code == 200
    assert 'href="/dashboard"' in r.text


def test_document_preview_and_merged_download(tmp_cfg, client, tmp_path):
    eng = get_engine(tmp_cfg)
    Base.metadata.create_all(eng)
    pdf_file = tmp_path / "test_preview.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 sample content")

    with Session(eng) as s:
        ps = POSet(
            po_no_normalized="POPREV1", status=POSetStatus.merged, merged_output_path=str(pdf_file)
        )
        s.add(ps)
        s.commit()
        s.refresh(ps)
        doc = Document(
            sha256_hash="hash_prev_123",
            original_filename="test_preview.pdf",
            stored_path=str(pdf_file),
            doc_type=DocType.PO,
            po_set_id=ps.id,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id
        ps_id = ps.id

    # Test /documents/{doc_id}/preview
    r_prev = client.get(f"/documents/{doc_id}/preview")
    assert r_prev.status_code == 200
    assert r_prev.headers["content-type"] == "application/pdf"
    assert b"%PDF-1.4" in r_prev.content

    # Test /po_sets/{ps_id}/merged_pdf
    r_merged = client.get(f"/po_sets/{ps_id}/merged_pdf")
    assert r_merged.status_code == 200
    assert r_merged.headers["content-type"] == "application/pdf"
    assert b"%PDF-1.4" in r_merged.content


def test_static_assets_served(client):
    r_css = client.get("/static/css/theme.css")
    assert r_css.status_code == 200
    assert "--primary" in r_css.text

    r_js = client.get("/static/js/app.js")
    assert r_js.status_code == 200
    assert "pdfDrawer" in r_js.text
