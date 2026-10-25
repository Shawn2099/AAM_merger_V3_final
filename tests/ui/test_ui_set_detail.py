"""Set page (UI-S-*): reason card, matrix, documents, actions. Reference implementation."""

import re
from pathlib import Path

from playwright.sync_api import Page, expect


def _open(page: Page, manifest, name: str):
    sc = manifest["scenarios"][name]
    page.goto(f"/po_sets/{sc['id']}/view")
    expect(page.get_by_test_id("set-po")).to_have_text(sc["po_no"])
    return sc


def test_ui_s_01_merged_set_shows_a_match_per_line_and_all_documents(page: Page, manifest):
    sc = _open(page, manifest, "merged_basic")
    rows = page.get_by_test_id("matrix-row")
    expect(rows).to_have_count(2)
    for i in range(2):
        expect(rows.nth(i)).to_contain_text("Match")
    expect(page.get_by_test_id("doc-row")).to_have_count(len(sc["docs"]))
    for doc in sc["docs"]:
        expect(page.locator(f'[data-testid="doc-row"][data-doc-id="{doc["id"]}"]')).to_contain_text(
            doc["file"]
        )
    # A merged set cannot be merged again.
    expect(page.get_by_test_id("btn-merge-now")).to_be_disabled()
    expect(page.get_by_test_id("btn-force-merge")).to_be_disabled()


def test_ui_s_02_mapped_set_says_which_numbering_rule_paired_the_lines(page: Page, manifest):
    _open(page, manifest, "merged_mapped_x10")
    expect(page.get_by_test_id("flag").first).to_contain_text("x10")
    expect(page.get_by_test_id("matrix-row")).to_have_count(2)


def test_ui_s_03_waiting_set_shows_the_reason_and_the_unmatched_vendor_line(page: Page, manifest):
    sc = _open(page, manifest, "pending_vendor_line_not_on_po")
    expect(page.get_by_test_id("reason-card")).to_contain_text(sc["reason"][:40])
    flag = page.get_by_test_id("flag")
    expect(flag).to_have_count(1)
    expect(flag).to_contain_text("Not on the PO")
    expect(flag).to_contain_text("SI line 2")
    expect(flag).to_contain_text("qty 50")
    expect(page.get_by_test_id("matrix-row")).to_contain_text("Awaiting")


def test_ui_s_04_quarantined_set_explains_why_and_offers_archive_not_merge_overrides(
    page: Page, manifest
):
    sc = _open(page, manifest, "quarantined_unmatched_vendor_line")
    expect(page.get_by_test_id("reason-card")).to_contain_text(sc["reason"][:40])
    expect(page.get_by_test_id("flag").first).to_contain_text("has no matching PO line")
    expect(page.get_by_test_id("btn-archive")).to_be_visible()


def test_ui_s_05_document_preview_opens_the_pdf_in_the_drawer(page: Page, manifest):
    sc = _open(page, manifest, "merged_basic")
    first = page.locator(f'[data-testid="doc-row"][data-doc-id="{sc["docs"][0]["id"]}"]')
    with page.expect_response(re.compile(r"/documents/\d+/preview")) as resp:
        first.get_by_test_id("btn-preview").click()
    assert resp.value.status == 200
    assert resp.value.headers["content-type"].startswith("application/pdf")


def test_ui_s_06_force_merge_needs_the_modal_and_produces_a_packet(page: Page, manifest, db):
    sc = _open(page, manifest, "force_merge_target")
    assert sc["mutable"] and sc["status"] == "mismatched"
    page.get_by_test_id("btn-force-merge").click()
    page.locator("#force-merge-justification").fill(
        "UI test: operator accepts the 7 of 10 delivery."
    )
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/force_merge")) as resp:
        page.get_by_test_id("btn-confirm-force-merge").click()
    assert resp.value.status == 200, resp.value.text()
    page.goto(f"/po_sets/{sc['id']}/view")
    row = db.execute(
        "select status, merged_output_path from po_sets where id=?", (sc["id"],)
    ).fetchone()
    assert row["status"] == "merged"
    assert Path(row["merged_output_path"]).read_bytes()[:4] == b"%PDF"
    audit = db.execute("select action from audit_log where po_set_id=?", (sc["id"],)).fetchall()
    assert "force_merge" in [a["action"] for a in audit]


