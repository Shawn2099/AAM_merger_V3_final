"""Shared upload limits.

SPEC is silent on upload sizes; the human decision (2026-09-05) caps a
single upload at 100MB. Both browser upload paths (dashboard manual docs
and the isolated manual merger) read whole files into memory on a 2-core
host, so the cap is enforced on the byte stream before persistence.
Centralized here so tests can monkeypatch one symbol.
"""

#: Max bytes accepted for a single uploaded file (human decision 2026-09-05).
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

#: Max number of PDFs in one manual merge (audit P2-15). The endpoint reads
#: whole files into memory on a 2-core host; 20 is far above any real packet
#: (PO + DN + SI + customs + shipping) and bounds a hostile request. The total
#: bytes of one manual merge are capped at MAX_UPLOAD_BYTES as well.
MAX_MANUAL_MERGE_FILES = 20
