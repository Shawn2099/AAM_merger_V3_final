"""Rasterised extraction transport (FR-6.1, amended): pages as images, one request.

No network: `instructor.from_openai` is faked and records the request. The REAL
`_call_vlm` and `_rasterize_pdf` run, so the request shape the model receives is
what is asserted.
"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

import app.services.extraction as ext
from app.core.config import load_config
from app.core.database import get_engine
from app.models import Base, DocType, Document, ExtractionStatus

_PNG_PREFIX = "data:image/png;base64,"


def _pdf(path: Path, widths=(100,), height=100) -> Path:
    w = PdfWriter()
    for width in widths:
        w.add_blank_page(width=width, height=height)
    with open(path, "wb") as f:
        w.write(f)
    return path


def _vlm_cfg(**vlm):
    base = {
        "model": "test/model",
        "request_timeout_seconds": 60,
        "api_key_env_var": "OPENROUTER_API_KEY",
    }
    base.update(vlm)
    return SimpleNamespace(vlm=SimpleNamespace(**base))


@pytest.fixture()
def seen(monkeypatch):
    """Fake the network; return the dict of kwargs the real code sent."""
    captured: dict = {}
    response = ext._VLMPageExtraction(document_type="PO", document_number="1")

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return response

    class FakeClient:
        chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(ext.instructor, "from_openai", lambda *a, **kw: FakeClient())
    return captured


def _images(captured) -> list[pymupdf.Pixmap]:
    parts = captured["messages"][1]["content"]
    out = []
    for part in parts[1:]:
        assert part["type"] == "image_url"
        url = part["image_url"]["url"]
        assert url.startswith(_PNG_PREFIX)
        out.append(pymupdf.Pixmap(base64.b64decode(url[len(_PNG_PREFIX) :])))
    return out


def test_pages_are_sent_as_images_in_real_page_order(tmp_path, seen):
    # Distinct widths make each page identifiable: the split page indices the model
    # returns for a COMBINED file are positions in this list.
    pdf = _pdf(tmp_path / "three.pdf", widths=(100, 200, 300))
    ext._call_vlm(str(pdf), "PO", _vlm_cfg())

    parts = seen["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": ext._PAGE_PROMPT}
    assert len(parts) == 4, "one text part plus one image per page"
    assert not any(p["type"] == "file" for p in parts), "no native-PDF part any more"
    widths = [pix.width for pix in _images(seen)]
    assert widths == sorted(widths) and len(set(widths)) == 3
    assert seen["messages"][0] == {"role": "system", "content": ext._SYSTEM_PROMPT}
    assert seen["max_retries"] == 0


def test_default_dpi_is_150_and_effort_is_high(tmp_path, seen):
    pdf = _pdf(tmp_path / "one.pdf", widths=(144,))  # 2 inches wide
    ext._call_vlm(str(pdf), "PO", _vlm_cfg())

    assert _images(seen)[0].width == 300  # 2 in x 150 DPI
    assert seen["extra_body"]["reasoning"] == {"effort": "high"}
    assert seen["extra_body"]["usage"] == {"include": True}
    # No silent middle-out truncation of an over-long request.
    assert seen["extra_body"]["transforms"] == []


def test_real_config_without_the_new_keys_gets_the_safe_defaults():
    cfg = load_config("config.example.yaml")
    assert ext._vlm_settings(cfg) == (
        getattr(cfg.vlm, "raster_dpi", 150),
        getattr(cfg.vlm, "reasoning_effort", "high"),
        getattr(cfg.vlm, "max_pages", 30),
    )


def test_dpi_and_effort_come_from_config(tmp_path, seen):
    pdf = _pdf(tmp_path / "one.pdf", widths=(144,))
    ext._call_vlm(str(pdf), "PO", _vlm_cfg(raster_dpi=72, reasoning_effort="low"))

    assert _images(seen)[0].width == 144  # 2 in x 72 DPI
    assert seen["extra_body"]["reasoning"] == {"effort": "low"}


@pytest.mark.parametrize(
    "bad",
    [
        {"raster_dpi": 10},
        {"raster_dpi": 1000},
        {"raster_dpi": "150"},
        {"reasoning_effort": "ultra"},
        {"max_pages": 0},
    ],
)
def test_bad_vlm_setting_fails_closed_before_any_request(tmp_path, monkeypatch, bad):
    pdf = _pdf(tmp_path / "one.pdf")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    def _boom(*a, **kw):
        raise AssertionError("no request may be built for a bad setting")

    monkeypatch.setattr(ext.instructor, "from_openai", _boom)
    with pytest.raises(RuntimeError, match="not configured") as info:
        ext._call_vlm(str(pdf), "PO", _vlm_cfg(**bad))
    # A config fault is the operator's, not the document's: treated as infrastructure.
    assert ext._is_infrastructure_error(info.value)


def test_page_cap_refuses_before_a_request_is_built(tmp_path, monkeypatch):
    pdf = _pdf(tmp_path / "big.pdf", widths=(100, 100, 100))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(
        ext.instructor,
        "from_openai",
        lambda *a, **kw: pytest.fail("an oversized PDF must never reach the API"),
    )
    with pytest.raises(ext.PdfTooLargeError, match="3 pages exceeds the 2-page limit"):
        ext._call_vlm(str(pdf), "PO", _vlm_cfg(max_pages=2))


def test_default_page_cap_is_30_and_exactly_the_cap_is_allowed(tmp_path):
    assert ext.DEFAULT_MAX_PAGES == 30
    ok = _pdf(tmp_path / "ok.pdf", widths=(50,) * 30)
    assert len(ext._rasterize_pdf(ok.read_bytes(), 72, 30)) == 30
    over = _pdf(tmp_path / "over.pdf", widths=(50,) * 31)
    with pytest.raises(ext.PdfTooLargeError):
        ext._rasterize_pdf(over.read_bytes(), 72, 30)


def test_oversized_page_and_total_bytes_are_refused(tmp_path, monkeypatch):
    # 5000 x 5000 pt at 150 DPI is ~109M pixels: a plotter sheet, not a document.
    huge = _pdf(tmp_path / "huge.pdf", widths=(5000,), height=5000)
    with pytest.raises(ext.PdfTooLargeError, match="page 1 is too large"):
        ext._rasterize_pdf(huge.read_bytes(), 150, 30)

    monkeypatch.setattr(ext, "_MAX_IMAGE_BYTES", 10)
    small = _pdf(tmp_path / "small.pdf", widths=(100, 100))
    with pytest.raises(ext.PdfTooLargeError, match="rendered pages exceed"):
        ext._rasterize_pdf(small.read_bytes(), 72, 30)


def test_corrupt_truncated_and_non_pdf_raise_a_clear_error(tmp_path, monkeypatch):
    good = _pdf(tmp_path / "good.pdf", widths=(100, 100)).read_bytes()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(
        ext.instructor,
        "from_openai",
        lambda *a, **kw: pytest.fail("an unreadable PDF must never reach the API"),
    )
    for name, data in {
        "truncated.pdf": good[:60],
        "garbage.pdf": b"this is not a pdf at all" * 20,
        "empty.pdf": b"",
    }.items():
        p = tmp_path / name
        p.write_bytes(data)
        with pytest.raises(ext.PdfRasterError, match=name):
            ext._call_vlm(str(p), "PO", _vlm_cfg())


def test_encrypted_pdf_raises_a_clear_error(tmp_path):
    src = _pdf(tmp_path / "plain.pdf")
    locked = tmp_path / "locked.pdf"
    with pymupdf.open(str(src)) as doc:
        doc.save(
            str(locked),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw="secret",
            owner_pw="owner",
        )
    with pytest.raises(ext.PdfRasterError, match="password-protected"):
        ext._rasterize_pdf(locked.read_bytes(), 150, 30, "locked.pdf")


def test_raster_errors_are_content_errors_not_infrastructure():
    assert not ext._is_infrastructure_error(ext.PdfRasterError("x"))
    assert not ext._is_infrastructure_error(ext.PdfTooLargeError("x"))


def test_corrupt_stored_file_takes_the_failed_extraction_path(tmp_path, monkeypatch):
    """Like docs 164/165: the document is marked failed (attempt counted), not left pending."""
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "raster.db")
    cfg.paths.log_folder = str(tmp_path / "logs")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    bad = tmp_path / "SIV-truncated.pdf"
    bad.write_bytes(b"%PDF-1.4\n1 0 obj\n<<")
    with Session(eng) as s:
        doc = Document(
            sha256_hash="trunc",
            original_filename=bad.name,
            stored_path=str(bad),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.commit()
        doc_id = doc.id

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(
        ext.instructor,
        "from_openai",
        lambda *a, **kw: pytest.fail("must not reach the API"),
    )
    with pytest.raises(ext.PdfRasterError):
        ext.extract_document(doc_id, cfg)
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        assert doc.extraction_status == ExtractionStatus.failed
        assert doc.extraction_attempt_count == 1


# ------------------------------------------------ verbatim identifiers (prompt P5)


@pytest.mark.parametrize(
    ("verbatim", "expected"),
    [
        ("PO-210851", "210851"),
        ("22613 , 0", "22613"),
        ("PO , Rev # 161538,0", "161538"),
        ("4500496514", "4500496514"),
        ("P.O. 8300023893", "8300023893"),
        ("12/3456", "123456"),
    ],
)
def test_verbatim_po_numbers_normalise_to_the_grouping_key(tmp_path, verbatim, expected):
    """The prompt returns PO numbers exactly as printed; the key is made here."""
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "norm.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="n" + str(abs(hash(verbatim))),
            original_filename="d.pdf",
            stored_path="x",
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.flush()
        ext.populate_from_raw_dict(
            doc,
            {"document_type": "DN", "document_number": "GDN-1", "po_no_raw": verbatim},
            s,
            cfg,
        )
        assert doc.po_no_raw == verbatim, "the printed text is kept for audit"
        assert doc.po_no_normalized == expected


def test_ambiguous_document_with_null_po_reference_is_still_held(tmp_path):
    """P5 returns po_reference=null AND po_reference_ambiguous=true. The document keeps
    the flag, gets no PO key and no set, so it stays unattached for a person."""
    from app.services.grouping import group_document

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "amb.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="amb",
            original_filename="d.pdf",
            stored_path="x",
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.pending,
            po_set_id=None,
        )
        s.add(doc)
        s.flush()
        ext.populate_from_raw_dict(
            doc,
            {
                "document_type": "DN",
                "document_number": "GDN-1",
                "po_no_raw": None,
                "po_reference": None,
                "po_reference_ambiguous": True,
            },
            s,
            cfg,
        )
        assert doc.po_reference_ambiguous is True
        assert doc.po_no_normalized is None
        assert doc.po_set_id is None
        set_id, reason = group_document(doc, cfg)
        assert set_id is None
        assert reason is not None, "a held document must carry a visible reason"