def test_ui_s_07_archive_removes_the_set_from_the_worklists(page: Page, manifest, db):
    sc = _open(page, manifest, "archive_target")
    page.get_by_test_id("btn-archive").click()
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/quarantine")) as resp:
        page.get_by_test_id("btn-confirm-archive").click()
    assert resp.value.status == 200, resp.value.text()
    page.goto("/dashboard")
    expect(page.locator(f'[data-testid="po-row"][data-set-id="{sc["id"]}"]')).to_have_count(0)
    assert db.execute("select archived_at from po_sets where id=?", (sc["id"],)).fetchone()[0]


def test_ui_s_08_customs_toggle_turns_the_requirement_on_and_off(page: Page, manifest, db):
    sc = _open(page, manifest, "customs_toggle_target")
    button = page.get_by_test_id("btn-toggle-customs")
    expect(button).to_contain_text("OFF")
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/toggle_customs")) as resp:
        button.click()
    assert resp.value.status == 200, resp.value.text()
    # The page reloads itself after the toggle; expect() retries until it has.
    expect(page.get_by_test_id("btn-toggle-customs")).to_contain_text("ON")
    # ...and the confirmation must survive that reload (F-07): a toast that is
    # destroyed by the reload it triggered is not feedback.
    expect(page.locator(".toast-info")).to_contain_text("Customs requirement setting updated")
    assert (
        db.execute("select has_customs_toggle from po_sets where id=?", (sc["id"],)).fetchone()[0]
        == 1
    )


def test_ui_s_17_an_unknown_set_shows_a_friendly_page(page: Page):
    """S-17: opening a set that no longer exists must not dead-end in JSON."""
    response = page.goto("/po_sets/999999/view")
    assert response is not None and response.status == 404
    expect(page.locator("nav.app-navbar")).to_be_visible()
    expect(page.get_by_text("does not exist", exact=False)).to_be_visible()
    assert "Traceback" not in page.content()


def test_ui_s_15_a_refused_force_merge_says_why_once(page: Page, manifest):
    """F-10: the toast must not repeat the icon the template already renders."""
    sc = _open(page, manifest, "force_merge_validation_target")
    page.get_by_test_id("btn-force-merge").click()
    page.locator("#force-merge-justification").fill("too short")
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/force_merge")) as resp:
        page.get_by_test_id("btn-confirm-force-merge").click()
    assert resp.value.status == 422
    toast = page.locator(".toast-error")
    expect(toast).to_contain_text("Justification must be at least 20 characters")
    text = toast.inner_text()
    assert text.count("❌") == 1, f"the type icon is rendered twice: {text!r}"


def test_ui_s_16_reextract_without_a_provider_key_reports_failure(page: Page, manifest):
    """F-01/S-16: the outcome is reported, not "queued".

    The set's status afterwards is the engine's decision (a failed re-read
    leaves it `pending`; pinned by tests/test_mvp_blockers.py). What this test
    requires is that the operator is told nothing was read, and that the
    message survives the reload the action triggers.
    """
    sc = _open(page, manifest, "reextract_target")
    assert sc["mutable"] and sc["status"] == "quarantined"
    with page.expect_response(
        re.compile(rf"/po_sets/{sc['id']}/redo_extract"), timeout=60000
    ) as resp:
        page.get_by_test_id("btn-reextract").click()
    assert resp.value.status == 200, resp.value.text()
    # The page reloads itself on success; the toast must survive that reload
    # and must not claim the documents were read (they were not).
    expect(page.locator(".toast-error")).to_contain_text("Extraction failed", timeout=20000)


