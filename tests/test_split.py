"""Layer-1 multi-doc split, REVISED contract (PLAN Rev 2 + Rev 3).

Filesystem split, NOT one-VLM-call children:
- Full extraction runs first; `COMBINED` is the trigger (no extra VLM call).
- `split_combined(parent_path, response, cfg)` validates the VLM's claimed
  page ranges against real pypdf ground truth and cuts child **files** into
  `input/` as `<sha16>_p<i>.pdf` (16-char SHA prefix, strictly numeric `i`).
- Child **rows** do not exist until the next sync re-extracts them fresh.
- SKIP consumes pages for coverage but cuts **no file**.
- Every check is a hard gate: page_count_mismatch / page_range_invalid /
  page_range_overlap / pages_unaccounted. Never guess a fallback range.
- No VLM / API key needed: the response is a plain dict, synthetic here.

Contract under test (services/splitting.py implements it):
- valid 3-way split -> 3 files in input/, real pypdf page slices
- SKIP -> coverage without a file
- count mismatch / OOB / start>end / overlap / gap -> SplitError(reason)
- child filenames parse strictly; non-numeric suffix rejected
- re-split is deterministic (same names, overwritten, never duplicated)
"""

from hashlib import sha256
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from app.core.config import load_config


def _cfg(tmp_path, name="split.db"):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / name)
    cfg.paths.input_folder = str(tmp_path / "input")
    cfg.paths.output_folder = str(tmp_path / "output")
    cfg.paths.quarantine_folder = str(tmp_path / "quarantine")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    cfg.paths.combined_folder = str(tmp_path / "combined")
    cfg.paths.log_folder = str(tmp_path / "logs")
    for p in (
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.combined_folder,
        cfg.paths.log_folder,
    ):
        Path(p).mkdir(parents=True, exist_ok=True)
    return cfg


def _pdf(path: Path, pages: int):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=100, height=100)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        w.write(f)


def _comp(dtype, pstart, pend):
    return {"doc_type": dtype, "page_start": pstart, "page_end": pend}


def _resp(*components, page_count=None, n_pages=3):
    return {
        "page_count": n_pages if page_count is None else page_count,
        "components": list(components),
    }


def test_valid_three_way_split_cuts_files(tmp_path):
    from app.services.splitting import split_combined

    cfg = _cfg(tmp_path, "s1.db")
    parent = tmp_path / "parent.pdf"
    _pdf(parent, 3)
    sha16 = sha256(parent.read_bytes()).hexdigest()[:16]
    resp = _resp(_comp("PO", 1, 1), _comp("DN", 2, 2), _comp("SI", 3, 3))

    out = split_combined(parent, resp, cfg)

    assert [p.name for p in out] == [f"{sha16}_p1.pdf", f"{sha16}_p2.pdf", f"{sha16}_p3.pdf"]
    for p in out:
        assert p.parent == Path(cfg.paths.input_folder)
        assert p.exists()
        assert len(PdfReader(str(p)).pages) == 1


def test_multipage_component_keeps_slice(tmp_path):
    from app.services.splitting import split_combined

    cfg = _cfg(tmp_path, "s1b.db")
    parent = tmp_path / "parent.pdf"
    _pdf(parent, 4)
    resp = _resp(_comp("PO", 1, 2), _comp("DN", 3, 4), n_pages=4)

    out = split_combined(parent, resp, cfg)

    assert len(out) == 2
    assert len(PdfReader(str(out[0])).pages) == 2
    assert len(PdfReader(str(out[1])).pages) == 2


def test_page_count_mismatch_is_hard_gate(tmp_path):
    from app.services.splitting import SplitError, split_combined

    cfg = _cfg(tmp_path, "s2.db")
    parent = tmp_path / "parent.pdf"
    _pdf(parent, 3)
    resp = _resp(_comp("PO", 1, 1), _comp("DN", 2, 2), _comp("SI", 3, 3), page_count=99)

    with pytest.raises(SplitError) as ei:
        split_combined(parent, resp, cfg)
    assert ei.value.reason == "page_count_mismatch"
    assert list(Path(cfg.paths.input_folder).glob("*.pdf")) == []


