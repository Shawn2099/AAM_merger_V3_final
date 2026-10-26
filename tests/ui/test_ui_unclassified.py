"""Unclassified page (UI-U-*), Quarantine page (UI-Q-*), Audit page (UI-A-*)."""

import re

from playwright.sync_api import Page, expect


def test_ui_u_01_every_held_document_is_listed_with_a_reason_to_wait(page: Page, db):
    """Every document waiting for a human is listed, by name.

    Order-independent: U-04 and U-10 place a held document into a PO Set, which
    removes its row, so the expectation comes from the database (the same query
    the page uses) and never from the build-time manifest.
    """
    held = db.execute(
        "select id, original_filename from documents where archived_at is null "
        "and po_set_id is null and (coalesce(is_split_parent, 0) = 0 or "
        "(split_completed_at is null and extraction_status = 'failed'))"
    ).fetchall()
    page.goto("/unclassified")
    rows = page.get_by_test_id("unclassified-row")
    expect(rows).to_have_count(len(held))
    for doc in held:
        row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{doc["id"]}"]')
        expect(row, str(doc["id"])).to_contain_text(doc["original_filename"])


def test_ui_u_02_failed_and_rejected_files_are_marked_failed(page: Page, manifest):
    page.goto("/unclassified")
    failed = page.locator(
        f'[data-testid="unclassified-row"][data-doc-id="{manifest["held"]["held_failed"]["id"]}"]'
    )
    expect(failed).to_contain_text("Failed")
    rejected = page.locator(
        f'[data-testid="unclassified-row"][data-doc-id="{manifest["held"]["rejected_on_arrival"]["id"]}"]'
    )
    expect(rejected).to_contain_text("Failed")
    expect(rejected).to_contain_text("Rejected on arrival")


def test_ui_u_03_route_button_is_disabled_until_a_type_is_chosen(page: Page, manifest):
    page.goto("/unclassified")
    row = page.locator(
        f'[data-testid="unclassified-row"][data-doc-id="{manifest["held"]["held_dn_no_po"]["id"]}"]'
    )
    expect(row.get_by_test_id("btn-route")).to_be_disabled()
    row.get_by_test_id("select-doc-type").select_option("DN")
    expect(row.get_by_test_id("btn-route")).to_be_enabled()


