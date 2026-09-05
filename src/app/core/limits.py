"""Shared upload limits.

SPEC is silent on upload sizes; the human decision (2026-09-05) caps a
single upload at 100MB. Both browser upload paths (dashboard manual docs
and the isolated manual merger) read whole files into memory on a 2-core
host, so the cap is enforced on the byte stream before persistence.
Centralized here so tests can monkeypatch one symbol.
"""

#: Max bytes accepted for a single uploaded file (human decision 2026-09-05).
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