def test_ui_s_21_a_wrong_customs_file_can_be_removed_before_the_merge(page: Page, manifest, db):
    """S-21: Remove takes the CUSTOMS file out, says so, and the gate shows it missing."""
    sc = _open(page, manifest, "customs_remove_target")
    assert sc["mutable"] and sc["status"] == "pending"
    customs = next(d for d in sc["docs"] if d["type"] == "CUSTOMS")
    shipping = next(d for d in sc["docs"] if d["type"] == "SHIPPING")
    customs_row = page.locator(f'[data-testid="doc-row"][data-doc-id="{customs["id"]}"]')
    expect(page.get_by_test_id(f"btn-remove-supporting-{customs['id']}")).to_be_visible()
    expect(page.get_by_test_id(f"btn-remove-supporting-{shipping['id']}")).to_be_visible()
    clearance = page.locator("div", has=page.locator("strong", has_text="Customs Clearance:")).last
    expect(clearance).to_contain_text("Attached")

    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(re.compile(r"/remove_supporting")) as resp:
        page.get_by_test_id(f"btn-remove-supporting-{customs['id']}").click()
    assert resp.value.status == 200, resp.value.text()

    # The page reloads itself; the confirmation must survive that reload.
    expect(customs_row).to_have_count(0)
    expect(page.locator(".toast-success")).to_contain_text(f"Removed {customs['file']}")
    expect(clearance).to_contain_text("Missing")
    expect(page.get_by_test_id(f"btn-remove-supporting-{shipping['id']}")).to_be_visible()
    row = db.execute(
        "select po_set_id, archived_at from documents where id=?", (customs["id"],)
    ).fetchone()
    assert row["po_set_id"] is None and row["archived_at"]


def test_ui_s_21b_a_merged_set_offers_no_remove_button(page: Page, manifest):
    _open(page, manifest, "merged_basic")
    expect(page.locator('[data-testid^="btn-remove-supporting-"]')).to_have_count(0)


def _num(text: str) -> int:
    """Page quantity text -> DB integer (x1000)."""
    return round(float(text.strip()) * 1000)


ORPHAN = re.compile(r"\b(DN|SI) line (\S+) \(qty ([0-9.]+)\)")


def test_ui_s_09_matrix_totals_equal_the_database_for_every_set(page: Page, db):
    """S-09: data-driven for every set — rows and per-side totals equal the DB.

    The matrix shows a vendor quantity on the PO line it was paired with. A
    vendor line paired with nothing is left out of the totals and must be
    shown as a flag instead, so it is added back from the flag text. All
    comparisons are on the DB's integers x1000.
    """
    problems = []
    set_ids = [r[0] for r in db.execute("select id from po_sets order by id")]
    for set_id in set_ids:
        page.goto(f"/po_sets/{set_id}/view")
        expect(page.get_by_test_id("set-po")).to_be_visible()
        rows = page.get_by_test_id("matrix-row")
        # A PO line with no printed number is keyed by its description
        # (matching.po_item_key), so the matrix has a row for it. Count the
        # same key here; a bare count(distinct line_item_no) ignores it.
        expected_rows = db.execute(
            "select count(distinct coalesce(li.line_item_no, 'd:' || coalesce(li.description, ''))) "
            "from line_items li "
            "join documents d on d.id = li.document_id "
            "where d.po_set_id = ? and d.doc_type = 'PO'",
            (set_id,),
        ).fetchone()[0]
        if rows.count() != expected_rows:
            problems.append(
                f"set {set_id}: {rows.count()} matrix rows, DB has {expected_rows} PO line numbers"
            )

        flags = page.get_by_test_id("flag")
        flag_text = [flags.nth(i).inner_text() for i in range(flags.count())]
        for pool in ("PO", "DN", "SI"):
            shown = sum(
                _num(rows.nth(i).get_by_test_id(f"cell-{pool.lower()}-qty").inner_text())
                for i in range(rows.count())
            )
            orphans = sum(
                _num(m.group(3))
                for text in flag_text
                for m in ORPHAN.finditer(text)
                if m.group(1) == pool
            )
            stored = db.execute(
                "select coalesce(sum(li.quantity), 0) from line_items li "
                "join documents d on d.id = li.document_id "
                "where d.po_set_id = ? and d.doc_type = ?",
                (set_id, pool),
            ).fetchone()[0]
            if shown + orphans != stored:
                problems.append(
                    f"set {set_id}: {pool} column {shown} + orphans {orphans} "
                    f"!= DB {stored} (x1000)"
                )
    assert not problems, "\n".join(problems)


