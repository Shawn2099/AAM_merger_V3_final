"""The dashboard must work on a LAN with no internet access.

Users open the page from their own PCs. HTMX and Alpine.js used to be loaded
from public CDNs, so in an office that blocks or lacks internet the page
rendered but no button worked: every action is an HTMX attribute.

The libraries are vendored under static/js and served by this app.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_scripts_are_served_locally():
    html = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

    assert "/static/js/htmx.min.js" in html
    assert "/static/js/alpine.min.js" in html
    assert "unpkg.com" not in html
    assert "cdn.jsdelivr.net" not in html


def test_vendored_scripts_are_present():
    for name in ("htmx.min.js", "alpine.min.js"):
        path = ROOT / "static" / "js" / name
        assert path.exists(), f"missing vendored asset {name}"
        assert path.stat().st_size > 10_000, f"{name} looks empty"
