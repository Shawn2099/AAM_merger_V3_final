"""Shared harness for the tests/test_robustness_*.py adversarial suite (2026-10-24).

Not a test module in itself (it holds no tests); the other robustness files
import from it. The fake sits at the HTTP layer: the real `_call_vlm`, the real
rasteriser, the real `openai` client and the real `instructor` parser all run,
and only the bytes on the wire are invented. That is what lets the suite
exercise truncated JSON, prose replies, 402/5xx/timeouts and schema-invalid
payloads the way the production client would meet them.

Each fake PDF is identified by its first-page width (in points), recovered from
the PNG the rasteriser sent, so the fake needs no knowledge of hashes or order.
"""

from __future__ import annotations

import base64
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import openai
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import Base, Document, POSet
from app.services import extraction as ext

__all__ = [
    "HttpVLM",
    "doc_by_name",
    "extraction",
    "line",
    "make_cfg",
    "run_sync",
    "sets",
    "write_pdf",
]


# --------------------------------------------------------------------------- config


def make_cfg(tmp_path: Path, monkeypatch) -> Any:
    """A config pointed entirely at tmp_path, with every flow/route bound to it."""
    cfg = load_config("config.example.yaml")
    base = tmp_path.as_posix()
    cfg.paths.input_folder = f"{base}/input"
    cfg.paths.output_folder = f"{base}/output"
    cfg.paths.quarantine_folder = f"{base}/quarantine"
    cfg.paths.stored_documents_folder = f"{base}/stored"
    cfg.paths.combined_folder = f"{base}/combined"
    cfg.paths.archive_folder = f"{base}/archive"
    cfg.paths.database_path = f"{base}/aam.db"
    cfg.paths.log_folder = f"{base}/logs"
    cfg.ingestion.stability_poll_interval_seconds = 0
    cfg.ingestion.stability_poll_count = 1
    # No sleeping between Prefect retries in tests.
    cfg.extraction.retry_backoff_seconds = [0, 0, 0]
    # Production default: combined PDFs are quarantined unsplit.
    cfg.extraction.split_combined = False
    for sub in ("input", "output", "quarantine", "stored", "logs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(cfg))

    import app.api.routes.dashboard as dash
    import app.api.routes.po_sets as po_routes
    import app.api.routes.sync as sync_route
    import app.flows.sync as flow_sync
    import app.services.sync_lock as sync_lock

    for mod in (dash, po_routes, sync_route, flow_sync, sync_lock):
        monkeypatch.setattr(mod, "load_config", lambda *a, **kw: cfg)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    # extract_document dumps raw JSON into the CWD-relative data/raw_extractions
    # whenever that folder exists (extraction.py:984-997), which bypasses the
    # conftest path redirect and writes into the repository. Run from tmp_path.
    monkeypatch.chdir(tmp_path)
    return cfg


# --------------------------------------------------------------------------- PDFs


def write_pdf(path: Path, width: float, pages: int = 1, height: float = 200) -> Path:
    """A blank PDF whose first page is `width` points wide (the fake's dispatch key)."""
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=height)
    path.parent.mkdir(parents=True, exist_ok=True)
    w.write(str(path))
    return path


# --------------------------------------------------------------------------- VLM payloads


def line(no, qty, desc="ITEM", price="1.00", dn_no=None) -> dict:
    return {
        "line_item_no": no,
        "description": desc,
        "quantity": qty,
        "unit_price": price,
        "dn_no": dn_no,
    }


def extraction(
    doc_type: str | None,
    number: str | None = None,
    po_ref: str | None = None,
    lines: list[dict] | None = None,
    **extra,
) -> dict:
    """A VLM JSON payload, as the model would put it in the tool-call arguments."""
    body: dict = {
        "document_type": doc_type,
        "document_number": number,
        "po_reference": po_ref,
        "po_reference_ambiguous": False,
        "vendor_name": "ACME",
        "line_items": lines or [],
    }
    body.update(extra)
    return body


# --------------------------------------------------------------------------- HTTP fake


