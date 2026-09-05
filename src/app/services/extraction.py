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
    "document_type enum[PO,SI,DN,COMBINED,SKIP,UNKNOWN], "
    "has_po_section:bool, has_dn_section:bool, has_si_section:bool, "
    "document_number (own SI No/DN No/PO No or null for COMBINED), "
    "po_reference (first PO No/P.O. Reference/Order No visible for SI/DN/COMBINED, null for PO), "
    'vendor_name, line_items[] {line_item_no, item_code, description, uom, quantity:str raw as printed e.g. "1" "12.5", '
    'unit_price:str raw as printed e.g. "1620.00" "350.00", total_price:str raw as printed, evidence:{source_text, page}, confidence}, '
    "confidence high/medium/low. COMBINED = single PDF containing PO+DN+SI sections together (CA merged). Omit nulls, no markdown."
)

_PAGE_PROMPT = (
    "STEP 1 — HEADER SCAN (first page top 20% for po_reference/document_number; each page table for rows):\n"
    "  Locate strings 'PO No'/'P.O. No'/'Purchase Order No'/'Buyers Order No'/'Order No'/'PO Reference' → po_reference "
    "(first visible if multiple DNs or COMBINED; strip non-alnum, upper; e.g. PO-210851 → 210851). Never use filename.\n"
    "  Locate 'DN No'/'Delivery Note No'/'GDN No'/'SI No'/'Invoice No'/'PO No' → document_number (its own number; for COMBINED use primary SI No or PO No).\n"
    "  vendor_name = supplier company header.\n\n"
    "STEP 2 — CLASSIFY document_type exactly one of PO, SI, DN, COMBINED, SKIP, UNKNOWN:\n"
    "  PO=Purchase Order, SI=Sales/Tax Invoice, DN=Delivery Note/Packing List, "
    "COMBINED=single PDF that visibly contains PO table + DN table + SI table together (often CA 'Combined' stamp, 3 sections, multi-page), "
    "SKIP=covers/T&C/blank pages, UNKNOWN=unreadable. DN bundles '(2 DNs)'/'(3 DNs)' are DN not COMBINED.\n"
    "  For COMBINED: set has_po_section=true, has_dn_section=true, has_si_section=true if and only if each respective section is visibly present; "
    "return document_type COMBINED (not PO) and fill po_reference with shared PO.\n\n"
    "STEP 3 — LINE ITEMS (only rows with product description AND quantity>0, scan ALL pages sequentially):\n"
    "  line_item_no: printed row number (Item No, Sl No, #) per page. item_code: SKU/part from column or embedded P/N: MFR:. "
    "description: COMPLETE, do not truncate. uom: EA/BOX/KG/SET/PCS or null.\n"
    "  For COMBINED: emit union of all sections but do NOT duplicate sections; backend will skip matching and send directly to output.\n"
    "  EXCLUDE subtotal, VAT, tax, total, amount-in-words, payment terms, signatures.\n\n"
    "STEP 4 — NUMBERS (copy verbatim, do NOT calculate, do NOT scale):\n"
    '  quantity: exact string as printed ("1", "50", "12.5")\n'
    '  unit_price: exact string as printed ("1620.00", "350.00")\n'
    '  total_price: exact string as printed ("1620.00", "17500.00") — copy verbatim, do NOT calculate.\n\n'
    "STEP 5 — MULTI-PAGE / MULTI-DN: if PDF contains 2-3 DNs or COMBINED multi-page, emit first po_reference, include ALL line_items across pages in order.\n\n"
    "EVIDENCE + CONFIDENCE: per line_item provide evidence.source_text verbatim snippet and confidence high/medium/low.\n\n"
    "FEW-SHOTS (raw strings, copy verbatim):\n"
    'SI: {"document_type":"SI","has_po_section":false,"has_dn_section":false,"has_si_section":true,"document_number":"SIV-ARS-26-4005","po_reference":"210851","vendor_name":"IBRAHIM ALI ALSHAB TRADING EST.","confidence":"high","line_items":[{"line_item_no":"12","item_code":"33818","description":"NUT, HEX 9/16 IN-12 UNC GRADE B YELLOW ZINC PLATED","quantity":"50","unit_price":"350.00","total_price":"17500.00","evidence":{"source_text":"12 33818 NUT... 50 350.00"},"confidence":"high"}]}\n'
    'PO: {"document_type":"PO","has_po_section":true,"has_dn_section":false,"has_si_section":false,"document_number":"210851","po_reference":null,"line_items":[{"line_item_no":"1","item_code":"33630","description":"WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS","quantity":"1","unit_price":"1620.00","total_price":"1620.00"}]}\n'
    'DN bundle: {"document_type":"DN","has_po_section":false,"has_dn_section":true,"has_si_section":false,"document_number":"GDN-ARS-26-4619","po_reference":"210851","line_items":[{"line_item_no":"1","item_code":"184799","description":"WASHER, LOCK, 3/8\\" - MFG: FLY","quantity":"50","unit_price":"1000.00","total_price":"50000.00"}]}\n'
    'COMBINED: {"document_type":"COMBINED","has_po_section":true,"has_dn_section":true,"has_si_section":true,"document_number":"SIV-RAK-25-3049","po_reference":"3049PO123","line_items":[{"line_item_no":"1","description":"WASHER, FLAT SAE 1/4 IN","quantity":"50","unit_price":"120.00"}],"confidence":"high"}\n'
    'DN with price preserved: {"document_type":"DN","has_po_section":false,"has_dn_section":true,"has_si_section":false,"document_number":"SIV-ARS-25-7230-P2","po_reference":"4500043712","line_items":[{"line_item_no":"10","description":"CRC Lectra Cleaner: 400ML Aerosol Can","quantity":"3","unit_price":"26.00","evidence":{"source_text":"10 CRC Lectra Cleaner 3"},"confidence":"high"}]}\n'
)


