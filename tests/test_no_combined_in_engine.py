"""Layer-2 must never name the COMBINED document type.

`DocType.COMBINED` survives in the enum as a Layer-1-only parent marker
(packaging fact about the input PDF). Every set-building query keys off the
generic hierarchy columns instead. If this test fails, combined-awareness has
leaked back into the engine — delete it there, do not allowlist it here.

Allowed: `models.py` (the enum itself), `extraction.py` (Layer 1, owns the
split). Test fixtures under `tests/` are intentionally NOT scanned: they must
be free to construct COMBINED rows to prove Layer 2 ignores them.

The single exception is an explicit rejection guard: a line carrying the
`combined-rejected` marker refuses the type (422) instead of handling it.
Any new COMBINED mention must either delete the handling or carry the marker.
"""

from pathlib import Path

_ALLOWED = {"models.py", "extraction.py"}


def test_no_combined_references_in_engine():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for base in (root / "src", root / "templates"):
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix not in {".py", ".html", ".js"}:
                continue
            if p.name in _ALLOWED:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if "COMBINED" in line and "combined-rejected" not in line:
                    offenders.append(f"{p.relative_to(root)}:{i}: {line.strip()[:100]}")
    assert not offenders, "COMBINED leaked into Layer 2:\n" + "\n".join(offenders)
