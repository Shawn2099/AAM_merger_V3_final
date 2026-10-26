"""Provider outages must not condemn documents (infrastructure vs content errors)."""

from __future__ import annotations

import contextlib
from pathlib import Path


def _cfg(tmp_path, name):
    from app.core.config import load_config

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / f"{name}.db")
    cfg.paths.stored_documents_folder = str(tmp_path / f"stored_{name}")
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)
    return cfg


def _pending_doc(cfg, tmp_path, sha, *, attempts=0):
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus
    from app.models.base import Base

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash=sha,
            original_filename=f"{sha}.pdf",
            stored_path=str(Path(cfg.paths.stored_documents_folder) / f"{sha}.pdf"),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.pending,
            extraction_attempt_count=attempts,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        return doc.id


def _load(cfg, doc_id):
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import Document

    with Session(get_engine(cfg)) as s:
        d = s.get(Document, doc_id)
        return d.extraction_status, d.extraction_attempt_count


def _rate_limit_error(status=429):
    import httpx
    import openai

    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    resp = httpx.Response(status, request=req, json={"error": {"message": "slow down"}})
    return openai.RateLimitError("rate limited", response=resp, body=None)


def _raise(exc):
    def _fn(*a, **kw):
        raise exc

    return _fn


def test_rate_limit_leaves_document_pending_and_uncounted(tmp_path, monkeypatch):
    import openai
    import pytest

    from app.models import ExtractionStatus
    from app.services.extraction import extract_document

    cfg = _cfg(tmp_path, "rl")
    doc_id = _pending_doc(cfg, tmp_path, "rl01", attempts=1)
    monkeypatch.setattr("app.services.extraction._call_vlm", _raise(_rate_limit_error()))

    with pytest.raises(openai.APIStatusError):
        extract_document(doc_id, cfg)

    status, attempts = _load(cfg, doc_id)
    assert status == ExtractionStatus.pending
    assert attempts == 1  # unchanged from before the call


def test_repeated_outage_never_fails_the_document(tmp_path, monkeypatch):
    import openai
    import pytest

    from app.models import ExtractionStatus
    from app.services.extraction import extract_document

    cfg = _cfg(tmp_path, "rep")
    doc_id = _pending_doc(cfg, tmp_path, "rep01")
    monkeypatch.setattr("app.services.extraction._call_vlm", _raise(_rate_limit_error(503)))

    for _ in range(4):
        with pytest.raises(openai.APIStatusError):
            extract_document(doc_id, cfg)

    status, attempts = _load(cfg, doc_id)
    assert status == ExtractionStatus.pending
    assert attempts == 0


def test_missing_api_key_stays_pending(tmp_path, monkeypatch):
    import pytest

    from app.models import ExtractionStatus
    from app.services.extraction import extract_document

    cfg = _cfg(tmp_path, "key")
    doc_id = _pending_doc(cfg, tmp_path, "key01")
    monkeypatch.setattr(
        "app.services.extraction._call_vlm",
        _raise(RuntimeError("OPENROUTER_API_KEY not configured — set in .env (fail-closed)")),
    )

    with pytest.raises(RuntimeError):
        extract_document(doc_id, cfg)

    status, attempts = _load(cfg, doc_id)
    assert status == ExtractionStatus.pending
    assert attempts == 0


def test_content_error_still_fails_and_counts(tmp_path, monkeypatch):
    import pydantic
    import pytest

    from app.models import ExtractionStatus
    from app.services.extraction import _VLMPageExtraction, extract_document

    try:
        _VLMPageExtraction.model_validate({"document_type": "NOPE"})
    except pydantic.ValidationError as ve:
        validation_error = ve
    else:  # pragma: no cover - the schema must reject this
        raise AssertionError("schema accepted an invalid document_type")

    cfg = _cfg(tmp_path, "content")
    doc_id = _pending_doc(cfg, tmp_path, "content01")
    monkeypatch.setattr("app.services.extraction._call_vlm", _raise(validation_error))

    with pytest.raises(pydantic.ValidationError):
        extract_document(doc_id, cfg)

    status, attempts = _load(cfg, doc_id)
    assert status == ExtractionStatus.failed
    assert attempts == 1


