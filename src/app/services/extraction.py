"""VLM extraction — one native-PDF call per document via instructor+OpenRouter.

Secrets: OPENROUTER_API_KEY only via env/.env (fail-closed, never logged).
Model: read from cfg.vlm.model, never hardcoded here.

The VLM's entire job is PDF -> structured extraction. Every decision after this
point is deterministic code (AAM_merger_V3_PRODUCT.md). The schema below is the
whole contract with the model: if a value is not a field here, it is not
extracted, stored, or matched on.
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import instructor
from openai import OpenAI
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import Document, ExtractionStatus
from app.models.base import Base

logger = logging.getLogger(__name__)

# --- Ultimate Luna prompt — strict JSON Schema via instructor response_model, OpenAI vision guide ---
# System role enforces parser identity; user role carries PDF + schema. No pypdf, no filename heuristic.
# Multi-page: first page header for PO/DN/SI numbers, each page table row for line_items.
_SYSTEM_PROMPT = (
    "You are Luna document parser for AAM_merger V3. Return strict JSON matching schema: "
    "document_type enum[PO,SI,DN,COMBINED,SKIP,UNKNOWN], "
    "has_po_section:bool, has_dn_section:bool, has_si_section:bool, "
    "page_count:int, components[] {document_type enum[PO,DN,SI,SKIP], page_start:int, page_end:int}, "
    "document_number (own SI No/DN No/PO No or null for COMBINED), "
    "po_reference (the PO No visible for SI/DN/COMBINED, null for PO), "
    "po_reference_ambiguous:bool, "
    "vendor_name, line_items[] {line_item_no, description, quantity: NUMBER-ONLY as printed "
    "(no unit/UOM/currency), dn_no, unit_price}. "
    "COMBINED = multi-document PDF containing runs of PO/DN/SI. When COMBINED: return page_count and "
    "components (1-based page ranges for splitting); do NOT extract line_items (leave line_items empty []). "
    "Omit nulls, no markdown."
)

_PAGE_PROMPT = (
    "STEP 1 — HEADER SCAN (first page top 20% for po_reference/document_number; each page table for rows):\n"
    "  For SI and DN (never for a PO): locate strings 'PO No'/'P.O. No'/'Purchase Order No'/'Buyers Order No'/'Order No'/'PO Reference' → po_reference "
    "(strip non-alnum, upper; e.g. PO-210851 → 210851). Never use filename.\n"
    "  If MORE THAN ONE distinct PO number is visible on a document, set po_reference to the first and set "
    "po_reference_ambiguous true — the document will be quarantined rather than guessed at.\n"
    "  Locate 'DN No'/'Delivery Note No'/'GDN No'/'SI No'/'Invoice No'/'PO No' → document_number (its own number; for COMBINED use primary SI No or PO No).\n"
    "  For a PO document po_reference is null and document_number is its own PO number; a quotation, RFQ, requisition or indent number printed on a PO is never the PO number.\n"
    "  vendor_name = supplier company header.\n\n"
    "STEP 2 — CLASSIFY document_type exactly one of PO, SI, DN, COMBINED, SKIP, UNKNOWN:\n"
    "  PO=Purchase Order, SI=Sales/Tax Invoice, DN=Delivery Note/Packing List, "
    "COMBINED=single PDF that visibly contains PO table + DN table + SI table together (often CA 'Combined' stamp, 3 sections, multi-page), "
    "SKIP=covers/T&C/blank pages, UNKNOWN=unreadable. DN bundles '(2 DNs)'/'(3 DNs)' are DN not COMBINED.\n"
    "  For COMBINED: set has_po_section=true, has_dn_section=true, has_si_section=true if and only if each respective section is visibly present.\n\n"
    "STEP 3 — LINE ITEMS (every row that has a product description, scan ALL pages sequentially; "
    "if a row's quantity is unreadable return quantity null — never drop the row):\n"
    "  line_item_no — MOST IMPORTANT. The vendor's own side column (Sl No / Item No / #) is often just a running "
    "docket counter and repeats '1' on every page or every docket. It is NOT the PO line. Resolution order:\n"
    "    (a) if the description or a nearby note contains a marker like 'Line Item - 3' or 'Line No. 3', use THAT number;\n"
    "    (b) otherwise use the printed side column number.\n"
    "  Copy the number exactly as printed, including any hyphen: '1-1', '2-1', '10' stay verbatim. Do not renumber, pad or strip leading zeros.\n"
    "  description: COMPLETE, do not truncate. Keep the 'Line Item - N' text inside it if it was printed there.\n"
    "  dn_no: only if a delivery-note number is printed against THIS individual row; otherwise null. "
    "Most vendors print the DN number once in the header, not per line.\n"
    "  For COMBINED: DO NOT EXTRACT LINE ITEMS. Leave line_items empty []. The file is split by page range, and each child document will have its line items extracted individually.\n"
    "  QUANTITY COLUMN, PO: quantity is the ORDERED quantity; never a received / delivered-to-date column.\n"
    "  QUANTITY COLUMN, DN and SI only: if a table prints several quantity columns, quantity is the quantity DELIVERED / SHIPPED / DISPATCHED / INVOICED on THIS document (headers such as Delivered Qty, Qty Shipped, Dispatch Qty, Invoiced Qty, Qty). NEVER use Ordered, Balance, Pending, Outstanding, Previous, Cumulative or To-date columns. If you cannot tell which column is the delivered/invoiced one, return quantity null for those rows rather than guessing.\n"
    "  EXCLUDE subtotal, VAT, tax, total, amount-in-words, payment terms, signatures, charge rows that have no item number (freight, transport, shipping charges, discount, rounding), and 'carried forward' / 'brought forward' / page-total rows. A goods line whose description merely contains a word like PACKING or HANDLING is an item: keep it.\n\n"
    "STEP 4 — QUANTITY (the NUMBER ONLY; do NOT calculate, do NOT scale):\n"
    '  quantity = the digits as printed, and nothing else. "1", "50", "12.5", "12.45000000".\n'
    "  Strip anything that is not part of the number: unit / UOM (EA, PCS, BOX, KG, M, M3, "
    "MT, BAGS), currency symbols, and words like EACH or SET. If the cell reads "
    "'12.5 EA' return \"12.5\"; if it reads '1,200.00 M3' return \"1200.00\".\n"
    "  UOM itself is NOT extracted anywhere in this system. Never return it.\n\n"
    "STEP 5 — MULTI-PAGE / MULTI-DN: if PDF contains 2-3 DNs, emit first po_reference, include ALL line_items across pages in order.\n"
    "STEP 6 — MULTI-DOC SPLIT (COMBINED branch only): enumerate each contiguous same-type run as a component "
    "with its REAL PDF page indices (1-based position in this file as pypdf sees it, never numbers printed on the page). "
    "A page that continues the current PO/DN/SI extends the run; a page that changes type ends the run and starts a new component. "
    "A page that is none of PO/DN/SI (cover, T&C, blank) is a SKIP component so every page 1..N is claimed exactly once. "
    "Also return page_count = the total number of pages in this file.\n\n"
    "FEW-SHOTS (raw strings, copy verbatim):\n"
    'SI: {"document_type":"SI","document_number":"SIV-ARS-26-4005","po_reference":"210851","vendor_name":"IBRAHIM ALI ALSHAB TRADING EST.","line_items":[{"line_item_no":"12","description":"NUT, HEX 9/16 IN-12 UNC GRADE B YELLOW ZINC PLATED","quantity":"50","unit_price":"350.00"}]}\n'
    'PO: {"document_type":"PO","document_number":"210851","po_reference":null,"line_items":[{"line_item_no":"1","description":"WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS","quantity":"1","unit_price":"1620.00"}]}\n'
    'DN bundle: {"document_type":"DN","document_number":"GDN-ARS-26-4619","po_reference":"210851","line_items":[{"line_item_no":"1","description":"WASHER, LOCK, 3/8\\" - MFG: FLY","quantity":"50","unit_price":"1000.00"}]}\n'
    'Re-indexed DN (side column lies, use the embedded marker): {"document_type":"DN","document_number":"GDN-RHO-25-513","po_reference":"8300023893","line_items":[{"line_item_no":"10","description":"GATE VALVE 2IN CL150 - Line Item - 10","quantity":"2","dn_no":"GDN-RHO-25-513"}]}\n'
    'COMBINED: {"document_type":"COMBINED","document_number":null,"po_reference":"3049PO123","page_count":3,"components":[{"document_type":"PO","page_start":1,"page_end":1},{"document_type":"DN","page_start":2,"page_end":2},{"document_type":"SI","page_start":3,"page_end":3}],"line_items":[]}\n'
)


def _coerce_number_to_str(value: object) -> object:
    """Before-validator for the string-typed VLM fields.

    The model sometimes emits a JSON number (50, 12.5, 1) where the schema asks
    for a string. With max_retries=0 there is no re-ask, so that would fail the
    whole document. Numbers become their printed text; everything else is left
    for pydantic to validate. bool, NaN and inf are deliberately not converted.

    Only the VALUE fields (quantity, unit_price, description) use this. The
    identity fields that key a set or a line (document_number, po_reference,
    dn_no) are never coerced: `normalize_po_no` refuses non-strings on purpose,
    and a number cannot carry a leading zero ('007' vs 7), so coercing would turn
    a loud failure into a plausible but wrong key. line_item_no accepts an int
    only (`_coerce_int_to_str`): a float such as 1.10 would print as '1.1' and
    collide with the separate line 1.1.
    """
    from decimal import Decimal

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return value
        if value.is_integer():
            return str(int(value))
        return format(Decimal(str(value)), "f")
    if isinstance(value, Decimal):
        if not value.is_finite():
            return value
        return format(value, "f")
    return value


def _coerce_int_to_str(value: object) -> object:
    """Before-validator for line_item_no: a bare integer becomes its text, a float
    or anything else is left for pydantic to reject (see _coerce_number_to_str)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return value


