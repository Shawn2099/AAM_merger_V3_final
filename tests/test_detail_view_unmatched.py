"""The set page must show every vendor quantity, even on a set that is only waiting (T8)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
from app.models.base import Base


@pytest.fixture()
def client_cfg(tmp_path, monkeypatch):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "view.db"
    for name in ("input", "output", "quarantine", "stored", "logs"):
        (tmp_path / name).mkdir()
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.log_folder = tmp_path / "logs"
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

    with TestClient(app) as client:
        yield client, cfg


def _doc(s, ps_id, kind, name, lines):
    doc = Document(
        sha256_hash=name.ljust(64, "0"),
        original_filename=name,
        stored_path=str(Path(name)),
        doc_type=kind,
        extraction_status=ExtractionStatus.valid,
        po_set_id=ps_id,
        po_no_raw="PO-1",
        po_no_normalized="1",
    )
    s.add(doc)
    s.flush()
    for no, qty in lines:
        s.add(LineItem(document_id=doc.id, line_item_no=no, description=f"item {no}", quantity=qty))


def test_a_waiting_set_still_shows_a_vendor_line_that_matches_no_po_line(client_cfg):
    client, cfg = client_cfg
    with Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized="1", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        _doc(s, ps.id, DocType.PO, "po.pdf", [("1", 100000)])
        _doc(s, ps.id, DocType.SI, "si.pdf", [("1", 50000), ("2", 50000)])
        s.commit()
        ps_id = ps.id

    page = client.get(f"/po_sets/{ps_id}/view").text
    assert "Not on the PO" in page
    assert "SI line 2" in page and "qty 50" in page
    assert "Awaiting DN" in page  # the waiting verdict is unchanged