def test_outage_then_success_on_next_call(tmp_path, monkeypatch):
    import openai
    import pytest

    from app.models import ExtractionStatus
    from app.services.extraction import extract_document

    cfg = _cfg(tmp_path, "recover")
    doc_id = _pending_doc(cfg, tmp_path, "recover01")
    calls = []

    def flaky(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise _rate_limit_error()
        return {"document_type": "PO", "line_items": []}

    monkeypatch.setattr("app.services.extraction._call_vlm", flaky)

    with pytest.raises(openai.APIStatusError):
        extract_document(doc_id, cfg)
    assert _load(cfg, doc_id) == (ExtractionStatus.pending, 0)

    doc = extract_document(doc_id, cfg)
    assert len(calls) == 2
    assert doc.extraction_status == ExtractionStatus.valid
    assert doc.extraction_attempt_count == 1


def test_helper_classifies_leaves_and_wrapped_causes():
    import httpx
    import openai

    from app.services.extraction import _is_infrastructure_error

    req = httpx.Request("POST", "https://x.invalid")

    def status_err(code):
        return openai.APIStatusError("boom", response=httpx.Response(code, request=req), body=None)

    # infrastructure
    assert _is_infrastructure_error(_rate_limit_error(429))
    assert _is_infrastructure_error(_rate_limit_error(503))
    assert _is_infrastructure_error(openai.APIConnectionError(request=req))
    assert _is_infrastructure_error(openai.APITimeoutError(request=req))
    assert _is_infrastructure_error(status_err(402))
    assert _is_infrastructure_error(status_err(408))
    assert _is_infrastructure_error(status_err(500))
    assert _is_infrastructure_error(httpx.ReadTimeout("slow", request=req))
    assert _is_infrastructure_error(httpx.ConnectError("nope", request=req))
    assert _is_infrastructure_error(TimeoutError("t"))
    assert _is_infrastructure_error(ConnectionResetError("reset"))
    assert _is_infrastructure_error(RuntimeError("OPENROUTER_API_KEY not configured"))
    assert _is_infrastructure_error(RuntimeError("vlm.model not configured in config.yaml"))

    # wrapped: infrastructure cause behind a content-looking wrapper
    try:
        try:
            raise _rate_limit_error()
        except Exception as inner:
            raise ValueError("wrapper") from inner
    except ValueError as outer:
        assert _is_infrastructure_error(outer)

    # content / unknown
    assert not _is_infrastructure_error(status_err(400))
    assert _is_infrastructure_error(status_err(404))  # retired model id: config fault
    # R-10: a missing stored copy is a storage fault, not a bad document
    assert _is_infrastructure_error(FileNotFoundError("stored_path not found"))
    assert not _is_infrastructure_error(RuntimeError("something else broke"))
    assert not _is_infrastructure_error(ValueError("bad"))
    assert not _is_infrastructure_error(None)

    import pydantic

    try:
        from app.services.extraction import _VLMPageExtraction

        _VLMPageExtraction.model_validate({"document_type": "NOPE"})
    except pydantic.ValidationError as ve:
        assert not _is_infrastructure_error(ve)


def test_helper_guards_cycles():
    from app.services.extraction import _is_infrastructure_error

    a = ValueError("a")
    b = ValueError("b")
    a.__cause__ = b
    b.__cause__ = a  # cycle, must terminate
    assert _is_infrastructure_error(a) is False


def test_real_instructor_stack_surfaces_429_as_infrastructure(tmp_path, monkeypatch):
    """Drive _call_vlm through instructor + openai with a mock 429 transport."""
    import httpx
    import openai
    import pytest

    import app.services.extraction as ext
    from app.services.extraction import _is_infrastructure_error

    pdf = tmp_path / "po.pdf"
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(str(pdf))

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def handler(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    real_openai = openai.OpenAI

    def offline_openai(**kwargs):
        kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(handler))
        kwargs["max_retries"] = 0
        return real_openai(**kwargs)

    monkeypatch.setattr(ext, "OpenAI", offline_openai)

    with pytest.raises(Exception) as info:
        ext._call_vlm(str(pdf), "PO", _cfg(tmp_path, "mock"))

    assert _is_infrastructure_error(info.value), repr(info.value)


def test_http_status_classes():
    """Config/credit/rate faults stay pending; request-shaped faults are content errors."""
    import httpx
    import openai

    from app.services.extraction import _is_infrastructure_error

    def status_error(code):
        req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        resp = httpx.Response(code, request=req, json={"error": {"message": "x"}})
        return openai.APIStatusError("status", response=resp, body=None)

    for code in (401, 402, 403, 404, 429, 500, 502, 503):
        assert _is_infrastructure_error(status_error(code)), code
    for code in (400, 413, 422):
        assert not _is_infrastructure_error(status_error(code)), code


def test_known_limitation_provider_fault_is_retried_every_run(tmp_path, monkeypatch):
    # A document the provider can never serve (e.g. always times out) is never
    # charged an attempt, so it is retried on every sync and never reaches
    # `failed`. PRODUCT section 8 records this; the dashboard shows it as "Not read".
    from app.models import ExtractionStatus
    from app.services.extraction import extract_document

    cfg = _cfg(tmp_path, "limit")
    doc_id = _pending_doc(cfg, tmp_path, "limit01")
    monkeypatch.setattr("app.services.extraction._call_vlm", _raise(_rate_limit_error(504)))
    for _ in range(6):
        with contextlib.suppress(Exception):
            extract_document(doc_id, cfg)
    status, attempts = _load(cfg, doc_id)[:2]
    assert status == ExtractionStatus.pending
    assert attempts == 0