class _VLMLineItem(BaseModel):
    @field_validator("description", "quantity", "unit_price", mode="before")
    @classmethod
    def _coerce_numbers(cls, value: object) -> object:
        return _coerce_number_to_str(value)

    @field_validator("line_item_no", mode="before")
    @classmethod
    def _coerce_line_no(cls, value: object) -> object:
        return _coerce_int_to_str(value)

    line_item_no: str | None = Field(
        None,
        description=(
            "The PO line this row refers to. Take it from a 'Line Item - N' "
            "marker in the description when present, otherwise the side column "
            "(Sl No / Item No / #). See STEP 3."
        ),
    )
    description: str | None = None
    # The NUMBER ONLY. UOM, unit symbols and currency are stripped: this system
    # does not extract, store or reconcile units, so a unit in this field would
    # only make the strict parser reject an otherwise legible row.
    quantity: str | None = Field(
        None,
        description=(
            'The numeric quantity as printed, digits only: "1", "50", "12.5", '
            '"12.45000000". No unit, no UOM, no currency, no surrounding text. '
            'A cell reading "12.5 EA" is returned as "12.5".'
        ),
    )
    dn_no: str | None = Field(
        None, description="Delivery-note number printed against THIS row, if any."
    )
    # Prices take no part in matching or merging. Kept only because the column is
    # NOT NULL and the merged packet carries it; the quantity is the sole
    # reconciliation signal.
    unit_price: str | None = None  # raw as printed, e.g. "1620.00", "350.00"