def test_ui_s_10_a_two_po_set_names_both_po_files(page: Page, manifest):
    """S-10: "2 PO documents" is not actionable without the file names."""
    sc = _open(page, manifest, "pending_two_po_documents")
    card = page.get_by_test_id("reason-card")
    expect(card).to_contain_text("holds 2 PO documents")
    for doc in sc["docs"]:
        if doc["type"] == "PO":
            expect(card).to_contain_text(doc["file"])


def test_ui_s_11_the_customs_hold_names_both_required_documents(page: Page, manifest, db):
    """S-11: the block is not a delay; it names the two files the gate needs."""
    sc = _open(page, manifest, "awaiting_customs")
    reason = db.execute("select reconcile_reason from po_sets where id=?", (sc["id"],)).fetchone()[
        0
    ]
    card = page.get_by_test_id("reason-card")
    expect(card).to_contain_text("CUSTOMS")
    expect(card).to_contain_text("SHIPPING")
    expect(card).to_contain_text(reason[:40])
    expect(page.get_by_test_id("upload-form")).to_be_visible()
    clearance = page.locator("div", has=page.locator("strong", has_text="Customs Clearance:")).last
    expect(clearance).to_contain_text("Missing")


def _pdf_bytes(width: int, title: str) -> bytes:
    """A one-page PDF whose bytes and page size are unique to `title`."""
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=width, height=791)
    writer.add_metadata({"/Title": title})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_ui_s_12_upload_refuses_a_non_pdf_and_attaches_two_pdfs(page: Page, manifest, db):
    """S-12: the upload form validates, then attaches one CUSTOMS and one SHIPPING file."""
    sc = _open(page, manifest, "upload_customs_target")
    assert sc["mutable"] and sc["status"] == "pending"
    before = db.execute(
        "select count(*) from documents where po_set_id=? and archived_at is null", (sc["id"],)
    ).fetchone()[0]

    # A file NAMED .pdf whose bytes are not a PDF is refused.
    form = page.get_by_test_id("upload-form")
    form.locator("input[type=file]").set_input_files(
        {
            "name": "not-a-pdf.pdf",
            "mimeType": "application/pdf",
            "buffer": b"hello, not a pdf",
        }
    )
    form.locator("select[name=doc_type]").select_option("CUSTOMS")
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/upload$")) as resp:
        form.get_by_role("button", name="Upload & Attach").click()
    assert resp.value.status == 302, resp.value.text()
    expect(page.get_by_test_id("notice")).to_contain_text("not a PDF file")
    after_bad = db.execute(
        "select count(*) from documents where po_set_id=? and archived_at is null", (sc["id"],)
    ).fetchone()[0]
    assert after_bad == before, "a refused upload must not attach a document"

    # One real PDF per required type: the attached document count rises by two.
    for doc_type, width, title, filename in (
        ("CUSTOMS", 611, "ui-s-12-customs", "CUSTOMS-upload.pdf"),
        ("SHIPPING", 613, "ui-s-12-shipping", "AWB-upload.pdf"),
    ):
        page.goto(f"/po_sets/{sc['id']}/view")
        form = page.get_by_test_id("upload-form")
        form.locator("input[type=file]").set_input_files(
            {
                "name": filename,
                "mimeType": "application/pdf",
                "buffer": _pdf_bytes(width, title),
            }
        )
        form.locator("select[name=doc_type]").select_option(doc_type)
        with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/upload$")) as resp:
            form.get_by_role("button", name="Upload & Attach").click()
        assert resp.value.status == 302, resp.value.text()

    expect(page.get_by_test_id("doc-row")).to_have_count(before + 2)
    types = [
        r[0]
        for r in db.execute(
            "select doc_type from documents where po_set_id=? and archived_at is null",
            (sc["id"],),
        ).fetchall()
    ]
    assert len(types) == before + 2
    assert types.count("CUSTOMS") == 1 and types.count("SHIPPING") == 1, types
    assert (
        db.execute("select customs_doc_count from po_sets where id=?", (sc["id"],)).fetchone()[0]
        == 2
    )


