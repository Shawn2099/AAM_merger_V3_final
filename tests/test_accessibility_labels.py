"""Batch 1 step 5: labels for the controls on the main pages, readable contrast,
and narrow-window rules. Checked against the shipped templates and stylesheet."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
CSS = (ROOT / "static" / "css" / "theme.css").read_text(encoding="utf-8")


def test_holding_page_row_controls_are_labelled():
    html = (TEMPLATES / "unclassified.html").read_text(encoding="utf-8")
    assert 'aria-label="Document type for {{ doc.original_filename }}"' in html
    assert 'aria-label="PO number for {{ doc.original_filename }}"' in html


def test_dashboard_search_and_status_filter_are_labelled():
    html = (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")
    assert 'aria-label="Search PO sets' in html
    assert '<label for="status-filter" class="sr-only">' in html


def test_nav_badge_meets_contrast_with_white_text():
    # White on #f59e0b is about 2.1:1; #b45309 is about 5:1.
    assert "background: #b45309;" in CSS
    assert "background: #f59e0b;" not in CSS


def test_tab_counts_are_darker_text():
    assert "color: #334155;" in CSS


def test_kpi_percentage_is_not_faded():
    html = (TEMPLATES / "_dashboard_kpi_grid.html").read_text(encoding="utf-8")
    assert "opacity:0.8" not in html


def test_narrow_window_rules_exist():
    assert "@media (max-width: 900px)" in CSS
    assert "@media (max-width: 600px)" in CSS
    assert ".table-container { overflow-x: auto; }" in CSS