class _VLMComponent(BaseModel):
    """One contiguous same-type run for the Layer-1 split (PLAN Rev 2 §0.6).

    `page_start`/`page_end` are 1-based indices into the real PDF file
    (pypdf ground truth), never numbers printed on the page. A page that is
    not a continuation of PO/DN/SI is `SKIP` so coverage stays complete.
    """

    doc_type: Literal["PO", "DN", "SI", "SKIP"] = Field(
        ..., alias="document_type", description="Type of this page run."
    )
    page_start: int = Field(..., ge=1, description="1-based first PDF page index.")
    page_end: int = Field(..., ge=1, description="1-based last PDF page index.")

    model_config = {"populate_by_name": True}


class _VLMPageExtraction(BaseModel):
    document_type: Literal["PO", "SI", "DN", "COMBINED", "SKIP", "UNKNOWN"] = Field(...)
    has_po_section: bool = False
    has_dn_section: bool = False
    has_si_section: bool = False
    # Multi-doc split (PLAN Step 4): total PDF pages (cross-checked against
    # pypdf) plus one entry per contiguous same-type run. Single-section
    # documents leave both at their defaults; no extra VLM call.
    page_count: int = Field(default=0, ge=0)
    components: list[_VLMComponent] = Field(default_factory=list)
    document_number: str | None = None
    po_reference: str | None = None
    po_reference_ambiguous: bool = Field(
        False,
        description=(
            "True when more than one distinct PO number is visible on this "
            "document. Such a document is quarantined rather than assigned to "
            "a guessed PO."
        ),
    )
    vendor_name: str | None = None
    line_items: list[_VLMLineItem] = Field(default_factory=list)


def is_manual_only(doc_type: str) -> bool:
    return doc_type in ("CUSTOMS", "SHIPPING")


#: The product's fixed default: three attempts before a document is terminal.
DEFAULT_EXTRACTION_ATTEMPTS = 3