def test_out_of_bounds_is_hard_gate(tmp_path):
    from app.services.splitting import SplitError, split_combined

    cfg = _cfg(tmp_path, "s3.db")
    parent = tmp_path / "parent.pdf"
    _pdf(parent, 2)
    resp = _resp(_comp("PO", 1, 9), n_pages=2)

    with pytest.raises(SplitError) as ei:
        split_combined(parent, resp, cfg)
    assert ei.value.reason == "page_range_invalid"


def test_start_after_end_is_hard_gate(tmp_path):
    from app.services.splitting import SplitError, split_combined

    cfg = _cfg(tmp_path, "s4.db")
    parent = tmp_path / "parent.pdf"
    _pdf(parent, 2)
    resp = _resp(_comp("PO", 2, 1), _comp("DN", 2, 2), n_pages=2)

    with pytest.raises(SplitError) as ei:
        split_combined(parent, resp, cfg)
    assert ei.value.reason == "page_range_invalid"


def test_overlap_is_hard_gate(tmp_path):
    from app.services.splitting import SplitError, split_combined

    cfg = _cfg(tmp_path, "s5.db")
    parent = tmp_path / "parent.pdf"
    _pdf(parent, 3)
    resp = _resp(_comp("PO", 1, 2), _comp("DN", 2, 3))

    with pytest.raises(SplitError) as ei:
        split_combined(parent, resp, cfg)
    assert ei.value.reason == "page_range_overlap"


def test_gap_is_hard_gate(tmp_path):
    from app.services.splitting import SplitError, split_combined

    cfg = _cfg(tmp_path, "s6.db")
    parent = tmp_path / "parent.pdf"
    _pdf(parent, 3)
    resp = _resp(_comp("PO", 1, 1), _comp("SI", 3, 3))  # page 2 unclaimed

    with pytest.raises(SplitError) as ei:
        split_combined(parent, resp, cfg)
    assert ei.value.reason == "pages_unaccounted"


def test_skip_consumes_coverage_but_cuts_no_file(tmp_path):
    from app.services.splitting import split_combined

    cfg = _cfg(tmp_path, "s7.db")
    parent = tmp_path / "parent.pdf"
    _pdf(parent, 2)
    resp = _resp(_comp("PO", 1, 1), _comp("SKIP", 2, 2), n_pages=2)

    out = split_combined(parent, resp, cfg)

    assert len(out) == 1
    assert len(PdfReader(str(out[0])).pages) == 1
    assert len(list(Path(cfg.paths.input_folder).glob("*.pdf"))) == 1


def test_mid_run_type_change_is_two_components(tmp_path):
    from app.services.splitting import split_combined

    cfg = _cfg(tmp_path, "s8.db")
    parent = tmp_path / "parent.pdf"
    _pdf(parent, 3)
    # PO run interrupted by DN on page 2, PO resumes page 3: three runs.
    resp = _resp(_comp("PO", 1, 1), _comp("DN", 2, 2), _comp("PO", 3, 3))

    out = split_combined(parent, resp, cfg)

    assert len(out) == 3


def test_child_filename_parses_strictly():
    from app.services.splitting import parse_child_filename

    sha, idx = parse_child_filename("abcdef1234567890_p3.pdf")
    assert sha == "abcdef1234567890"
    assert idx == 3
    for bad in ("parent.pdf", "abcdef_pX.pdf", "abcdef_.pdf", "abcdef1234567890_p.pdf", ""):
        with pytest.raises(ValueError):
            parse_child_filename(bad)


def test_resplit_is_deterministic(tmp_path):
    from app.services.splitting import split_combined

    cfg = _cfg(tmp_path, "s9.db")
    parent = tmp_path / "parent.pdf"
    _pdf(parent, 3)
    resp = _resp(_comp("PO", 1, 1), _comp("DN", 2, 2), _comp("SI", 3, 3))

    first = split_combined(parent, resp, cfg)
    second = split_combined(parent, resp, cfg)  # crash-recovery re-derive

    assert [p.name for p in first] == [p.name for p in second]
    assert len(list(Path(cfg.paths.input_folder).glob("*.pdf"))) == 3