class _VLMLineItem(BaseModel):
    line_item_no: str | None = Field(None, description="Printed row number")
    item_code: str | None = None
    description: str | None = None
    uom: str | None = None
    quantity: str | None = None  # raw as printed, e.g. "1", "12.5"
    unit_price: str | None = None  # raw as printed, e.g. "1620.00", "350.00"
    total_price: str | None = None  # raw as printed


class _VLMPageExtraction(BaseModel):
    document_type: Literal["PO", "SI", "DN", "COMBINED", "SKIP", "UNKNOWN"] = Field(...)
    has_po_section: bool = False
    has_dn_section: bool = False
    has_si_section: bool = False
    document_number: str | None = None
    po_reference: str | None = None
    vendor_name: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    line_items: list[_VLMLineItem] = Field(default_factory=list)


def is_manual_only(doc_type: str) -> bool:
    return doc_type in ("CUSTOMS", "SHIPPING", "COMMERCIAL_INVOICE")


def _parse_scaled_int(val: int | float | str | None) -> int:
    """Parse raw quantity/price string into exact integer scaled x1000 (SPEC §6.4).

    Deterministic Decimal scaling — LLM copies verbatim, code scales.
    Never uses float to avoid drift.
    """
    if val is None or str(val).strip() == "":
        return 0
    try:
        from decimal import Decimal, InvalidOperation

        d = Decimal(str(val).strip().replace(",", "").replace(" ", ""))
        return int((d * Decimal("1000")).to_integral_value())
    except (InvalidOperation, ValueError, AttributeError):
        return 0
    except Exception:
        return 0