def _attempt_cap(cfg) -> int:
    """Attempts a document gets before extraction is permanently failed.

    The product fixes this at three attempts, and `extraction.max_retries`
    (1..5) is the operator's knob for the same envelope. The old hard-coded
    `>= 3` made any configured value above 3 inert — the knob read as
    "attempts" but attempts 4 and 5 were never made. Derive the cap from the
    configured value so the knob does what it says, while the default (3)
    keeps the product behaviour unchanged.
    """
    raw = getattr(getattr(cfg, "extraction", None), "max_retries", DEFAULT_EXTRACTION_ATTEMPTS)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_EXTRACTION_ATTEMPTS


def _parse_scaled_int(val: int | float | str | None, locale: str = "en_IN") -> int:
    """Parse a raw quantity/price string into an exact integer scaled x1000.

    The model copies the printed string verbatim; this scales it. Anything the
    sanitizer refuses (bad grouping, inner spaces, more than 2 significant
    decimals, zero, negative) becomes 0, which routes the PO Set to quarantine
    as `non_positive_quantity` rather than being silently mis-parsed.

    See AAM_merger_V3_PRODUCT.md section 5 for the parsing contract.
    """
    from app.services.sanitizer import parse_quantity_scaled

    if val is None or str(val).strip() == "":
        return 0
    try:
        return parse_quantity_scaled(str(val), locale=locale)
    except ValueError:
        return 0
    except Exception:
        return 0


def _call_vlm(stored_path: str, doc_type: str, cfg) -> dict:
    """Single Luna native PDF call via instructor (no rasterization, FR-6.1). Fail-closed on missing secrets."""
    # --- fail-closed secret handling (never hardcoded, never logged) ---
    # The secret lives in .env or in the environment. `load_env` is the one
    # place that reads the file (app.core.config); calling it here keeps the
    # guarantee even when this service is reached without a config load
    # first.
    from app.core.config import load_env

    load_env()

    api_key_env = getattr(cfg.vlm, "api_key_env_var", "OPENROUTER_API_KEY") or "OPENROUTER_API_KEY"
    api_key = os.getenv(api_key_env) or os.getenv("OPENROUTER_API_KEY")

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
        OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            timeout=timeout,
            # The SDK default is 2 inner retries; the attempt budget validated in
            # config (validate_attempt_budget) assumes exactly one HTTP call per try.
            max_retries=0,
        )
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
    # Normalize to dict expected by caller — return raw strings (scaling done
    # in extract_document). Only fields the schema above actually declares may
    # be read here; anything else raises AttributeError and fails the whole
    # extraction.
    return {
        "document_type": resp.document_type,
        "has_po_section": resp.has_po_section,
        "has_dn_section": resp.has_dn_section,
        "has_si_section": resp.has_si_section,
        "page_count": resp.page_count,
        "components": [
            {
                "doc_type": c.doc_type,
                "document_type": c.doc_type,
                "page_start": c.page_start,
                "page_end": c.page_end,
            }
            for c in (resp.components or [])
        ],
        "document_number": resp.document_number,
        # A PO keys on its OWN number first; DN/SI/COMBINED key only on the PO they cite.
        "po_no_raw": (resp.document_number or resp.po_reference)
        if resp.document_type == "PO"
        else resp.po_reference,
        "po_reference": resp.po_reference,
        "po_reference_ambiguous": resp.po_reference_ambiguous,
        "vendor_name": resp.vendor_name,
        "line_items": [
            {
                "line_item_no": li.line_item_no,
                "description": li.description,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
                "dn_no": li.dn_no,
            }
            for li in resp.line_items
        ],
    }


