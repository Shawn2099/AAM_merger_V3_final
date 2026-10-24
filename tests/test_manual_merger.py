"""Manual PDF merger - production contract (FR-14.11-14.13).

The tool is deliberately outside the pipeline: no DB, no PO association, no
audit row. These tests pin the behaviour of the rebuilt endpoint: real PDF
validation, a friendly error contract, bounded uploads, active-content
stripping, and the download headers the browser UI relies on.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def pdf_bytes(*widths: int) -> bytes:
    """A valid PDF with one blank page per width (width is the page identity)."""
    writer = PdfWriter()
    for width in widths:
        writer.add_blank_page(width=width, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def upload(name: str, data: bytes, ctype: str = "application/pdf"):
    return ("files", (name, data, ctype))


def post(files, data=None, accept: str | None = None):
    headers = {"accept": accept} if accept else None
    return client.post("/manual/merge", files=files, data=data or {}, headers=headers)


def widths(payload: bytes) -> list[float]:
    return [float(p.mediabox.width) for p in PdfReader(io.BytesIO(payload)).pages]


def error_of(response) -> dict:
    assert response.headers["content-type"].startswith("application/json"), response.text
    return response.json()


def download_name(response) -> str:
    """Read the filename Starlette chose (plain or RFC 5987)."""
    from urllib.parse import unquote

    header = response.headers["content-disposition"]
    if "filename*=" in header:
        encoded = header.split("filename*=", 1)[1].split(";", 1)[0]
        return unquote(encoded.split("''", 1)[1])
    return header.split('filename="', 1)[1].rsplit('"', 1)[0]


# ---------------------------------------------------------------------------
# service: ordering and validation
# ---------------------------------------------------------------------------


def test_manual_merge_applies_the_requested_order(tmp_path):
    from app.services.manual_merge import manual_merge

    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    c = tmp_path / "c.pdf"
    a.write_bytes(pdf_bytes(100))
    b.write_bytes(pdf_bytes(200))
    c.write_bytes(pdf_bytes(300))

    out = manual_merge([a, b, c], order=[2, 0, 1])

    assert widths(out.read_bytes()) == [300, 100, 200]


@pytest.mark.parametrize("order", [[0, 0], [0, 5], [-1, 0], [0, 1]])
def test_manual_merge_rejects_bad_orders(tmp_path, order):
    from app.services.manual_merge import ManualMergeError, manual_merge

    a = tmp_path / "a.pdf"
    a.write_bytes(pdf_bytes(100))

    with pytest.raises(ManualMergeError) as excinfo:
        manual_merge([a], order=order)

    assert excinfo.value.code == "bad_order"


def test_manual_merge_rejects_no_files():
    from app.services.manual_merge import ManualMergeError, manual_merge

    with pytest.raises(ManualMergeError) as excinfo:
        manual_merge([], order=[])

    assert excinfo.value.code == "no_files"


def test_manual_merge_rejects_a_zero_page_pdf(tmp_path):
    from app.services.manual_merge import ManualMergeError, manual_merge

    empty = tmp_path / "empty.pdf"
    writer = PdfWriter()
    buf = io.BytesIO()
    writer.write(buf)
    empty.write_bytes(buf.getvalue())

    with pytest.raises(ManualMergeError) as excinfo:
        manual_merge([empty], order=[0])

    assert excinfo.value.code == "no_pages"
    assert excinfo.value.filename == "empty.pdf"


def test_manual_merge_rejects_a_truncated_pdf_with_its_name(tmp_path):
    from app.services.manual_merge import ManualMergeError, manual_merge

    broken = tmp_path / "broken.pdf"
    broken.write_bytes(pdf_bytes(100)[:-120])

    with pytest.raises(ManualMergeError) as excinfo:
        manual_merge([broken], order=[0])

    assert excinfo.value.code == "unreadable"
    assert excinfo.value.filename == "broken.pdf"


def test_manual_merge_rejects_a_user_password(tmp_path):
    from app.services.manual_merge import ManualMergeError, manual_merge

    locked = tmp_path / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("user-secret", "owner-secret")
    buf = io.BytesIO()
    writer.write(buf)
    locked.write_bytes(buf.getvalue())

    with pytest.raises(ManualMergeError) as excinfo:
        manual_merge([locked], order=[0])

    assert excinfo.value.code == "encrypted"
    assert excinfo.value.filename == "locked.pdf"


def test_manual_merge_rejects_an_owner_password_only_pdf(tmp_path):
    """Every encrypted PDF is refused — no silent decryption, no dropped permissions."""
    from app.services.manual_merge import ManualMergeError, manual_merge

    statement = tmp_path / "statement.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("", "owner-secret")
    buf = io.BytesIO()
    writer.write(buf)
    statement.write_bytes(buf.getvalue())

    with pytest.raises(ManualMergeError) as excinfo:
        manual_merge([statement], order=[0])

    assert excinfo.value.code == "encrypted"
    assert excinfo.value.filename == "statement.pdf"
    assert "unlocked copy" in str(excinfo.value)


def test_manual_merge_strips_javascript_and_launch_actions(tmp_path):
    """A vendor PDF is untrusted: page actions must not ride into the packet."""
    from app.services.manual_merge import manual_merge

    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)

    def action(kind: str) -> DictionaryObject:
        item = DictionaryObject()
        item[NameObject("/S")] = NameObject(kind)
        if kind == "/JavaScript":
            item[NameObject("/JS")] = TextStringObject("app.alert('x')")
        if kind == "/URI":
            item[NameObject("/URI")] = TextStringObject("https://example.invalid/")
        return item

    def link(kind: str) -> DictionaryObject:
        annot = DictionaryObject()
        annot[NameObject("/Type")] = NameObject("/Annot")
        annot[NameObject("/Subtype")] = NameObject("/Link")
        annot[NameObject("/Rect")] = ArrayObject(
            [NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]
        )
        annot[NameObject("/A")] = action(kind)
        return annot

    page[NameObject("/Annots")] = ArrayObject([link("/JavaScript"), link("/Launch"), link("/URI")])
    page_actions = DictionaryObject()
    page_actions[NameObject("/O")] = action("/JavaScript")
    page[NameObject("/AA")] = page_actions

    buf = io.BytesIO()
    writer.write(buf)
    hostile = tmp_path / "hostile.pdf"
    hostile.write_bytes(buf.getvalue())

    out = manual_merge([hostile], order=[0])
    merged = PdfReader(str(out)).pages[0]

    assert "/AA" not in merged
    kinds = [str((a.get_object().get("/A")).get_object().get("/S")) for a in merged["/Annots"]]
    assert "/JavaScript" not in kinds
    assert "/Launch" not in kinds
    assert "/URI" in kinds, "ordinary links must survive"


def test_manual_merge_rejects_a_total_page_bomb(tmp_path, monkeypatch):
    import app.services.manual_merge as mm

    monkeypatch.setattr(mm, "MAX_TOTAL_PAGES", 2)
    pdf = tmp_path / "three.pdf"
    pdf.write_bytes(pdf_bytes(100, 200, 300))

    with pytest.raises(mm.ManualMergeError) as excinfo:
        mm.manual_merge([pdf], order=[0])

    assert excinfo.value.code == "too_many_pages"


def test_manual_merge_writes_every_page(tmp_path):
    from app.services.manual_merge import manual_merge

    a = tmp_path / "a.pdf"
    a.write_bytes(pdf_bytes(100, 200))
    b = tmp_path / "b.pdf"
    b.write_bytes(pdf_bytes(300))

    out = manual_merge([a, b], order=[0, 1])

    assert widths(out.read_bytes()) == [100, 200, 300]


# ---------------------------------------------------------------------------
# service: download filename
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Invoice 1234 (final).pdf", "Invoice 1234 (final).pdf"),
        ("../../etc/passwd", "passwd.pdf"),
        ("a\r\nb.pdf", "ab.pdf"),
        ('a"b.pdf', "ab.pdf"),
        ("report", "report.pdf"),
        ("", "merged-document.pdf"),
        ("   ", "merged-document.pdf"),
        (".pdf", "merged-document.pdf"),
        ("发票2024.pdf", "发票2024.pdf"),
        ("CON.pdf", "_CON.pdf"),
        ("LPT1.pdf", "_LPT1.pdf"),
    ],
)
def test_safe_pdf_name(raw, expected):
    from app.services.manual_merge import safe_pdf_name

    assert safe_pdf_name(raw) == expected


def test_safe_pdf_name_caps_the_length():
    from app.services.manual_merge import safe_pdf_name

    name = safe_pdf_name("x" * 500 + ".pdf")

    assert name.endswith(".pdf")
    assert len(name) <= 104


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("report", "report.pdf"),
        ("report.PDF", "report.pdf"),
        ("  report.pdf  ", "report.pdf"),
        ("Invoice 1234 (final).pdf", "Invoice 1234 (final).pdf"),
        ("a  b.pdf", "a b.pdf"),
        ("a.b.pdf", "a.b.pdf"),
        ("发票2024.pdf", "发票2024.pdf"),
        ("LPT1 final.pdf", "LPT1 final.pdf"),
    ],
)
def test_validate_pdf_name_accepts(raw, expected):
    from app.services.manual_merge import validate_pdf_name

    assert validate_pdf_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        ".pdf",
        "report.",
        ".hidden.pdf",
        " report .pdf",
        "a/b.pdf",
        "a\\b.pdf",
        'a"b.pdf',
        "a<b.pdf",
        "a|b.pdf",
        "CON.pdf",
        "con.foo.pdf",
        "LPT1.pdf",
        "x" * 101,
        "a\x00b.pdf",
    ],
)
def test_validate_pdf_name_rejects(raw):
    from app.services.manual_merge import ManualMergeError, validate_pdf_name

    with pytest.raises(ManualMergeError) as excinfo:
        validate_pdf_name(raw)

    assert excinfo.value.code == "bad_filename"


def test_validate_pdf_name_names_the_offending_character():
    from app.services.manual_merge import ManualMergeError, validate_pdf_name

    with pytest.raises(ManualMergeError, match='"\\/"'):
        validate_pdf_name("a/b.pdf")


# ---------------------------------------------------------------------------
# route: success contract
# ---------------------------------------------------------------------------


def test_route_merges_in_order_and_sets_safe_headers():
    response = post(
        [upload("a.pdf", pdf_bytes(100)), upload("b.pdf", pdf_bytes(200))],
        {"order": "1,0", "output_filename": "packet.pdf"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert 'filename="packet.pdf"' in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert widths(response.content) == [200, 100]


def test_route_accepts_a_pdf_with_leading_bytes():
    """A BOM / blank lines before %PDF- is valid in practice; ingestion accepts it."""
    response = post([upload("scanned.pdf", b"\xef\xbb\xbf\r\n" + pdf_bytes(100))])

    assert response.status_code == 200, response.text
    assert len(PdfReader(io.BytesIO(response.content)).pages) == 1


def test_route_keeps_spaces_in_the_download_name():
    response = post(
        [upload("a.pdf", pdf_bytes(100))], {"output_filename": "Invoice 1234 (final).pdf"}
    )

    assert response.status_code == 200
    assert download_name(response) == "Invoice 1234 (final).pdf"


def test_route_appends_pdf_to_a_bare_name():
    response = post([upload("a.pdf", pdf_bytes(100))], {"output_filename": "Quarterly packet"})

    assert response.status_code == 200
    assert download_name(response) == "Quarterly packet.pdf"


def test_route_rejects_a_blank_filename():
    response = post([upload("a.pdf", pdf_bytes(100))], {"output_filename": "   "})

    assert response.status_code == 422
    payload = error_of(response)
    assert payload["code"] == "bad_filename"
    assert "merged file" in payload["detail"]


@pytest.mark.parametrize(
    "bad_name",
    ["a/b.pdf", "a\\b.pdf", 'a"b.pdf', "CON.pdf", " report .pdf", ".hidden.pdf", "x" * 101],
)
def test_route_rejects_an_invalid_filename(bad_name):
    response = post([upload("a.pdf", pdf_bytes(100))], {"output_filename": bad_name})

    assert response.status_code == 422
    assert error_of(response)["code"] == "bad_filename"


def test_route_uses_the_default_when_the_name_is_missing():
    response = post([upload("a.pdf", pdf_bytes(100))])

    assert response.status_code == 200
    assert download_name(response) == "merged-document.pdf"


# ---------------------------------------------------------------------------
# route: error contract
# ---------------------------------------------------------------------------


def test_route_rejects_a_non_pdf_extension_with_its_name():
    response = post([upload("notes.txt", pdf_bytes(100), "text/plain")])

    assert response.status_code == 422
    payload = error_of(response)
    assert payload["code"] == "not_pdf"
    assert payload["filename"] == "notes.txt"


def test_route_rejects_bytes_that_are_not_a_pdf():
    response = post([upload("fake.pdf", b"this is not a pdf")])

    assert response.status_code == 422
    payload = error_of(response)
    assert payload["code"] == "not_pdf"
    assert "fake.pdf" in payload["filename"]


def test_route_rejects_a_zero_page_pdf():
    writer = PdfWriter()
    buf = io.BytesIO()
    writer.write(buf)

    response = post([upload("empty.pdf", buf.getvalue())])

    assert response.status_code == 422
    payload = error_of(response)
    assert payload["code"] == "no_pages"
    assert payload["filename"] == "empty.pdf"


def test_route_rejects_too_many_files():
    from app.core.limits import MAX_MANUAL_MERGE_FILES

    files = [upload(f"p{i}.pdf", pdf_bytes(100)) for i in range(MAX_MANUAL_MERGE_FILES + 1)]
    response = post(files)

    assert response.status_code == 422
    payload = error_of(response)
    assert payload["code"] == "too_many_files"
    assert str(MAX_MANUAL_MERGE_FILES) in payload["detail"]


def test_route_rejects_a_single_file_over_the_cap(monkeypatch):
    from app.core.config import load_config

    cfg = load_config("config.example.yaml")
    cfg.manual_merge.max_total_mb = 1
    monkeypatch.setattr("app.api.routes.manual_merger.load_config", lambda: cfg)

    response = post([upload("big.pdf", b"%PDF-1.4" + b"x" * (2 * 1024 * 1024))])

    assert response.status_code == 413
    payload = error_of(response)
    assert payload["code"] == "too_large"
    assert "1 MB" in payload["detail"]


def test_route_rejects_the_total_over_the_cap(monkeypatch):
    from app.core.config import load_config

    cfg = load_config("config.example.yaml")
    cfg.manual_merge.max_total_mb = 1
    monkeypatch.setattr("app.api.routes.manual_merger.load_config", lambda: cfg)

    response = post(
        [
            upload("a.pdf", b"%PDF-1.4" + b"a" * 600_000),
            upload("b.pdf", b"%PDF-1.4" + b"b" * 600_000),
        ]
    )

    assert response.status_code == 413
    payload = error_of(response)
    assert payload["code"] == "too_large"
    assert "more than" in payload["detail"]


def test_route_uses_the_configured_file_limit(monkeypatch):
    from app.core.config import load_config

    cfg = load_config("config.example.yaml")
    cfg.manual_merge.max_files = 1
    monkeypatch.setattr("app.api.routes.manual_merger.load_config", lambda: cfg)

    response = post([upload("a.pdf", pdf_bytes(100)), upload("b.pdf", pdf_bytes(200))])

    assert response.status_code == 422
    payload = error_of(response)
    assert payload["code"] == "too_many_files"
    assert "1" in payload["detail"]


def test_route_uses_the_configured_page_limit(monkeypatch):
    from app.core.config import load_config

    cfg = load_config("config.example.yaml")
    cfg.manual_merge.max_pages = 1
    monkeypatch.setattr("app.api.routes.manual_merger.load_config", lambda: cfg)

    response = post([upload("two-pages.pdf", pdf_bytes(100, 200))])

    assert response.status_code == 422
    payload = error_of(response)
    assert payload["code"] == "too_many_pages"
    assert "more than 1 page" in payload["detail"]


def test_route_stages_inside_the_configured_temp_folder(tmp_path, monkeypatch):
    from app.core.config import load_config

    cfg = load_config("config.example.yaml")
    folder = tmp_path / "mm-staging"
    cfg.manual_merge.temp_folder = folder
    monkeypatch.setattr("app.api.routes.manual_merger.load_config", lambda: cfg)

    real_mkstemp = tempfile.mkstemp
    seen_dirs: list[str | None] = []

    def spy(suffix=None, prefix=None, dir=None, **kwargs):
        seen_dirs.append(dir)
        return real_mkstemp(suffix=suffix, prefix=prefix, dir=dir, **kwargs)

    monkeypatch.setattr(tempfile, "mkstemp", spy)

    response = post([upload("a.pdf", pdf_bytes(100))])

    assert response.status_code == 200, response.text
    assert seen_dirs, "expected the route to create staging files"
    assert all(Path(path).resolve() == folder.resolve() for path in seen_dirs)
    assert folder.is_dir()
    assert list(folder.glob("mm-*.pdf")) == [], "background cleanup should empty it"


def test_sweep_temp_folder_removes_only_our_old_files(tmp_path, monkeypatch):
    import os
    import time

    from app.services.manual_merge import TEMP_FILE_PREFIX, sweep_temp_folder

    old = tmp_path / f"{TEMP_FILE_PREFIX}old.pdf"
    new = tmp_path / f"{TEMP_FILE_PREFIX}new.pdf"
    real = tmp_path / "invoice.pdf"
    other = tmp_path / f"{TEMP_FILE_PREFIX}old.txt"
    for path in (old, new, real, other):
        path.write_bytes(b"x")

    eight_hours_ago = time.time() - 8 * 3600
    for path in (old, real, other):
        os.utime(path, (eight_hours_ago, eight_hours_ago))

    removed = sweep_temp_folder(tmp_path, older_than_hours=6)

    assert removed == 1
    assert not old.exists()
    assert new.exists()
    assert real.exists(), "a real document in the folder must never be swept"
    assert other.exists(), "only our mm-*.pdf staging files are swept"


def test_resolve_temp_folder_defaults_beside_the_database(tmp_path):
    from app.services.manual_merge import resolve_temp_folder

    folder = resolve_temp_folder(None, tmp_path / "aam_merger.db")

    assert folder == tmp_path / "manual_merge_tmp"
    assert folder.is_dir()


def test_manual_merge_config_defaults():
    from app.core.config import ManualMergeConfig

    mm = ManualMergeConfig()

    assert (mm.max_files, mm.max_total_mb, mm.max_pages) == (20, 100, 5000)
    assert mm.temp_folder is None
    assert mm.sweep_after_hours == 6
    assert mm.max_total_bytes == 100 * 1024 * 1024
    assert mm.max_request_bytes == mm.max_total_bytes + 2 * 1024 * 1024


def _write_manual_merge_config(tmp_path, temp_folder: str) -> Path:
    import yaml

    data = yaml.safe_load((Path("config.example.yaml")).read_text(encoding="utf-8"))
    data["paths"] = {
        "input_folder": str(tmp_path / "input"),
        "output_folder": str(tmp_path / "output"),
        "quarantine_folder": str(tmp_path / "quarantine"),
        "stored_documents_folder": str(tmp_path / "stored"),
        "database_path": str(tmp_path / "aam_merger.db"),
        "log_folder": str(tmp_path / "logs"),
    }
    data["manual_merge"]["temp_folder"] = temp_folder
    target = tmp_path / "config.yaml"
    target.write_text(yaml.safe_dump(data), encoding="utf-8")
    return target


def test_relative_temp_folder_resolves_against_the_config_file(tmp_path):
    from app.core.config import load_config

    config_path = _write_manual_merge_config(tmp_path, "./mm-staging")

    cfg = load_config(config_path)

    assert cfg.manual_merge.temp_folder == (tmp_path / "mm-staging").resolve()


def test_temp_folder_inside_a_pipeline_folder_is_refused(tmp_path):
    from app.core.config import load_config

    config_path = _write_manual_merge_config(tmp_path, str(tmp_path / "output" / "staging"))

    with pytest.raises(Exception, match="separate from"):
        load_config(config_path)


def test_route_rejects_a_bad_order():
    response = post(
        [upload("a.pdf", pdf_bytes(100)), upload("b.pdf", pdf_bytes(200))],
        {"order": "0,0"},
    )

    assert response.status_code == 422
    assert error_of(response)["code"] == "bad_order"


def test_route_reports_missing_files_as_json():
    response = client.post("/manual/merge", data={"output_filename": "x.pdf"})

    assert response.status_code == 422
    assert error_of(response)["code"] == "no_files"


def test_route_returns_a_browser_page_for_a_form_post():
    response = post(
        [upload("fake.pdf", b"not a pdf")],
        accept="text/html,application/xhtml+xml",
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "fake.pdf" in response.text
    assert "damaged" in response.text


def test_route_does_not_touch_the_database(monkeypatch):
    import app.core.database as database

    def trap(*args, **kwargs):
        raise AssertionError("the manual merger must not use the database")

    monkeypatch.setattr(database, "get_engine", trap)

    response = post([upload("a.pdf", pdf_bytes(100))])

    assert response.status_code == 200, response.text


def test_route_removes_every_temp_file(tmp_path, monkeypatch):
    real_mkstemp = tempfile.mkstemp
    created: list[str] = []

    def spy(suffix=None, prefix=None, dir=None, **kwargs):
        fd, path = real_mkstemp(suffix=suffix, prefix=prefix or "tmp", dir=str(tmp_path), **kwargs)
        created.append(path)
        return fd, path

    monkeypatch.setattr(tempfile, "mkstemp", spy)

    response = post([upload("a.pdf", pdf_bytes(100)), upload("b.pdf", pdf_bytes(200))])

    assert response.status_code == 200
    assert len(created) == 3  # two inputs + one output
    assert all(not Path(path).exists() for path in created)


# ---------------------------------------------------------------------------
# request-body guard (before multipart parsing)
# ---------------------------------------------------------------------------


def _scope(path: str = "/manual/merge", length: int | None = None) -> dict:
    headers = []
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    headers.append((b"accept", b"application/json"))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }


@pytest.mark.asyncio
async def test_body_limit_rejects_an_oversized_content_length_before_the_app():
    from app.main import BodySizeLimitMiddleware

    called = False

    async def endpoint(scope, receive, send):  # pragma: no cover - must not run
        nonlocal called
        called = True

    middleware = BodySizeLimitMiddleware(endpoint, paths=("/manual/merge",), max_bytes=16)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(_scope(length=1024), receive, send)

    assert called is False
    assert sent[0]["status"] == 413
    assert b'"code": "too_large"' in sent[1]["body"] or b'"code":"too_large"' in sent[1]["body"]


@pytest.mark.asyncio
async def test_body_limit_rejects_a_chunked_body_that_grows_past_the_cap():
    from app.main import BodySizeLimitMiddleware

    async def endpoint(scope, receive, send):
        # Consume the body the way a form parser would, until it raises.
        while True:
            message = await receive()
            if not message.get("more_body"):
                break

    middleware = BodySizeLimitMiddleware(endpoint, paths=("/manual/merge",), max_bytes=10)
    sent = []
    chunks = [b"12345678", b"12345678"]

    async def receive():
        return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}

    async def send(message):
        sent.append(message)

    await middleware(_scope(length=None), receive, send)

    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_limit_ignores_other_paths():
    from app.main import BodySizeLimitMiddleware

    async def endpoint(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BodySizeLimitMiddleware(endpoint, paths=("/manual/merge",), max_bytes=1)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(_scope(path="/health", length=10_000), receive, send)

    assert sent[0]["status"] == 200