def test_ui_s_13_rematch_reloads_and_keeps_the_reason(page: Page, manifest, db):
    """S-13: Re-Match is a free re-run, not a status change."""
    sc = _open(page, manifest, "rematch_target")
    assert sc["mutable"] and sc["status"] == "pending"
    before = db.execute(
        "select status, reconcile_reason from po_sets where id=?", (sc["id"],)
    ).fetchone()
    assert before["reconcile_reason"]
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/redo_match")) as resp:
        page.get_by_test_id("btn-rematch").click()
    assert resp.value.status == 200, resp.value.text()
    # The page reloads itself; the confirmation must survive that reload.
    # The toast now states what matching decided, not only that it ran (I-43).
    expect(page.locator(".toast")).to_contain_text("Re-matched", timeout=15000)
    expect(page.get_by_test_id("reason-card")).to_contain_text(before["reconcile_reason"][:40])
    after = db.execute(
        "select status, reconcile_reason from po_sets where id=?", (sc["id"],)
    ).fetchone()
    assert after["reconcile_reason"] == before["reconcile_reason"], (
        "re-matching with no new documents changed the reason"
    )
    assert after["status"] == "pending"


def test_ui_s_14_merge_now_on_a_mismatch_does_not_merge(page: Page, manifest, db):
    """S-14: the gated Merge Now may not merge a discrepancy, and must say so."""
    sc = _open(page, manifest, "merge_now_mismatch_target")
    assert sc["mutable"] and sc["status"] == "mismatched"
    reason = db.execute("select reconcile_reason from po_sets where id=?", (sc["id"],)).fetchone()[
        0
    ]
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/merge$")) as resp:
        page.get_by_test_id("btn-merge-now").click()
    assert resp.value.status == 200, resp.value.text()
    assert resp.value.json()["status"] == "mismatched"
    expect(page.locator(".toast-warning")).to_contain_text("Not merged - still mismatched")
    row = db.execute(
        "select status, merged_output_path from po_sets where id=?", (sc["id"],)
    ).fetchone()
    assert row["status"] == "mismatched"
    assert row["merged_output_path"] is None
    expect(page.get_by_test_id("reason-card")).to_contain_text(reason[:40])


def test_ui_s_14b_merge_now_toast_states_the_quantity_difference(page: Page, manifest):
    """S-14b: the toast should repeat the quantity difference the page shows."""
    sc = _open(page, manifest, "merge_now_reason_target")
    assert sc["mutable"] and sc["status"] == "mismatched"
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/merge$")) as resp:
        page.get_by_test_id("btn-merge-now").click()
    assert resp.value.status == 200, resp.value.text()
    expect(page.locator(".toast-warning")).to_contain_text("delivered 8 of 10")


def test_ui_s_18_the_preview_drawer_closes_with_its_button_and_escape(page: Page, manifest):
    """S-18: the slide-over must be dismissible both ways."""
    sc = _open(page, manifest, "merged_basic")
    first = page.locator(f'[data-testid="doc-row"][data-doc-id="{sc["docs"][0]["id"]}"]')
    drawer = page.get_by_test_id("pdf-drawer")

    with page.expect_response(re.compile(r"/documents/\d+/preview")):
        first.get_by_test_id("btn-preview").click()
    expect(drawer).to_have_class(re.compile(r"\bopen\b"))
    page.get_by_test_id("btn-drawer-close").click()
    expect(drawer).not_to_have_class(re.compile(r"\bopen\b"))

    with page.expect_response(re.compile(r"/documents/\d+/preview")):
        first.get_by_test_id("btn-preview").click()
    expect(drawer).to_have_class(re.compile(r"\bopen\b"))
    page.keyboard.press("Escape")
    expect(drawer).not_to_have_class(re.compile(r"\bopen\b"))


def test_ui_s_19_a_merged_set_offers_its_packet_for_download(page: Page, manifest, db):
    """S-19: the packet the set was merged into must be reachable from its page."""
    sc = _open(page, manifest, "merged_basic")
    link = page.get_by_test_id("btn-download-merged")
    expect(link).to_be_visible()
    with page.expect_download() as info:
        link.click()
    path = info.value.path()
    stored = db.execute(
        "select merged_output_path from po_sets where id=?", (sc["id"],)
    ).fetchone()[0]
    assert Path(path).read_bytes() == Path(stored).read_bytes()
    assert Path(path).read_bytes()[:4] == b"%PDF"


