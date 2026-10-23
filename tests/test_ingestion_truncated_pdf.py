"""Ingestion must refuse a truncated or header-only PDF and retry it once complete."""

import hashlib
import io
import os
import time

import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import Document
from app.models.base import Base
from app.services import ingestion
from app.services.ingestion import ingest_file, is_file_stable


def _pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "ingest.db"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    (tmp_path / "stored").mkdir()
    (tmp_path / "input").mkdir()
    Base.metadata.create_all(get_engine(cfg))
    return cfg


def _doc_count_for(cfg, data: bytes) -> int:
    sha = hashlib.sha256(data).hexdigest()
    with Session(get_engine(cfg)) as s:
        return s.query(Document).filter_by(sha256_hash=sha).count()


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(ingestion.time, "sleep", lambda _s: None)


def test_complete_pdf_ingests(tmp_path):
    cfg = _cfg(tmp_path)
    p = tmp_path / "po.pdf"
    p.write_bytes(_pdf_bytes())
    doc = ingest_file(p, cfg)
    assert doc.id is not None
    assert doc.original_filename == "po.pdf"


def test_truncated_pdf_rejected_without_row_and_retried(tmp_path):
    cfg = _cfg(tmp_path)
    full = _pdf_bytes()
    truncated = full[: len(full) // 2]
    p = tmp_path / "po.pdf"
    p.write_bytes(truncated)

    with pytest.raises(ValueError, match="incomplete or unreadable PDF"):
        ingest_file(p, cfg)
    # Left in the input folder, and no Document row for the partial bytes.
    assert p.exists()
    assert _doc_count_for(cfg, truncated) == 0

    # The complete file arrives under the same name and is ingested.
    p.write_bytes(full)
    doc = ingest_file(p, cfg)
    assert doc.sha256_hash == hashlib.sha256(full).hexdigest()
    assert _doc_count_for(cfg, full) == 1


def _age(p, seconds):
    t = time.time() - seconds
    os.utime(p, (t, t))


def test_old_header_only_file_is_quarantined_with_a_reason(tmp_path):
    cfg = _cfg(tmp_path)
    p = tmp_path / "stub.pdf"
    p.write_bytes(b"%PDF-1.4")
    _age(p, 3600)
    with pytest.raises(ValueError, match="incomplete or unreadable PDF"):
        ingest_file(p, cfg)
    assert not p.exists()
    reasons = list((tmp_path / "quarantine" / "_documents").glob("*.reason.txt"))
    assert len(reasons) == 1
    assert "incomplete or unreadable PDF" in reasons[0].read_text(encoding="utf-8")
    assert _doc_count_for(cfg, b"%PDF-1.4") == 0


def test_recent_incomplete_file_stays_in_input_for_a_retry(tmp_path):
    cfg = _cfg(tmp_path)
    p = tmp_path / "stub.pdf"
    p.write_bytes(b"%PDF-1.4")  # just written: the copy may still be running
    with pytest.raises(ValueError, match="incomplete or unreadable PDF"):
        ingest_file(p, cfg)
    assert p.exists()
    assert not (tmp_path / "quarantine" / "_documents").exists()


def test_is_file_stable_rejects_zero_byte_file(tmp_path, no_sleep):
    p = tmp_path / "empty.pdf"
    p.write_bytes(b"")
    assert is_file_stable(p, interval=1, count=3) is False


def test_is_file_stable_accepts_steady_nonempty_file(tmp_path, no_sleep):
    p = tmp_path / "steady.pdf"
    p.write_bytes(b"%PDF-1.4 steady")
    assert is_file_stable(p, interval=1, count=3) is True
