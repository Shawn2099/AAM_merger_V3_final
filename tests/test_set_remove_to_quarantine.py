"""Batch 1 step 13: "Move to quarantine and remove set" (decision 2026-10-25).

The set's PDFs are copied and verified into the quarantine folder first. Only then are
the set's rows deleted, in one transaction, with an audit row that keeps the old set id.
Stored copies in data/stored are never deleted.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
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
    assert (folder / "REMOVED.txt").is_file()
    assert not (folder / "REMOVAL_IN_PROGRESS.txt").exists()
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


def test_a_merged_set_can_be_removed_to_quarantine(cfg):
    set_id, doc_ids, _input, _unrelated = _set_with_files(cfg, status=POSetStatus.merged)
    output_dir = Path(cfg.paths.output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "PO8300_merged.pdf"
    out_file.write_bytes(b"%PDF-1.4 merged packet")
    with Session(get_engine(cfg)) as s:
        s.get(POSet, set_id).merged_output_path = str(out_file)
        s.commit()
    result = q.remove_set_to_quarantine(set_id, cfg)
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, set_id) is None
        assert len(s.scalars(select(Document).where(Document.id.in_(doc_ids))).all()) == 0
    folders = _removed_folders(cfg)
    assert len(folders) == 1
    moved_output = list((folders[0] / "output").glob("*.pdf"))
    assert len(moved_output) == 1
    assert moved_output[0].read_bytes() == b"%PDF-1.4 merged packet"
    assert not out_file.exists()


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


def test_a_failed_copy_leaves_rows_and_input_untouched_and_keeps_the_folder_as_failed(
    cfg, monkeypatch
):
    """Review 2: a step-1 failure keeps the folder, reported as failed, with a 500 for the operator."""
    set_id, doc_ids, input_copy, _unrelated = _set_with_files(cfg)
    monkeypatch.setattr(q, "_same_bytes", lambda path, data: False)
    with pytest.raises(q.SetRemovalRefused) as exc:
        q.remove_set_to_quarantine(set_id, cfg)
    assert exc.value.status_code == 500
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, set_id) is not None
        assert len(s.scalars(select(Document).where(Document.id.in_(doc_ids))).all()) == 2
    assert input_copy.is_file()
    # The failure is before the folder is renamed into place, so there is no folder to keep.
    assert _removed_folders(cfg) == []


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


def test_a_removed_set_card_is_shown_on_open_and_merged_sets_but_not_archived(cfg, client):
    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg)
    assert "remove-set-card" in client.get(f"/po_sets/{set_id}/view").text
    merged_id, _d, _i, _u = _set_with_files(cfg, po="PO8301", status=POSetStatus.merged)
    assert "remove-set-card" in client.get(f"/po_sets/{merged_id}/view").text
    with Session(get_engine(cfg)) as s:
        s.get(POSet, set_id).archived_at = datetime.now(UTC)
        s.commit()
    assert "remove-set-card" not in client.get(f"/po_sets/{set_id}/view").text


def test_a_failed_delete_restores_the_input_files_and_deletes_nothing(cfg):
    """Review A2: a real database error in the delete transaction puts the input files back."""
    from sqlalchemy import text

    set_id, doc_ids, input_copy, _unrelated = _set_with_files(cfg)
    with get_engine(cfg).begin() as conn:
        conn.execute(
            text(
                "CREATE TRIGGER block_po_set_delete BEFORE DELETE ON po_sets BEGIN SELECT RAISE(ABORT, 'blocked'); END;"
            )
        )
    with pytest.raises(q.SetRemovalRefused) as exc:
        q.remove_set_to_quarantine(set_id, cfg)
    assert exc.value.status_code == 500
    assert "nothing was deleted" in exc.value.message
    assert input_copy.is_file(), "the input file must be put back"
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, set_id) is not None
        assert len(s.scalars(select(Document).where(Document.id.in_(doc_ids))).all()) == 2
    folders = _removed_folders(cfg)
    assert len(folders) == 1, "the copies stay on disk for the operator"
    with get_engine(cfg).begin() as conn:
        conn.execute(text("DROP TRIGGER block_po_set_delete"))


def test_a_failed_restore_keeps_the_folder_and_names_the_files(cfg, monkeypatch):
    """Review A4: when a moved input file cannot be put back, its only copy is kept."""
    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg)
    # Fail only the input-copy check: that happens after the folder is in place.
    monkeypatch.setattr(q, "_same_bytes", lambda path, data: path.parent.name != "input")
    monkeypatch.setattr(q, "_restore_input_copies", lambda moved: ["po.pdf"])
    with pytest.raises(q.SetRemovalRefused) as exc:
        q.remove_set_to_quarantine(set_id, cfg)
    assert "po.pdf" in exc.value.message
    assert len(_removed_folders(cfg)) == 1, "the folder is kept when a file could not be restored"


def test_an_input_copy_held_by_another_active_document_stays_in_the_input_folder(cfg):
    """Plan rule: an input copy is not moved when a different active document holds its bytes."""
    set_id, doc_ids, input_copy, _unrelated = _set_with_files(cfg)
    shared_sha = hashlib.sha256(input_copy.read_bytes()).hexdigest()
    with Session(get_engine(cfg)) as s:
        # The set's PO document is archived (so the hash is free for an active row elsewhere),
        # and an active unattached document holds the same bytes.
        s.get(Document, doc_ids[0]).archived_at = datetime.now(UTC)
        s.add(
            Document(
                sha256_hash=shared_sha,
                original_filename="elsewhere.pdf",
                stored_path=str(Path(cfg.paths.stored_documents_folder) / "elsewhere.pdf"),
                doc_type=DocType.UNKNOWN,
                extraction_status=ExtractionStatus.valid,
                po_set_id=None,
            )
        )
        s.commit()
    result = q.remove_set_to_quarantine(set_id, cfg)
    assert input_copy.is_file(), "a hash still held by an active document must not be moved"
    assert result["moved_input_count"] == 0


def test_a_locked_set_is_refused_through_the_route(cfg, client):
    set_id, _doc_ids, input_copy, _unrelated = _set_with_files(cfg)
    with Session(get_engine(cfg)) as s:
        s.get(POSet, set_id).locked_by_action = "merge_now"
        s.get(POSet, set_id).locked_at = datetime.now(UTC)
        s.commit()
    resp = client.post(f"/po_sets/{set_id}/remove_to_quarantine", data={})
    assert resp.status_code == 409
    assert input_copy.is_file()


def test_an_archived_set_is_refused(cfg):
    from datetime import UTC, datetime

    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg)
    with Session(get_engine(cfg)) as s:
        s.get(POSet, set_id).archived_at = datetime.now(UTC)
        s.commit()
    with pytest.raises(q.SetRemovalRefused) as exc:
        q.remove_set_to_quarantine(set_id, cfg)
    assert exc.value.status_code == 409


def test_a_grouping_sweep_after_removal_does_nothing(cfg):
    """After removal the removed documents are not re-grouped into a new set by the sweep."""
    from app.services.grouping import resolve_unattached_documents

    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg)
    q.remove_set_to_quarantine(set_id, cfg)
    resolve_unattached_documents(cfg)
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, set_id) is None
        assert s.query(POSet).count() == 0


def test_a_browser_refusal_gets_a_page_and_an_api_client_gets_json(cfg, client, monkeypatch):
    from app.services import sync_lock

    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg)
    monkeypatch.setattr(sync_lock, "_is_sync_running", lambda *a, **k: True)
    browser = client.post(
        f"/po_sets/{set_id}/remove_to_quarantine",
        data={},
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    assert browser.status_code == 409
    assert "text/html" in browser.headers["content-type"]
    assert "was not removed" in browser.text
    api = client.post(
        f"/po_sets/{set_id}/remove_to_quarantine", data={}, headers={"Accept": "application/json"}
    )
    assert api.status_code == 409
    assert "detail" in api.json()


def test_a_browser_success_gets_the_result_page(cfg, client):
    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg)
    resp = client.post(
        f"/po_sets/{set_id}/remove_to_quarantine",
        data={},
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    assert resp.status_code == 200
    assert "Removed PO Set #" in resp.text
    assert "Back to dashboard" in resp.text


def test_a_real_restore_failure_keeps_the_only_copy_and_says_so(cfg, monkeypatch):
    """Review M3: the restore itself fails (a real write error), not a stub.

    Two input copies. The first moves into the folder; the second fails its check. The
    first must then be put back, and the write fails, so the folder keeps its only copy.
    """
    set_id, doc_ids, _input_copy, _unrelated = _set_with_files(cfg, po="PO8350")
    input_dir = Path(cfg.paths.input_folder)
    with Session(get_engine(cfg)) as s:
        dn_bytes = Path(s.get(Document, doc_ids[1]).stored_path).read_bytes()
    (input_dir / "dn.pdf").write_bytes(dn_bytes)
    real_write = Path.write_bytes

    def failing_write(self, data):
        if self.parent == input_dir and self.name.endswith(".restoring"):
            raise OSError("disk refused the restore")
        return real_write(self, data)

    calls = {"n": 0}

    def second_input_fails(path, data):
        if path.parent.name != "input":
            return True
        calls["n"] += 1
        return calls["n"] == 1

    monkeypatch.setattr(Path, "write_bytes", failing_write)
    monkeypatch.setattr(q, "_same_bytes", second_input_fails)
    with pytest.raises(q.SetRemovalRefused) as exc:
        q.remove_set_to_quarantine(set_id, cfg)
    assert exc.value.status_code == 500
    assert "Input files not put back" in exc.value.message
    folder = _removed_folders(cfg)[0]
    moved = [p for p in (folder / "input").iterdir() if p.suffix == ".pdf"]
    assert moved, "the moved copy stays in the folder when it cannot be restored"
    assert (folder / "REMOVAL_FAILED.txt").is_file()
    assert not (folder / "REMOVED.txt").exists()


def test_a_failed_removal_says_it_did_not_finish(cfg):
    """Review L1: a removal that fails keeps its folder, reported as failed."""
    from sqlalchemy import text

    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg)
    with get_engine(cfg).begin() as conn:
        conn.execute(
            text(
                "CREATE TRIGGER block_again BEFORE DELETE ON po_sets BEGIN SELECT RAISE(ABORT, 'blocked'); END;"
            )
        )
    with pytest.raises(q.SetRemovalRefused):
        q.remove_set_to_quarantine(set_id, cfg)
    folder = _removed_folders(cfg)[0]
    assert (folder / "REMOVAL_FAILED.txt").is_file()
    assert not (folder / "REMOVED.txt").exists()
    with get_engine(cfg).begin() as conn:
        conn.execute(text("DROP TRIGGER block_again"))


def test_a_document_attached_between_the_two_steps_stops_the_delete(cfg, monkeypatch):
    """Review M2: a document that arrives after the copy is refused at the delete step."""
    from app.services import ingestion

    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg)

    def attach_then_list(cfg_arg):
        with Session(get_engine(cfg)) as s:
            s.add(
                Document(
                    sha256_hash="arrived".ljust(64, "a"),
                    original_filename="late.pdf",
                    stored_path=str(Path(cfg.paths.stored_documents_folder) / "late.pdf"),
                    doc_type=DocType.DN,
                    extraction_status=ExtractionStatus.valid,
                    po_set_id=set_id,
                )
            )
            s.commit()
        return []

    monkeypatch.setattr(ingestion, "input_pdfs", attach_then_list)
    with pytest.raises(q.SetRemovalRefused) as exc:
        q.remove_set_to_quarantine(set_id, cfg)
    assert exc.value.status_code == 409
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, set_id) is not None


def test_a_removed_id_can_be_reused_and_the_old_folder_is_not_adopted(cfg):
    """Review M2: after removal the id is reused (no AUTOINCREMENT). The old folder keeps a
    `removed:` marker, so the new set gets its own folder (PRODUCT section 8, O)."""
    set_id, _doc_ids, _input, _unrelated = _set_with_files(cfg, po="PO8400")
    with Session(get_engine(cfg)) as s:
        old = s.get(POSet, set_id)
        old_folder = q._quarantine_set_folder(old, cfg)
    q.remove_set_to_quarantine(set_id, cfg)
    assert (old_folder / ".po_set_id").read_text(encoding="utf-8") == f"removed:{set_id}:" + (
        (old_folder / ".po_set_id").read_text(encoding="utf-8").split(":")[-1]
    )
    with Session(get_engine(cfg)) as s:
        new = POSet(po_no_normalized="PO8400", status=POSetStatus.pending)
        s.add(new)
        s.commit()
        s.refresh(new)
        new_id = new.id
        new_folder = q._quarantine_set_folder(new, cfg)
    assert new_id == set_id, "SQLite reuses the highest removed id (PRODUCT section 8, O)"
    assert new_folder != old_folder
    assert (new_folder / ".po_set_id").read_text(encoding="utf-8") == str(new_id)


def test_the_api_response_names_the_missing_files(cfg, client):
    """Review L5: API clients still see the missing-file count and the folder."""
    set_id, doc_ids, _input, _unrelated = _set_with_files(cfg, po="PO8500")
    with Session(get_engine(cfg)) as s:
        Path(s.get(Document, doc_ids[0]).stored_path).unlink()
    resp = client.post(
        f"/po_sets/{set_id}/remove_to_quarantine", data={}, headers={"Accept": "application/json"}
    )
    assert resp.status_code == 200
    assert "stored file(s) were missing" in resp.text
    assert "_removed_sets" in resp.text


def test_the_same_bytes_can_be_added_again_after_removal(cfg):
    """Review M2 (re-ingest): once the removed rows are gone, the same file gets a fresh row.
    The database check is the one that decides; ingest uses the same hash rule."""
    set_id, doc_ids, _input, _unrelated = _set_with_files(cfg, po="PO8600")
    with Session(get_engine(cfg)) as s:
        sha = s.get(Document, doc_ids[0]).sha256_hash
    q.remove_set_to_quarantine(set_id, cfg)
    with Session(get_engine(cfg)) as s:
        s.add(
            Document(
                sha256_hash=sha,
                original_filename="po.pdf",
                stored_path=str(Path(cfg.paths.stored_documents_folder) / "again.pdf"),
                doc_type=DocType.PO,
                extraction_status=ExtractionStatus.valid,
                po_set_id=None,
            )
        )
        s.commit()
        assert s.query(Document).filter(Document.sha256_hash == sha).count() == 1


def test_an_unreadable_input_file_stops_the_removal(cfg, monkeypatch):
    """Review 1: an input file that cannot be read might hold the set's bytes; the removal stops."""
    set_id, _doc_ids, _input_copy, unrelated = _set_with_files(cfg, po="PO8700")
    real_read = Path.read_bytes

    def unreadable(self):
        if self.name == "unrelated.pdf":
            raise OSError("locked by another program")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    with pytest.raises(q.SetRemovalRefused) as exc:
        q.remove_set_to_quarantine(set_id, cfg)
    assert exc.value.status_code == 500
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, set_id) is not None
    assert unrelated.is_file()


def test_restore_never_overwrites_a_file_the_operator_put_back(cfg):
    """Review 7: a restore writes under a free name rather than overwriting, and leaves no
    temporary file behind."""
    input_dir = Path(cfg.paths.input_folder)
    folder = Path(cfg.paths.quarantine_folder) / "restore-test"
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / "po.pdf"
    copy.write_bytes(b"%PDF-1.4 the copy")
    original = input_dir / "po.pdf"
    original.write_bytes(b"%PDF-1.4 the operator's new file")

    failed = q._restore_input_copies([(copy, original)])

    assert failed == []
    assert original.read_bytes() == b"%PDF-1.4 the operator's new file"
    restored = [p for p in input_dir.iterdir() if p.name != "po.pdf"]
    assert [p.read_bytes() for p in restored] == [b"%PDF-1.4 the copy"]
    assert not any(p.name.endswith(".restoring") for p in input_dir.iterdir())