def test_ui_s_20_an_archived_set_is_read_only(page: Page, base_url, manifest, db):
    """S-20: an archived set keeps its evidence but offers no action."""
    sc = manifest["scenarios"]["archived_readonly"]
    assert sc["archived"]
    _open(page, manifest, "archived_readonly")
    expect(page.get_by_test_id("archived-banner")).to_be_visible()
    for testid in (
        "btn-toggle-customs",
        "btn-merge-now",
        "btn-rematch",
        "btn-reextract",
        "btn-force-merge",
        "btn-archive",
        "upload-form",
        "btn-confirm-placement",
    ):
        expect(page.get_by_test_id(testid), testid).to_have_count(0)
    expect(page.locator('[data-testid^="btn-remove-supporting-"]')).to_have_count(0)
    # Viewing is not acting: the documents are still listed.
    expect(page.get_by_test_id("doc-row")).to_have_count(len(sc["docs"]))

    # The read-only rule is enforced by the server, not only by hidden buttons.
    refused = page.request.post(f"{base_url}/po_sets/{sc['id']}/redo_match")
    assert refused.status == 409, refused.text()
    assert "archived" in refused.text()
    stored = db.execute(
        "select status, archived_at from po_sets where id=?", (sc["id"],)
    ).fetchone()
    assert stored["status"] == "quarantined"
    assert stored["archived_at"] is not None


def test_ui_s_22_a_settling_set_page_shows_why_it_waits(page: Page, manifest, db):
    """S-22: the poller-started wait — every gate passed, only the clock is left."""
    from app.services.reconciliation import SETTLING_PREFIX

    sc = _open(page, manifest, "settling")
    assert sc["status"] == "pending"
    stored = db.execute(
        "select status, reconcile_reason from po_sets where id=?", (sc["id"],)
    ).fetchone()
    reason = stored["reconcile_reason"]
    card = page.get_by_test_id("reason-card")
    expect(card).to_contain_text(reason[:40])
    expect(card).to_contain_text("Why this set is waiting")
    # Nothing is missing: the wait is the settle window, not a document.
    rows = page.get_by_test_id("matrix-row")
    expect(rows.first).to_contain_text("Match")
    for i in range(rows.count()):
        expect(rows.nth(i)).to_contain_text("Match")
    expect(page.get_by_test_id("btn-merge-now")).to_be_enabled()
    assert stored["status"] == "pending"
    assert reason.startswith(SETTLING_PREFIX)


def test_ui_s_23_merge_now_merges_a_settling_set_at_once(page: Page, manifest, db):
    """S-23/D5: only the poller waits. Merge Now runs with settle 0."""
    sc = _open(page, manifest, "settling_merge_now_target")
    assert sc["mutable"] and sc["status"] == "pending"
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/merge$")) as resp:
        page.get_by_test_id("btn-merge-now").click()
    assert resp.value.status == 200, resp.value.text()
    assert resp.value.json()["status"] == "merged"
    expect(page.locator(".toast-success")).to_contain_text("Merged:")
    row = db.execute(
        "select status, merged_output_path from po_sets where id=?", (sc["id"],)
    ).fetchone()
    assert row["status"] == "merged"
    assert Path(row["merged_output_path"]).read_bytes()[:4] == b"%PDF"


