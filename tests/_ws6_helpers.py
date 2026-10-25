"""Shared helpers for tests/test_ws6_*.py. Not a test module."""

from __future__ import annotations

import hashlib
import threading
from collections import Counter
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_robustness_harness import line, write_pdf

from app.core.database import get_engine
from app.models import AuditAction, AuditLog, Document, POSet
from app.services import extraction


class SimulatedCrash(BaseException):
    """Raised inside the VLM call to simulate the process dying mid-extraction.

    BaseException on purpose: the product's `except Exception` blocks must not
    catch it, exactly as they could not catch a killed process.
    """


def payload(kind: str, number: str | None, po_ref: str | None, lines: list[dict]) -> dict:
    """The dict `extraction._call_vlm` returns (same shape as scripts/ui_test_env.py)."""
    return {
        "document_type": kind,
        "document_number": number,
        "po_no_raw": number if kind == "PO" else po_ref,
        "po_reference": None if kind == "PO" else po_ref,
        "po_reference_ambiguous": False,
        "vendor_name": "ACME",
        "components": [],
        "line_items": lines,
    }


class CannedVLM:
    """Replaces `extraction._call_vlm`; keyed by SHA-256 (the stored file is `<sha>.pdf`)."""

    def __init__(self, monkeypatch):
        self.payloads: dict[str, dict] = {}
        self.calls: Counter[str] = Counter()
        self.crash_on: dict[str, int] = {}  # sha -> the call number that raises SimulatedCrash
        monkeypatch.setattr(extraction, "_call_vlm", self)

        def _no_network(**kwargs):
            raise AssertionError("a WS6 test tried to build a real OpenAI client")

        monkeypatch.setattr(extraction, "OpenAI", _no_network)

    def add(self, path: Path, body: dict) -> str:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        self.payloads[sha] = body
        return sha

    def __call__(self, stored_path, doc_type, cfg):
        sha = Path(stored_path).stem
        self.calls[sha] += 1
        if self.crash_on.get(sha) == self.calls[sha]:
            raise SimulatedCrash(sha)
        return dict(self.payloads[sha])


def plain_extract_task_for(cfg):
    """`flows.sync._extract_task_for` without the Prefect task wrapper.

    Same extraction code (`extract_task.fn` -> `extract_document`); only the
    Prefect task-run bookkeeping is skipped, so 500 documents fit in a test
    and a SimulatedCrash is not intercepted by the Prefect engine.
    """
    from app.flows.sync import extract_task

    return lambda doc_id, cfg_path=None: extract_task.fn(doc_id, cfg_path=cfg_path)


def bind_poller_config(monkeypatch, cfg) -> None:
    """`run_poller_sync` reads the settle time from `app.core.config.load_config()`."""
    monkeypatch.setattr("app.core.config.load_config", lambda path=None: cfg)


class SyncRecorder:
    """Records every sync pass this test starts through `POST /sync` or a poller tick.

    The scan route and `run_poller_sync` both import `sync_flow` at call time and then
    throw its result away (`POST /sync` answers before the pass even starts, and a
    background thread's exception goes to the log). A pass that reported
    `database is locked` would therefore be invisible to the test. This wraps the
    module attribute, calls the real flow, and keeps the result — or the exception.
    """

    def __init__(self, monkeypatch):
        import app.flows.sync as flow_sync

        self.results: list[dict] = []
        self.exceptions: list[BaseException] = []
        real = flow_sync.sync_flow

        def recording(*args, **kwargs):
            try:
                result = real(*args, **kwargs)
            except BaseException as e:  # a crash must reach the test, not just the log
                self.exceptions.append(e)
                raise
            self.results.append(result or {})
            return result

        monkeypatch.setattr(flow_sync, "sync_flow", recording)

    def clear(self) -> None:
        self.results.clear()
        self.exceptions.clear()

    def assert_clean(self) -> None:
        """No pass raised, and none reported an error (a locked DB is an error)."""
        assert self.exceptions == [], [repr(e) for e in self.exceptions]
        bad = [r for r in self.results if r.get("errors", 0)]
        assert bad == [], bad


def document_row(cfg, name: str) -> Document | None:
    """The newest non-archived document row with this original filename."""
    with Session(get_engine(cfg)) as s:
        doc = (
            s.query(Document)
            .filter(Document.original_filename == name, Document.archived_at.is_(None))
            .order_by(Document.id.desc())
            .first()
        )
        if doc is not None:
            s.expunge(doc)
        return doc


def backdate_documents(cfg, seconds: float, column: str = "created_at") -> None:
    from datetime import UTC, datetime, timedelta

    when = datetime.now(UTC) - timedelta(seconds=seconds)
    with Session(get_engine(cfg)) as s:
        s.execute(update(Document).values({column: when}))
        s.commit()


def output_pdfs(cfg) -> list[Path]:
    return sorted(p for p in Path(cfg.paths.output_folder).iterdir() if p.suffix.lower() == ".pdf")


def hidden_output_files(cfg) -> list[Path]:
    return sorted(p for p in Path(cfg.paths.output_folder).iterdir() if p.name.startswith("."))


def audit_count(cfg, po_set_id: int, action: str | AuditAction) -> int:
    with Session(get_engine(cfg)) as s:
        return s.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.po_set_id == po_set_id, AuditLog.action == action
            )
        )


def set_row(cfg, po_no: str) -> POSet:
    with Session(get_engine(cfg)) as s:
        ps = s.scalars(select(POSet).where(POSet.po_no_normalized == po_no)).one()
        s.expunge(ps)
        return ps


class Gate:
    """Blocks the first call of a wrapped function until `release()`."""

    def __init__(self):
        self.entered = threading.Event()
        self.released = threading.Event()

    def wrap(self, real):
        def gated(*args, **kwargs):
            if not self.entered.is_set():
                self.entered.set()
                assert self.released.wait(120), "the test never released the gate"
            return real(*args, **kwargs)

        return gated

    def release(self):
        self.released.set()


def sync_run_threads() -> set[threading.Thread]:
    """The daemon threads POST /sync starts (`_run_sync` in tests/test_concurrency.py)."""
    return {t for t in threading.enumerate() if "_run_sync" in t.name}


def join_new_sync_threads(before: set[threading.Thread], timeout: float = 120) -> None:
    """Wait for the `_run_sync` threads a test started, before its patches go.

    Copied in spirit from `tests/test_concurrency.py::_join_new_sync_threads`: POST
    /sync hands the flow to a daemon thread and returns at once, so a test that ends
    first would let that thread read the next test's config or database.
    """
    for t in sync_run_threads() - before:
        t.join(timeout)
        assert not t.is_alive(), f"sync thread {t.name} still running after {timeout}s"


def drop_set(cfg, vlm, po: str, width: float, dn_qty="10", si_qty="10", po_qty="10", names=None):
    """Write PO, DN, SI for one set into the input folder; return {kind: (path, sha)}."""
    names = names or {"PO": f"PO-{po}.pdf", "DN": f"DN-{po}.pdf", "SI": f"SI-{po}.pdf"}
    out = {}
    inp = Path(cfg.paths.input_folder)
    for i, (kind, qty) in enumerate((("PO", po_qty), ("DN", dn_qty), ("SI", si_qty))):
        path = write_pdf(inp / names[kind], width + i)
        number = po if kind == "PO" else f"{kind}-{po}"
        sha = vlm.add(
            path, payload(kind, number, None if kind == "PO" else po, [line("1", qty, "BOLT")])
        )
        out[kind] = (path, sha)
    return out
