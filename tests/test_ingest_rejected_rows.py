"""A file ingest refused for good (R-03) is listed, but can never enter a PO Set."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import Document, ExtractionStatus, POSet, POSetStatus
from app.models.base import Base
from app.services.ingestion import ingest_file


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "rejected.db"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.log_folder = tmp_path / "logs"
    for p in (
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.log_folder,
    ):
        Path(p).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(cfg))
    for target in (
        "app.api.routes.sync.load_config",
        "app.api.routes.po_sets.load_config",
        "app.api.routes.dashboard.load_config",
        "app.flows.sync.load_config",
        "app.services.sync_lock.load_config",
    ):
        monkeypatch.setattr(target, lambda path=None: cfg)
    from app.main import app

    bad = Path(cfg.paths.input_folder) / "fake.pdf"
    bad.write_bytes(b"<html>404 not found</html>")
    with pytest.raises(ValueError, match="%PDF-"):
        ingest_file(bad, cfg)
    with Session(get_engine(cfg)) as s:
        doc_id = s.query(Document).one().id
        ps = POSet(po_no_normalized="1000", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        ps_id = ps.id
    with TestClient(app) as client:
        yield client, cfg, doc_id, ps_id


def test_the_rejected_file_is_listed_with_its_reason(env):
    client, cfg, doc_id, _ = env
    with Session(get_engine(cfg)) as s:
        doc = s.get(Document, doc_id)
        assert doc.extraction_status == ExtractionStatus.failed and doc.po_set_id is None
        assert "%PDF-" in doc.ingest_reject_reason
    page = client.get("/unclassified")
    assert page.status_code == 200 and "Rejected on arrival" in page.text


def test_reclassify_refuses_a_rejected_file(env):
    client, cfg, doc_id, _ = env
    r = client.post(
        f"/unclassified/{doc_id}/reclassify", data={"doc_type": "DN", "po_no": "PO1000"}
    )
    assert r.status_code == 422 and "rejected on arrival" in r.text.lower()
    with Session(get_engine(cfg)) as s:
        assert s.get(Document, doc_id).po_set_id is None


def test_preview_says_why_instead_of_a_404(env):
    client, _cfg, doc_id, _ = env
    r = client.get(f"/documents/{doc_id}/preview")
    assert r.status_code == 422 and "Rejected on arrival" in r.text
    # The drawer is an iframe, i.e. a browser: it gets a readable page, not
    # raw JSON (F-02). API clients keep the JSON contract above.
    assert r.headers["content-type"].startswith("application/json")


def test_preview_renders_a_page_for_a_browser_accept_header(env):
    client, _cfg, doc_id, _ = env
    r = client.get(
        f"/documents/{doc_id}/preview",
        headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
    )
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("text/html")
    assert "cannot be previewed" in r.text
    assert "Rejected on arrival" in r.text
    assert "Traceback" not in r.text


def test_uploading_the_rejected_bytes_as_customs_is_refused(env):
    client, cfg, doc_id, ps_id = env
    r = client.post(
        f"/po_sets/{ps_id}/upload",
        data={"doc_type": "CUSTOMS"},
        files={"file": ("fake.pdf", b"<html>404 not found</html>", "application/pdf")},
    )
    assert r.status_code in (409, 422), r.text
    with Session(get_engine(cfg)) as s:
        assert s.get(Document, doc_id).po_set_id is None
