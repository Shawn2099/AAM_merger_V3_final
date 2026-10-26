"""QUARANTINE.txt explains itself: what happened, which files, what to do (T2).

Sets and single documents share one layout. Copies keep the operator's original
filename, and the same text is stored in an audit_log row at quarantine time.
"""

import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import (
    AuditAction,
    AuditLog,
    DocType,
    Document,
    ExtractionStatus,
    POSet,
    POSetStatus,
)
from app.models.base import Base
from app.services.quarantine import quarantine_copy, quarantine_document


def _cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test.db"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    for d in ("stored", "quarantine", "input", "output"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return cfg


def _pdf(path: Path, width: int = 200) -> Path:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=width, height=200)
    w.write(str(path))
    return path


def _doc(tmp_path, cfg, ps_id, kind, original, number=None, po_raw="LPO26/2360", width=200):
    stored = _pdf(
        tmp_path / "stored" / f"{hashlib.sha256(original.encode()).hexdigest()}.pdf", width
    )
    doc = Document(
        sha256_hash=hashlib.sha256(stored.read_bytes()).hexdigest(),
        original_filename=original,
        stored_path=str(stored),
        doc_type=kind,
        extraction_status=ExtractionStatus.valid,
        po_set_id=ps_id,
        po_no_raw=po_raw,
        po_no_normalized="2612360",
        dn_no=number if kind == DocType.DN else None,
        si_no=number if kind == DocType.SI else None,
    )
    return doc


def _set_with_docs(tmp_path, cfg, docs_spec):
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = POSet(po_no_normalized="2612360", status=POSetStatus.quarantined)
        s.add(ps)
        s.commit()
        for i, (kind, original, number) in enumerate(docs_spec):
            s.add(_doc(tmp_path, cfg, ps.id, kind, original, number, width=200 + i))
        s.commit()
        return ps.id


def test_set_report_lists_files_explains_and_says_what_to_do(tmp_path):
    cfg = _cfg(tmp_path)
    ps_id = _set_with_docs(
        tmp_path,
        cfg,
        [
            (DocType.PO, "PO 2360 final.pdf", None),
            (DocType.DN, "DN2182.pdf", "DN-2182"),
            (DocType.SI, "INV-77.pdf", "SI-77"),
        ],
    )

    folder = quarantine_copy(ps_id, cfg, reason="quantity_mismatch")

    text = (folder / "QUARANTINE.txt").read_text(encoding="utf-8")
    assert "What happened" in text
    assert "PO, delivery, and invoice quantities do not agree" in text
    assert "Documents in this set" in text
    for name in ("PO 2360 final.pdf", "DN2182.pdf", "INV-77.pdf"):
        assert name in text
        assert (folder / name).is_file(), f"{name} must keep its original filename"
    assert "DN-2182" in text and "SI-77" in text and "LPO26/2360" in text
    assert "What to do" in text
    assert "Compare PO, DN and SI quantities" in text


def test_unknown_reason_gets_the_generic_what_to_do(tmp_path):
    cfg = _cfg(tmp_path)
    ps_id = _set_with_docs(tmp_path, cfg, [(DocType.PO, "a.pdf", None)])

    folder = quarantine_copy(ps_id, cfg, reason="some_future_code")

    text = (folder / "QUARANTINE.txt").read_text(encoding="utf-8")
    assert "some_future_code" in text
    assert "Review the listed documents" in text


def test_clashing_original_filenames_get_a_numeric_suffix(tmp_path):
    cfg = _cfg(tmp_path)
    ps_id = _set_with_docs(
        tmp_path,
        cfg,
        [
            (DocType.DN, "scan.pdf", "DN-1"),
            (DocType.SI, "scan.pdf", "SI-1"),
            (DocType.PO, "SCAN.PDF", None),
        ],
    )

    folder = quarantine_copy(ps_id, cfg, reason="quantity_mismatch")

    pdfs = sorted(p.name.lower() for p in folder.glob("*.pdf"))
    assert pdfs == ["scan.pdf", "scan_2.pdf", "scan_3.pdf"]


def test_requarantining_the_same_set_reproduces_the_same_names(tmp_path):
    cfg = _cfg(tmp_path)
    ps_id = _set_with_docs(
        tmp_path, cfg, [(DocType.DN, "scan.pdf", "DN-1"), (DocType.SI, "scan.pdf", "SI-1")]
    )

    first = quarantine_copy(ps_id, cfg, reason="quantity_mismatch")
    before = sorted(p.name for p in first.glob("*.pdf"))
    second = quarantine_copy(ps_id, cfg, reason="quantity_mismatch")

    assert second == first
    assert sorted(p.name for p in second.glob("*.pdf")) == before == ["scan.pdf", "scan_2.pdf"]


def test_a_document_cannot_overwrite_the_report_by_its_filename(tmp_path):
    cfg = _cfg(tmp_path)
    ps_id = _set_with_docs(tmp_path, cfg, [(DocType.PO, "QUARANTINE.txt", None)])

    folder = quarantine_copy(ps_id, cfg, reason="quantity_mismatch")

    assert "What happened" in (folder / "QUARANTINE.txt").read_text(encoding="utf-8")
    assert (folder / "QUARANTINE_2.txt").is_file()


def test_set_quarantine_writes_an_audit_row_with_the_same_text(tmp_path):
    cfg = _cfg(tmp_path)
    ps_id = _set_with_docs(tmp_path, cfg, [(DocType.PO, "a.pdf", None)])

    folder = quarantine_copy(ps_id, cfg, reason="quantity_mismatch")

    with Session(get_engine(cfg)) as s:
        rows = s.query(AuditLog).filter(AuditLog.action == AuditAction.quarantined).all()
    assert len(rows) == 1
    assert rows[0].po_set_id == ps_id
    assert rows[0].source == "system"
    detail = json.loads(rows[0].detail)
    assert detail["reason"] == "quantity_mismatch"
    assert detail["report"] == (folder / "QUARANTINE.txt").read_text(encoding="utf-8")


def test_single_document_report_name_and_audit(tmp_path):
    cfg = _cfg(tmp_path)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        d = _doc(tmp_path, cfg, None, DocType.DN, "broken scan.pdf", "DN-9")
        d.extraction_status = ExtractionStatus.failed
        s.add(d)
        s.commit()
        doc_id = d.id
    (tmp_path / "input" / "broken scan.pdf").write_bytes(Path(d.stored_path).read_bytes())

    folder = quarantine_document(doc_id, cfg, reason="extraction failed: timeout")

    text = (folder / "QUARANTINE.txt").read_text(encoding="utf-8")
    assert "What happened" in text and "What to do" in text
    assert "Documents in this set" in text and "broken scan.pdf" in text
    assert (folder / "broken scan.pdf").is_file()
    assert not (tmp_path / "input" / "broken scan.pdf").exists()
    with Session(eng) as s:
        row = s.query(AuditLog).filter(AuditLog.action == AuditAction.quarantined).one()
    assert row.po_set_id is None
    detail = json.loads(row.detail)
    assert detail["document_id"] == doc_id
    assert detail["report"] == text
