"""Quarantine copies keep the operator's filename, made safe for Windows."""

import pytest

from app.services.quarantine import _safe_filename

BS = chr(92)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("scan.pdf", "scan.pdf"),
        ("PO 2360 final.pdf", "PO 2360 final.pdf"),
        ("a:b?.pdf", "a_b_.pdf"),  # ':' would write an alternate data stream
        ("dir/CON.pdf", "_CON.pdf"),  # reserved device stem
        ("nul", "_nul"),
        ("../../etc/x.pdf", "x.pdf"),  # no path parts
        (f"C:{BS}x{BS}y z.pdf", "y z.pdf"),
        ("report. ", "report"),  # Windows drops trailing dots and spaces
        ("   ", "FALL.pdf"),
        ("", "FALL.pdf"),
        (None, "FALL.pdf"),
    ],
)
def test_safe_filename(raw, expected):
    assert _safe_filename(raw, "FALL.pdf") == expected


def test_long_names_are_capped_and_keep_their_extension():
    out = _safe_filename("x" * 300 + ".pdf", "FALL.pdf")
    assert out.endswith(".pdf") and len(out) <= 100


def test_control_characters_are_replaced():
    assert _safe_filename("a\x00b\x1fc.pdf", "FALL.pdf") == "a_b_c.pdf"
