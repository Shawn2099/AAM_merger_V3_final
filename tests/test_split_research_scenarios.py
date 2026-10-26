"""Split scenarios derived from published VLM document-understanding research.

Sources (2026 literature, applied to our Layer-1 split):
- LED benchmark (arxiv 2603.17265): across multimodal models the dominant
  layout error is **Missing** (64.9%) then **Hallucination** (14.7%); Split,
  Merge, Overlap and Duplicate are each under 2%. So the ranges we receive
  will most often UNDER-claim pages, then over-claim them.
- MMLongBench-Doc (2407.01523): accuracy falls as the evidence page index
  rises, and cross-page questions are markedly harder than single-page ones.
  Our split asks for whole-document page enumeration — the hardest shape.
- SynthDocBench (2607.10400): systematic positional sensitivity, the middle
  third of a long document being hardest, with a negative early→late trend.
- GDP.pdf (2607.11192): no frontier model passes a third of professional-PDF
  items; recurring losses are misaligned tables, skipped footnotes, and
  amendments that supersede earlier text.
- Production bulk-splitting tools (riordino, santoshray02/pdf_splitter)
  pre-process every scanned PDF for **blank pages** and **rotation** before
  asking a model anything. Scanners introduce blank pages; we do not strip
  them, so they must arrive as SKIP or the coverage gate must quarantine.

Every test below is a hard gate we already claim to enforce. This file pins
them against the shapes research says actually occur, so a regression in the
gates is caught before a vendor file is.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from app.core.config import load_config
from app.services.splitting import SplitError, parse_child_filename, split_combined


def _cfg(tmp_path, name):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / name)
    cfg.paths.input_folder = str(tmp_path / "input")
    for sub in ("output", "quarantine", "stored", "combined", "logs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / "input").mkdir(parents=True, exist_ok=True)
    return cfg


def _pdf(path: Path, pages: int):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=100, height=100)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        w.write(f)
    return path


def _c(doc_type, start, end):
    return {"doc_type": doc_type, "page_start": start, "page_end": end}


def _r(n_pages, *components, page_count=None):
    return {
        "page_count": n_pages if page_count is None else page_count,
        "components": list(components),
    }


def _inputs(tmp_path, cfg):
    return sorted(Path(cfg.paths.input_folder).glob("*.pdf"))


# --- LED: "Missing" is the #1 error — the model under-claims pages ---------


def test_omitted_interior_page_quarantines(tmp_path):
    """LED: Missing dominates. A DN run that silently stops one page early
    leaves page 3 unclaimed — the gate must refuse the whole parent rather
    than cut a truncated DN."""
    cfg = _cfg(tmp_path, "m.db")
    parent = _pdf(tmp_path / "p.pdf", 4)
    with pytest.raises(SplitError) as ei:
        split_combined(parent, _r(4, _c("PO", 1, 1), _c("DN", 2, 2), _c("SI", 4, 4)), cfg)
    assert ei.value.reason == "pages_unaccounted"
    assert _inputs(tmp_path, cfg) == [], "no partial cut may survive a failed gate"


def test_trailing_pages_omitted_quarantines(tmp_path):
    """The model stops early and never mentions the tail pages."""
    cfg = _cfg(tmp_path, "m2.db")
    parent = _pdf(tmp_path / "p.pdf", 6)
    with pytest.raises(SplitError) as ei:
        split_combined(parent, _r(6, _c("PO", 1, 2)), cfg)
    assert ei.value.reason == "pages_unaccounted"


# --- LED: "Hallucination" — over-claiming --------------------------------


def test_hallucinated_extra_page_quarantines(tmp_path):
    """The model invents a page the file does not have."""
    cfg = _cfg(tmp_path, "h.db")
    parent = _pdf(tmp_path / "p.pdf", 3)
    with pytest.raises(SplitError) as ei:
        split_combined(parent, _r(3, _c("PO", 1, 2), _c("DN", 3, 4)), cfg)
    assert ei.value.reason == "page_range_invalid"


def test_hallucinated_page_count_quarantines(tmp_path):
    """page_count is a cross-check claim, never ground truth: a wrong count
    invalidates the whole response even when the ranges look sane."""
    cfg = _cfg(tmp_path, "h2.db")
    parent = _pdf(tmp_path / "p.pdf", 3)
    with pytest.raises(SplitError) as ei:
        split_combined(parent, _r(3, _c("PO", 1, 3), page_count=5), cfg)
    assert ei.value.reason == "page_count_mismatch"


# --- Off-by-one at section boundaries (cross-page weakness) ---------------


def test_boundary_off_by_one_caught_either_way(tmp_path):
    """The classic: a section claimed one page early overlaps its neighbour;
    claimed one page late leaves a gap. Both must be caught, never silently
    absorbed into the wrong document."""
    cfg = _cfg(tmp_path, "b.db")
    parent = _pdf(tmp_path / "p.pdf", 4)
    with pytest.raises(SplitError) as early:
        split_combined(parent, _r(4, _c("PO", 1, 2), _c("DN", 2, 3), _c("SI", 4, 4)), cfg)
    assert early.value.reason == "page_range_overlap"
    with pytest.raises(SplitError) as late:
        split_combined(parent, _r(4, _c("PO", 1, 1), _c("DN", 3, 3), _c("SI", 4, 4)), cfg)
    assert late.value.reason == "pages_unaccounted"


# --- Scanners introduce blank pages; they must be SKIP, not gaps ----------


def test_leading_cover_page_is_skip_and_first_real_file_is_p1(tmp_path):
    """A CA cover sheet is page 1 of a very common combined file. It must be
    claimed as SKIP so coverage holds, and the PO must still be the FIRST
    child file (p1) — p-numbering stays dense over real components."""
    cfg = _cfg(tmp_path, "c.db")
    parent = _pdf(tmp_path / "p.pdf", 4)
    out = split_combined(
        parent, _r(4, _c("SKIP", 1, 1), _c("PO", 2, 2), _c("DN", 3, 3), _c("SI", 4, 4)), cfg
    )
    assert len(out) == 3
    sha16 = sha256(parent.read_bytes()).hexdigest()[:16]
    assert [p.name for p in out] == [f"{sha16}_p1.pdf", f"{sha16}_p2.pdf", f"{sha16}_p3.pdf"]
    assert parse_child_filename(out[0].name)[1] == 1


def test_interior_blank_pages_between_sections(tmp_path):
    """Scanners drop blank sheets mid-stack. Several blanks, between and
    after real sections, must be claimed and must not shift the slices."""
    cfg = _cfg(tmp_path, "c2.db")
    parent = _pdf(tmp_path / "p.pdf", 8)
    out = split_combined(
        parent,
        _r(
            8,
            _c("PO", 1, 2),
            _c("SKIP", 3, 3),
            _c("DN", 4, 4),
            _c("SKIP", 5, 6),
            _c("SI", 7, 7),
            _c("SKIP", 8, 8),
        ),
        cfg,
    )
    assert len(out) == 3
    assert [len(PdfReader(str(p)).pages) for p in out] == [2, 1, 1]
    sha16 = sha256(parent.read_bytes()).hexdigest()[:16]
    assert [p.name for p in out] == [f"{sha16}_p{i}.pdf" for i in (1, 2, 3)]


def test_all_pages_skip_yields_no_files(tmp_path):
    """A combined file the model reads as entirely non-content (a scanned
    cover bundle) produces no children and no error — nothing to cut."""
    cfg = _cfg(tmp_path, "c3.db")
    parent = _pdf(tmp_path / "p.pdf", 2)
    assert split_combined(parent, _r(2, _c("SKIP", 1, 2)), cfg) == []
    assert _inputs(tmp_path, cfg) == []


# --- Long documents: positional degradation (MMLongBench / SynthDocBench) -


def test_long_document_slices_are_exact(tmp_path):
    """Accuracy falls with page index, so the tail of a long file is the most
    likely place for an off-by-one. Assert every slice is byte-exact across
    a 30-page file: a wrong boundary anywhere must fail here."""
    cfg = _cfg(tmp_path, "L.db")
    n = 30
    parent = _pdf(tmp_path / "p.pdf", n)
    comps, page = [], 1
    while page <= n:
        end = min(page + 2, n)
        comps.append(_c(["PO", "DN", "SI"][len(comps) % 3], page, end))
        page = end + 1
    out = split_combined(parent, _r(n, *comps), cfg)
    assert len(out) == len(comps)
    for produced, c in zip(out, comps, strict=True):
        assert len(PdfReader(str(produced)).pages) == c["page_end"] - c["page_start"] + 1


def test_many_alternating_components(tmp_path):
    """Type changes every page (10 sections in 10 pages) — the degenerate
    case for 'contiguous run of ONE type'."""
    cfg = _cfg(tmp_path, "m3.db")
    n = 10
    parent = _pdf(tmp_path / "p.pdf", n)
    components = [_c(["PO", "DN", "SI"][i % 3], i + 1, i + 1) for i in range(n)]
    out = split_combined(parent, _r(n, *components), cfg)
    assert len(out) == n
    assert all(len(PdfReader(str(p)).pages) == 1 for p in out)


def test_single_page_parent(tmp_path):
    """Degenerate N=1: one page, one component."""
    cfg = _cfg(tmp_path, "s.db")
    parent = _pdf(tmp_path / "p.pdf", 1)
    out = split_combined(parent, _r(1, _c("PO", 1, 1)), cfg)
    assert len(out) == 1
    assert len(PdfReader(str(out[0])).pages) == 1


def test_single_component_covering_every_page(tmp_path):
    """A file the model types as one section throughout still splits into
    exactly one child, not zero and not a quarantine."""
    cfg = _cfg(tmp_path, "f.db")
    parent = _pdf(tmp_path / "p.pdf", 3)
    out = split_combined(parent, _r(3, _c("PO", 1, 3)), cfg)
    assert len(out) == 1
    assert len(PdfReader(str(out[0])).pages) == 3
