"""Rasterised extraction transport (FR-6.1, amended): pages as images, one request.

No network: `instructor.from_openai` is faked and records the request. The REAL
`_call_vlm` and `_rasterize_pdf` run, so the request shape the model receives is
what is asserted.
"""

from __future__ import annotations

import base64
import io
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
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


def _images(captured) -> list[Image.Image]:
    parts = captured["messages"][1]["content"]
    out = []
    for part in parts[1:]:
        assert part["type"] == "image_url"
        url = part["image_url"]["url"]
        assert url.startswith(_PNG_PREFIX)
        image = Image.open(io.BytesIO(base64.b64decode(url[len(_PNG_PREFIX) :])))
        image.load()
        out.append(image)
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
    images = _images(seen)
    widths = [image.width for image in images]
    assert widths == sorted(widths) and len(set(widths)) == 3
    assert {image.mode for image in images} == {"RGB"}, "the provider gets RGB, never RGBA"
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
    # AES-256 with a non-empty user password: the case a vendor may actually send.
    # pypdf writes it (cryptography is a test dependency); the renderer must refuse
    # it instead of returning blank pages.
    src = _pdf(tmp_path / "plain.pdf", widths=(100, 200))
    locked = tmp_path / "locked.pdf"
    writer = PdfWriter(clone_from=str(src))
    writer.encrypt(user_password="secret", owner_password="owner", algorithm="AES-256")
    with open(locked, "wb") as f:
        writer.write(f)

    with pytest.raises(ext.PdfRasterError, match="password-protected"):
        ext._rasterize_pdf(locked.read_bytes(), 150, 30, "locked.pdf")


def test_owner_only_encrypted_pdf_still_renders(tmp_path):
    """An empty user password opens normally (PDFium only needs the user password)."""
    src = _pdf(tmp_path / "plain.pdf", widths=(100,))
    owner_only = tmp_path / "owner_only.pdf"
    writer = PdfWriter(clone_from=str(src))
    writer.encrypt(user_password="", owner_password="owner", algorithm="AES-256")
    with open(owner_only, "wb") as f:
        writer.write(f)

    images = ext._rasterize_pdf(owner_only.read_bytes(), 72, 30, "owner_only.pdf")
    assert Image.open(io.BytesIO(images[0])).size == (100, 100)


# ---------------------------------------------------------------------------
# Page-box fidelity: a renderer must not silently crop a page.
# ---------------------------------------------------------------------------


def _handbuilt_pdf(
    path: Path,
    *,
    media: str | None = None,
    crop: str | None = None,
    inherited_media: str | None = None,
    rotate: int = 0,
) -> Path:
    """A minimal one-page PDF whose boxes are written verbatim (no library repair).

    `inherited_media` puts /MediaBox on the /Pages node instead of the page, which
    is legal PDF and common in the wild.
    """
    pages_dict = "<< /Type /Pages /Kids [3 0 R] /Count 1"
    if inherited_media is not None:
        pages_dict += f" /MediaBox {inherited_media}"
    pages_dict += " >>"

    page_dict = "<< /Type /Page /Parent 2 0 R /Contents 4 0 R"
    if media is not None:
        page_dict += f" /MediaBox {media}"
    if crop is not None:
        page_dict += f" /CropBox {crop}"
    if rotate:
        page_dict += f" /Rotate {rotate}"
    page_dict += " >>"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        pages_dict.encode(),
        page_dict.encode(),
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]

    out = bytearray(b"%PDF-1.7\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))
    return path


def _size(pdf_bytes: bytes, name: str) -> tuple[int, int]:
    (png,) = ext._rasterize_pdf(pdf_bytes, 72, 5, name)
    image = Image.open(io.BytesIO(png))
    image.load()
    assert image.mode == "RGB"
    return image.size


def test_eight_number_mediabox_is_repaired_not_cropped(tmp_path):
    """SIV-RHO-26-234B: `[0 0 595 841 0 0 595 841]`. PDFium falls back to Letter
    and clips the A4 page unless the box is repaired in memory."""
    pdf = _handbuilt_pdf(
        tmp_path / "eight_numbers.pdf",
        media="[0 0 300 400 0 0 300 400]",
        crop="[0 0 300 400 0 0 300 400]",
    )
    assert _size(pdf.read_bytes(), "eight_numbers.pdf") == (300, 400)


def test_inherited_mediabox_is_not_rewritten_to_letter(tmp_path):
    """A /MediaBox on the /Pages node is legal. The repair must not mistake PDFium's
    Letter fallback in get_mediabox() for a malformed array and shrink the page."""
    pdf = _handbuilt_pdf(
        tmp_path / "inherited.pdf",
        inherited_media="[0 0 300 400]",
    )
    assert _size(pdf.read_bytes(), "inherited.pdf") == (300, 400)


def test_cropbox_smaller_than_mediabox_is_honoured(tmp_path):
    """A deliberate trim (crop marks removed) must render the crop, not the media."""
    pdf = _handbuilt_pdf(
        tmp_path / "cropped.pdf",
        media="[0 0 400 500]",
        crop="[50 50 350 450]",
    )
    assert _size(pdf.read_bytes(), "cropped.pdf") == (300, 400)


def test_rotated_page_keeps_its_rotation_and_is_not_repaired(tmp_path):
    pdf = _handbuilt_pdf(
        tmp_path / "rotated.pdf",
        media="[0 0 300 400]",
        rotate=90,
    )
    assert _size(pdf.read_bytes(), "rotated.pdf") == (400, 300)


def test_pdf_with_no_pages_raises_a_clear_error(tmp_path):
    no_pages = tmp_path / "no_pages.pdf"
    writer = PdfWriter()
    with open(no_pages, "wb") as f:
        writer.write(f)

    with pytest.raises(ext.PdfRasterError, match="no pages"):
        ext._rasterize_pdf(no_pages.read_bytes(), 72, 30, "no_pages.pdf")


# ---------------------------------------------------------------------------
# PDFium is process-globally thread-unsafe: every call must be serialised.
# ---------------------------------------------------------------------------


class _DetectingLock:
    """A lock that records how many callers are inside the critical section."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.inside = 0
        self.max_inside = 0
        self.entries = 0

    def __enter__(self):
        self._lock.acquire()
        self.entries += 1
        self.inside += 1
        self.max_inside = max(self.max_inside, self.inside)
        return self

    def __exit__(self, *exc_info):
        self.inside -= 1
        self._lock.release()
        return False


def test_pdfium_calls_are_serialized_across_threads(tmp_path, monkeypatch):
    lock = _DetectingLock()
    monkeypatch.setattr(ext, "_PDFIUM_GLOBAL_LOCK", lock)
    pdf = _pdf(tmp_path / "two.pdf", widths=(100, 200)).read_bytes()
    errors: list[BaseException] = []

    def render() -> None:
        try:
            for _ in range(5):
                assert len(ext._rasterize_pdf(pdf, 72, 5, "two.pdf")) == 2
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=render) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert lock.entries >= 4, "the rasteriser did not take the process-wide lock"
    assert lock.max_inside == 1, "two threads entered PDFium at the same time"


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
