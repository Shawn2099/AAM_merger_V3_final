"""A matrix for the uncommitted manual-merger, config and name-validation work.

This complements the existing manual-merger suite with:

* a table of download names accepted and refused by the server;
* configuration bounds and the staging-folder separation rule;
* merge-order, encrypted-input, corrupt-input, page-cap and zero-page cases.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from app.core.config import AppConfig, ManualMergeConfig
from app.services.manual_merge import (
    INVALID_PDF_NAME_CHARS,
    ManualMergeError,
    manual_merge,
    validate_pdf_name,
)

# ---------------------------------------------------------------------------
# Download-name matrix (server rules, mirrored by the browser).
# ---------------------------------------------------------------------------

VALID_NAMES = [
    ("plain", "statement.pdf", "statement.pdf"),
    ("spaces", "Statement 2026.pdf", "Statement 2026.pdf"),
    ("unicode", "résumé.pdf", "résumé.pdf"),
    ("parentheses", "a (1).pdf", "a (1).pdf"),
    ("multiple dots", "a.b.pdf", "a.b.pdf"),
    ("missing suffix", "statement", "statement.pdf"),
    ("uppercase suffix", "statement.PDF", "statement.pdf"),
    ("max length", "a" * 100, "a" * 100 + ".pdf"),
]


@pytest.mark.parametrize(("label", "raw", "expected"), VALID_NAMES, ids=[v[0] for v in VALID_NAMES])
def test_valid_download_names(label, raw, expected):
    assert validate_pdf_name(raw) == expected


INVALID_NAMES = [
    ("empty", ""),
    ("spaces only", "   "),
    ("suffix only", ".pdf"),
    ("leading dot", ".hidden.pdf"),
    ("trailing dot", "statement..pdf"),
    ("trailing space", "statement .pdf"),
    ("forward slash", "folder/statement.pdf"),
    ("backslash", "folder\\statement.pdf"),
    ("double quote", 'state"ment.pdf'),
    ("colon", "state:ment.pdf"),
    ("question", "state?ment.pdf"),
    ("pipe", "state|ment.pdf"),
    ("star", "state*ment.pdf"),
    ("lt", "state<ment.pdf"),
    ("gt", "state>ment.pdf"),
    ("control", "state\x01ment.pdf"),
    ("tab", "state\tment.pdf"),
    ("reserved CON", "CON.pdf"),
    ("reserved COM1", "COM1.pdf"),
    ("reserved LPT9", "LPT9.pdf"),
    ("reserved dotted", "con.report.pdf"),
    ("too long", "a" * 101 + ".pdf"),
]


@pytest.mark.parametrize(
    "raw", [case[1] for case in INVALID_NAMES], ids=[c[0] for c in INVALID_NAMES]
)
def test_invalid_download_names_are_refused(raw):
    with pytest.raises(ManualMergeError) as excinfo:
        validate_pdf_name(raw)
    assert excinfo.value.code == "bad_filename"


def test_missing_name_is_refused():
    with pytest.raises(ManualMergeError) as excinfo:
        validate_pdf_name(None)
    assert excinfo.value.code == "bad_filename"


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=140))
def test_a_validated_name_is_always_a_safe_pdf_name(raw):
    """If validation returns, the name is one Windows can actually save."""
    try:
        name = validate_pdf_name(raw)
    except ManualMergeError:
        return

    assert name.endswith(".pdf")
    stem = name[:-4]
    assert 0 < len(stem) <= 100
    assert not any(ch in INVALID_PDF_NAME_CHARS for ch in stem)
    assert stem[0] not in (".", " ")
    assert stem[-1] not in (".", " ")
    assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in stem)


# ---------------------------------------------------------------------------
# ManualMergeConfig bounds and request-size arithmetic.
# ---------------------------------------------------------------------------


def test_manual_merge_defaults_and_request_arithmetic():
    mm = ManualMergeConfig()
    assert mm.max_files == 20
    assert mm.max_total_mb == 100
    assert mm.max_pages == 5000
    assert mm.temp_folder is None
    assert mm.sweep_after_hours == 6
    assert mm.sweep_on_startup is True
    assert mm.max_total_bytes == 100 * 1024 * 1024
    assert mm.max_request_bytes == 100 * 1024 * 1024 + 2 * 1024 * 1024


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_files": 0},
        {"max_files": 101},
        {"max_total_mb": 0},
        {"max_total_mb": 1025},
        {"max_pages": 0},
        {"max_pages": 100_001},
        {"sweep_after_hours": 0},
        {"sweep_after_hours": 721},
    ],
)
def test_manual_merge_bounds_are_enforced(kwargs):
    with pytest.raises(ValidationError):
        ManualMergeConfig(**kwargs)


def _app_config(tmp_path, temp_folder):
    return AppConfig.model_validate(
        {
            "paths": {
                "input_folder": str(tmp_path / "input"),
                "output_folder": str(tmp_path / "output"),
                "quarantine_folder": str(tmp_path / "quarantine"),
                "stored_documents_folder": str(tmp_path / "stored"),
                "database_path": str(tmp_path / "aam.db"),
                "log_folder": str(tmp_path / "logs"),
            },
            "manual_merge": {"temp_folder": temp_folder},
        }
    )


def test_staging_folder_may_be_a_sibling(tmp_path):
    cfg = _app_config(tmp_path, str(tmp_path / "manual_merge_tmp"))
    assert cfg.manual_merge.temp_folder == tmp_path / "manual_merge_tmp"


def test_staging_folder_inside_a_pipeline_folder_is_refused(tmp_path):
    with pytest.raises(ValidationError, match="must be separate"):
        _app_config(tmp_path, str(tmp_path / "input" / "staging"))


def test_staging_folder_containing_a_pipeline_folder_is_refused(tmp_path):
    with pytest.raises(ValidationError, match="must be separate"):
        _app_config(tmp_path, str(tmp_path))


def test_staging_folder_that_is_a_file_is_refused(tmp_path):
    target = tmp_path / "not_a_folder"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ValidationError, match="not a directory"):
        _app_config(tmp_path, str(target))


def test_staging_folder_default_is_allowed(tmp_path):
    cfg = _app_config(tmp_path, None)
    assert cfg.manual_merge.temp_folder is None


# ---------------------------------------------------------------------------
# Merge behaviour matrix.
# ---------------------------------------------------------------------------


def _pdf(path, width=100, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=200)
    writer.write(str(path))
    return path


def test_order_is_respected_and_pages_are_combined(tmp_path):
    a = _pdf(tmp_path / "a.pdf", width=100)
    b = _pdf(tmp_path / "b.pdf", width=200)
    out = tmp_path / "out.pdf"
    manual_merge([a, b], [1, 0], out)
    widths = [float(page.mediabox.width) for page in PdfReader(str(out)).pages]
    assert widths == [200, 100]


@pytest.mark.parametrize(
    "order",
    [
        [0],  # too short
        [0, 0],  # duplicate
        [0, 2],  # out of range
        [1, 0],  # fine (control)
    ],
)
def test_order_validation(tmp_path, order):
    a = _pdf(tmp_path / "a.pdf", width=100)
    b = _pdf(tmp_path / "b.pdf", width=200)
    if order == [1, 0]:
        manual_merge([a, b], order, tmp_path / "ok.pdf")
        return
    with pytest.raises(ManualMergeError) as excinfo:
        manual_merge([a, b], order, tmp_path / "bad.pdf")
    assert excinfo.value.code == "bad_order"


def test_empty_input_is_refused(tmp_path):
    with pytest.raises(ManualMergeError) as excinfo:
        manual_merge([], [], tmp_path / "out.pdf")
    assert excinfo.value.code == "no_files"


def test_missing_file_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        manual_merge([tmp_path / "missing.pdf"], [0], tmp_path / "out.pdf")


def test_encrypted_pdf_is_refused(tmp_path):
    source = tmp_path / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.encrypt("secret")
    writer.write(str(source))

    with pytest.raises(ManualMergeError) as excinfo:
        manual_merge([source], [0], tmp_path / "out.pdf")
    assert excinfo.value.code == "encrypted"


def test_corrupt_pdf_is_refused(tmp_path):
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"%PDF-1.7\nnot really a pdf")
    with pytest.raises(ManualMergeError) as excinfo:
        manual_merge([source], [0], tmp_path / "out.pdf")
    assert excinfo.value.code == "unreadable"


def test_zero_page_pdf_is_refused(tmp_path):
    source = tmp_path / "empty.pdf"
    PdfWriter().write(str(source))
    with pytest.raises(ManualMergeError) as excinfo:
        manual_merge([source], [0], tmp_path / "out.pdf")
    assert excinfo.value.code == "no_pages"


def test_page_cap_is_refused(tmp_path):
    source = _pdf(tmp_path / "two.pdf", pages=2)
    with pytest.raises(ManualMergeError) as excinfo:
        manual_merge([source], [0], tmp_path / "out.pdf", max_pages=1)
    assert excinfo.value.code == "too_many_pages"


def test_every_page_is_kept(tmp_path):
    source = _pdf(tmp_path / "three.pdf", pages=3)
    out = tmp_path / "out.pdf"
    manual_merge([source], [0], out)
    assert len(PdfReader(str(out)).pages) == 3
