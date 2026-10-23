"""A combined PDF (PO + DN + SI in one file) is quarantined whole by default.

Splitting is off (`extraction.split_combined: false`): nothing is cut, no child
documents exist, and the file waits in the holding area for a person.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.api.routes.dashboard import _holding_area_query
from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, POSet
from app.models.base import Base
from app.services.extraction import extract_document

_COMBINED = {
    "document_type": "COMBINED",
    "has_po_section": True,
    "has_dn_section": True,
    "has_si_section": True,
    "page_count": 3,
    "components": [
        {"type": "PO", "page_start": 1, "page_end": 1},
        {"type": "DN", "page_start": 2, "page_end": 2},
        {"type": "SI", "page_start": 3, "page_end": 3},
    ],
    "po_no_raw": "PO100",
    "line_items": [],
}


def _setup(tmp_path, monkeypatch):
    cfg = load_config("config.example.yaml")
    cfg.extraction.split_combined = False
    cfg.paths.database_path = str(tmp_path / "t.db")
    cfg.paths.input_folder = str(tmp_path / "input")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    cfg.paths.quarantine_folder = str(tmp_path / "quarantine")
    cfg.paths.combined_folder = str(tmp_path / "combined")
    Path(cfg.paths.input_folder).mkdir(parents=True)
    pdf = Path(cfg.paths.input_folder) / "bundle.pdf"
    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=100, height=100)
    with open(pdf, "wb") as f:
        w.write(f)
    stored = Path(cfg.paths.stored_documents_folder) / "bundle.pdf"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(pdf.read_bytes())
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash=hashlib.sha256(pdf.read_bytes()).hexdigest(),
            original_filename="bundle.pdf",
            stored_path=str(stored),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.commit()
        doc_id = doc.id
    calls = []

    def fake_vlm(*a, **kw):
        calls.append(1)
        return dict(_COMBINED)

    monkeypatch.setattr("app.services.extraction._call_vlm", fake_vlm)
    return cfg, eng, doc_id, pdf, calls


def test_splitting_is_off_by_default_in_config():
    from app.core.config import ExtractionConfig

    assert ExtractionConfig().split_combined is False


def test_a_combined_pdf_is_quarantined_whole_and_never_split(tmp_path, monkeypatch):
    cfg, eng, doc_id, pdf, _ = _setup(tmp_path, monkeypatch)

    extract_document(doc_id, cfg)

    with Session(eng) as s:
        d = s.get(Document, doc_id)
        assert d.doc_type == DocType.COMBINED
        assert d.extraction_status == ExtractionStatus.valid
        assert d.is_split_parent is False
        assert d.po_set_id is None and d.po_no_normalized is None
        assert d.line_items == []
        assert s.query(Document).count() == 1  # no child documents
        assert s.query(POSet).count() == 0  # a combined file never mints a set
        assert [x.id for x in _holding_area_query(s).all()] == [doc_id]
    assert not pdf.exists()  # input copy removed, so the next sync does not re-hash it
    assert list(Path(cfg.paths.input_folder).glob("*.pdf")) == []  # nothing was cut
    assert not Path(cfg.paths.combined_folder).exists()
    folders = list((Path(cfg.paths.quarantine_folder) / "_documents").iterdir())
    assert len(folders) == 1
    report = (folders[0] / "QUARANTINE.txt").read_text(encoding="utf-8")
    assert "combined_document" in report


def test_a_quarantined_combined_pdf_is_not_read_again(tmp_path, monkeypatch):
    cfg, _, doc_id, _, calls = _setup(tmp_path, monkeypatch)

    extract_document(doc_id, cfg)
    extract_document(doc_id, cfg)

    assert len(calls) == 1  # one paid VLM call, not one per sync
