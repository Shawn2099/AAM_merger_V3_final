"""Browser E2E for the Merge PDFs tool (opt-in).

Run with:

    RUN_E2E=1 .venv\\Scripts\\python.exe -m pytest tests/test_manual_merger_e2e.py -q

The default test run skips these: a CI host without Playwright's Chromium can
still run the unit/route suite. The suite starts its own uvicorn process with a
temporary `config.yaml` (via `AAM_CONFIG_PATH`) and a temporary database,
staging folder and data paths, so it never touches the real data folder.
"""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, expect
from pypdf import PdfReader, PdfWriter

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("RUN_E2E") != "1",
        reason="set RUN_E2E=1 (and install Chromium) to run the browser E2E suite",
    ),
]

REPO = Path(__file__).resolve().parents[1]


def _pdf(path: Path, *widths: int, encrypted: bool = False) -> Path:
    writer = PdfWriter()
    for width in widths:
        writer.add_blank_page(width=width, height=200)
    if encrypted:
        writer.encrypt("user-secret", "owner-secret")
    buf = io.BytesIO()
    writer.write(buf)
    path.write_bytes(buf.getvalue())
    return path


def _widths(path: Path) -> list[float]:
    return [float(page.mediabox.width) for page in PdfReader(str(path)).pages]


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """A real uvicorn process on a free port, with an isolated config."""
    import httpx
    import yaml

    tmp = tmp_path_factory.mktemp("manual-merge-e2e")
    data = yaml.safe_load((REPO / "config.example.yaml").read_text(encoding="utf-8"))
    data["paths"] = {
        "input_folder": str(tmp / "input"),
        "output_folder": str(tmp / "output"),
        "quarantine_folder": str(tmp / "quarantine"),
        "stored_documents_folder": str(tmp / "stored"),
        "database_path": str(tmp / "aam_merger.db"),
        "log_folder": str(tmp / "logs"),
    }
    data["manual_merge"]["temp_folder"] = str(tmp / "manual_merge_tmp")
    data["schedule"]["poll_enabled"] = False
    config = tmp / "config.yaml"
    config.write_text(yaml.safe_dump(data), encoding="utf-8")

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    env = os.environ.copy()
    env["AAM_CONFIG_PATH"] = str(config)
    env["PYTHONPATH"] = str(REPO / "src")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                if httpx.get(base + "/health", timeout=0.5).status_code == 200:
                    break
            except Exception:
                time.sleep(0.1)
        else:  # pragma: no cover - only on a broken environment
            output = process.communicate(timeout=5)[0]
            raise RuntimeError(f"uvicorn did not start:\n{output}")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
        if process.stdout is not None:
            process.stdout.close()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Downloads are the product's output, so accept them in the test context."""
    return {**browser_context_args, "accept_downloads": True}


def _open(page: Page, base: str) -> None:
    page.goto(base + "/manual/merger")
    expect(page.locator("#manual-merger")).to_be_visible()


def test_merge_in_the_displayed_order(page: Page, live_server, tmp_path):
    _open(page, live_server)
    a = _pdf(tmp_path / "a.pdf", 100)
    b = _pdf(tmp_path / "b.pdf", 200)
    c = _pdf(tmp_path / "c.pdf", 300)

    page.set_input_files("#file-input", [str(a), str(b), str(c)])
    expect(page.locator(".mm-file")).to_have_count(3)

    page.get_by_role("button", name="Move c.pdf up").click()
    page.get_by_role("button", name="Move c.pdf up").click()
    expect(page.locator(".mm-file-name")).to_have_text(["c.pdf", "a.pdf", "b.pdf"])

    page.fill("#output_filename", "E2E packet")
    expect(page.locator("#mm-name-preview")).to_have_text("E2E packet.pdf")
    expect(page.locator("#merge-btn")).to_be_enabled()

    with page.expect_download() as download_info:
        page.locator("#merge-btn").click()
    download = download_info.value

    assert download.suggested_filename == "E2E packet.pdf"
    merged = tmp_path / "merged.pdf"
    download.save_as(merged)
    assert _widths(merged) == [300, 100, 200]
    expect(page.locator("#mm-status")).to_contain_text("3 files merged")


def test_invalid_name_blocks_merge(page: Page, live_server, tmp_path):
    _open(page, live_server)
    page.set_input_files("#file-input", [str(_pdf(tmp_path / "a.pdf", 100))])

    page.fill("#output_filename", "bad/name.pdf")
    expect(page.locator("#mm-name-error")).to_be_visible()
    expect(page.locator("#mm-name-error")).to_contain_text("Please remove")
    expect(page.locator("#merge-btn")).to_be_disabled()

    page.fill("#output_filename", "Invoice 1234 (final)")
    expect(page.locator("#mm-name-error")).to_be_hidden()
    expect(page.locator("#mm-name-preview")).to_have_text("Invoice 1234 (final).pdf")
    expect(page.locator("#merge-btn")).to_be_enabled()


def test_os_file_dropped_on_a_row_is_added_not_reordered(page: Page, live_server, tmp_path):
    _open(page, live_server)
    a = _pdf(tmp_path / "a.pdf", 100)
    b = _pdf(tmp_path / "b.pdf", 200)
    page.set_input_files("#file-input", [str(a), str(b)])
    expect(page.locator(".mm-file")).to_have_count(2)

    page.evaluate(
        """() => {
            const dt = new DataTransfer();
            dt.items.add(new File(
                [new Uint8Array([37, 80, 68, 70, 45, 49, 46, 52, 10])],
                'dropped-on-row.pdf',
                { type: 'application/pdf' },
            ));
            document.querySelectorAll('.mm-file')[1].dispatchEvent(
                new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt })
            );
        }"""
    )

    expect(page.locator(".mm-file-name")).to_have_text(["a.pdf", "b.pdf", "dropped-on-row.pdf"])


def test_encrypted_pdf_shows_an_inline_error_without_navigation(page: Page, live_server, tmp_path):
    _open(page, live_server)
    encrypted = _pdf(tmp_path / "locked.pdf", 100, encrypted=True)
    page.set_input_files("#file-input", [str(encrypted)])
    page.fill("#output_filename", "packet.pdf")

    page.locator("#merge-btn").click()

    expect(page.locator("#mm-alert")).to_be_visible()
    expect(page.locator("#mm-alert-text")).to_contain_text("password-protected")
    expect(page.locator(".mm-file.has-error")).to_have_count(1)
    expect(page.locator(".mm-file")).to_have_count(1)
    expect(page).to_have_url(live_server + "/manual/merger")


def test_twenty_one_files_stages_only_twenty(page: Page, live_server, tmp_path):
    _open(page, live_server)
    files = [_pdf(tmp_path / f"p{i}.pdf", 100) for i in range(21)]

    page.set_input_files("#file-input", [str(path) for path in files])

    expect(page.locator(".mm-file")).to_have_count(20)
    expect(page.locator("#mm-alert-text")).to_contain_text("couldn't be added")
    expect(page.locator("#mm-summary")).to_contain_text("20 files")


def test_mobile_layout_is_a_single_column(page: Page, live_server):
    page.set_viewport_size({"width": 420, "height": 900})
    _open(page, live_server)

    columns = page.evaluate(
        "getComputedStyle(document.querySelector('.mm-grid')).gridTemplateColumns"
    )

    assert " " not in columns.strip(), f"expected one column, got {columns!r}"
