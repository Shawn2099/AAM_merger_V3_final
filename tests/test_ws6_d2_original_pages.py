"""WS6 / D2 — a "1 of 6" PO, and the packet is built from the ORIGINAL PDF pages.

D2 (2026-10-24): a PO supplied as page 1 of 6 is matched and merged when the file holds
everything the program needs; the **packet uses the ORIGINAL PDF pages, never the PNGs
sent to the model**. The model only ever sees a rasterised image of each page, so a
merge that embedded the raster would silently deliver a picture instead of the page.

These tests use `HttpVLM`: the real rasteriser, the real parser, the real
`instructor`/`openai` stack, and only the bytes on the wire are invented. The pages
carry real text, so a re-rendered page would lose its text layer and gain an image
XObject — both are asserted against.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf
import pytest
from pypdf import PdfReader
from test_robustness_harness import HttpVLM, extraction, line, make_cfg, run_sync, sets

PO_NO = "4500060001"
W_PO, W_DN, W_SI = 401.0, 503.0, 607.0
LINES = [line("1", "10", "BOLT"), line("2", "5", "NUT")]


def text_pdf(path: Path, width: float, texts: list[str], height: float = 842) -> Path:
    """One page per entry in `texts`, all `width` points wide (the fake's dispatch key)."""
    doc = pymupdf.open()
    for t in texts:
        page = doc.new_page(width=width, height=height)
        page.insert_text((40, 60), t, fontsize=12)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def images(page) -> list[str]:
    """The image XObjects of a page (a rasterised page has one; these sources have none)."""
    xo = (page.get("/Resources") or {}).get("/XObject") or {}
    return [k for k, v in xo.items() if v.get_object().get("/Subtype") == "/Image"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_stored_originals(ps, sha_in: dict[str, str]) -> None:
    """Every attached document is the uploaded file itself: same SHA, same stored bytes."""
    by_name = {d.original_filename: d for d in ps.documents}
    for name, sha in sha_in.items():
        doc = by_name[name]
        assert doc.sha256_hash == sha, f"{name}: the row records different bytes"
        assert _sha(Path(doc.stored_path)) == sha, f"{name}: the stored file was rewritten"


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    return make_cfg(tmp_path, monkeypatch)


@pytest.fixture()
def responses():
    return {
        W_PO: extraction("PO", PO_NO, None, LINES),
        W_DN: extraction("DN", "DN-D2-1", PO_NO, LINES),
        W_SI: extraction("SI", "INV-D2-1", PO_NO, LINES),
    }


def test_ws6_a_one_of_six_po_merges_from_the_original_pdf_pages(cfg, responses, monkeypatch):
    vlm = HttpVLM(monkeypatch, responses)
    inp = Path(cfg.paths.input_folder)
    po_text = f"PURCHASE ORDER {PO_NO}  Page 1 of 6"
    sources = {
        "PO": text_pdf(inp / "PO_page1.pdf", W_PO, [po_text]),
        "DN": text_pdf(inp / "DN.pdf", W_DN, ["DELIVERY NOTE line 1 BOLT 10"]),
        "SI": text_pdf(inp / "SI.pdf", W_SI, ["INVOICE INV-D2-1 line 1 BOLT 10"]),
    }
    sha_in = {path.name: _sha(path) for path in sources.values()}
    source_pages = {
        name: [(float(p.mediabox.width), p.extract_text()) for p in PdfReader(str(path)).pages]
        for name, path in sources.items()
    }
    assert source_pages["PO"] == [(W_PO, po_text)]

    result = run_sync()

    assert result["errors"] == 0, result
    (ps,) = sets(cfg)
    assert ps.status.value == "merged", ps.reconcile_reason
    assert ps.merged_output_path

    # 2. Each document is the uploaded file itself: same SHA, same stored bytes.
    _assert_stored_originals(ps, sha_in)

    # 3. Three supplied pages, in the configured legal order SI -> DN -> PO.
    packet = PdfReader(str(ps.merged_output_path))
    assert len(packet.pages) == 3
    assert [float(p.mediabox.width) for p in packet.pages] == [W_SI, W_DN, W_PO]

    # 4. Each packet page is the source page: same box, same text, in source order.
    for page, kind in zip(packet.pages, ("SI", "DN", "PO"), strict=True):
        ((width, text),) = source_pages[kind]
        assert float(page.mediabox.width) == width
        assert page.extract_text() == text
    # 5. No page carries an image: a rasterised page would.
    for page in packet.pages:
        assert images(page) == []

    # 6. The PNGs went to the model exactly once per document.
    assert dict(vlm.calls) == {W_PO: 1, W_DN: 1, W_SI: 1}


def test_ws6_a_two_page_partial_po_keeps_both_pages_in_order(cfg, responses, monkeypatch):
    vlm = HttpVLM(monkeypatch, responses)
    inp = Path(cfg.paths.input_folder)
    page_texts = [f"PURCHASE ORDER {PO_NO}  Page 1 of 6", f"PURCHASE ORDER {PO_NO}  Page 2 of 6"]
    sources = {
        "PO": text_pdf(inp / "PO_pages1_2.pdf", W_PO, page_texts),
        "DN": text_pdf(inp / "DN.pdf", W_DN, ["DELIVERY NOTE"]),
        "SI": text_pdf(inp / "SI.pdf", W_SI, ["INVOICE INV-D2-2"]),
    }
    sha_in = {path.name: _sha(path) for path in sources.values()}
    source_po = [p.extract_text() for p in PdfReader(str(sources["PO"])).pages]

    result = run_sync()

    assert result["errors"] == 0, result
    (ps,) = sets(cfg)
    assert ps.status.value == "merged", ps.reconcile_reason
    # Same check as 8.1: a partial PO is still merged from the uploaded bytes.
    _assert_stored_originals(ps, sha_in)
    packet = PdfReader(str(ps.merged_output_path))
    assert len(packet.pages) == 4
    assert [float(p.mediabox.width) for p in packet.pages] == [W_SI, W_DN, W_PO, W_PO]
    assert [p.extract_text() for p in packet.pages[-2:]] == source_po
    for page in packet.pages:
        assert images(page) == []
    assert dict(vlm.calls) == {W_PO: 1, W_DN: 1, W_SI: 1}
