"""Every move must leave the claimed file on disk, and every message must be true.

These tests do not trust the page. After the UI says a move succeeded, they
look in the configured folder, check the files are real PDFs, read the export
manifest, and compare the database.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from playwright.sync_api import Page, expect
from pypdf import PdfReader

from release_helpers import pdf_bytes, wait_for_alpine

REPO = Path(__file__).resolve().parents[3]


def _absolute(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else REPO / candidate


def test_set_aside_message_and_copy_on_disk(page: Page, manifest, db, harness_paths):
    held = manifest["held"]["release_set_aside_target"]
    query = db.execute(
        "select stored_path, original_filename from documents where id=?", (held["id"],)
    ).fetchone()
    stored = _absolute(query["stored_path"])

    page.goto("/unclassified")
    wait_for_alpine(page)
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(
        re.compile(rf"/unclassified/{held['id']}/set_aside")
    ) as response:
        row.get_by_test_id("btn-set-aside").click()
    assert response.value.status == 200, response.value.text()

    result = page.get_by_test_id("set-aside-result")
    expect(result).to_contain_text("Moved to the quarantine folder")
    expect(result).to_contain_text("Fix or split it")
    message = result.inner_text()
    assert "cap reached" not in message
    assert "C:\\" not in message, message
    assert "_documents" in message, message

    folder = harness_paths["quarantine_folder"] / "_documents" / stored.name.removesuffix(".pdf")
    assert folder.is_dir(), f"quarantine folder missing: {folder}"
    copies = list(folder.glob("*.pdf"))
    assert copies, f"no PDF copied into {folder}"
    assert all(copy.stat().st_size > 0 for copy in copies), copies
    assert (folder / "QUARANTINE.txt").is_file()

    after = db.execute(
        "select archived_at from documents where id=?", (held["id"],)
    ).fetchone()
    assert after["archived_at"] is not None, "the holding row was not archived"


def test_remove_set_message_and_full_export_on_disk(page: Page, manifest, db, harness_paths):
    sc = manifest["scenarios"]["release_remove_target"]
    documents = db.execute(
        "select id, original_filename, stored_path from documents where po_set_id=?",
        (sc["id"],),
    ).fetchall()
    assert documents, "the removal scenario has no documents"

    page.goto(f"/po_sets/{sc['id']}/view")
    expect(page.get_by_test_id("remove-set-card")).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_navigation():
        page.get_by_test_id("btn-confirm-remove-set").click()

    result = page.get_by_test_id("remove-set-result")
    expect(result).to_be_visible()
    text = result.inner_text()
    assert "Removed PO Set" in text
    assert sc["po_no"] in text
    assert "quarantine/_removed_sets/" in text
    assert "C:\\" not in text, text
    assert str(manifest["root"]) not in text, text

    relative = result.locator("code").inner_text().strip()
    assert relative.startswith("quarantine/_removed_sets/"), relative
    folder = harness_paths["quarantine_folder"] / relative.split("quarantine/", 1)[1]
    assert folder.is_dir(), f"removal folder missing: {folder}"

    for name in (
        "REMOVED.txt",
        "manifest.json",
        "po_set.json",
        "documents.json",
        "line_items.json",
        "audit_log.json",
    ):
        assert (folder / name).is_file(), f"{name} missing from {folder}"

    files = list((folder / "files").glob("*.pdf"))
    assert len(files) == len(documents), f"{len(files)} copies for {len(documents)} documents"
    assert all(copy.stat().st_size > 0 for copy in files), files

    removed_text = (folder / "REMOVED.txt").read_text(encoding="utf-8")
    for document in documents:
        assert document["original_filename"] in removed_text
    assert not (folder / "REMOVAL_IN_PROGRESS.txt").exists()

    exported = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert exported["po_set_id"] == sc["id"]
    assert exported["po_no_normalized"] == sc["po_no"]
    assert exported["document_count"] == len(documents)
    assert exported["missing_files"] == []

    assert (
        db.execute("select count(*) from po_sets where id=?", (sc["id"],)).fetchone()[0] == 0
    ), "the set row was not deleted"
    assert (
        db.execute(
            "select count(*) from documents where po_set_id=?", (sc["id"],)
        ).fetchone()[0]
        == 0
    ), "document rows were not deleted"

    audits = db.execute(
        "select detail from audit_log where action='quarantine_delete'"
    ).fetchall()
    removal = [
        json.loads(row["detail"])
        for row in audits
        if row["detail"] and '"removed_po_set_id"' in row["detail"]
    ]
    assert any(
        entry.get("removed_po_set_id") == sc["id"] and entry.get("deleted") is True
        for entry in removal
    ), removal

    missing = page.goto(f"/po_sets/{sc['id']}/view")
    assert missing is not None and missing.status == 404
    expect(page.locator("body")).to_contain_text("archived or removed")


def test_archive_message_and_export_on_disk(page: Page, manifest, db):
    sc = manifest["scenarios"]["release_archive_target"]
    documents = db.execute(
        "select id from documents where po_set_id=?", (sc["id"],)
    ).fetchall()
    assert documents

    page.goto("/quarantine")
    row = page.locator(f'[data-testid="quarantine-row"][data-set-id="{sc["id"]}"]')
    row.get_by_test_id("btn-archive-row").click()
    with page.expect_response(
        re.compile(rf"/po_sets/{sc['id']}/quarantine$")
    ) as response:
        row.get_by_test_id("btn-confirm-archive-row").click()
    assert response.value.status == 200, response.value.text()
    expect(
        page.locator(f'[data-testid="quarantine-row"][data-set-id="{sc["id"]}"]')
    ).to_have_count(0)

    archived = db.execute(
        "select archived_at, archive_path from po_sets where id=?", (sc["id"],)
    ).fetchone()
    assert archived["archived_at"] is not None
    assert archived["archive_path"], "no archive path recorded"
    folder = _absolute(archived["archive_path"])
    assert folder.is_dir(), f"archive folder missing: {folder}"

    for name in (
        "ARCHIVE.txt",
        "manifest.json",
        "po_set.json",
        "documents.json",
        "line_items.json",
        "audit_log.json",
    ):
        assert (folder / name).is_file(), f"{name} missing from {folder}"

    files = list((folder / "files").glob("*.pdf"))
    assert len(files) == len(documents), f"{len(files)} copies for {len(documents)} documents"
    assert all(copy.stat().st_size > 0 for copy in files), files
    assert "ARCHIVED PO SET" in (folder / "ARCHIVE.txt").read_text(encoding="utf-8")

    # Archive keeps the rows; only the worklists hide them.
    assert (
        db.execute("select count(*) from po_sets where id=?", (sc["id"],)).fetchone()[0] == 1
    )
    assert (
        db.execute(
            "select count(*) from documents where po_set_id=?", (sc["id"],)
        ).fetchone()[0]
        == len(documents)
    )

    page.goto("/audit")
    entry = (
        page.get_by_test_id("audit-row")
        .filter(has_text="Quarantine Archive")
        .filter(has_text="Archived to")
        .first
    )
    expect(entry).to_contain_text(folder.name)


def test_merge_message_and_packet_on_disk(page: Page, manifest, db):
    sc = manifest["scenarios"]["release_merge_target"]
    page.goto(f"/po_sets/{sc['id']}/view")
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/merge$")) as response:
        page.get_by_test_id("btn-merge-now").click()
    assert response.value.status == 200, response.value.text()
    body = response.value.json()
    assert body["status"] == "merged", body

    toast = page.locator(".toast-success").first
    expect(toast).to_contain_text("Merged:")
    toast_text = toast.inner_text()
    assert "C:\\" not in toast_text, toast_text

    stored = db.execute(
        "select status, merged_output_path from po_sets where id=?", (sc["id"],)
    ).fetchone()
    assert stored["status"] == "merged"
    packet = _absolute(stored["merged_output_path"])
    assert packet.is_file(), f"merged packet missing: {packet}"
    assert packet.stat().st_size > 0
    assert len(PdfReader(packet).pages) >= 1
    assert packet.name in toast_text, toast_text


def test_upload_stores_the_file_and_a_bad_upload_explains_itself(
    page: Page, manifest, db
):
    sc = manifest["scenarios"]["release_upload_target"]
    page.goto(f"/po_sets/{sc['id']}/view")
    form = page.get_by_test_id("upload-form")
    data = pdf_bytes()

    form.locator('input[type="file"]').set_input_files(
        {"name": "CUSTOMS-release.pdf", "mimeType": "application/pdf", "buffer": data}
    )
    form.locator('select[name="doc_type"]').select_option("CUSTOMS")
    with page.expect_navigation():
        form.get_by_role("button", name="Upload & Attach").click()

    document = db.execute(
        "select id, stored_path, original_filename from documents "
        "where po_set_id=? and doc_type='CUSTOMS'",
        (sc["id"],),
    ).fetchone()
    assert document is not None, "no CUSTOMS document row was created"
    stored = _absolute(document["stored_path"])
    assert stored.is_file(), f"stored file missing: {stored}"
    assert stored.stat().st_size == len(data)
    expect(
        page.locator(f'[data-testid="doc-row"][data-doc-id="{document["id"]}"]')
    ).to_contain_text("CUSTOMS-release.pdf")

    # A refused upload must say why in human words and change nothing.
    before = db.execute(
        "select count(*) from documents where po_set_id=?", (sc["id"],)
    ).fetchone()[0]
    page.goto(f"/po_sets/{sc['id']}/view")
    form = page.get_by_test_id("upload-form")
    form.locator('input[type="file"]').set_input_files(
        {"name": "not-a-pdf.txt", "mimeType": "text/plain", "buffer": b"not a pdf"}
    )
    form.locator('select[name="doc_type"]').select_option("CUSTOMS")
    with page.expect_navigation():
        form.get_by_role("button", name="Upload & Attach").click()

    notice = page.get_by_test_id("notice")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text("only .pdf uploads accepted")
    assert "Traceback" not in page.content()
    after = db.execute(
        "select count(*) from documents where po_set_id=?", (sc["id"],)
    ).fetchone()[0]
    assert after == before


def test_merged_download_is_a_real_pdf(page: Page, manifest, db):
    sc = manifest["scenarios"]["merged_basic"]
    page.goto(f"/po_sets/{sc['id']}/view")
    with page.expect_download() as download:
        page.get_by_test_id("btn-download-merged").click()
    path = Path(download.value.path())
    assert path.is_file() and path.stat().st_size > 0
    assert len(PdfReader(path).pages) >= 1

    stored = db.execute(
        "select merged_output_path from po_sets where id=?", (sc["id"],)
    ).fetchone()
    assert _absolute(stored["merged_output_path"]).is_file()
