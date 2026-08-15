"""VLM extraction — Luna native PDF via instructor+OpenRouter, no mocks in prod (FR-6.1-6.8).

Secrets: OPENROUTER_API_KEY only via env/.env (fail-closed, never logged).
Model: cfg.vlm.model (openai/gpt-5.6-luna) from config.yaml, never hardcoded.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Literal

import instructor
from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import Document, ExtractionStatus
from app.models.base import Base

# --- Ultimate Luna prompt — strict JSON Schema via instructor response_model, OpenAI vision guide ---
# System role enforces parser identity; user role carries PDF + schema. No pypdf, no filename heuristic.
# Multi-page: first page header for PO/DN/SI numbers, each page table row for line_items.
_SYSTEM_PROMPT = (
    "You are Luna document parser for AAM_merger V3. Return strict JSON matching schema: "
    "document_type enum[PO,SI,DN,COMBINED,SKIP,UNKNOWN], document_number (own SI No/DN No/PO No or null for COMBINED), "
    "po_reference (first PO No/P.O. Reference/Order No visible for SI/DN/COMBINED, null for PO), "
    "vendor_name, line_items[] {line_item_no, item_code, description, uom, quantity:int×1000, "
    "unit_price:int×1000, total_price:int×1000, evidence:{source_text, page}, confidence}, "
    "confidence high/medium/low. COMBINED = single PDF containing PO+DN+SI sections together (CA merged). Omit nulls, no markdown."
)

_PAGE_PROMPT = (
    "STEP 1 — HEADER SCAN (first page top 20% for po_reference/document_number; each page table for rows):\n"
    "  Locate strings 'PO No'/'P.O. No'/'Purchase Order No'/'Order No'/'PO Reference' → po_reference "
    "(first visible if multiple DNs or COMBINED; strip non-alnum, upper; e.g. PO-210851 → 210851). Never use filename.\n"
    "  Locate 'DN No'/'Delivery Note No'/'GDN No'/'SI No'/'Invoice No'/'PO No' → document_number (its own number; for COMBINED use primary SI No or PO No).\n"
    "  vendor_name = supplier company header.\n\n"
    "STEP 2 — CLASSIFY document_type exactly one of PO, SI, DN, COMBINED, SKIP, UNKNOWN:\n"
    "  PO=Purchase Order, SI=Sales/Tax Invoice, DN=Delivery Note/Packing List, "
    "COMBINED=single PDF that visibly contains PO table + DN table + SI table together (often CA 'Combined' stamp, 3 sections, multi-page), "
    "SKIP=covers/T&C/blank pages, UNKNOWN=unreadable. DN bundles '(2 DNs)'/'(3 DNs)' are DN not COMBINED.\n"
    "  For COMBINED return document_type COMBINED (not PO) and fill po_reference with shared PO.\n\n"
    "STEP 3 — LINE ITEMS (only rows with product description AND quantity>0, scan ALL pages sequentially):\n"
    "  line_item_no: printed row number (Item No, Sl No, #) per page. item_code: SKU/part from column or embedded P/N: MFR:. "
    "description: COMPLETE, do not truncate. uom: EA/BOX/KG/SET/PCS or null.\n"
    "  For COMBINED: emit union of all sections but do NOT duplicate sections; backend will skip matching and send directly to output.\n"
    "  EXCLUDE subtotal, VAT, tax, total, amount-in-words, payment terms, signatures.\n\n"
    "STEP 4 — NUMBERS ×1000 (SPEC §6.4 exact ints, 16.5 and 16.50 both 16500):\n"
    "  quantity int×1000: 50→50000, 50.00→50000, 12.5→12500, 1.5kg→1500. unit_price int×1000: 12.50→12500, 350.00→350000.\n"
    "  If unit_price blank but total_price shown, compute unit_price = total_price/qty×1000, never 0 if SI counterpart has price for same line_item_no.\n"
    "  total_price int×1000 similarly.\n\n"
    "STEP 5 — MULTI-PAGE / MULTI-DN: if PDF contains 2-3 DNs or COMBINED multi-page, emit first po_reference, include ALL line_items across pages in order.\n\n"
    "EVIDENCE + CONFIDENCE: per line_item provide evidence.source_text verbatim snippet and confidence high/medium/low.\n\n"
    "FEW-SHOTS (corrected ×1000):\n"
    'SI: {"document_type":"SI","document_number":"SIV-ARS-26-4005","po_reference":"210851","vendor_name":"IBRAHIM ALI ALSHAB TRADING EST.","confidence":"high","line_items":[{"line_item_no":"12","item_code":"33818","description":"NUT, HEX 9/16 IN-12 UNC GRADE B YELLOW ZINC PLATED","quantity":50000,"unit_price":350000,"total_price":17500000,"evidence":{"source_text":"12 33818 NUT... 50 350.00"},"confidence":"high"}]}\n'
    'PO: {"document_type":"PO","document_number":"210851","po_reference":null,"line_items":[{"line_item_no":"1","item_code":"33630","description":"WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS","quantity":50000,"unit_price":120000,"total_price":6000000}]}\n'
    'DN bundle: {"document_type":"DN","document_number":"GDN-ARS-26-4619","po_reference":"210851","line_items":[{"line_item_no":"1","item_code":"184799","description":"WASHER, LOCK, 3/8\\" - MFG: FLY","quantity":50000,"unit_price":1000000,"total_price":50000000}]}\n'
    'COMBINED: {"document_type":"COMBINED","document_number":"SIV-RAK-25-3049","po_reference":"3049PO123","line_items":[{"line_item_no":"1","description":"WASHER, FLAT SAE 1/4 IN","quantity":50000,"unit_price":120000}],"confidence":"high"}\n'
    'DN with price preserved: {"document_type":"DN","document_number":"SIV-ARS-25-7230-P2","po_reference":"4500043712","line_items":[{"line_item_no":"10","description":"CRC Lectra Cleaner: 400ML Aerosol Can","quantity":3000,"unit_price":26000,"evidence":{"source_text":"10 CRC Lectra Cleaner 3"},"confidence":"high"}]}\n'
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
    document_type: Literal["PO", "SI", "DN", "COMBINED", "SKIP", "UNKNOWN"] = Field(...)
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
    # Native PDF input — no JPEG rasterization; system role per OpenAI guide, strict schema via response_model
    resp: _VLMPageExtraction = client.chat.completions.create(
        model=model,
        response_model=_VLMPageExtraction,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
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
            },
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

        if (doc.extraction_attempt_count or 0) >= 3:
            doc.extraction_status = ExtractionStatus.failed
            s.commit()
            s.refresh(doc)
            return doc

        doc.extraction_attempt_count = (doc.extraction_attempt_count or 0) + 1
        try:
            # Multi-page PDFs (SPEC §7.3 FR-6.1): The entire PDF (all pages) is passed as a
            # single base64 data URL. Luna sees every page natively in one API call.
            result = _call_vlm(doc.stored_path, dtype, cfg)
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
                if result.get("document_type") == "COMBINED" and result.get("document_number"):
                    # for COMBINED, treat document_number as invoice_no fallback, keep po_no_raw as po_reference
                    doc.invoice_no = result["document_number"]
                    doc.si_no = result["document_number"]
                # update doc_type if VLM classified differently (e.g. UNKNOWN -> SI, or SKIP -> UNKNOWN)
                vtype = result.get("document_type")
                if vtype and vtype in ("PO", "DN", "SI", "COMBINED", "SKIP", "UNKNOWN"):
                    import contextlib

                    from app.models import DocType

                    with contextlib.suppress(Exception):
                        effective_type = "UNKNOWN" if vtype == "SKIP" else vtype
                        doc.doc_type = DocType(effective_type)  # type: ignore[arg-type]
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
            doc.extraction_status = ExtractionStatus.failed
            s.commit()
            s.refresh(doc)
            import logging

            logging.getLogger(__name__).warning(
                "VLM extraction failed for doc %s (attempt %s): %s",
                doc_id,
                doc.extraction_attempt_count,
                type(e).__name__,
            )
            raise e
