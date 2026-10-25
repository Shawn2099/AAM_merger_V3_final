"""Batch 1 step 10: "Move to quarantine" on the holding page (D9, D10).

A held document is copied to the quarantine folder and leaves the holding list.
The stored copy and the quarantine copy are kept, and the input copy is removed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import AuditAction, AuditLog, DocType, Document, ExtractionStatus
from app.models.base import Base


@pytest.fixture()
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c.paths.database_path = tmp_path / "set_aside.db"
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
        Path(p).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(c))
    return c


@pytest.fixture()
def client(cfg, monkeypatch):
    for mod in (
        "app.api.routes.dashboard",
        "app.api.routes.po_sets",
        "app.flows.sync",
        "app.services.sync_lock",
    ):
        monkeypatch.setattr(f"{mod}.load_config", lambda path=None: cfg)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _pdf_bytes() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=120, height=160)
    import io

    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _held_valid_si(cfg, name: str = "SIV-SET-ASIDE.pdf") -> tuple[int, str, Path]:
    data = _pdf_bytes()
    sha = hashlib.sha256(data).hexdigest()
    stored = Path(cfg.paths.stored_documents_folder) / f"{sha}.pdf"
    stored.write_bytes(data)
    input_copy = Path(cfg.paths.input_folder) / name
    input_copy.write_bytes(data)
    with Session(get_engine(cfg)) as s:
        doc = Document(
            sha256_hash=sha,
            original_filename=name,
            stored_path=str(stored),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=None,
            po_no_normalized="PO8100",
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        return doc.id, sha, input_copy


def _held_rejected(cfg, sha: str = "9" * 64) -> int:
    rejected_name = f"rejected_{sha[:8]}.pdf"
    quarantined = Path(cfg.paths.quarantine_folder) / "_documents" / rejected_name
    quarantined.parent.mkdir(parents=True, exist_ok=True)
    quarantined.write_bytes(b"not a pdf")
    with Session(get_engine(cfg)) as s:
        doc = Document(
            sha256_hash=sha,
            original_filename="SIV-DTS-25-584-2.pdf",
            stored_path=str(quarantined),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.failed,
            po_set_id=None,
            raw_extraction_json=json.dumps({"ingest_rejected": "no %PDF- header"}),
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        return doc.id


def _set_aside_rows(cfg) -> list[AuditLog]:
    with Session(get_engine(cfg)) as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action == AuditAction.quarantined)).all()
        return [
            r
            for r in rows
            if '"set_aside"' in (r.detail or "") or "operator_set_aside" in (r.detail or "")
        ]


def test_a_valid_unplaced_document_is_moved_and_its_copies_are_kept(cfg, client):
    doc_id, _sha, input_copy = _held_valid_si(cfg)
    resp = client.post(f"/unclassified/{doc_id}/set_aside", headers={"HX-Request": "true"})
    assert resp.status_code == 200, resp.text
    assert "Moved to the quarantine folder" in resp.text

    with Session(get_engine(cfg)) as s:
        doc = s.get(Document, doc_id)
        assert doc.archived_at is not None
        assert Path(doc.stored_path).is_file()
    copy = (
        Path(cfg.paths.quarantine_folder)
        / "_documents"
        / Path(doc.stored_path).stem
        / "SIV-SET-ASIDE.pdf"
    )
    assert copy.is_file()
    assert not input_copy.exists()

    rows = _set_aside_rows(cfg)
    assert len(rows) == 1
    assert rows[0].source == "operator"

    assert (
        f'data-testid="unclassified-row" data-doc-id="{doc_id}"'
        not in client.get("/unclassified").text
    )


def test_a_rejected_row_shows_no_placement_controls_and_gets_no_second_copy(cfg, client):
    sha = "9" * 64
    doc_id = _held_rejected(cfg, sha=sha)
    stem = f"rejected_{sha[:8]}"
    page = client.get("/unclassified").text
    assert "Document type for SIV-DTS-25-584-2.pdf" not in page
    assert "PO number for SIV-DTS-25-584-2.pdf" not in page
    assert "Cannot be placed: rejected on arrival" in page

    resp = client.post(f"/unclassified/{doc_id}/set_aside", headers={"HX-Request": "true"})
    assert resp.status_code == 200, resp.text
    # The file already sits in the quarantine folder: no second copy, no new folder.
    documents = Path(cfg.paths.quarantine_folder) / "_documents"
    assert sorted(p.name for p in documents.iterdir()) == [f"{stem}.pdf"]

    with Session(get_engine(cfg)) as s:
        assert s.get(Document, doc_id).archived_at is not None


def test_an_attached_document_cannot_be_moved(cfg, client):
    from app.models import POSet, POSetStatus

    with Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized="PO8101", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        set_id = ps.id
    doc_id, _sha, _input = _held_valid_si(cfg, name="SIV-ATTACHED.pdf")
    with Session(get_engine(cfg)) as s:
        s.get(Document, doc_id).po_set_id = set_id
        s.commit()
    resp = client.post(f"/unclassified/{doc_id}/set_aside", headers={"HX-Request": "true"})
    assert resp.status_code == 409


def test_a_document_being_read_cannot_be_moved(cfg, client):
    doc_id, _sha, _input = _held_valid_si(cfg, name="SIV-READING.pdf")
    with Session(get_engine(cfg)) as s:
        s.get(Document, doc_id).extraction_status = ExtractionStatus.processing
        s.commit()
    resp = client.post(f"/unclassified/{doc_id}/set_aside", headers={"HX-Request": "true"})
    assert resp.status_code == 409


def test_a_missing_document_is_404(cfg, client):
    resp = client.post("/unclassified/999999/set_aside", headers={"HX-Request": "true"})
    assert resp.status_code == 404


def test_a_set_aside_is_refused_while_the_sync_lock_is_held(cfg, client, monkeypatch):
    """Review A1: the move itself takes the sync lock, not only the route pre-check."""
    from app.services import quarantine as q
    from app.services import sync_lock

    doc_id, _sha, input_copy = _held_valid_si(cfg, name="SIV-LOCKED.pdf")
    monkeypatch.setattr(sync_lock, "acquire_sync_lock", lambda *a, **k: None)
    with pytest.raises(q.SetAsideRefused) as exc:
        q.set_aside_document(doc_id, cfg)
    assert exc.value.status_code == 409
    assert input_copy.is_file()
    with Session(get_engine(cfg)) as s:
        assert s.get(Document, doc_id).archived_at is None


def test_a_rejected_row_whose_stored_file_is_missing_keeps_its_input_copy(cfg, client):
    """Review L3: no quarantine PDF and no stored file means nothing is removed."""
    from app.services import quarantine as q

    sha = "7" * 64
    doc_id = _held_rejected(cfg, sha=sha)
    stem = f"rejected_{sha[:8]}"
    (Path(cfg.paths.quarantine_folder) / "_documents" / f"{stem}.pdf").unlink()
    with pytest.raises(q.SetAsideRefused) as exc:
        q.set_aside_document(doc_id, cfg)
    assert exc.value.status_code == 409
    with Session(get_engine(cfg)) as s:
        assert s.get(Document, doc_id).archived_at is None
