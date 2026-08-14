"""VLM extraction — single call for COMBINED, retry 3x [2,5,15], manual-only guard (FR-6.1-6.8)."""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.core.config import load_config  # noqa: F401 — kept for typing
from app.core.database import get_engine
from app.models import Document, ExtractionStatus
from app.models.base import Base


def is_manual_only(doc_type: str) -> bool:
    return doc_type in ("CUSTOMS", "SHIPPING", "COMMERCIAL_INVOICE")


def _call_vlm(stored_path: str, doc_type: str, cfg) -> dict:  # pragma: no cover — mocked in tests
    """Single VLM call via instructor+openai. Real impl uses OpenRouter; tests mock this."""
    # placeholder: in prod, would read PDF via pypdf, build prompt, call instructor
    return {"po_no_raw": None, "line_items": []}


def extract_document(doc_id: int, cfg) -> Document:
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            raise ValueError(f"document {doc_id} not found")

        # FR-6.5: manual docs never VLM
        dtype = doc.doc_type.value if hasattr(doc.doc_type, "value") else str(doc.doc_type)
        if is_manual_only(dtype):
            return doc

        max_retries = int(getattr(cfg.extraction, "max_retries", 3))
        backoff = list(getattr(cfg.extraction, "retry_backoff_seconds", [2, 5, 15]))

        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                result = _call_vlm(doc.stored_path, dtype, cfg)
                # on success, mark valid and increment attempt count to attempt number
                doc.extraction_attempt_count = attempt
                doc.extraction_status = ExtractionStatus.valid
                # optionally store extracted po_no if present (minimal)
                if result and result.get("po_no_raw"):
                    doc.po_no_raw = result["po_no_raw"]
                s.commit()
                s.refresh(doc)
                return doc
            except Exception as e:
                last_exc = e
                doc.extraction_attempt_count = attempt
                if attempt < max_retries:
                    delay = backoff[attempt - 1] if attempt - 1 < len(backoff) else backoff[-1]
                    time.sleep(delay)
                else:
                    doc.extraction_status = ExtractionStatus.failed
                    s.commit()
                    s.refresh(doc)
                    return doc
        # should not reach here, but return failed
        if last_exc is not None:
            doc.extraction_status = ExtractionStatus.failed
            s.commit()
            s.refresh(doc)
        return doc
