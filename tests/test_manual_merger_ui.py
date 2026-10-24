"""Static checks for the rebuilt Merge PDFs UI.

The earlier version had no behavioural JS tests at all; the drag-and-drop
regression lived in an inline script and only a source grep protected it.
These tests pin the contracts the browser code relies on.  A real browser run
is still part of verification (see MANUAL_MERGER_AUDIT_2026-10-23.md).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "manual_merger.html"
ERROR_TEMPLATE = ROOT / "templates" / "manual_merger_error.html"
JS = ROOT / "static" / "js" / "manual_merger.js"
CSS = ROOT / "static" / "css" / "manual_merger.css"
DASHBOARD = ROOT / "templates" / "dashboard.html"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_template_uses_the_application_shell():
    html = read(TEMPLATE)

    assert html.lstrip().startswith('{% extends "base.html" %}')
    assert "manual_merger.css" in html
    assert "manual_merger.js" in html


def test_template_passes_the_server_limits_to_the_client():
    html = read(TEMPLATE)

    assert 'data-max-files="{{ max_files }}"' in html
    assert 'data-max-bytes="{{ max_bytes }}"' in html
    assert "{{ max_mb }}" in html


def test_file_input_is_labelled_and_limited_to_pdfs():
    html = read(TEMPLATE)

    assert 'for="file-input"' in html
    assert 'type="file"' in html
    assert 'id="file-input"' in html
    assert "multiple" in html
    assert 'accept=".pdf,application/pdf"' in html


def test_dropzone_is_keyboard_operable():
    html = read(TEMPLATE)

    assert 'role="button"' in html
    assert 'tabindex="0"' in html
    assert 'aria-label="Add PDF files' in html


def test_staged_list_and_alerts_are_announced():
    html = read(TEMPLATE)

    assert 'aria-live="polite"' in html
    assert 'role="alert"' in html
    assert 'aria-live="assertive"' in html
    assert 'role="progressbar"' in html
    assert 'aria-valuenow="0"' in html


def test_no_inline_event_handlers_or_legacy_dom_strings():
    html = read(TEMPLATE)
    script = read(JS)

    assert "onclick=" not in html
    assert ".innerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "document.write" not in script


def test_filenames_are_always_written_as_text():
    script = read(JS)

    assert "name.textContent = item.name" in script
    assert "name.title = item.name" in script


def test_os_file_drop_is_added_never_reordered():
    """The regression: a file dropped on a row used to move the first staged file."""
    script = read(JS)

    assert "event.dataTransfer.files" in script
    assert "dropped && dropped.length" in script
    assert "addFiles(dropped)" in script
    # The reorder path must come after the file-drop branch.
    assert script.index("addFiles(dropped)") < script.index("getData('text/plain')")


def test_drops_anywhere_are_guarded_against_navigation():
    script = read(JS)

    assert "document.addEventListener('dragover'" in script
    assert "document.addEventListener('drop'" in script
    assert "event.preventDefault()" in script


def test_upload_order_comes_from_the_display_list_not_a_filelist():
    script = read(JS)

    assert "new FormData()" in script
    assert "state.items.forEach" in script
    assert "formData.append('files'" in script
    # The old approach mutated the file input through DataTransfer.
    assert "new DataTransfer(" not in script
    assert "input.files =" not in script


def test_xhr_reports_progress_and_keeps_the_page():
    script = read(JS)

    assert "XMLHttpRequest" in script
    assert "xhr.upload.onprogress" in script
    assert "xhr.onprogress" in script
    assert "setRequestHeader('Accept', 'application/json')" in script
    assert "Content-Disposition" in script
    assert r"filename\*" in script
    assert "UTF-8''" in script


def test_errors_are_rendered_in_place_with_the_offending_file():
    script = read(JS)
    html = read(TEMPLATE)

    assert "readError" in script
    assert "payload.filename" in script
    assert "showAlert(" in script
    assert 'id="mm-alert"' in html
    assert 'id="mm-alert-text"' in html


def test_client_limits_match_the_servers_vocabulary():
    script = read(JS)

    assert "MAX_FILES" in script
    assert "MAX_BYTES" in script
    assert "only PDF files can be merged" in script
    assert "you can merge up to" in script


def test_assets_are_offline_only():
    combined = read(TEMPLATE) + read(JS) + read(CSS)

    assert "http://" not in combined
    assert "https://" not in combined
    assert "fonts.googleapis" not in combined


def test_css_is_part_of_the_design_system():
    css = read(CSS)

    assert "var(--primary)" in css
    assert "var(--text-muted)" in css
    assert "@media (max-width: 900px)" in css
    assert ".mm-dropzone" in css
    assert ".mm-file.has-error" in css
    assert ".mm-progress-bar" in css


def test_error_page_extends_the_shell_and_offers_a_way_back():
    html = read(ERROR_TEMPLATE)

    assert html.lstrip().startswith('{% extends "base.html" %}')
    assert 'href="/manual/merger"' in html
    assert "Nothing was added to the reconciliation pipeline" in html


def test_dashboard_links_to_the_tool():
    html = read(DASHBOARD)

    assert 'href="/manual/merger"' in html
    assert "Merge PDFs" in html


def test_output_name_is_validated_live():
    script = read(JS)
    html = read(TEMPLATE)

    assert "function nameProblem" in script
    assert "INVALID_NAME_CHARS" in script
    assert "reserved by Windows" in script
    assert "finalPdfName" in script
    assert "els.nameError" in script
    assert "aria-invalid" in script
    assert "Boolean(nameIssue)" in script
    assert 'maxlength="104"' in html
    assert 'id="mm-name-error"' in html
    assert 'id="mm-name-preview"' in html
    assert 'value="{{ default_name }}"' in html


def test_output_name_rules_match_the_server():
    from app.services.manual_merge import INVALID_PDF_NAME_CHARS, MAX_PDF_NAME_STEM

    script = read(JS)

    assert "MAX_NAME_STEM = " + str(MAX_PDF_NAME_STEM) in script
    for char in INVALID_PDF_NAME_CHARS:
        assert char in script
    assert "CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]" in script


def test_ui_states_the_encryption_policy():
    html = read(TEMPLATE)

    assert "Password-protected PDFs can't be merged" in html
    assert "upload an unlocked copy" in html
