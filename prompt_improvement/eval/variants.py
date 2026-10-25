"""Transport / parameter variants of extraction._call_vlm (copied logic, additive).

make_call_vlm(spec) -> function with _call_vlm's signature. It reads _SYSTEM_PROMPT /
_PAGE_PROMPT from app.services.extraction at call time and returns a dict like the app's
plus "_usage": {prompt_tokens, completion_tokens, reasoning_tokens, cost, latency_s}.

Variant spec grammar:  transport[+param...]
  transport: default | native | ocr | img150 | img200   (img<N> = PyMuPDF PNG at N DPI)
  params   : eff=<x>   extra_body {"reasoning": {"effort": x}}
             reff=<x>  extra_body {"reasoning_effort": x}
             seed=<n>  top-level seed
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import fitz  # PyMuPDF
import instructor
from openai import OpenAI

from app.services import extraction as ex


def parse_variant(spec: str) -> dict:
    parts = spec.split("+")
    v = {"transport": parts[0], "extra": {}, "top": {}}
    for p in parts[1:]:
        k, _, val = p.partition("=")
        if k == "eff":
            v["extra"]["reasoning"] = {"effort": val}
        elif k == "reff":
            v["extra"]["reasoning_effort"] = val
        elif k == "seed":
            v["top"]["seed"] = int(val)
        else:
            raise ValueError(p)
    return v


def _content(v: dict, pdf_path: Path, pdf_bytes: bytes) -> tuple[list, dict]:
    parts: list = [{"type": "text", "text": ex._PAGE_PROMPT}]
    extra: dict = {}
    t = v["transport"]
    if t in ("default", "native", "ocr"):
        b64 = base64.b64encode(pdf_bytes).decode()
        parts.append(
            {
                "type": "file",
                "file": {
                    "filename": pdf_path.name,
                    "file_data": f"data:application/pdf;base64,{b64}",
                },
            }
        )
        if t == "native":
            extra["plugins"] = [{"id": "file-parser", "pdf": {"engine": "native"}}]
        elif t == "ocr":
            extra["plugins"] = [{"id": "file-parser", "pdf": {"engine": "mistral-ocr"}}]
    elif t.startswith("img"):
        dpi = int(t[3:])
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            png = page.get_pixmap(dpi=dpi).tobytes("png")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()},
                }
            )
    else:
        raise ValueError(t)
    return parts, extra


def make_call_vlm(spec: str):
    v = parse_variant(spec)

    def _call(stored_path: str, doc_type: str, cfg) -> dict:
        api_key = os.getenv(cfg.vlm.api_key_env_var) or os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("no api key")
        pdf_path = Path(stored_path)
        parts, extra = _content(v, pdf_path, pdf_path.read_bytes())
        extra.update(v["extra"])
        extra["usage"] = {"include": True}  # accounting only: OpenRouter returns usage.cost
        client = instructor.from_openai(
            OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
                timeout=max(int(cfg.vlm.request_timeout_seconds), 180),
                max_retries=0,
            )
        )
        t0 = time.time()
        resp, comp = client.chat.completions.create_with_completion(
            model=cfg.vlm.model,
            response_model=ex._VLMPageExtraction,
            messages=[
                {"role": "system", "content": ex._SYSTEM_PROMPT},
                {"role": "user", "content": parts},
            ],
            max_retries=0,
            extra_body=extra,
            **v["top"],
        )
        lat = round(time.time() - t0, 2)
        u = comp.usage
        ud = u.model_dump() if u is not None else {}
        usage = {
            "prompt_tokens": ud.get("prompt_tokens"),
            "completion_tokens": ud.get("completion_tokens"),
            "reasoning_tokens": (ud.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "cost": ud.get("cost"),
            "latency_s": lat,
        }
        return {
            "document_type": resp.document_type,
            "document_number": resp.document_number,
            "po_reference": resp.po_reference,
            "po_reference_ambiguous": resp.po_reference_ambiguous,
            "page_count": resp.page_count,
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
            "_usage": usage,
        }

    return _call