def _handle_combined(doc, s, result: dict, cfg) -> Document:
    """Persist a sterile split parent and cut child files (PLAN Rev 2+3 §6).

    - Child re-reading as COMBINED (`parent_document_id` non-null): never
      re-split. The child fails and the *parent* quarantines as
      `child_recombined` — a COMBINED child is otherwise invisible because
      Layer 2 names no types.
    - Already-split parent (`is_split_parent` with `split_completed_at` set):
      idempotent return, no duplicate children.
    - Normal parent: sterile row (COMBINED/valid, no numbers/set/lines, full
      raw JSON kept), `split_combined` cuts files into `input/`, parent input
      copy moves to `combined/`, `split_completed_at` set.
    - SplitError (any range gate): quarantine the whole parent, mark failed,
      return normally — validation failures must NOT raise into the Prefect
      retry envelope and burn VLM calls on a deterministic verdict.
    """
    from app.models import DocType

    # Loop guard 1: a child never re-enters the splitter.
    if getattr(doc, "parent_document_id", None) is not None:
        doc.extraction_status = ExtractionStatus.failed
        s.commit()
        s.refresh(doc)
        try:
            from app.services.quarantine import quarantine_document

            quarantine_document(
                doc.parent_document_id,
                cfg,
                reason=(
                    "child_recombined: child "
                    f"{doc.original_filename} re-read as COMBINED; "
                    "split parent quarantined for operator review"
                ),
            )
        except Exception:
            logger.warning(
                "Could not quarantine split parent %s for recombined child %s",
                doc.parent_document_id,
                doc.id,
                exc_info=True,
            )
        logger.error(
            "Child %s (%s) re-read as COMBINED; parent %s quarantined, child failed",
            doc.id,
            doc.original_filename,
            doc.parent_document_id,
        )
        return doc

    # Loop guard 2: already split — idempotent, never duplicate children.
    if getattr(doc, "is_split_parent", False) and getattr(doc, "split_completed_at", None):
        doc.doc_type = DocType.COMBINED
        doc.extraction_status = ExtractionStatus.valid
        s.commit()
        s.refresh(doc)
        return doc

    if not cfg.extraction.split_combined:
        # Splitting is off: the file is quarantined whole. The row stays a valid,
        # unattached COMBINED document (so it is never re-read and the holding area
        # lists it) and carries no numbers, set or lines for Layer 2 to trust.
        doc.doc_type = DocType.COMBINED
        doc.extraction_status = ExtractionStatus.valid
        doc.is_split_parent = False
        doc.po_no_raw = None
        doc.po_no_normalized = None
        doc.po_reference_ambiguous = False
        doc.dn_no = None
        doc.si_no = None
        doc.invoice_no = None
        doc.po_set_id = None
        for li in list(doc.line_items):
            s.delete(li)
        s.commit()
        s.refresh(doc)
        try:
            from app.services.quarantine import quarantine_document

            quarantine_document(
                doc.id,
                cfg,
                reason="combined_document: one PDF holds several documents; "
                "quarantined unsplit, a person must separate it",
            )
        except Exception:
            logger.warning("Could not quarantine combined document %s", doc.id, exc_info=True)
        return doc

    # Sterile parent: the trigger row carries no data — children own it, so
    # anything scanning the parent would double-count.
    doc.doc_type = DocType.COMBINED
    doc.extraction_status = ExtractionStatus.valid
    doc.is_split_parent = True
    doc.po_no_raw = None
    doc.po_no_normalized = None
    doc.po_reference_ambiguous = False
    doc.dn_no = None
    doc.si_no = None
    doc.invoice_no = None
    doc.po_set_id = None
    for li in list(doc.line_items):
        s.delete(li)
    s.flush()

    from app.services.splitting import SplitError, split_combined

    try:
        children = split_combined(doc.stored_path, result, cfg)
        if not children:
            # An all-SKIP decomposition retires the whole PDF with no child
            # document, no quarantine and no trace. A real packet misread as
            # covers must fail visibly instead.
            raise SplitError(
                "no_document_pages",
                "the splitter claimed every page as SKIP; no PO/DN/SI document would exist",
            )
    except SplitError as e:
        doc.extraction_status = ExtractionStatus.failed
        doc.split_completed_at = None
        s.commit()
        s.refresh(doc)
        try:
            from app.services.quarantine import quarantine_document

            quarantine_document(doc.id, cfg, reason=f"split_failed:{e.reason}: {e}")
        except Exception:
            logger.warning("Could not quarantine failed split parent %s", doc.id, exc_info=True)
        logger.error(
            "Split failed for parent %s (%s): %s — quarantined",
            doc.id,
            doc.original_filename,
            e.reason,
        )
        return doc

    # Success: parent input copy moves to combined/ (never re-scanned — only
    # input/ is scanned), children wait in input/ for the next run.
    try:
        import shutil

        input_copy = Path(cfg.paths.input_folder) / (doc.original_filename or "")
        combined_dir = Path(cfg.paths.combined_folder)
        combined_dir.mkdir(parents=True, exist_ok=True)
        if input_copy.exists():
            dest = combined_dir / (doc.original_filename or Path(doc.stored_path).name)
            if dest.exists():
                input_copy.unlink(missing_ok=True)
            else:
                shutil.move(str(input_copy), str(dest))
        # A byte-identical copy under another name is the same document. It was
        # de-duplicated to this parent row, so nothing will ever read it; left
        # in input/ it is re-hashed on every sync and never cleared. Remove
        # every input copy whose content is the parent's (stored/ is untouched).
        import hashlib

        for other in Path(cfg.paths.input_folder).iterdir():
            if not other.is_file() or other.suffix.lower() != ".pdf":
                continue
            if other.name == (doc.original_filename or ""):
                continue
            try:
                if hashlib.sha256(other.read_bytes()).hexdigest() == doc.sha256_hash:
                    other.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove duplicate input copy %s", other, exc_info=True)
    except Exception:
        logger.warning("Could not park split parent %s in combined/", doc.id, exc_info=True)
    doc.split_completed_at = datetime.now(UTC)
    s.commit()
    s.refresh(doc)
    return doc