def test_ui_u_04_reclassifying_a_dn_with_a_po_number_places_it_in_that_set(
    page: Page, manifest, db
):
    held = manifest["held"]["held_reclassify_target"]
    target = manifest["scenarios"]["pending_no_dn"]
    assert held["mutable"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    row.get_by_test_id("select-doc-type").select_option("DN")
    row.get_by_test_id("input-po-no").fill(target["po_no"])
    with page.expect_response(re.compile(rf"/unclassified/{held['id']}/reclassify")) as resp:
        row.get_by_test_id("btn-route").click()
    assert resp.value.status == 200, resp.value.text()
    placed = db.execute(
        "select po_set_id, doc_type from documents where id=?", (held["id"],)
    ).fetchone()
    assert placed["po_set_id"] == target["id"] and placed["doc_type"] == "DN"


def test_ui_q_01_quarantine_page_lists_exactly_the_quarantined_sets(page: Page, db):
    """Order-independent: other tests archive or re-extract sets in the shared
    session environment (S-07 archives one, S-16 re-extracts one), so the
    expectation comes from the database, never from the build-time manifest."""
    expected = {
        r["id"]
        for r in db.execute(
            "select id from po_sets where status='quarantined' and archived_at is null"
        )
    }
    page.goto("/quarantine")
    rows = page.get_by_test_id("quarantine-row")
    seen = {int(rows.nth(i).get_attribute("data-set-id")) for i in range(rows.count())}
    assert seen == expected, (
        f"listed but not quarantined: {seen - expected}; missing: {expected - seen}"
    )


def test_ui_q_04_the_destructive_control_is_called_archive(page: Page):
    """The action archives the set (rows + PDFs are kept). Calling it `Delete`
    tells the operator the opposite of what happens to the evidence."""
    page.goto("/quarantine")
    button = page.get_by_test_id("btn-archive-row").first
    expect(button).to_be_visible(timeout=5000)
    expect(button).to_have_text(re.compile(r"Move to Quarantine Folder|Archive"))


def test_ui_a_01_audit_page_shows_the_seeded_row_and_its_justification(page: Page):
    """Other tests add newer audit rows, so find the seeded one by its text."""
    page.goto("/audit")
    seeded = page.get_by_test_id("audit-row").filter(has_text="Seeded row for the UI tests")
    expect(seeded).to_have_count(1)


def test_ui_x_01_a_hostile_filename_is_shown_as_text_not_executed(page: Page, manifest):
    """Filenames come from outside. HTML in one must be escaped on every page that lists it."""
    held = manifest["held"]["held_hostile_name"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    expect(row).to_contain_text("<b>bold</b>")  # the markup is visible as literal text
    assert page.evaluate("window.__xss") is None  # and the onerror handler never ran
    expect(row.locator("img[src='x']")).to_have_count(0)
    expect(row.locator("b")).to_have_count(0)


def test_ui_u_07_previewing_a_failed_file_explains_itself(page: Page, manifest):
    """U-07: the drawer must not render chrome's JSON viewer (F-02)."""
    held = manifest["held"]["rejected_on_arrival"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    with page.expect_response(re.compile(rf"/documents/{held['id']}/preview")) as resp:
        row.get_by_text("Preview").click()
    assert resp.value.status == 422
    assert resp.value.headers["content-type"].startswith("text/html")
    body = resp.value.text()
    assert "cannot be previewed" in body
    assert "Rejected on arrival" in body
    expect(
        page.frame_locator(".drawer-iframe").get_by_text("cannot be previewed", exact=False)
    ).to_be_visible()


def test_ui_u_06_an_ambiguous_po_is_marked_on_its_own_row(page: Page, manifest):
    """F-06/U-06: one badge for the page is not enough to find the file."""
    ambiguous = manifest["held"]["held_ambiguous_po"]
    other = manifest["held"]["held_dn_no_po"]
    page.goto("/unclassified")
    ambiguous_row = page.locator(
        f'[data-testid="unclassified-row"][data-doc-id="{ambiguous["id"]}"]'
    )
    other_row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{other["id"]}"]')
    expect(ambiguous_row).to_contain_text("multiple PO numbers")
    expect(other_row).not_to_contain_text("multiple PO numbers")


def test_ui_u_11_the_stored_type_is_visible_without_preselecting_it(page: Page, manifest):
    """F-05/U-11: never pre-select the control (U-03), but show what is stored."""
    held = manifest["held"]["held_dn_no_po"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    expect(row).to_contain_text(f"Stored type: {held['type']}")
    expect(row.get_by_test_id("select-doc-type")).to_have_value("")
    expect(row.get_by_test_id("btn-route")).to_be_disabled()


def test_ui_u_09_a_document_that_cannot_be_placed_says_so_after_the_refresh(
    page: Page, manifest, db
):
    """F-05: the route's explanation arrives with HX-Refresh, which reloads
    the page before htmx would swap the row in. It must survive as a toast."""
    held = manifest["held"]["held_reclassify_no_po"]
    assert held["mutable"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    row.get_by_test_id("select-doc-type").select_option("DN")
    with page.expect_response(re.compile(rf"/unclassified/{held['id']}/reclassify")) as resp:
        row.get_by_test_id("btn-route").click()
    assert resp.value.status == 200
    # The response body is gone once htmx reloads; the toast proves the
    # message in it was read before the reload.
    expect(page.locator(".toast-warning")).to_contain_text("no open PO Set exists", timeout=15000)
    row_after = db.execute(
        "select doc_type, po_set_id from documents where id=?", (held["id"],)
    ).fetchone()
    assert row_after["doc_type"] == "DN"
    assert row_after["po_set_id"] is None, "it must stay held, not be placed"


def test_ui_u_05_typing_a_po_number_previews_the_key_the_server_will_store(page: Page, manifest):
    """U-05: the operator must see the key before routing, not discover it later."""
    held = manifest["held"]["held_dn_no_po"]
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    with page.expect_response(re.compile(r"/unclassified/normalize\?po_no=")) as resp:
        row.get_by_test_id("input-po-no").fill("PO-7000003")
    assert resp.value.json()["normalized"] == "7000003"
    expect(row.get_by_test_id("po-key-preview")).to_contain_text("Server key: 7000003")


def test_ui_u_08_a_file_rejected_on_arrival_cannot_be_routed(page: Page, manifest, db):
    """U-08: a rejected file offers no placement controls, only the reason and a move (I-40)."""
    held = manifest["held"]["rejected_on_arrival"]
    assert held["po_set_id"] is None
    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    expect(row).to_have_count(1)
    expect(row.get_by_test_id("select-doc-type")).to_have_count(0)
    expect(row.get_by_test_id("input-po-no")).to_have_count(0)
    expect(row.get_by_test_id("btn-route")).to_be_hidden()
    expect(row).to_contain_text("Cannot be placed: rejected on arrival")
    expect(row.get_by_test_id("btn-set-aside")).to_be_visible()
    stored = db.execute(
        "select doc_type, po_set_id from documents where id=?", (held["id"],)
    ).fetchone()
    assert stored["po_set_id"] is None
    assert stored["doc_type"] == "UNKNOWN"


def _awaiting_badge_count(page: Page) -> int:
    text = page.get_by_test_id("unclassified-count").inner_text()
    return int(re.search(r"\d+", text).group())


def test_ui_q_02_each_quarantine_row_shows_its_reason_and_opens_its_set(page: Page, manifest, db):
    """Q-02: a quarantine list without the reason is a list of numbers."""
    quarantined = {
        r["id"]: r["reconcile_reason"]
        for r in db.execute(
            "select id, reconcile_reason from po_sets "
            "where status='quarantined' and archived_at is null"
        )
    }
    page.goto("/quarantine")
    expect(page.get_by_test_id("quarantine-row")).to_have_count(len(quarantined))
    for set_id, reason in quarantined.items():
        row = page.locator(f'[data-testid="quarantine-row"][data-set-id="{set_id}"]')
        if reason:
            expect(row.get_by_test_id("quarantine-row-reason")).to_contain_text(reason[:40])
        else:
            expect(row.get_by_test_id("quarantine-row-reason")).to_contain_text(
                "Reason not recorded"
            )

    # Inspect must open THAT set, not merely a set page.
    sc = manifest["scenarios"]["quarantined_po_no_lines"]
    page.locator(f'[data-testid="quarantine-row"][data-set-id="{sc["id"]}"]').get_by_test_id(
        "btn-inspect-row"
    ).click()
    page.wait_for_url(re.compile(rf"/po_sets/{sc['id']}/view"))
    expect(page.get_by_test_id("set-po")).to_have_text("7000010")


def test_ui_q_03_the_table_archive_modal_cancels_or_archives(page: Page, manifest, db):
    """Q-03: Cancel must change nothing; Confirm must archive that set."""
    sc = manifest["scenarios"]["archive_modal_target"]
    assert sc["mutable"] and sc["status"] == "quarantined"
    deleted: list[str] = []
    page.on("request", lambda r: deleted.append(r.url) if r.method == "DELETE" else None)
    page.goto("/quarantine")
    row = page.locator(f'[data-testid="quarantine-row"][data-set-id="{sc["id"]}"]')
    modal = row.get_by_test_id("archive-row-modal")

    # Cancel closes the dialog and changes nothing.
    row.get_by_test_id("btn-archive-row").click()
    expect(modal).to_have_class(re.compile(r"\bopen\b"))
    row.get_by_test_id("btn-cancel-archive-row").click()
    expect(modal).not_to_have_class(re.compile(r"\bopen\b"))
    assert deleted == [], f"Cancel sent a DELETE: {deleted}"
    assert (
        db.execute("select archived_at from po_sets where id=?", (sc["id"],)).fetchone()[0] is None
    ), "Cancel archived the set"

    # Confirm archives exactly this set.
    row.get_by_test_id("btn-archive-row").click()
    expect(modal).to_have_class(re.compile(r"\bopen\b"))
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/quarantine$")) as resp:
        row.get_by_test_id("btn-confirm-archive-row").click()
    assert resp.value.status == 200, resp.value.text()
    assert resp.value.request.method == "DELETE"
    expect(row).to_have_count(0)
    archived = db.execute("select archived_at from po_sets where id=?", (sc["id"],)).fetchone()[0]
    assert archived, "Confirm did not archive the set"
    audits = db.execute(
        "select count(*) from audit_log where po_set_id=? and action='quarantine_delete'",
        (sc["id"],),
    ).fetchone()[0]
    assert audits == 1, f"archiving wrote {audits} quarantine_delete audit rows"


def test_ui_a_02_a_force_merge_leaves_its_justification_in_the_audit_log(page: Page, manifest, db):
    """A-02: the durable "why" must be readable after the action."""
    sc = manifest["scenarios"]["audit_force_merge_target"]
    assert sc["mutable"] and sc["status"] == "mismatched"
    justification = "A-02 audit case: operator accepted the short delivery."
    page.goto(f"/po_sets/{sc['id']}/view")
    page.get_by_test_id("btn-force-merge").click()
    page.locator("#force-merge-justification").fill(justification)
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/force_merge")) as resp:
        page.get_by_test_id("btn-confirm-force-merge").click()
    assert resp.value.status == 200, resp.value.text()

    page.goto("/audit")
    entry = page.get_by_test_id("audit-row").filter(has_text=justification)
    expect(entry).to_have_count(1)
    expect(entry).to_contain_text("Force Merge")
    expect(entry.get_by_role("link", name=f"PO Set #{sc['id']}", exact=True)).to_have_count(1)
    stored = db.execute(
        "select action, justification from audit_log where po_set_id=? and action='force_merge'",
        (sc["id"],),
    ).fetchone()
    assert stored["justification"] == justification


def test_ui_a_03_archiving_a_quarantined_set_is_audited(page: Page, manifest, db):
    """A-03: the archive action needs a row of its own, attached to that set."""
    sc = manifest["scenarios"]["audit_archive_target"]
    assert sc["mutable"] and sc["status"] == "quarantined"
    page.goto(f"/po_sets/{sc['id']}/view")
    page.get_by_test_id("btn-archive").click()
    with page.expect_response(re.compile(rf"/po_sets/{sc['id']}/quarantine$")) as resp:
        page.get_by_test_id("btn-confirm-archive").click()
    assert resp.value.status == 200, resp.value.text()

    page.goto("/audit")
    entry = (
        page.get_by_test_id("audit-row")
        .filter(has=page.get_by_role("link", name=f"PO Set #{sc['id']}", exact=True))
        .filter(has_text="Quarantine Archive")
    )
    expect(entry).to_have_count(1)
    stored = db.execute(
        "select count(*) from audit_log where po_set_id=? and action='quarantine_delete'",
        (sc["id"],),
    ).fetchone()[0]
    assert stored == 1


def test_ui_u_10_the_count_badge_follows_a_reclassify(page: Page, manifest, db):
    """U-10: the holding-area count is the row count, not a build-time constant.

    Appended last on purpose: it removes a row from the holding area, so every
    other Unclassified case in this file sees the set of rows the build made.
    The page is reloaded before the two counts are compared: the successful
    route swaps only the row, so the server-rendered badge is stale until the
    next navigation (reported as H-03 in the WS1 report).
    """
    held = manifest["held"]["held_count_target"]
    assert held["mutable"] and held["po_set_id"] is None
    page.goto("/unclassified")
    rows = page.get_by_test_id("unclassified-row")
    before = rows.count()
    assert _awaiting_badge_count(page) == before

    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{held["id"]}"]')
    row.get_by_test_id("select-doc-type").select_option("DN")
    row.get_by_test_id("input-po-no").fill("7000090")
    with page.expect_response(re.compile(rf"/unclassified/{held['id']}/reclassify")) as resp:
        row.get_by_test_id("btn-route").click()
    assert resp.value.status == 200, resp.value.text()
    placed = db.execute("select po_set_id from documents where id=?", (held["id"],)).fetchone()[0]
    assert placed is not None, "the document was not placed"
    assert (
        db.execute("select po_no_normalized from po_sets where id=?", (placed,)).fetchone()[0]
        == "7000090"
    )

    page.reload()
    expect(page.get_by_test_id("unclassified-row")).to_have_count(before - 1)
    expect(page.get_by_test_id("unclassified-count")).to_have_text(
        f"{before - 1} awaiting classification"
    )
    badge = re.findall(r"\d+", page.get_by_test_id("nav-unclassified").inner_text())
    assert badge and int(badge[-1]) == before - 1, page.get_by_test_id(
        "nav-unclassified"
    ).inner_text()


def test_ui_u_20_move_to_quarantine_takes_a_held_row_off_the_list(page: Page, ui_env):
    """Batch step 10 (decision D10): a held document moves to the quarantine folder, its row
    leaves the holding list, and the page says where the copy is. Seeds its own document, so
    other tests' counts are not affected (the move archives it again)."""
    import hashlib
    import io
    from datetime import UTC, datetime

    from pypdf import PdfWriter
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus

    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=111, height=222)
    writer.write(buf)
    data = buf.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    stored = ui_env.root / f"{sha}.pdf"
    stored.write_bytes(data)
    cfg = load_config(str(ui_env.root / "config.yaml"))
    with Session(get_engine(cfg)) as s:
        doc = Document(
            sha256_hash=sha,
            original_filename="U20-MOVE-ME.pdf",
            stored_path=str(stored),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    page.goto("/unclassified")
    row = page.locator(f'[data-testid="unclassified-row"][data-doc-id="{doc_id}"]')
    expect(row).to_have_count(1)
    page.once("dialog", lambda d: d.accept())
    row.get_by_test_id("btn-set-aside").click()
    expect(page.get_by_test_id("set-aside-result")).to_contain_text(
        "quarantine folder", timeout=15000
    )
    page.goto("/unclassified")
    expect(page.locator(f'[data-testid="unclassified-row"][data-doc-id="{doc_id}"]')).to_have_count(
        0
    )
