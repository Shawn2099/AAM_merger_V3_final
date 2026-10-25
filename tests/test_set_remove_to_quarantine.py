"""Batch 1 step 13: "Move to quarantine and remove set" (decision 2026-10-25).

The set's PDFs are copied and verified into the quarantine folder first. Only then are
the set's rows deleted, in one transaction, with an audit row that keeps the old set id.
Stored copies in data/stored are never deleted.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import (
    AuditAction,
    AuditLog,
    DocType,
    Document,
    ExtractionStatus,
    LineItem,
    POSet,
    POSetStatus,
)
from app.models.base import Base
from app.services import quarantine as q


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    c = load_config("config.example.yaml")
    c.paths.database_path = tmp_path / "remove_set.db"
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
    # The sync lock file is kept in the temp folder by pointing its config at it.
    from app.services import sync_lock

    monkeypatch.setattr(sync_lock, "load_config", lambda path=None: c)
    return c


@pytest.fixture()
def client(cfg, monkeypatch):
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: cfg)
    monkeypatch.setattr("app.api.routes.dashboard.load_config", lambda path=None: cfg)
    from app.main import app

    yield TestClient(app)


def _pdf_bytes(tag: str) -> bytes:
    """A distinct PDF per tag: the page size comes from the tag, so hashes differ."""
    w = PdfWriter()
    width = 100 + int(hashlib.md5(tag.encode()).hexdigest()[:4], 16) % 400
    w.add_blank_page(width=width, height=150)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _set_with_files(cfg, po: str = "PO8300", status=POSetStatus.pending):
    """A pending set with PO and DN documents, their stored PDFs, and one input copy."""
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po, status=status)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        set_id = ps.id
    doc_ids = []
    input_copy = None
    for dtype, name in ((DocType.PO, "po.pdf"), (DocType.DN, "dn.pdf")):
        data = _pdf_bytes(f"{po}:{name}")
        sha = hashlib.sha256(data).hexdigest()
        stored = Path(cfg.paths.stored_documents_folder) / f"{sha}.pdf"
        stored.write_bytes(data)
        with Session(eng) as s:
            doc = Document(
                sha256_hash=sha,
                original_filename=name,
                stored_path=str(stored),
                doc_type=dtype,
                extraction_status=ExtractionStatus.valid,
                po_set_id=set_id,
                po_no_normalized=po,
            )
            s.add(doc)
            s.commit()
            s.refresh(doc)
            s.add(LineItem(document_id=doc.id, line_item_no="1", description="BOLT", quantity=5000))
            s.commit()
            doc_ids.append(doc.id)
        if dtype == DocType.PO:
            input_copy = Path(cfg.paths.input_folder) / name
            input_copy.write_bytes(data)
    # An unrelated file in the input folder must stay where it is.
    unrelated = Path(cfg.paths.input_folder) / "unrelated.pdf"
    unrelated.write_bytes(_pdf_bytes("unrelated"))
    return set_id, doc_ids, input_copy, unrelated


def _removed_folders(cfg) -> list[Path]:
    base = Path(cfg.paths.quarantine_folder) / q.REMOVED_SETS_FOLDER
    return (
        sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".tmp-"))
        if base.exists()
        else []
    )


def test_the_set_rows_are_deleted_and_the_audit_row_keeps_the_old_id(cfg):
    set_id, doc_ids, _input, _unrelated = _set_with_files(cfg)
    q.remove_set_to_quarantine(set_id, cfg)
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, set_id) is None
        assert s.scalars(select(Document).where(Document.id.in_(doc_ids))).all() == []
        assert s.scalars(select(LineItem).where(LineItem.document_id.in_(doc_ids))).all() == []
        rows = s.scalars(
            select(AuditLog).where(AuditLog.action == AuditAction.quarantine_delete)
        ).all()
        assert len(rows) == 1
        assert rows[0].po_set_id is None
        detail = json.loads(rows[0].detail)
        assert detail["deleted"] is True and detail["removed_po_set_id"] == set_id
        assert detail["po_no_normalized"] == "PO8300"
        assert detail["folder"].startswith("_removed_sets/")


def test_the_folder_holds_the_pdfs_the_export_and_the_report_and_stored_copies_stay(cfg):
    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg)
    result = q.remove_set_to_quarantine(set_id, cfg)
    folder = Path(result["folder"])
    assert (folder / "files" / "po.pdf").is_file()
    assert (folder / "files" / "dn.pdf").is_file()
    for name in (
        "po_set.json",
        "documents.json",
        "line_items.json",
        "audit_log.json",
        "manifest.json",
        "REMOVED.txt",
    ):
        assert (folder / name).is_file(), name
    assert "How to restore" in (folder / "REMOVED.txt").read_text(encoding="utf-8")
    assert all(Path(p).is_file() for p in Path(cfg.paths.stored_documents_folder).glob("*.pdf"))


def test_the_input_copy_moves_into_the_folder_and_an_unrelated_file_stays(cfg):
    set_id, _doc_ids, input_copy, unrelated = _set_with_files(cfg)
    result = q.remove_set_to_quarantine(set_id, cfg)
    assert not input_copy.exists()
    assert (Path(result["folder"]) / "input" / "po.pdf").is_file()
    assert unrelated.is_file()
    assert result["moved_input_count"] == 1


def test_a_merged_set_is_refused_and_nothing_changes(cfg):
    set_id, doc_ids, _input, _unrelated = _set_with_files(cfg, status=POSetStatus.merged)
    with pytest.raises(q.SetRemovalRefused) as exc:
        q.remove_set_to_quarantine(set_id, cfg)
    assert exc.value.status_code == 409
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, set_id) is not None
        assert len(s.scalars(select(Document).where(Document.id.in_(doc_ids))).all()) == 2
    assert _removed_folders(cfg) == []


def test_an_unknown_set_is_404(cfg):
    with pytest.raises(q.SetRemovalRefused) as exc:
        q.remove_set_to_quarantine(987654, cfg)
    assert exc.value.status_code == 404


def test_a_missing_stored_file_is_recorded_and_the_removal_goes_ahead(cfg):
    set_id, doc_ids, _input, _unrelated = _set_with_files(cfg)
    with Session(get_engine(cfg)) as s:
        stored = Path(s.get(Document, doc_ids[0]).stored_path)
    stored.unlink()
    result = q.remove_set_to_quarantine(set_id, cfg)
    assert result["missing_files"] == ["po.pdf"]
    report = (Path(result["folder"]) / "REMOVED.txt").read_text(encoding="utf-8")
    assert "po.pdf" in report
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, set_id) is None


def test_a_failed_copy_leaves_rows_and_input_untouched_and_no_folder(cfg, monkeypatch):
    set_id, doc_ids, input_copy, _unrelated = _set_with_files(cfg)
    monkeypatch.setattr(q, "_same_bytes", lambda path, data: False)
    with pytest.raises(OSError):
        q.remove_set_to_quarantine(set_id, cfg)
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, set_id) is not None
        assert len(s.scalars(select(Document).where(Document.id.in_(doc_ids))).all()) == 2
    assert input_copy.is_file()
    assert _removed_folders(cfg) == []
    base = Path(cfg.paths.quarantine_folder) / q.REMOVED_SETS_FOLDER
    assert not base.exists() or list(base.iterdir()) == []


def test_the_route_removes_the_set_and_shows_the_folder(cfg, client):
    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg)
    resp = client.post(f"/po_sets/{set_id}/remove_to_quarantine", data={"note": "test"})
    assert resp.status_code == 200, resp.text
    assert "remove-set-result" in resp.text
    assert "_removed_sets" in resp.text
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, set_id) is None


def test_the_route_refuses_while_a_sync_reads_documents(cfg, client, monkeypatch):
    from app.services import sync_lock

    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg)
    monkeypatch.setattr(sync_lock, "_is_sync_running", lambda *a, **k: True)
    resp = client.post(f"/po_sets/{set_id}/remove_to_quarantine", data={})
    assert resp.status_code == 409
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, set_id) is not None


def test_a_removed_set_card_is_shown_only_on_open_sets(cfg, client):
    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg)
    assert "remove-set-card" in client.get(f"/po_sets/{set_id}/view").text
    merged_id, _d, _i, _u = _set_with_files(cfg, po="PO8301", status=POSetStatus.merged)
    assert "remove-set-card" not in client.get(f"/po_sets/{merged_id}/view").text
