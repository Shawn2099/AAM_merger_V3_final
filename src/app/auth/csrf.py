"""CSRF protection — a synchronizer token tied to the server-side session.

Every authenticated write must carry the session's token, either as the
`X-CSRF-Token` header (HTMX/fetch) or as a hidden `csrf_token` form field
(native `application/x-www-form-urlencoded` forms).

`multipart/form-data` is deliberately exempt from parsing: the upload routes
already stream up to 100 MB, and parsing a second time just to read a field is
wasteful. A browser sending a cross-site multipart POST always sends
`Origin`/`Referer`, which `OriginGuardMiddleware` validates; a request with
neither header is a deliberate programmatic call.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


async def csrf_protect(request: Request) -> None:
    if request.method in _SAFE_METHODS:
        return
    # The middleware only marks a request as auth-enforced when authentication
    # is on and the session was resolved. With auth off (lab/tests) there is no
    # token and no state to protect.
    if not getattr(request.state, "auth_enforced", False):
        return

    expected = getattr(request.state, "csrf_token", None)
    supplied = request.headers.get("x-csrf-token")
    content_type = request.headers.get("content-type", "").casefold()

    if supplied is None:
        if content_type.startswith("application/x-www-form-urlencoded"):
            form = await request.form()
            value = form.get("csrf_token")
            supplied = str(value) if value is not None else None
        elif content_type.startswith("multipart/form-data"):
            # Same-origin browser form: OriginGuard already checked it.
            if request.headers.get("origin") or request.headers.get("referer"):
                return
            # No Origin and no header/field is a programmatic call. It still
            # needs the header if it has a session, which the check below enforces.
    if not expected or not supplied or not secrets.compare_digest(str(supplied), str(expected)):
        raise HTTPException(
            status_code=403,
            detail="Security token missing or expired. Reload the page and try again.",
        )
