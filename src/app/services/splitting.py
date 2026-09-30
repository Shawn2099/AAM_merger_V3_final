"""Layer-1 multi-doc split — filesystem cutter (PLAN Rev 2 + Rev 3).

Pure function over a VLM response dict: no API key, no DB, no VLM call here.
The caller (extraction) owns the sterile parent row, the `combined/` move,
`split_completed_at`, and quarantining. This module only validates claimed
page ranges against pypdf ground truth and cuts child files into `input/`.

Filename contract: `<sha16>_p<i>.pdf` — 16-char SHA-256 prefix of the parent
bytes (WS2016 MAX_PATH), `p<i>` strictly numeric 1-based among NON-SKIP
components in listed order. SKIP consumes coverage but cuts no file.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pypdf import PdfReader, PdfWriter

_CHILD_RE = re.compile(r"^([0-9a-fA-F]{16})_p(\d+)\.pdf$", re.IGNORECASE)

# Hard-gate reason codes. Every failure quarantines the whole parent —
# never guess a fallback range.
REASONS = (
    "page_count_mismatch",
    "page_range_invalid",
    "page_range_overlap",
    "pages_unaccounted",
)


class SplitError(ValueError):
    """A claimed split failed validation. `reason` is one of REASONS."""

    def __init__(self, reason: str, message: str):
        if reason not in REASONS:
            raise ValueError(f"unknown split reason: {reason!r}")
        self.reason = reason
        super().__init__(message)


def parse_child_filename(name: str) -> tuple[str, int]:
    """Parse `<sha16>_p<i>.pdf` -> (sha16_lower, i). Strict: rejects all else.

    Guards the ingestion linkage: an employee hand-dropping a lookalike name
    must not attach to someone else's parent.
    """
    m = _CHILD_RE.match(name or "")
    if not m:
        raise ValueError(f"not a split-child filename: {name!r}")
    sha16, num = m.group(1).lower(), int(m.group(2))
    if num < 1:
        raise ValueError(f"not a split-child filename: {name!r}")
    return sha16, num


def split_combined(parent_path: str | Path, response: dict, cfg) -> list[Path]:
    """Validate `response` ranges and cut non-SKIP components into `input/`.

    Args:
        parent_path: the combined PDF on disk.
        response: `{"page_count": int, "components": [{"doc_type": ...,
            "page_start": int, "page_end": int}]}`. `doc_type` is informational
            here (PO/DN/SI/SKIP); contiguity/type-change splitting is the VLM
            prompt's job — this function cuts what it is given.
        cfg: app config (uses `paths.input_folder`).

    Returns:
        Child PDF paths in listed non-SKIP order. Deterministic names:
        re-running overwrites the same files, never duplicates.

    Raises:
        SplitError: with `.reason` one of REASONS. Caller quarantines.
    """
    parent = Path(parent_path)
    if not parent.exists():
        raise SplitError("page_range_invalid", f"parent not found: {parent}")
    data = parent.read_bytes()
    real_pages = len(PdfReader(str(parent)).pages)

    claimed_count = (response or {}).get("page_count")
    if claimed_count != real_pages:
        raise SplitError(
            "page_count_mismatch",
            f"VLM claimed page_count={claimed_count}, pypdf ground truth={real_pages}",
        )

    components = list((response or {}).get("components") or [])
    if not components:
        raise SplitError("pages_unaccounted", "no components claimed; all pages unaccounted")

    # Per-range shape check first (1..N, start <= end, ints).
    for c in components:
        try:
            start, end = int(c["page_start"]), int(c["page_end"])
        except (KeyError, TypeError, ValueError) as e:
            raise SplitError("page_range_invalid", f"non-integer range in {c!r}: {e}") from e
        if start < 1 or end > real_pages or start > end:
            raise SplitError(
                "page_range_invalid",
                f"range {start}-{end} outside 1..{real_pages}: {c!r}",
            )

    # Overlap: any page claimed twice.
    seen: set[int] = set()
    for c in components:
        for p in range(int(c["page_start"]), int(c["page_end"]) + 1):
            if p in seen:
                raise SplitError("page_range_overlap", f"page {p} claimed twice: {c!r}")
            seen.add(p)

    # Incomplete coverage: every page 1..N claimed (SKIP counts as claimed).
    missing = [p for p in range(1, real_pages + 1) if p not in seen]
    if missing:
        raise SplitError("pages_unaccounted", f"pages unclaimed: {missing}")

    # Cut non-SKIP components. Names are dense over kept components so a
    # SKIP in the middle does not leave a p-number gap on disk.
    input_dir = Path(cfg.paths.input_folder)
    input_dir.mkdir(parents=True, exist_ok=True)
    sha16 = hashlib.sha256(data).hexdigest()[:16]
    reader = PdfReader(str(parent))
    out: list[Path] = []
    kept = [c for c in components if str(c.get("doc_type", "")).upper() != "SKIP"]
    for i, c in enumerate(kept, 1):
        writer = PdfWriter()
        for p in range(int(c["page_start"]) - 1, int(c["page_end"])):
            writer.add_page(reader.pages[p])
        dest = input_dir / f"{sha16}_p{i}.pdf"
        with open(dest, "wb") as f:
            writer.write(f)
        out.append(dest)
    return out
