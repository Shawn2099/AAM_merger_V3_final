"""Operator recovery path for a permanently failed document.

A document that exhausts its attempt cap is terminal: no further automatic
attempts are made. Before this file existed there was no way to recover one
from the UI — the failure was silent, then unlabelled, then unrecoverable.

The supported path is now:
    unclassified page -> enter the PO number -> attaches to a PO Set
    -> open that set -> Redo/Re-extract -> retries the file

This file proves that path actually works end to end, because the guidance
printed on the unclassified page is only honest if it does.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import Base, DocType, Document, ExtractionStatus, POSet, POSetStatus
from app.services import extraction as ext

PO_NO = "4500043712"
INVOICE_NO = "SIV-ARS-26-4005"
W_PO, W_DN, W_SI = 111.0, 222.0, 333.0
ONE = [("1", "NUT, HEX 9/16", "50.00", "350.00")]


def _pdf(path: Path, width: float) -> Path:
    w = PdfWriter()
    w.add_blank_page(width=width, height=200)
    path.parent.mkdir(parents=True, exist_ok=True)
    w.write(str(path))
    return path


def _extraction(doc_type, number, po_ref, lines):
    return ext._VLMPageExtraction(
        document_type=doc_type,
        has_po_section=doc_type in ("PO", "COMBINED"),
        has_dn_section=doc_type in ("DN", "COMBINED"),
        has_si_section=doc_type in ("SI", "COMBINED"),
        document_number=number,
        po_reference=po_ref,
        po_reference_ambiguous=False,
        vendor_name="ACME TRADING EST.",
        line_items=[
            ext._VLMLineItem(line_item_no=n, description=d, quantity=q, unit_price=p, dn_no=None)
            for n, d, q, p in lines
        ],
    )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = load_config("config.example.yaml")
    base = tmp_path.as_posix()
    cfg.paths.input_folder = f"{base}/input"
    cfg.paths.output_folder = f"{base}/output"
    cfg.paths.quarantine_folder = f"{base}/quarantine"
    cfg.paths.stored_documents_folder = f"{base}/stored"
    cfg.paths.database_path = f"{base}/aam.db"
    cfg.paths.log_folder = f"{base}/logs"
    cfg.ingestion.stability_poll_interval_seconds = 0
    cfg.ingestion.stability_poll_count = 1
    for sub in ("input", "output", "quarantine", "stored", "unclassified", "logs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(cfg))

    import app.api.routes.dashboard as dash
    import app.api.routes.po_sets as po_routes
    import app.api.routes.sync as sync_route
    import app.flows.sync as flow_sync
    import app.services.sync_lock as sync_lock

    for mod in (dash, po_routes, sync_route, flow_sync, sync_lock):
        monkeypatch.setattr(mod, "load_config", lambda *a, **kw: cfg)
    return cfg


@pytest.fixture()
def vlm(monkeypatch):
    """The DN starts unreadable, then becomes readable. Models a vendor
    re-sending a corrected file after the operator reported a bad scan."""
    state = {"dn_readable": False, "responses": {}}

    def create(self, **kwargs):
        payload = None
        for part in kwargs["messages"][1]["content"]:
            if part.get("type") == "file":
                payload = part["file"]["file_data"].split(",", 1)[1]
        raw = base64.b64decode(payload)
        try:
            width = float(PdfReader(io.BytesIO(raw)).pages[0].mediabox.width)
        except Exception:
            width = "UNREADABLE"
        if width == W_DN and not state["dn_readable"]:
            raise ValueError("vendor PDF is a bad scan")
        if width not in state["responses"]:
            raise ValueError(f"unexpected document width={width}")
        return state["responses"][width]

    class Completions:
        pass

    Completions.create = create

    class Chat:
        completions = Completions()

    class Client:
        chat = Chat()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(ext.instructor, "from_openai", lambda *a, **kw: Client())
    state["responses"] = {
        W_PO: _extraction("PO", PO_NO, None, ONE),
        W_DN: _extraction("DN", "GDN-1", PO_NO, ONE),
        W_SI: _extraction("SI", INVOICE_NO, PO_NO, ONE),
    }
    return state


def test_a_failed_document_recovers_through_the_po_set_and_merges(env, vlm):
    """A DN that fails permanently is recovered by the documented UI path.

    Step 1  DN is a bad scan; PO and SI extract fine. The set is stuck pending.
    Step 2  The failed DN is listed and labelled in /unclassified.
    Step 3  Operator enters its PO number, which attaches it to the set.
    Step 4  The set's detail page now lists the DN as a related file, so the
            operator can see that is what is missing.
    Step 5  Redo/Re-extract resets the cap and retries; the vendor file is
            now readable, so extraction succeeds and the set merges.
    """
    from fastapi.testclient import TestClient

    import app.api.routes.dashboard as dash
    from app.api.routes.po_sets import redo_extract
    from app.flows.sync import sync_flow
    from app.main import app

    cfg = env
    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "PO.pdf", W_PO)
    _pdf(inp / "DN.pdf", W_DN)
    _pdf(inp / "SI.pdf", W_SI)

    # ---- step 1: the DN scan is unreadable, so it fails permanently
    result = sync_flow()
    assert result["errors"] == 1, f"the failed DN must be counted: {result}"

    with Session(get_engine(cfg)) as s:
        ps = s.query(POSet).one()
        set_id = ps.id
        assert ps.status == POSetStatus.pending, "the set must not merge without its DN"
        failed = s.query(Document).filter_by(extraction_status=ExtractionStatus.failed).one()
        failed_id, failed_name = failed.id, failed.original_filename
        assert failed.doc_type == DocType.UNKNOWN
        assert failed.po_set_id is None, "precondition: it is in no PO Set"
        assert failed.extraction_attempt_count == 3, "precondition: the cap was reached"

    # the two good documents did form the set
    with Session(get_engine(cfg)) as s:
        assert len(s.get(POSet, set_id).documents) == 2

    # ---- step 2: it is listed and labelled in the holding area
    dash.load_config = lambda: cfg
    client = TestClient(app)
    page = client.get("/unclassified").text
    assert failed_name in page
    assert "Failed" in page, "the failed document must be labelled"
    assert "1 failed" in page

    # ---- step 3: the operator enters the PO number, attaching it to the set
    resp = client.post(
        f"/unclassified/{failed_id}/reclassify",
        data={"doc_type": "DN", "po_no": PO_NO},
    )
    assert resp.status_code == 200, resp.text
    with Session(get_engine(cfg)) as s:
        attached = s.get(Document, failed_id)
        assert attached.po_set_id == set_id, "the PO number should attach it to the set"
        assert attached.extraction_status == ExtractionStatus.failed, (
            "reclassifying to DN must not pretend the file was read"
        )
        assert attached.extraction_attempt_count == 3, (
            "reclassify does not reset the cap; only redo_extract does"
        )

    # ---- step 4: the set's detail page lists the DN as a related file
    detail = client.get(f"/po_sets/{set_id}/detail").text
    assert "Related files" in detail, "the detail page must list the set's files"
    assert failed_name in detail, "the failed DN must be visible on the set"
    assert detail.count("<tr") >= 4, "all three files should be listed"
    assert "Redo/Re-extract" in detail and "Redo matching" in detail

    # ---- step 5: the vendor resends a readable file; Redo/Re-extract retries it
    vlm["dn_readable"] = True
    redo = redo_extract(set_id)
    assert redo["status"] == "redo_extract_complete"
    by_id = {r["doc_id"]: r for r in redo["extractions"]}
    assert by_id[failed_id]["status"] == "valid", redo["extractions"]

    # ---- the set now has all three documents and merges
    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, set_id)
        assert len(ps.documents) == 3, "the set should now hold all three files"
        assert ps.status == POSetStatus.merged, f"got {ps.status}"
    out = [p.name for p in Path(cfg.paths.output_folder).iterdir()]
    assert out == [f"{INVOICE_NO}.pdf"], out
    assert PdfReader(str(Path(cfg.paths.output_folder) / f"{INVOICE_NO}.pdf")).pages


def test_detail_page_lists_each_document_with_its_read_state(env, vlm):
    """A set stuck on `pending` must say which of its files were not read.

    Without this, a set missing its SI is indistinguishable from a set still
    waiting for the vendor to send a third document.
    """
    from fastapi.testclient import TestClient

    import app.api.routes.dashboard as dash
    from app.flows.sync import sync_flow
    from app.main import app

    cfg = env
    vlm["dn_readable"] = True  # every file we supply is readable
    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "PO.pdf", W_PO)
    _pdf(inp / "DN.pdf", W_DN)
    # SI never arrives: the set is incomplete but every file it has was read

    sync_flow()

    dash.load_config = lambda: cfg
    client = TestClient(app)
    with Session(get_engine(cfg)) as s:
        set_id = s.query(POSet).one().id

    detail = client.get(f"/po_sets/{set_id}/detail").text
    assert "Related files (2)" in detail, detail
    assert "PO.pdf" in detail and "DN.pdf" in detail
    assert "Read" in detail
    # every listed file was read, so no failure note should appear
    assert "attempts used" not in detail, detail
