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
from pathlib import Path
from typing import Literal

import instructor
from openai import OpenAI
from pydantic import BaseModel, Field
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
    "document_number (own SI No/DN No/PO No or null for COMBINED), "
    "po_reference (the PO No visible for SI/DN/COMBINED, null for PO), "
    "po_reference_ambiguous:bool, "
    "vendor_name, line_items[] {line_item_no, description, quantity: NUMBER-ONLY as printed "
    "(no unit/UOM/currency), dn_no}. "
    "COMBINED = single PDF containing PO+DN+SI sections together (CA merged). Omit nulls, no markdown."
)

_PAGE_PROMPT = (
    "STEP 1 — HEADER SCAN (first page top 20% for po_reference/document_number; each page table for rows):\n"
    "  Locate strings 'PO No'/'P.O. No'/'Purchase Order No'/'Buyers Order No'/'Order No'/'PO Reference' → po_reference "
    "(strip non-alnum, upper; e.g. PO-210851 → 210851). Never use filename.\n"
    "  If MORE THAN ONE distinct PO number is visible on a document, set po_reference to the first and set "
    "po_reference_ambiguous true — the document will be quarantined rather than guessed at.\n"
    "  Locate 'DN No'/'Delivery Note No'/'GDN No'/'SI No'/'Invoice No'/'PO No' → document_number (its own number; for COMBINED use primary SI No or PO No).\n"
    "  vendor_name = supplier company header.\n\n"
    "STEP 2 — CLASSIFY document_type exactly one of PO, SI, DN, COMBINED, SKIP, UNKNOWN:\n"
    "  PO=Purchase Order, SI=Sales/Tax Invoice, DN=Delivery Note/Packing List, "
    "COMBINED=single PDF that visibly contains PO table + DN table + SI table together (often CA 'Combined' stamp, 3 sections, multi-page), "
    "SKIP=covers/T&C/blank pages, UNKNOWN=unreadable. DN bundles '(2 DNs)'/'(3 DNs)' are DN not COMBINED.\n"
    "  For COMBINED: set has_po_section=true, has_dn_section=true, has_si_section=true if and only if each respective section is visibly present.\n\n"
    "STEP 3 — LINE ITEMS (only rows with product description AND quantity>0, scan ALL pages sequentially):\n"
    "  line_item_no — MOST IMPORTANT. The vendor's own side column (Sl No / Item No / #) is often just a running "
    "docket counter and repeats '1' on every page or every docket. It is NOT the PO line. Resolution order:\n"
    "    (a) if the description or a nearby note contains a marker like 'Line Item - 3' or 'Line No. 3', use THAT number;\n"
    "    (b) otherwise use the printed side column number.\n"
    "  Copy the number exactly as printed, including any hyphen: '1-1', '2-1', '10' stay verbatim. Do not renumber, pad or strip leading zeros.\n"
    "  description: COMPLETE, do not truncate. Keep the 'Line Item - N' text inside it if it was printed there.\n"
    "  dn_no: only if a delivery-note number is printed against THIS individual row; otherwise null. "
    "Most vendors print the DN number once in the header, not per line.\n"
    "  For COMBINED: emit union of all sections but do NOT duplicate sections.\n"
    "  EXCLUDE subtotal, VAT, tax, total, amount-in-words, payment terms, signatures.\n\n"
    "STEP 4 — QUANTITY (the NUMBER ONLY; do NOT calculate, do NOT scale):\n"
    '  quantity = the digits as printed, and nothing else. "1", "50", "12.5", "12.45000000".\n'
    "  Strip anything that is not part of the number: unit / UOM (EA, PCS, BOX, KG, M, M3, "
    "MT, BAGS), currency symbols, and words like EACH or SET. If the cell reads "
    "'12.5 EA' return \"12.5\"; if it reads '1,200.00 M3' return \"1200.00\".\n"
    "  UOM itself is NOT extracted anywhere in this system. Never return it.\n\n"
    "STEP 5 — MULTI-PAGE / MULTI-DN: if PDF contains 2-3 DNs or COMBINED multi-page, emit first po_reference, include ALL line_items across pages in order.\n"
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
    'COMBINED: {"document_type":"COMBINED","document_number":"SIV-RAK-25-3049","po_reference":"3049PO123","line_items":[{"line_item_no":"1","description":"WASHER, FLAT SAE 1/4 IN","quantity":"50","unit_price":"120.00"}]}\n'
)


class _VLMLineItem(BaseModel):
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
        "po_no_raw": resp.po_reference or resp.document_number,
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
    from datetime import UTC, datetime

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
        split_combined(doc.stored_path, result, cfg)
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

    if result and result.get("po_no_raw"):
        doc.po_no_raw = result["po_no_raw"]
        # normalize for grouping — same as grouping.normalize_po_no (split revision ", 0")
        from app.services.grouping import normalize_po_no

        doc.po_no_normalized = normalize_po_no(result["po_no_raw"])
    if result and result.get("po_reference_ambiguous"):
        doc.po_reference_ambiguous = True

    # also store DN/SI numbers if present (outside po_no_raw guard)
    if result.get("document_type") == "SI" and result.get("document_number"):
        doc.si_no = result["document_number"]
        doc.invoice_no = result["document_number"]
    if result.get("document_type") == "DN" and result.get("document_number"):
        doc.dn_no = result["document_number"]
    if result.get("document_type") == "PO" and result.get("document_number") and not doc.po_no_raw:
        doc.po_no_raw = result["document_number"]
        from app.services.grouping import normalize_po_no

        doc.po_no_normalized = normalize_po_no(result["document_number"])

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
        locale = getattr(getattr(cfg, "matching", None), "locale", "en_IN") or "en_IN"
        qty_i = _parse_scaled_int(qty, locale=locale)
        price_i = _parse_scaled_int(price, locale=locale)
        s.add(
            LineItem(
                document_id=doc.id,
                line_item_no=str(li.get("line_item_no")) if li.get("line_item_no") else None,
                description=str(li.get("description")),
                quantity=qty_i,
                unit_price=price_i,
                dn_no=str(li.get("dn_no")) if li.get("dn_no") else None,
            )
        )


def repopulate_from_raw_json(doc_id: int, cfg) -> Document | None:
    """Re-derive normalized fields and line items from stored raw JSON without calling VLM."""
    import json

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if doc is None or not doc.raw_extraction_json:
            return None
        try:
            result = json.loads(doc.raw_extraction_json)
        except Exception:
            return None
        populate_from_raw_dict(doc, result, s, cfg)
        s.commit()
        s.refresh(doc)
        return doc


def extract_document(doc_id: int, cfg) -> Document:
    from sqlalchemy import update as _sa_update

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            raise ValueError(f"document {doc_id} not found")

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
            if cas_result.rowcount == 0:
                # Another caller already claimed it — return current persisted state.
                s.refresh(doc)
                return doc
            s.refresh(doc)
        elif cur_status == ExtractionStatus.processing:
            # Already claimed by a concurrent caller; leave it.
            return doc

        if (doc.extraction_attempt_count or 0) >= 3:
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
                doc.extraction_attempt_count,
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