def populate_from_raw_dict(doc: Document, result: dict, s: Session, cfg) -> None:
    """Populate normalized fields and line items from a raw extraction dictionary.

    Decouples raw extraction persistence from normalization/scaling so
    normalization rules or locales can be re-applied over stored JSON at any time.
    """
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

    # A parent whose split never completed is not a parent: the split failed
    # (or the read changed its mind) and this read says it is an ordinary
    # PO/DN/SI/UNKNOWN document. Leaving `is_split_parent=True` made it
    # invisible to the holding area and unplaceable by every attach sweep, so a
    # perfectly readable PO could never reach a set.
    if (
        vtype
        and vtype != "COMBINED"
        and getattr(doc, "is_split_parent", False)
        and not getattr(doc, "split_completed_at", None)
    ):
        doc.is_split_parent = False

    # Fields derived from the PREVIOUS read must not survive a re-read. The
    # old code only ever assigned them, so an SI that used to read INV-999 kept
    # naming the packet after a re-read returned no number, and a one-time
    # ambiguity flag made the document un-attachable forever.
    manual_po = (getattr(doc, "po_no_source", None) or "") == "manual"
    doc.si_no = None
    doc.invoice_no = None
    doc.dn_no = None
    if not manual_po:
        doc.po_no_raw = None
        doc.po_no_normalized = None
        # If this read produced no PO reference, the document must be held,
        # not left attached to the set its PREVIOUS read was placed in. The
        # reconciliation drift guard only looks at documents that still carry a
        # number, so a cleared number would otherwise compare unmoved.
        if not result.get("po_no_raw"):
            doc.po_set_id = None
    # The current read's ambiguity, true or false — never the previous read's.
    # A manual PO is a human decision, so it resolves the model's uncertainty
    # even when this read still reports more than one PO number.
    doc.po_reference_ambiguous = False if manual_po else bool(result.get("po_reference_ambiguous"))

    if result and result.get("po_no_raw") and not manual_po:
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
        and not manual_po
        and not doc.po_no_raw
    ):
        doc.po_no_raw = result["document_number"]
        from app.services.grouping import normalize_po_no

        doc.po_no_normalized = normalize_po_no(result["document_number"])

    # persist line items (replace existing for this doc)
    from app.models import LineItem

    # clear old items for idempotency on retry
    for li in list(doc.line_items):
        s.delete(li)
    s.flush()
    # A row the VLM returned is never dropped. Dropping a PO row removes a
    # line from the baseline the DN and SI are compared against, and the set
    # can then merge with that line never checked. A row the VLM could not
    # read a quantity from is kept with a zero quantity, which the existing
    # `non_positive_quantity` guard quarantines — the safe direction, and
    # the reviewer sees the line. A row without a description keeps its
    # quantity: the description is a fallback matching signal, not a
    # reconciliation signal.
    without_description = 0
    without_quantity = 0
    for li in result.get("line_items", []) or []:
        qty = li.get("quantity")
        price = li.get("unit_price")
        description = li.get("description")
        if description is None:
            description = ""
            without_description += 1
        locale = getattr(getattr(cfg, "matching", None), "locale", "en_IN") or "en_IN"
        qty_i = _parse_scaled_int(qty, locale=locale)
        from app.services.sanitizer import parse_price_scaled

        price_i = parse_price_scaled(price)
        if qty_i == 0:
            # No usable quantity — unreadable, rejected by the parser, or
            # printed as zero. Stored as zero on purpose: the set's
            # `non_positive_quantity` guard then quarantines it, which is the
            # safe direction, and the reviewer sees the line.
            without_quantity += 1
        s.add(
            LineItem(
                document_id=doc.id,
                line_item_no=str(li.get("line_item_no")) if li.get("line_item_no") else None,
                description=str(description),
                quantity=qty_i,
                unit_price=price_i,
                dn_no=str(li.get("dn_no")) if li.get("dn_no") else None,
            )
        )
    if without_description or without_quantity:
        logger.warning(
            "Document %s: kept %d line item(s) the VLM returned without a description and "
            "%d stored with a zero quantity. They are stored, not dropped: a zero quantity "
            "quarantines the PO Set as non_positive_quantity.",
            doc.id,
            without_description,
            without_quantity,
        )


