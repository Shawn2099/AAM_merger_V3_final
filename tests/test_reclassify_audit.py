"""Batch 1 step 6: a manual reclassify leaves an audit row (I-38). Refusals write none."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import AuditAction, AuditLog, DocType, Document, ExtractionStatus
from app.models.base import Base


@pytest.fixture()
def tmp_cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test_reclassify_audit.db"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.log_folder = tmp_path / "logs"
    cfg.paths.archive_folder = tmp_path / "archived_sets"
    for p in [
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.log_folder,
        cfg.paths.archive_folder,
    ]:
        Path(p).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(cfg))
    return cfg


@pytest.fixture()
def client(tmp_cfg, monkeypatch):
    for mod in [
        "app.api.routes.sync",
        "app.api.routes.po_sets",
        "app.api.routes.dashboard",
        "app.flows.sync",
        "app.services.sync_lock",
    ]:
        monkeypatch.setattr(f"{mod}.load_config", lambda path=None: tmp_cfg)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _held_doc(cfg, sha: str = "c" * 64) -> int:
    with Session(get_engine(cfg)) as s:
        doc = Document(
            sha256_hash=sha,
            original_filename="ORDER-2026.pdf",
            stored_path=str(Path(cfg.paths.stored_documents_folder) / f"{sha}.pdf"),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=None,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        return doc.id


def _reclassify_rows(cfg) -> list[AuditLog]:
    with Session(get_engine(cfg)) as s:
        rows = s.scalars(
            select(AuditLog).where(AuditLog.action == AuditAction.manual_status_change)
        ).all()
        return [r for r in rows if '"reclassified"' in (r.detail or "")]


def test_placing_with_a_po_writes_one_row_with_the_full_note(client, tmp_cfg):
    doc_id = _held_doc(tmp_cfg)
    resp = client.post(
        f"/unclassified/{doc_id}/reclassify", data={"doc_type": "DN", "po_no": "PO5555"}
    )
    assert resp.status_code < 400, resp.text
    rows = _reclassify_rows(tmp_cfg)
    assert len(rows) == 1
    detail = json.loads(rows[0].detail)
    assert detail["old_doc_type"] == "UNKNOWN"
    assert detail["new_doc_type"] == "DN"
    assert detail["po_no_raw"] == "PO5555"
    assert "from UNKNOWN to DN" in detail["note"]
    assert rows[0].po_set_id is not None


def test_type_only_change_writes_a_row_with_no_set(client, tmp_cfg):
    doc_id = _held_doc(tmp_cfg, sha="d" * 64)
    resp = client.post(f"/unclassified/{doc_id}/reclassify", data={"doc_type": "SI"})
    assert resp.status_code < 400, resp.text
    rows = _reclassify_rows(tmp_cfg)
    assert len(rows) == 1
    assert rows[0].po_set_id is None
    assert json.loads(rows[0].detail)["new_doc_type"] == "SI"


def test_refused_reclassify_writes_no_row(client, tmp_cfg):
    doc_id = _held_doc(tmp_cfg, sha="e" * 64)
    resp = client.post(
        f"/unclassified/{doc_id}/reclassify", data={"doc_type": "NOT_A_TYPE", "po_no": "PO1"}
    )
    assert resp.status_code >= 400
    assert _reclassify_rows(tmp_cfg) == []


def test_an_unsplit_combined_bundle_cannot_be_reclassified_and_stays_unchanged(client, tmp_cfg):
    """Step 12 (D17): a multi-document PDF is refused, and the row is not touched."""
    with Session(get_engine(tmp_cfg)) as s:
        doc = Document(
            sha256_hash="f" * 64,
            original_filename="BUNDLE-2DNS.pdf",
            stored_path=str(Path(tmp_cfg.paths.stored_documents_folder) / "f.pdf"),
            doc_type=DocType.COMBINED,
            extraction_status=ExtractionStatus.valid,
            is_split_parent=False,
            po_set_id=None,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    resp = client.post(
        f"/unclassified/{doc_id}/reclassify", data={"doc_type": "DN", "po_no": "PO6666"}
    )
    assert resp.status_code == 409
    assert "several documents in one PDF" in resp.text
    with Session(get_engine(tmp_cfg)) as s:
        row = s.get(Document, doc_id)
        assert row.doc_type == DocType.COMBINED
        assert row.po_set_id is None
    assert _reclassify_rows(tmp_cfg) == []
