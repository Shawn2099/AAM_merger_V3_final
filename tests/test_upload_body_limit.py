"""H1-4: the set page's customs/shipping upload is size-limited before the body is parsed."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.core.http_errors import BodyTooLarge
from app.main import BodySizeLimitMiddleware

PATTERN = re.compile(r"^/po_sets/\d+/upload$")


def _scope(path="/po_sets/7/upload", method="POST", length=None, accept=""):
    headers = [(b"accept", accept.encode())]
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    return {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }


def _mw(endpoint, max_bytes=10):
    return BodySizeLimitMiddleware(
        endpoint,
        path_pattern=PATTERN,
        methods=("POST",),
        max_bytes=max_bytes,
        display_mb=100,
        heading="That upload didn't work",
        back_href=lambda scope: scope["path"].removesuffix("/upload") + "/view",
    )


async def _run(middleware, scope, chunks):
    sent = []
    queue = list(chunks)

    async def receive():
        body = queue.pop(0) if queue else b""
        return {"type": "http.request", "body": body, "more_body": bool(queue)}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_an_oversized_content_length_is_refused_before_the_app_runs():
    called = False

    async def endpoint(scope, receive, send):  # pragma: no cover - must not run
        nonlocal called
        called = True

    sent = await _run(_mw(endpoint), _scope(length=1024), [b""])

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_a_chunked_body_over_the_cap_is_a_413_even_when_the_app_answers_400():
    # FastAPI turns an exception raised while it parses File/Form parameters into HTTP 400.
    async def endpoint(scope, receive, send):
        try:
            while True:
                message = await receive()
                if not message.get("more_body"):
                    break
        except BodyTooLarge:
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"parse error"})

    sent = await _run(_mw(endpoint), _scope(), [b"12345678", b"12345678"])

    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]
    assert b"parse error" not in b"".join(m.get("body", b"") for m in sent)


@pytest.mark.asyncio
async def test_an_app_that_answers_413_itself_keeps_its_own_message():
    async def endpoint(scope, receive, send):
        try:
            while True:
                if not (await receive()).get("more_body"):
                    break
        except BodyTooLarge:
            await send({"type": "http.response.start", "status": 413, "headers": []})
            await send({"type": "http.response.body", "body": b"mine"})

    sent = await _run(_mw(endpoint), _scope(), [b"12345678", b"12345678"])

    assert b"mine" in b"".join(m.get("body", b"") for m in sent)


@pytest.mark.asyncio
async def test_other_paths_and_methods_are_not_limited():
    async def endpoint(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    for scope in (
        _scope(path="/po_sets/7/merge", length=10_000),
        _scope(path="/po_sets/7/upload", method="GET", length=10_000),
        _scope(path="/po_sets/x/upload", length=10_000),
        _scope(path="/po_sets/7/upload/extra", length=10_000),
    ):
        sent = await _run(_mw(endpoint, max_bytes=1), scope, [b""])
        assert sent[0]["status"] == 200, scope["path"]


@pytest.mark.asyncio
async def test_the_browser_page_names_the_limit_and_links_back_to_the_set():
    async def endpoint(scope, receive, send):  # pragma: no cover
        pass

    sent = await _run(_mw(endpoint), _scope(length=1024, accept="text/html"), [b""])
    page = sent[1]["body"].decode()

    assert sent[0]["status"] == 413
    assert "100 MB" in page
    assert "href='/po_sets/7/view'" in page
    assert "That upload didn" in page
    assert "/manual/merger" not in page


# --- through the real app (a fresh create_app, with a small cap) -----------------------


@pytest.fixture()
def small_cap_client(tmp_path, monkeypatch):
    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models.base import Base

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "t.db"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.combined_folder = tmp_path / "combined"
    cfg.paths.log_folder = tmp_path / "logs"
    for p in (
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.combined_folder,
        cfg.paths.log_folder,
    ):
        Path(p).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(cfg))
    for target in (
        "app.api.routes.sync.load_config",
        "app.api.routes.po_sets.load_config",
        "app.api.routes.dashboard.load_config",
        "app.flows.sync.load_config",
        "app.services.sync_lock.load_config",
    ):
        monkeypatch.setattr(target, lambda path=None: cfg)
    monkeypatch.setattr("app.main.MAX_UPLOAD_BYTES", 200_000)
    from app.main import create_app
    from app.models import POSet, POSetStatus

    with __import__("sqlalchemy.orm", fromlist=["Session"]).Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized="PO-CAP", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        pid = ps.id
    with TestClient(create_app()) as c:
        yield c, cfg, pid


def _pdf(pages=1) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_a_small_upload_still_works(small_cap_client):
    client, _cfg, pid = small_cap_client
    r = client.post(
        f"/po_sets/{pid}/upload",
        files={"file": ("c.pdf", _pdf(), "application/pdf")},
        data={"doc_type": "CUSTOMS"},
        follow_redirects=False,
    )

    assert r.status_code == 302


def test_a_declared_oversized_upload_is_a_413_and_writes_nothing(small_cap_client):
    client, cfg, pid = small_cap_client
    big = b"%PDF-1.4\n" + b"0" * 2_500_000
    r = client.post(
        f"/po_sets/{pid}/upload",
        files={"file": ("big.pdf", big, "application/pdf")},
        data={"doc_type": "CUSTOMS"},
    )

    assert r.status_code == 413
    assert list(Path(cfg.paths.stored_documents_folder).glob("*")) == []


def test_a_chunked_oversized_upload_is_a_413_not_a_400(small_cap_client):
    client, cfg, pid = small_cap_client
    boundary = "xBOUNDARYx"

    def body():
        yield (
            f'--{boundary}\r\nContent-Disposition: form-data; name="doc_type"\r\n\r\nCUSTOMS\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="big.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode()
        for _ in range(30):
            yield b"0" * 100_000
        yield f"\r\n--{boundary}--\r\n".encode()

    r = client.post(
        f"/po_sets/{pid}/upload",
        content=body(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    assert r.status_code == 413
    assert list(Path(cfg.paths.stored_documents_folder).glob("*")) == []