def test_ui_s_24_placed_by_number_set_offers_confirm_on_that_document_only(
    page: Page, base_url, manifest, db
):
    """S-24: the confirm button is on the placed document, and nowhere else."""
    sc = _open(page, manifest, "placed_by_number_only")
    placed = next(d for d in sc["docs"] if d["po_no_source"] == "dn_number")
    po_doc = next(d for d in sc["docs"] if d["type"] == "PO")
    assert placed["file"] == "DN-7000021-b.pdf"
    reason = db.execute("select reconcile_reason from po_sets where id=?", (sc["id"],)).fetchone()[
        0
    ]
    expect(page.get_by_test_id("reason-card")).to_contain_text(reason[:40])
    expect(page.get_by_test_id("reason-card")).to_contain_text("DN-7000021-b.pdf")
    expect(page.get_by_test_id("btn-confirm-placement")).to_have_count(1)
    row = page.locator(f'[data-testid="doc-row"][data-doc-id="{placed["id"]}"]')
    expect(row.get_by_test_id("btn-confirm-placement")).to_be_visible()

    # A document that needs no confirmation is refused, and nothing changes.
    refused = page.request.post(
        f"{base_url}/po_sets/{sc['id']}/documents/{po_doc['id']}/confirm_placement"
    )
    assert refused.status == 409, refused.text()
    assert "needs no confirmation" in refused.text()
    assert (
        db.execute("select status from po_sets where id=?", (sc["id"],)).fetchone()[0] == "pending"
    )


def test_ui_s_25_confirming_the_placement_lets_the_set_merge(page: Page, manifest, db):
    """S-25/WS1: confirming a number-only placement clears the hold and merges."""
    sc = _open(page, manifest, "confirm_placement_target")
    assert sc["mutable"] and sc["status"] == "pending"
    placed = next(d for d in sc["docs"] if d["po_no_source"] == "dn_number")
    row = page.locator(f'[data-testid="doc-row"][data-doc-id="{placed["id"]}"]')
    # The button belongs to the placed document and to no other.
    expect(page.get_by_test_id("btn-confirm-placement")).to_have_count(1)
    # This action reloads the page as soon as it completes, which destroys the
    # response body. Fetch the same request through the router and replay the
    # identical response, so the JSON can be read without changing behaviour.
    payloads: list[dict] = []

    def _capture(route):
        response = route.fetch()
        payloads.append(response.json())
        route.fulfill(response=response)

    matcher = re.compile(r".*/confirm_placement$")
    page.route(matcher, _capture)
    try:
        with page.expect_response(matcher) as resp:
            row.get_by_test_id("btn-confirm-placement").click()
    finally:
        page.unroute(matcher, _capture)
    assert resp.value.status == 200, resp.value.text()
    assert payloads and payloads[0]["status"] == "merged", payloads

    # No toast is shown for this action; only the reloaded page reports it.
    expect(page.get_by_test_id("btn-confirm-placement")).to_have_count(0, timeout=15000)

    stored = db.execute(
        "select po_no_source, po_set_id from documents where id=?", (placed["id"],)
    ).fetchone()
    assert stored["po_no_source"] == "manual", "the confirmation was not recorded"
    set_row = db.execute(
        "select status, merged_output_path from po_sets where id=?", (sc["id"],)
    ).fetchone()
    assert set_row["status"] == "merged", (
        f"the confirmed set was re-evaluated to {set_row['status']!r}"
    )
    assert Path(set_row["merged_output_path"]).read_bytes()[:4] == b"%PDF"
    audits = db.execute(
        "select count(*) from audit_log where po_set_id=? and action='manual_status_change' "
        "and detail like '%confirmed_placement%'",
        (sc["id"],),
    ).fetchone()[0]
    assert audits == 1, f"confirming a placement wrote {audits} audit rows"


HOSTILE_NAME = '<img src=x onerror="window.__xss=1"><b>bold</b>.pdf'


def test_ui_x_02_a_hostile_filename_is_escaped_on_the_set_page(page: Page, manifest):
    """X-02: a filename is untrusted data on every page that prints it."""
    sc = _open(page, manifest, "hostile_name_in_set")
    hostile = next(d for d in sc["docs"] if d["file"] == HOSTILE_NAME)
    row = page.locator(f'[data-testid="doc-row"][data-doc-id="{hostile["id"]}"]')
    expect(row).to_contain_text("<b>bold</b>")  # the markup is visible as literal text
    expect(page.get_by_test_id("reason-card")).to_contain_text("<b>bold</b>")
    assert page.evaluate("window.__xss") is None  # the onerror handler never ran
    expect(page.locator("img[src='x']")).to_have_count(0)
    expect(page.get_by_test_id("reason-card").locator("b")).to_have_count(0)
    expect(row.locator("b")).to_have_count(0)
