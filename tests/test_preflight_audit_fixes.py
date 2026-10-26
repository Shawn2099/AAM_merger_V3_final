"""Targeted unit tests verifying pre-flight audit hardening fixes:
1. Rejection of UNC / network share database_path in AppConfig.
2. Preservation of merged packet in output folder if remove_set_to_quarantine fails.
3. Clean unlinking of merged packet in output folder when remove_set_to_quarantine succeeds.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.core.config import PathsConfig, load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
from app.models.base import Base
from app.services import quarantine as q


def test_unc_database_path_rejected(tmp_path):
    """V6-5: database_path must not be a UNC or SMB network share path."""
    from app.core.config import AppConfig

    paths_dict = {
        "input_folder": tmp_path / "input",
        "output_folder": tmp_path / "output",
        "quarantine_folder": tmp_path / "quarantine",
        "stored_documents_folder": tmp_path / "stored",
        "log_folder": tmp_path / "logs",
        "database_path": r"\\server\share\aam_merger.db",
    }
    with pytest.raises(ValueError, match="cannot be a UNC or network share path"):
        AppConfig(paths=PathsConfig(**paths_dict))

    paths_dict["database_path"] = "//nas/storage/aam_merger.db"
    with pytest.raises(ValueError, match="cannot be a UNC or network share path"):
        AppConfig(paths=PathsConfig(**paths_dict))

    # Local path succeeds
    paths_dict["database_path"] = tmp_path / "local.db"
    cfg = AppConfig(paths=PathsConfig(**paths_dict))
    assert cfg.paths.database_path == tmp_path / "local.db"


@pytest.fixture()
def audit_cfg(tmp_path):
    c = load_config("config.example.yaml")
    c.paths.database_path = tmp_path / "audit_test.db"
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


def test_remove_set_preserves_merged_packet_on_failure(audit_cfg):
    """V5-1: If removal fails or rolls back, the delivered packet in output/ is NOT deleted."""
    eng = get_engine(audit_cfg)
    merged_file = Path(audit_cfg.paths.output_folder) / "PO9999_PACKET.pdf"
    merged_file.write_bytes(b"%PDF-1.4 original delivered packet")

    stored_file = Path(audit_cfg.paths.stored_documents_folder) / "po.pdf"
    stored_file.write_bytes(b"%PDF-1.4 dummy stored document")

    with Session(eng) as s:
        po_set = POSet(
            po_no_normalized="PO9999",
            status=POSetStatus.merged,
            merged_output_path=str(merged_file),
        )
        s.add(po_set)
        s.commit()
        s.refresh(po_set)
        set_id = po_set.id

        doc = Document(
            sha256_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            original_filename="po.pdf",
            stored_path=str(stored_file),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=set_id,
        )
        s.add(doc)
        s.commit()

    # Simulate a DB error during delete transaction
    with (
        patch.object(Session, "commit", side_effect=RuntimeError("Simulated DB lock timeout")),
        pytest.raises(q.SetRemovalRefused),
    ):
        q.remove_set_to_quarantine(set_id, audit_cfg)

    # The merged packet must STILL exist in output/
    assert merged_file.is_file(), "Delivered merged packet was deleted despite DB transaction failure!"
    assert merged_file.read_bytes() == b"%PDF-1.4 original delivered packet"


def test_remove_set_unlinks_merged_packet_on_success(audit_cfg):
    """V5-1: When removal succeeds, the packet is moved to quarantine and unlinked from output/."""
    eng = get_engine(audit_cfg)
    merged_file = Path(audit_cfg.paths.output_folder) / "PO8888_PACKET.pdf"
    merged_file.write_bytes(b"%PDF-1.4 delivered packet to be removed")

    stored_file = Path(audit_cfg.paths.stored_documents_folder) / "po8888.pdf"
    stored_file.write_bytes(b"%PDF-1.4 doc bytes")

    with Session(eng) as s:
        po_set = POSet(
            po_no_normalized="PO8888",
            status=POSetStatus.merged,
            merged_output_path=str(merged_file),
        )
        s.add(po_set)
        s.commit()
        s.refresh(po_set)
        set_id = po_set.id

        doc = Document(
            sha256_hash="abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
            original_filename="po8888.pdf",
            stored_path=str(stored_file),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.valid,
            po_set_id=set_id,
        )
        s.add(doc)
        s.commit()

    result = q.remove_set_to_quarantine(set_id, audit_cfg)
    assert result["folder"]

    # Merged file in output/ is now unlinked
    assert not merged_file.exists(), "Merged packet should be unlinked from output/ on successful removal"

    # And preserved in quarantine/_removed_sets
    removed_folder = Path(audit_cfg.paths.quarantine_folder) / result["folder"]
    archived_packet = removed_folder / "output" / "PO8888_PACKET.pdf"
    assert archived_packet.is_file()
    assert archived_packet.read_bytes() == b"%PDF-1.4 delivered packet to be removed"


def test_windows_ctime_fallback_in_last_touched(monkeypatch):
    """Python 3.11 on Windows lacks st_birthtime; st_ctime is the file creation time."""
    from types import SimpleNamespace

    from app.services import ingestion

    st = SimpleNamespace(st_mtime=100.0, st_ctime=500.0)
    monkeypatch.setattr(ingestion.os, "name", "nt")
    assert ingestion._last_touched(st) == 500.0


def test_non_windows_ignores_ctime(monkeypatch):
    """On Linux/POSIX, ctime is metadata change time and must NOT be used as birthtime."""
    from types import SimpleNamespace

    from app.services import ingestion

    st = SimpleNamespace(st_mtime=100.0, st_ctime=500.0)
    monkeypatch.setattr(ingestion.os, "name", "posix")
    assert ingestion._last_touched(st) == 100.0


def test_failed_report_write_still_unlinks_packet(audit_cfg, monkeypatch):
    """If REMOVED.txt write raises OSError, the delivered packet is still unlinked from output/."""
    eng = get_engine(audit_cfg)
    merged_file = Path(audit_cfg.paths.output_folder) / "PO7777_PACKET.pdf"
    merged_file.write_bytes(b"%PDF-1.4 packet")
    stored_file = Path(audit_cfg.paths.stored_documents_folder) / "po7777.pdf"
    stored_file.write_bytes(b"%PDF-1.4 bytes")

    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO7777", status=POSetStatus.merged, merged_output_path=str(merged_file))
        s.add(ps)
        s.commit()
        s.refresh(ps)
        s.add(
            Document(
                sha256_hash="7" * 64,
                original_filename="po7777.pdf",
                stored_path=str(stored_file),
                doc_type=DocType.PO,
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps.id,
            )
        )
        s.commit()
        set_id = ps.id

    real_write = Path.write_text

    def fake_write(self, *a, **kw):
        if self.name == "REMOVED.txt":
            raise OSError("Simulated disk error writing REMOVED.txt")
        return real_write(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", fake_write)
    q.remove_set_to_quarantine(set_id, audit_cfg)
    assert not merged_file.exists(), "Merged packet should still be unlinked even if report write had OSError"