def reclaim_stale_processing(cfg) -> int:
    """Release documents left in `processing` by a worker that died.

    `extract_document` claims a document with a compare-and-swap
    (pending -> processing) before the VLM call, and that claim is committed.
    If the process dies between the claim and the result — a service
    restart, a killed Prefect worker, a machine reboot — the row stays
    `processing` forever: the recovery sweep only looks at `pending`, and a
    later extraction attempt returns the row unchanged because the claim is
    already taken. The document would be reported as processed and never
    read again.

    A claim older than `extraction.stale_processing_seconds` cannot still be
    in flight: one attempt is bounded by `vlm.request_timeout_seconds` and
    the retry envelope, and the threshold is set well above that. Such a
    claim is released back to `pending` and the document is read again on
    the next pass.

    Returns the number of documents reclaimed.
    """
    from datetime import timedelta

    from sqlalchemy import update as _sa_update

    from app.models import Document

    timeout = int(getattr(cfg.extraction, "stale_processing_seconds", 900) or 900)
    cutoff = datetime.now(UTC) - timedelta(seconds=timeout)
    eng = get_engine(cfg)
    with Session(eng) as s:
        result = s.execute(
            _sa_update(Document)
            .where(
                Document.archived_at.is_(None),
                Document.extraction_status == ExtractionStatus.processing,
                Document.updated_at <= cutoff,
            )
            .values(extraction_status=ExtractionStatus.pending)
            .execution_options(synchronize_session=False)
        )
        s.commit()
        reclaimed = getattr(result, "rowcount", 0) or 0
    if reclaimed:
        logger.error(
            "Reclaimed %d document(s) left in 'processing' for more than %ds — the worker "
            "that claimed them died. They will be read again.",
            reclaimed,
            timeout,
        )
    return reclaimed


def repopulate_from_raw_json(doc_id: int, cfg) -> Document | None:
    """Re-derive normalized fields and line items from stored raw JSON without calling VLM."""
    import json

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if doc is None or doc.archived_at is not None or not doc.raw_extraction_json:
            return None
        try:
            result = json.loads(doc.raw_extraction_json)
        except Exception:
            return None
        populate_from_raw_dict(doc, result, s, cfg)
        s.commit()
        s.refresh(doc)
        return doc


_INFRA_STATUS_CODES = frozenset({401, 402, 403, 404, 408, 409, 425, 429})
_INFRA_CHAIN_DEPTH = 5


def _is_infrastructure_leaf(exc: BaseException) -> bool:
    import httpx
    import openai

    if isinstance(
        exc,
        (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        ),
    ):
        return True
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", None)
        return isinstance(status, int) and (status in _INFRA_STATUS_CODES or status >= 500)
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, httpx.NetworkError)):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    # Fail-closed config raised by _call_vlm (missing API key or vlm.model).
    return isinstance(exc, RuntimeError) and "not configured" in str(exc)


def _is_infrastructure_error(exc: BaseException | None) -> bool:
    """True when the failure is the provider or network's fault, not the document's.

    Walks the exception and its __cause__/__context__ chain (bounded depth,
    cycle-guarded). Content errors (schema or validation failures, a missing
    stored file, anything unknown) return False, so they keep the document's
    failed verdict.
    """
    frontier: list[tuple[BaseException, int]] = [(exc, 0)] if exc is not None else []
    seen: set[int] = set()
    while frontier:
        current, depth = frontier.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if _is_infrastructure_leaf(current):
            return True
        if depth + 1 < _INFRA_CHAIN_DEPTH:
            for nxt in (current.__cause__, current.__context__):
                if nxt is not None:
                    frontier.append((nxt, depth + 1))
    return False


