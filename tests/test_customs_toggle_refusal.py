"""Decision 2026-10-25: the customs requirement can be turned ON at any time. Turning it
OFF on an UNMERGED set is refused while CUSTOMS or SHIPPING files are attached. Merged
sets keep their switch rule (D5/D18)."""

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
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c.paths.database_path = tmp_path / "toggle.db"
    c.paths.input_folder = tmp_path / "input"
    c.paths.output_folder = tmp_path / "output"
    c.paths.quarantine_folder = tmp_path / "quarantine"
    c.paths.stored_documents_folder = tmp_path / "stored"
    c.paths.combined_folder = tmp_path / "combined"
    c.paths.log_folder = tmp_path / "logs"
    for p in (
        c.paths.input_folder,
        c.paths.output_folder,
        c.paths.quarantine_folder,
        c.paths.stored_documents_folder,
        c.paths.combined_folder,
        c.paths.log_folder,
    ):
        Path(p).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(c))
    return c


@pytest.fixture()
def client(cfg, monkeypatch):
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: cfg)
    monkeypatch.setattr("app.api.routes.dashboard.load_config", lambda path=None: cfg)
    monkeypatch.setattr("app.services.customs.load_config", lambda path=None: cfg, raising=False)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _set(cfg, po: str, *, status=POSetStatus.pending, customs_on=True, file_type=None) -> int:
    with Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized=po, status=status, has_customs_toggle=customs_on)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        set_id = ps.id
    if file_type is not None:
        stored = Path(cfg.paths.stored_documents_folder) / f"{po}-{file_type.value}.pdf"
        stored.write_bytes(b"%PDF-1.4 test")
        with Session(get_engine(cfg)) as s:
            s.add(
                Document(
                    sha256_hash=f"{po}-{file_type.value}".ljust(64, "k"),
                    original_filename=f"{file_type.value}.pdf",
                    stored_path=str(stored),
                    doc_type=file_type,
                    extraction_status=ExtractionStatus.valid,
                    po_set_id=set_id,
                )
            )
            s.commit()
    return set_id


def _customs_on(cfg, set_id) -> bool:
    with Session(get_engine(cfg)) as s:
        return s.get(POSet, set_id).has_customs_toggle


def test_turning_off_is_refused_while_a_customs_file_is_attached(cfg, client):
    set_id = _set(cfg, "PO9300", file_type=DocType.CUSTOMS)
    resp = client.post(f"/po_sets/{set_id}/toggle_customs")
    assert resp.status_code == 409
    assert "Delete them first" in resp.json()["detail"]
    assert _customs_on(cfg, set_id) is True


def test_turning_off_works_once_the_files_are_removed(cfg, client):
    set_id = _set(cfg, "PO9301", file_type=DocType.SHIPPING)
    with Session(get_engine(cfg)) as s:
        doc = s.query(Document).filter(Document.po_set_id == set_id).one()
        doc.archived_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
        s.commit()
    resp = client.post(f"/po_sets/{set_id}/toggle_customs")
    assert resp.status_code == 200, resp.text
    assert _customs_on(cfg, set_id) is False


def test_turning_on_is_always_allowed(cfg, client):
    set_id = _set(cfg, "PO9302", customs_on=False)
    resp = client.post(f"/po_sets/{set_id}/toggle_customs")
    assert resp.status_code == 200, resp.text
    assert _customs_on(cfg, set_id) is True


def test_a_merged_set_keeps_the_switch_rule(cfg, client):
    """D5/D18: switching off a merged set is still allowed; the packet is kept."""
    set_id = _set(cfg, "PO9303", status=POSetStatus.merged, file_type=DocType.CUSTOMS)
    resp = client.post(f"/po_sets/{set_id}/toggle_customs")
    assert resp.status_code == 200, resp.text
    assert _customs_on(cfg, set_id) is False


def test_every_open_set_page_says_how_to_merge(cfg, client):
    """B3: the merge instruction is on the page of an open set."""
    set_id = _set(cfg, "PO9304", status=POSetStatus.pending, customs_on=False)
    page = client.get(f"/po_sets/{set_id}/view").text
    assert 'data-testid="merge-instruction"' in page
    assert "press <strong>Merge Now</strong>" in page
    assert "Force Merge</strong> merges without the checks" in page
