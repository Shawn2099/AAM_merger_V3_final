"""VLM extraction — Luna native PDF via instructor+OpenRouter, no mocks in prod (FR-6.1-6.8).

Secrets: OPENROUTER_API_KEY only via env/.env (fail-closed, never logged).
Model: cfg.vlm.model (openai/gpt-5.6-luna) from config.yaml, never hardcoded.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Literal

import instructor
from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import Document, ExtractionStatus
from app.models.base import Base

# --- VLM prompt — copied from V2 src/engine/extraction.py _PAGE_PROMPT (few-shots for SIV/GDN/PO) ---
_PAGE_PROMPT = (
    "Extract structured data from this scanned commercial document (PO, SI, or DN).\n\n"
    "CLASSIFY document_type as exactly one of: PO, SI, DN, SKIP, UNKNOWN.\n"
    "  PO=Purchase Order, SI=Sales/Tax Invoice, DN=Delivery Note/Packing List.\n"
    "  SKIP=covers/T&C/blanks, UNKNOWN=unreadable.\n\n"
    "EXTRACT HEADER FIELDS:\n"
    "  document_number: primary reference (PO No, Invoice No, DN No).\n"
    "  po_reference: PO number referenced by SI/DN. Null for PO pages.\n"
    "  vendor_name: supplier company name.\n\n"
    "EXTRACT LINE ITEMS — only real products with quantities, NOT summary rows:\n"
    "  line_item_no: exact printed row number (Item No, Sl No, #, etc).\n"
    "  item_code: product code/SKU/part number. Extract from columns OR embedded in description (P/N:, MFR:).\n"
    "  description: COMPLETE product description, do not truncate.\n"
    "  uom: unit of measure (EA, BOX, KG, SET, PCS). Null if not visible.\n"
    "  quantity: plain integer (5 not 5.0). Only multiply if fractional (1.5kg→1500).\n"
    "  unit_price: integer x1000 (12.50→12500). Null if not shown.\n"
    "  total_price: integer x1000 (62.50→62500). Null if not shown.\n\n"
    "EXCLUDE: subtotal, VAT, tax, total, amount-in-words, payment terms, signatures.\n"
    "A valid line item MUST have a specific product description AND quantity > 0.\n\n"
    "RATE CONFIDENCE: high (clear), medium (minor blur), low (ambiguous/missing).\n\n"
    "FEW-SHOT EXAMPLES:\n\n"
    "Example 1 — Sales Invoice (SI):\n"
    '{"document_type": "SI", "document_number": "SIV-ARS-26-4005", "po_reference": "210851",\n'
    ' "vendor_name": "IBRAHIM ALI ALSHAB TRADING EST.", "confidence": "high",\n'
    ' "line_items": [\n'
    '   {"line_item_no": "12", "item_code": "33818", "description": "NUT, HEX 9/16 IN-12 UNC GRADE B YELLOW ZINC PLATED",\n'
    '    "quantity": 50, "unit_price": 350, "total_price": 17500},\n'
    '   {"line_item_no": "13", "item_code": "33802", "description": "WASHER, FLAT SAE 5/8 IN PLAIN CS",\n'
    '    "quantity": 50, "unit_price": 190, "total_price": 9500}\n'
    " ]}\n\n"
    "Example 2 — Purchase Order (PO):\n"
    '{"document_type": "PO", "document_number": "210851", "po_reference": null,\n'
    ' "vendor_name": "FASTENAL COMPANY", "confidence": "high",\n'
    ' "line_items": [\n'
    '   {"line_item_no": "1", "item_code": "33630", "description": "WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS",\n'
    '    "quantity": 50, "unit_price": 120, "total_price": 6000},\n'
    '   {"line_item_no": "2", "item_code": "33632", "description": "NUT, HEX 1/4 IN-20 UNC GRADE B YELLOW ZINC PLATED",\n'
    '    "quantity": 50, "unit_price": 120, "total_price": 6000}\n'
    " ]}\n\n"
    "Example 3 — Delivery Note (DN):\n"
    '{"document_type": "DN", "document_number": "GDN-ARS-26-4619", "po_reference": "210851",\n'
    ' "vendor_name": "IBRAHIM ALI ALSHAB TRADING EST.", "confidence": "medium",\n'
    ' "line_items": [\n'
    '   {"line_item_no": "1", "item_code": "184799", "description": "WASHER, LOCK, 3/8\\" - MFG: FLY",\n'
    '    "quantity": 50, "unit_price": 1000, "total_price": 50000},\n'
    '   {"line_item_no": "2", "item_code": "LP-019-0-NP526-01-4F", "description": "COUPLING, SELF SEALING, TYPE: LP-019, W/ BREAKAWAY FUNCTION - MFG: WALTHER PRAE - P/N: LP-019-0-NP526-01-4F-Z75",\n'
    '    "quantity": 1, "unit_price": 4300000, "total_price": 4300000}\n'
    " ]}\n\n"
    "Return ONLY a valid JSON object — no explanation, no markdown.\n"
)


class _VLMLineItem(BaseModel):
    line_item_no: str | None = Field(None, description="Printed row number")
    item_code: str | None = None
    description: str | None = None
    uom: str | None = None
    quantity: int | None = None
    unit_price: int | None = None
    total_price: int | None = None


class _VLMPageExtraction(BaseModel):
    document_type: Literal["PO", "SI", "DN", "SKIP", "UNKNOWN"] = Field(...)
    document_number: str | None = None
    po_reference: str | None = None
    vendor_name: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    line_items: list[_VLMLineItem] = Field(default_factory=list)


def is_manual_only(doc_type: str) -> bool:
    return doc_type in ("CUSTOMS", "SHIPPING", "COMMERCIAL_INVOICE")


def _call_vlm(stored_path: str, doc_type: str, cfg) -> dict:
    """Single Luna native PDF call via instructor (no rasterization, FR-6.1). Fail-closed on missing secrets."""
    # --- fail-closed secret handling (never hardcoded, never logged) ---
    # Load .env if present (python-dotenv) so os.getenv sees it without shell export
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except Exception:
        pass
    api_key_env = getattr(cfg.vlm, "api_key_env_var", "OPENROUTER_API_KEY") or "OPENROUTER_API_KEY"
    api_key = os.getenv(api_key_env) or os.getenv("OPENROUTER_API_KEY")
    # also try reading .env directly as fallback (if dotenv not loaded)
    if not api_key:
        try:
            env_path = Path(".env")
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if line.startswith(f"{api_key_env}="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
                    if (
                        line.startswith("OPENROUTER_API_KEY=")
                        and api_key_env != "OPENROUTER_API_KEY"
                    ):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except Exception:
            pass
    if not api_key:
        raise RuntimeError(f"{api_key_env} not configured — set in .env or env var (fail-closed)")
    model = getattr(cfg.vlm, "model", None)
    if not model:
        raise RuntimeError("vlm.model not configured in config.yaml (fail-closed)")
    timeout = int(getattr(cfg.vlm, "request_timeout_seconds", 60))

    pdf_path = Path(stored_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"stored_path not found: {stored_path}")
    pdf_bytes = pdf_path.read_bytes()
    # OpenRouter expects base64 PDF as data URL; instructor will handle response_model validation
    b64 = base64.b64encode(pdf_bytes).decode()

    client = instructor.from_openai(
        OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key, timeout=timeout)
    )
    # Native PDF input — no JPEG rasterization
    resp: _VLMPageExtraction = client.chat.completions.create(
        model=model,
        response_model=_VLMPageExtraction,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PAGE_PROMPT},
                    {
                        "type": "file",
                        "file": {
                            "filename": pdf_path.name,
                            "file_data": f"data:application/pdf;base64,{b64}",
                        },
                    },
                ],
            }
        ],
        max_retries=0,  # we handle retries in Python loop with backoff [2,5,15]
    )
    # Normalize to dict expected by caller
    return {
        "document_type": resp.document_type,
        "document_number": resp.document_number,
        "po_no_raw": resp.po_reference or resp.document_number,
        "po_reference": resp.po_reference,
        "vendor_name": resp.vendor_name,
        "confidence": resp.confidence,
        "line_items": [
            {
                "line_item_no": li.line_item_no,
                "item_code": li.item_code,
                "description": li.description,
                "uom": li.uom,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
                "total_price": li.total_price,
            }
            for li in resp.line_items
        ],
    }


def extract_document(doc_id: int, cfg) -> Document:
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            raise ValueError(f"document {doc_id} not found")

        dtype = doc.doc_type.value if hasattr(doc.doc_type, "value") else str(doc.doc_type)
        if is_manual_only(dtype):
            return doc

        max_retries = int(getattr(cfg.extraction, "max_retries", 3))
        backoff = list(getattr(cfg.extraction, "retry_backoff_seconds", [2, 5, 15]))

        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                result = _call_vlm(doc.stored_path, dtype, cfg)
                doc.extraction_attempt_count = attempt
                doc.extraction_status = ExtractionStatus.valid
                if result and result.get("po_no_raw"):
                    doc.po_no_raw = result["po_no_raw"]
                    # normalize for grouping (strip non-alnum, upper) — same as grouping.py
                    import re

                    norm = re.sub(r"[^A-Za-z0-9]", "", result["po_no_raw"]).upper()
                    doc.po_no_normalized = norm
                    # also store DN/SI numbers if present
                    if result.get("document_type") == "SI" and result.get("document_number"):
                        doc.si_no = result["document_number"]
                        doc.invoice_no = result["document_number"]
                    if result.get("document_type") == "DN" and result.get("document_number"):
                        doc.dn_no = result["document_number"]
                    if result.get("document_type") == "PO" and result.get("document_number"):
                        doc.po_no_raw = result["document_number"]
                    # update doc_type if VLM classified differently (e.g. UNKNOWN -> SI)
                    vtype = result.get("document_type")
                    if vtype and vtype in ("PO", "DN", "SI", "COMBINED", "SKIP", "UNKNOWN"):
                        import contextlib

                        from app.models import DocType

                        with contextlib.suppress(Exception):
                            doc.doc_type = DocType(vtype)  # type: ignore[arg-type]
                # persist line items (replace existing for this doc)
                from app.models import LineItem

                # clear old items for idempotency on retry
                for li in list(doc.line_items):
                    s.delete(li)
                s.flush()
                for li in result.get("line_items", []) or []:
                    qty = li.get("quantity")
                    price = li.get("unit_price")
                    if qty is None or li.get("description") is None:
                        continue
                    # already x1000 per prompt; ensure int
                    try:
                        qty_i = int(qty)
                        price_i = int(price) if price is not None else 0
                    except Exception:
                        continue
                    s.add(
                        LineItem(
                            document_id=doc.id,
                            line_item_no=str(li.get("line_item_no"))
                            if li.get("line_item_no")
                            else None,
                            description=str(li.get("description")),
                            quantity=qty_i,
                            unit_price=price_i,
                        )
                    )
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
                    # do not log api_key; log only safe fields
                    import logging

                    logging.getLogger(__name__).warning(
                        "VLM extraction failed for doc %s after %s attempts: %s",
                        doc_id,
                        attempt,
                        type(e).__name__,
                    )
                    return doc
        if last_exc is not None:
            doc.extraction_status = ExtractionStatus.failed
            s.commit()
            s.refresh(doc)
        return doc
