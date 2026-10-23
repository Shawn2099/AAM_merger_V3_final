"""Reclassify is for the holding area only.

A document already attached to an open PO Set must not be retyped or moved by
hand: its line items, its set's status and its set's lock would go stale. The
route refuses it; the holding-area cases (held and failed-but-unplaced) still
work, because naming the PO is the documented way to place them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
from app.models.base import Base


@pytest.fixture()
def tmp_cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test_reclassify_scope.db"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.log_folder = tmp_path / "logs"
    for p in [
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.log_folder,
    ]:
        Path(p).mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    return cfg


@pytest.fixture()
def client(tmp_cfg, monkeypatch):
    monkeypatch.setattr("app.api.routes.sync.load_config", lambda path=None: tmp_cfg)
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: tmp_cfg)
    monkeypatch.setattr("app.flows.sync.load_config", lambda path=None: tmp_cfg)
    monkeypatch.setattr("app.services.sync_lock.load_config", lambda path=None: tmp_cfg)
    monkeypatch.setattr("app.api.routes.dashboard.load_config", lambda path=None: tmp_cfg)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _open_set(cfg, po_no: str) -> int:
    with Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized=po_no, status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        return ps.id


def _doc(cfg, name: str, **fields) -> int:
    defaults = {
        "sha256_hash": f"sha_{name}",
        "original_filename": f"{name}.pdf",
        "stored_path": f"data/stored/{name}.pdf",
        "doc_type": DocType.DN,
        "extraction_status": ExtractionStatus.valid,
        "po_set_id": None,
    }
    defaults.update(fields)
    with Session(get_engine(cfg)) as s:
        doc = Document(**defaults)
        s.add(doc)
        s.commit()
        s.refresh(doc)
        return doc.id


def test_a_held_dn_can_be_named_and_attaches(tmp_cfg, client):
    doc_id = _doc(tmp_cfg, "held-dn")

    r = client.post(
        f"/unclassified/{doc_id}/reclassify",
        data={"doc_type": "DN", "po_no": "PO-HELD-1"},
        follow_redirects=False,
    )
    assert r.status_code in (200, 302), r.text
    with Session(get_engine(tmp_cfg)) as s:
        doc = s.get(Document, doc_id)
        assert doc.po_set_id is not None
        assert s.get(POSet, doc.po_set_id).po_no_normalized == "HELD1"


def test_known_limitation_an_attached_document_cannot_be_reclassified(tmp_cfg, client):
    set_a = _open_set(tmp_cfg, "PO-OPEN-A")
    doc_id = _doc(tmp_cfg, "attached-dn", po_set_id=set_a)

    r = client.post(
        f"/unclassified/{doc_id}/reclassify",
        data={"doc_type": "PO", "po_no": "PO-OTHER-B"},
        follow_redirects=False,
    )
    assert r.status_code == 409, r.text
    assert "already placed in PO Set" in r.text
    assert "Re-Extract" in r.text and "Delete" in r.text

    with Session(get_engine(tmp_cfg)) as s:
        doc = s.get(Document, doc_id)
        assert doc.doc_type == DocType.DN
        assert doc.po_set_id == set_a
        assert s.get(POSet, set_a).status == POSetStatus.pending
        assert s.query(POSet).count() == 1


def test_a_failed_unplaced_document_can_still_be_named(tmp_cfg, client):
    doc_id = _doc(
        tmp_cfg,
        "failed-unplaced",
        doc_type=DocType.UNKNOWN,
        extraction_status=ExtractionStatus.failed,
    )

    r = client.post(
        f"/unclassified/{doc_id}/reclassify",
        data={"doc_type": "PO", "po_no": "PO-RECOVER-1"},
        follow_redirects=False,
    )
    assert r.status_code in (200, 302), r.text
    with Session(get_engine(tmp_cfg)) as s:
        doc = s.get(Document, doc_id)
        assert doc.doc_type == DocType.PO
        assert doc.po_set_id is not None
