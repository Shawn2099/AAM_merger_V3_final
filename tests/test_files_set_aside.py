"""Batch 1 step 11: the Quarantine page lists "Files set aside" (D9).

The list holds unattached documents the system could not process: failed reads and
combined bundles that were not split. Everything else stays on the holding list only.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
from app.models.base import Base


@pytest.fixture()
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c.paths.database_path = tmp_path / "set_aside_list.db"
    c.paths.input_folder = tmp_path / "input"
    c.paths.output_folder = tmp_path / "output"
    c.paths.quarantine_folder = tmp_path / "quarantine"
    c.paths.stored_documents_folder = tmp_path / "stored"
    c.paths.combined_folder = tmp_path / "combined"
    c.paths.log_folder = tmp_path / "logs"
    c.paths.archive_folder = tmp_path / "archived_sets"
    for p in (
        c.paths.input_folder,
        c.paths.output_folder,
        c.paths.quarantine_folder,
        c.paths.stored_documents_folder,
        c.paths.combined_folder,
        c.paths.log_folder,
        c.paths.archive_folder,
    ):
        p.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(c))
    return c


@pytest.fixture()
def client(cfg, monkeypatch):
    for mod in ("app.api.routes.dashboard", "app.api.routes.po_sets", "app.services.sync_lock"):
        monkeypatch.setattr(f"{mod}.load_config", lambda path=None: cfg)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _doc(cfg, name: str, **fields) -> int:
    sha = hashlib.sha256(name.encode()).hexdigest()
    base = {
        "sha256_hash": sha,
        "original_filename": name,
        "stored_path": str(cfg.paths.stored_documents_folder / f"{sha}.pdf"),
        "doc_type": DocType.UNKNOWN,
        "extraction_status": ExtractionStatus.valid,
        "po_set_id": None,
    }
    base.update(fields)
    with Session(get_engine(cfg)) as s:
        doc = Document(**base)
        s.add(doc)
        s.commit()
        s.refresh(doc)
        return doc.id


def _set_aside_section(client) -> str:
    text = client.get("/quarantine").text
    return (
        text.split('data-testid="files-set-aside"', 1)[1]
        if 'data-testid="files-set-aside"' in text
        else ""
    )


def test_an_unsplit_combined_bundle_is_listed(cfg, client):
    _doc(cfg, "BUNDLE-2DNS.pdf", doc_type=DocType.COMBINED, is_split_parent=False)
    section = _set_aside_section(client)
    assert "BUNDLE-2DNS.pdf" in section
    assert "several documents in one pdf" in section.lower()


def test_a_failed_read_is_listed_with_its_attempt_count(cfg, client):
    _doc(
        cfg,
        "FAILED-READ.pdf",
        extraction_status=ExtractionStatus.failed,
        extraction_attempt_count=3,
    )
    section = _set_aside_section(client)
    assert "FAILED-READ.pdf" in section
    assert "after 3 attempts" in section


def test_an_archived_row_is_not_listed(cfg, client):
    _doc(
        cfg,
        "ARCHIVED-FAILED.pdf",
        extraction_status=ExtractionStatus.failed,
        archived_at=datetime.now(UTC),
    )
    assert "ARCHIVED-FAILED.pdf" not in _set_aside_section(client)


def test_an_attached_failed_document_is_not_listed(cfg, client):
    with Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized="PO8200", status=POSetStatus.quarantined)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        set_id = ps.id
    _doc(
        cfg,
        "ATTACHED-FAILED.pdf",
        extraction_status=ExtractionStatus.failed,
        po_set_id=set_id,
    )
    assert "ATTACHED-FAILED.pdf" not in _set_aside_section(client)


def test_a_valid_unplaced_document_is_not_listed(cfg, client):
    _doc(cfg, "VALID-UNKNOWN.pdf")
    assert "VALID-UNKNOWN.pdf" not in _set_aside_section(client)


def test_the_split_parent_of_a_combined_bundle_is_not_listed(cfg, client):
    _doc(cfg, "PARENT-BUNDLE.pdf", doc_type=DocType.COMBINED, is_split_parent=True)
    assert "PARENT-BUNDLE.pdf" not in _set_aside_section(client)