def _png_width_points(data_url: str, dpi: int = 150) -> float:
    raw = base64.b64decode(data_url.split(",", 1)[1])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    pixels = int.from_bytes(raw[16:20], "big")
    return float(round(pixels * 72 / dpi))


def _completion(name: str, arguments: str | None, content: str | None = None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    finish = "stop"
    if arguments is not None:
        message["tool_calls"] = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ]
        finish = "tool_calls"
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "fake/model",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class HttpVLM:
    """A fake OpenRouter endpoint behind the real `openai` + `instructor` stack.

    `responses[width]` may be:
      * a dict                      -> a well-formed tool call with these arguments
      * ("args", "<raw string>")    -> a tool call whose arguments are this exact text
      * ("prose", "<text>")         -> a plain assistant message with no tool call
      * ("status", code[, body])    -> an HTTP error status
      * ("raise", exc)              -> the transport raises (timeout, connect error)
      * ("body", "<raw text>", code)-> a raw HTTP body (e.g. truncated JSON)
      * a callable(n_call) -> one of the above (n_call is 1-based per width)
    """

    def __init__(self, monkeypatch, responses: dict[float, Any]):
        self.responses = responses
        self.calls: Counter[float] = Counter()
        self.unknown: list[float] = []
        mp = monkeypatch

        def factory(**kw):
            return openai.OpenAI(
                base_url=kw.get("base_url"),
                api_key=kw.get("api_key"),
                timeout=kw.get("timeout"),
                max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(self._handle)),
            )

        mp.setattr(ext, "OpenAI", factory)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        content = body["messages"][1]["content"]
        width = next(
            _png_width_points(p["image_url"]["url"])
            for p in content
            if p.get("type") == "image_url"
        )
        self.calls[width] += 1
        if width not in self.responses:
            self.unknown.append(width)
            return httpx.Response(500, json={"error": {"message": f"no fake for width {width}"}})
        spec = self.responses[width]
        if callable(spec):
            spec = spec(self.calls[width])
        tools = body.get("tools") or []
        name = tools[0]["function"]["name"] if tools else "_VLMPageExtraction"
        if isinstance(spec, dict):
            return httpx.Response(200, json=_completion(name, json.dumps(spec)))
        kind = spec[0]
        if kind == "args":
            return httpx.Response(200, json=_completion(name, spec[1]))
        if kind == "prose":
            return httpx.Response(200, json=_completion(name, None, content=spec[1]))
        if kind == "status":
            code = spec[1]
            payload = (
                spec[2] if len(spec) > 2 else {"error": {"message": f"HTTP {code}", "code": code}}
            )
            return httpx.Response(code, json=payload)
        if kind == "raise":
            raise spec[1]
        if kind == "body":
            return httpx.Response(
                spec[2], content=spec[1].encode(), headers={"content-type": "application/json"}
            )
        raise AssertionError(f"bad fake spec {spec!r}")


# --------------------------------------------------------------------------- running + reading


def run_sync() -> dict:
    from app.flows.sync import sync_flow

    return sync_flow()


def sets(cfg) -> list[POSet]:
    with Session(get_engine(cfg)) as s:
        rows = s.query(POSet).order_by(POSet.id).all()
        for r in rows:
            _ = [d.original_filename for d in r.documents]
        s.expunge_all()
        return rows


def doc_by_name(cfg, name: str) -> Document | None:
    with Session(get_engine(cfg)) as s:
        d = (
            s.query(Document)
            .filter(Document.original_filename == name)
            .order_by(Document.id.desc())
            .first()
        )
        if d is not None:
            _ = list(d.line_items)
            s.expunge(d)
        return d


def all_docs(cfg) -> list[Document]:
    with Session(get_engine(cfg)) as s:
        rows = s.query(Document).order_by(Document.id).all()
        for d in rows:
            _ = list(d.line_items)
        s.expunge_all()
        return rows


def quarantined_doc_folders(cfg) -> list[str]:
    root = Path(cfg.paths.quarantine_folder) / "_documents"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


ResponseSpec = dict | tuple | Callable[[int], Any]
