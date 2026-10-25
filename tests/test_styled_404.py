"""Batch 1 step 3: an unknown address is a page for browsers and JSON for API clients."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_browser_navigation_to_unknown_url_gets_the_styled_page():
    resp = TestClient(app).get("/nonsense", headers={"Accept": "text/html,application/xhtml+xml"})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert "Page not found" in resp.text
    assert "Back to dashboard" in resp.text


def test_api_client_gets_the_default_json_body():
    resp = TestClient(app).get("/nonsense", headers={"Accept": "application/json"})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_default_client_without_html_accept_gets_json():
    resp = TestClient(app).get("/nonsense")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_htmx_request_gets_json_even_when_html_is_accepted():
    resp = TestClient(app).get("/nonsense", headers={"Accept": "text/html", "HX-Request": "true"})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
