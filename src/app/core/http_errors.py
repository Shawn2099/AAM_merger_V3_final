"""Shared HTTP error shapes for browser and API clients.

The manual merger is used by two kinds of caller at once: the browser page
(XHR, which wants JSON it can render in place) and a plain form post (which
wants a readable page).  Keeping the decision here means the route, the
request-body guard and any future caller answer the same way.
"""

from __future__ import annotations

import html

from fastapi.responses import HTMLResponse, JSONResponse


#: Raised by the request-body guard while a handler is consuming the body.
class BodyTooLarge(Exception):
    """The request body crossed the configured byte cap."""


_NO_STORE = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


def wants_html(request) -> bool:
    """True when the caller is a browser form post rather than an API client."""
    return "text/html" in (request.headers.get("accept") or "").lower()


def error_payload(code: str, message: str, filename: str | None = None) -> dict:
    payload: dict[str, str] = {"detail": message, "code": code}
    if filename:
        payload["filename"] = filename
    return payload


def _html_page(status: int, message: str, filename: str | None) -> str:
    detail = f"<p class='muted'><strong>{html.escape(filename)}</strong></p>" if filename else ""
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>That didn't work - AAM Merger V3</title>"
        "<link rel='stylesheet' href='/static/css/theme.css'></head>"
        "<body><main class='container' style='max-width:720px;'>"
        "<div class='card'>"
        f"<span class='badge badge-quarantined'>HTTP {status}</span>"
        "<h1 class='card-title' style='margin:12px 0 8px;'>We couldn't merge those files</h1>"
        f"<p style='color:var(--text-main);'>{html.escape(message)}</p>{detail}"
        "<p style='margin-top:20px;'>"
        "<a class='btn btn-primary' href='/manual/merger'>Back and try again</a>"
        "</p></div></main></body></html>"
    )


def error_response(
    request,
    status: int,
    code: str,
    message: str,
    filename: str | None = None,
):
    """A JSON error for API/XHR callers, a small themed page for browsers."""
    if wants_html(request):
        return HTMLResponse(
            content=_html_page(status, message, filename),
            status_code=status,
            headers=_NO_STORE,
        )
    return JSONResponse(
        content=error_payload(code, message, filename),
        status_code=status,
        headers=_NO_STORE,
    )