def _call_vlm(stored_path: str, doc_type: str, cfg) -> dict:
    """Single Luna native PDF call via instructor (no rasterization, FR-6.1). Fail-closed on missing secrets."""
    # --- fail-closed secret handling (never hardcoded, never logged) ---
    # Load .env if present (python-dotenv) so os.getenv sees it without shell export
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
        # Also check .env next to config or database parent directory
        if hasattr(cfg, "paths") and getattr(cfg.paths, "database_path", None):
            cfg_dir = Path(cfg.paths.database_path).parent
            if (cfg_dir / ".env").exists():
                load_dotenv(cfg_dir / ".env", override=False)
    except Exception:
        pass

    api_key_env = getattr(cfg.vlm, "api_key_env_var", "OPENROUTER_API_KEY") or "OPENROUTER_API_KEY"
    api_key = os.getenv(api_key_env) or os.getenv("OPENROUTER_API_KEY")
    # Also try reading .env directly as fallback via dotenv_values
    if not api_key:
        try:
            from dotenv import dotenv_values

            for candidate in [Path(".env"), Path(__file__).resolve().parents[3] / ".env"]:
                if candidate.exists():
                    vals = dotenv_values(candidate)
                    api_key = vals.get(api_key_env) or vals.get("OPENROUTER_API_KEY")
                    if api_key:
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
        max_retries=0,  # retries owned by the Prefect task envelope (flows/sync.py), driven by config
    )
    # Normalize to dict expected by caller — return raw strings (scaling done in extract_document)
    return {
        "document_type": resp.document_type,
        "has_po_section": resp.has_po_section,
        "has_dn_section": resp.has_dn_section,
        "has_si_section": resp.has_si_section,
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
            # Persist raw VLM JSON for audit (Task 1) + dev file dump mapped to filename (Task 6)
            import json

            try:
                doc.raw_extraction_json = json.dumps(result, ensure_ascii=False, default=str)
            except Exception:
                doc.raw_extraction_json = None
            # Dev-only file dump for optimization/debugging — mapped to filename+hash, gitignored
            try:
                raw_dir = Path(cfg.paths.log_folder).parent / "raw_extractions"
                # also respect data/raw_extractions if log_folder is elsewhere
                alt_raw_dir = Path("data/raw_extractions")
                if (
                    raw_dir.exists()
                    or alt_raw_dir.exists()
                    or Path("data/raw_extractions").exists()
                ):
                    target_dir = raw_dir if raw_dir.exists() else alt_raw_dir
                    target_dir.mkdir(parents=True, exist_ok=True)
                    safe_stem = Path(doc.original_filename).stem.replace(" ", "_")[:80]
                    fname = f"{doc.sha256_hash[:8]}_{safe_stem}.json"
                    (target_dir / fname).write_text(
                        json.dumps(result, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8",
                    )
                elif Path("data").exists():
                    # still create if DEBUG env is set
                    import os

                    if os.getenv("DEBUG") == "1" or os.getenv("AAM_SAVE_RAW") == "1":
                        target_dir = Path("data/raw_extractions")
                        target_dir.mkdir(parents=True, exist_ok=True)
                        safe_stem = Path(doc.original_filename).stem.replace(" ", "_")[:80]
                        fname = f"{doc.sha256_hash[:8]}_{safe_stem}.json"
                        (target_dir / fname).write_text(
                            json.dumps(result, indent=2, ensure_ascii=False, default=str),
                            encoding="utf-8",
                        )
            except Exception:
                pass

            # COMBINED Document Gate (SPEC §7.3 FR-6.7):
            # A COMBINED document must have all 3 sub-sections (PO, DN, SI) visibly identified.
            if result.get("document_type") == "COMBINED":
                has_3_sections = (
                    result.get("has_po_section", False)
                    and result.get("has_dn_section", False)
                    and result.get("has_si_section", False)
                )
                if not has_3_sections:
                    raise ValueError(
                        "COMBINED document missing required PO, DN, or SI sub-sections (FR-6.7)"
                    )

            doc.extraction_status = ExtractionStatus.valid
            # update doc_type if VLM classified differently (e.g. UNKNOWN -> DN, or SKIP -> UNKNOWN)
            # MUST run regardless of po_no_raw — otherwise DN/SI with missed PO stays UNKNOWN (bug SIV-DTS-25-576-2)
            vtype = result.get("document_type")
            if vtype and vtype in ("PO", "DN", "SI", "COMBINED", "SKIP", "UNKNOWN"):
                import contextlib

                from app.models import DocType

                with contextlib.suppress(Exception):
                    effective_type = "UNKNOWN" if vtype == "SKIP" else vtype
                    doc.doc_type = DocType(effective_type)  # type: ignore[arg-type]

            if result and result.get("po_no_raw"):
                doc.po_no_raw = result["po_no_raw"]
                # normalize for grouping — same as grouping.normalize_po_no (split revision ", 0")
                from app.services.grouping import normalize_po_no

                doc.po_no_normalized = normalize_po_no(result["po_no_raw"])
            # also store DN/SI numbers if present (outside po_no_raw guard)
            if result.get("document_type") == "SI" and result.get("document_number"):
                doc.si_no = result["document_number"]
                doc.invoice_no = result["document_number"]
            if result.get("document_type") == "DN" and result.get("document_number"):
                doc.dn_no = result["document_number"]
            if (
                result.get("document_type") == "PO"
                and result.get("document_number")
                and not doc.po_no_raw
            ):
                doc.po_no_raw = result["document_number"]
                from app.services.grouping import normalize_po_no

                doc.po_no_normalized = normalize_po_no(result["document_number"])
            if result.get("document_type") == "COMBINED" and result.get("document_number"):
                # for COMBINED, treat document_number as invoice_no fallback, keep po_no_raw as po_reference
                doc.invoice_no = result["document_number"]
                doc.si_no = result["document_number"]

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
                qty_i = _parse_scaled_int(qty)
                price_i = _parse_scaled_int(price)
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