def extract_document(doc_id: int, cfg) -> Document:
    from sqlalchemy import update as _sa_update

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            raise ValueError(f"document {doc_id} not found")
        if doc.archived_at is not None:
            # Archived documents are evidence, not work. A direct call must
            # not spend a VLM request or rewrite their extracted fields.
            return doc

        dtype = doc.doc_type.value if hasattr(doc.doc_type, "value") else str(doc.doc_type)
        if is_manual_only(dtype):
            return doc

        # Compare-and-swap: atomically claim pending → processing so two concurrent
        # callers (e.g. redo_extract route + recovery sweep running at the same time)
        # cannot both enter the VLM extraction for the same document.
        # Uses the same UPDATE…WHERE pattern as locking.acquire_lock — proven for SQLite WAL.
        cur_status = doc.extraction_status
        if cur_status == ExtractionStatus.pending:
            cas_result = s.execute(
                _sa_update(Document)
                .where(
                    Document.id == doc_id,
                    Document.extraction_status == ExtractionStatus.pending,
                )
                .values(extraction_status=ExtractionStatus.processing)
                .execution_options(synchronize_session=False)
            )
            s.commit()
            if getattr(cas_result, "rowcount", 0) == 0:
                # Another caller already claimed it — return current persisted state.
                s.refresh(doc)
                return doc
            s.refresh(doc)
        elif cur_status == ExtractionStatus.processing:
            # Already claimed by a concurrent caller — unless the claim is
            # stale (the worker died mid-call). Without this check a Prefect
            # retry saw `processing`, returned "success" and the document was
            # not read again until the next night's reclaim.
            timeout = int(getattr(cfg.extraction, "stale_processing_seconds", 900) or 900)
            cutoff = datetime.now(UTC) - timedelta(seconds=timeout)
            stale = s.execute(
                _sa_update(Document)
                .where(
                    Document.id == doc_id,
                    Document.extraction_status == ExtractionStatus.processing,
                    Document.updated_at <= cutoff,
                )
                .values(extraction_status=ExtractionStatus.pending)
                .execution_options(synchronize_session=False)
            )
            s.commit()
            if getattr(stale, "rowcount", 0) > 0:
                # Re-enter through the normal claim path; the CAS protects
                # against a second retry doing the same.
                return extract_document(doc_id, cfg)
            s.refresh(doc)
            return doc
        elif cur_status == ExtractionStatus.valid:
            # Already read. A file stays in the input folder until its PO
            # Set merges, so a set that is still waiting for a document would
            # otherwise be re-read on every sync: one VLM call per file per
            # night, and the attempt count climbing until a perfectly good
            # document was marked permanently failed. A deliberate re-read is
            # the operator's: redo_extract sets the status back to pending.
            #
            # The one exception is a split parent whose split never
            # completed (the worker died between cutting the children and
            # stamping the row): it must be re-processed so the children are
            # re-derived from the parent.
            split_incomplete = bool(getattr(doc, "is_split_parent", False)) and not getattr(
                doc, "split_completed_at", None
            )
            if not split_incomplete:
                return doc

        if (doc.extraction_attempt_count or 0) >= _attempt_cap(cfg):
            # Attempt cap reached. This is a terminal state, not a no-op: the
            # document will never be read again until an operator resets the
            # count (POST /po_sets/{id}/redo_extract). It returns normally
            # rather than raising so Prefect does not burn its remaining
            # retries re-raising an already-decided verdict, which means the
            # CALLER must not treat a clean task return as success — the
            # persisted status is the source of truth (see
            # app/flows/sync.py::_persisted_extraction_status).
            doc.extraction_status = ExtractionStatus.failed
            s.commit()
            s.refresh(doc)
            logger.error(
                "Extraction permanently failed for doc %s (%s): attempt cap %d reached, "
                "no further attempts will be made without an operator reset",
                doc_id,
                doc.original_filename,
                _attempt_cap(cfg),
            )
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

            # Multi-doc split (PLAN Rev 2 Step 4): COMBINED is the trigger for a
            # filesystem split, not a 3-section union. The parent row stays
            # sterile (no numbers, no set, no lines); children are cut as files
            # into input/ and re-extracted fresh on the next run. The old
            # 3-section gate (FR-6.7) and union persistence are deleted.
            if result.get("document_type") == "COMBINED":
                return _handle_combined(doc, s, result, cfg)

            populate_from_raw_dict(doc, result, s, cfg)
            s.commit()
            s.refresh(doc)
            return doc
        except Exception as e:
            if _is_infrastructure_error(e):
                # A provider or network fault says nothing about the document.
                # Refund the attempt and leave it pending, so Prefect's retry
                # and the next sync claim it again instead of condemning it.
                doc.extraction_attempt_count = max(0, (doc.extraction_attempt_count or 1) - 1)
                doc.extraction_status = ExtractionStatus.pending
                s.commit()
                s.refresh(doc)
                logger.warning(
                    "VLM infrastructure fault for doc %s (%s): %s. Document stays pending "
                    "for the next run; attempt not counted.",
                    doc_id,
                    doc.original_filename,
                    type(e).__name__,
                )
                raise e
            doc.extraction_status = ExtractionStatus.failed
            s.commit()
            s.refresh(doc)
            logger.warning(
                "VLM extraction failed for doc %s (attempt %s): %s",
                doc_id,
                doc.extraction_attempt_count,
                type(e).__name__,
            )
            raise e
