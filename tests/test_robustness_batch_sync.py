"""Robustness: one nightly sync fed a batch of hostile inputs (2026-10-24 audit).

ONE `sync_flow()` run ingests a good PO/DN/SI set together with ~70 bad files:
broken VLM replies, provider faults, junk/zero-byte/encrypted/truncated files and
seventeen PO sets that each carry one adversarial quantity / line / PO-number
pattern. The module fixture runs the sync once; every test below then asserts the
outcome of ONE input. That proves containment directly: the good set must still
merge however many neighbours misbehave in the same run.

Passing tests pin acceptable behaviour. `xfail(strict=True)` marks a defect
against the rule "merged output is absolutely correct; doubtful documents are
held for the operator; inconsistent sets are quarantined; nothing silently lost".
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter
from test_robustness_harness import (
    HttpVLM,
    all_docs,
    extraction,
    line,
    make_cfg,
    run_sync,
    sets,
    write_pdf,
)

from app.models import DocType, ExtractionStatus, POSetStatus

GOOD_PO = "4500012345"

# ---------------------------------------------------------------- singleton documents
# width -> (filename, VLM reply). Each is a document that must NOT disturb the good set.
SINGLES: dict[float, tuple[str, object]] = {
    120.0: (
        "allnull_unknown.pdf",
        {
            "document_type": "UNKNOWN",
            "document_number": None,
            "po_reference": None,
            "vendor_name": None,
            "line_items": [],
        },
    ),
    123.0: ("null_type.pdf", extraction(None, "X", GOOD_PO, [line("1", "10")])),
    126.0: ("invoice_type.pdf", extraction("INVOICE", "INV-9", GOOD_PO, [line("1", "10")])),
    129.0: (
        "unknown_with_good_po.pdf",
        extraction("UNKNOWN", "Z-1", GOOD_PO, [line("1", "10"), line("2", "5")]),
    ),
    132.0: ("po_without_number.pdf", extraction("PO", None, None, [line("1", "10")])),
    135.0: (
        "dn_ambiguous_but_ref_set.pdf",
        extraction(
            "DN", "DN-AMB", GOOD_PO, [line("1", "10"), line("2", "5")], po_reference_ambiguous=True
        ),
    ),
    138.0: ("prose.pdf", ("prose", "I'm sorry, I cannot read this document.")),
    141.0: ("truncated_args.pdf", ("args", '{"document_type": "PO", "document_number": "77')),
    144.0: ("http402.pdf", ("status", 402)),
    147.0: ("http500.pdf", ("status", 500)),
    150.0: ("timeout.pdf", ("raise", httpx.ReadTimeout("read timed out"))),
    153.0: ("http400.pdf", ("status", 400)),
    156.0: ("skip_cover.pdf", extraction("SKIP")),
    159.0: (
        "combined.pdf",
        extraction(
            "COMBINED",
            None,
            GOOD_PO,
            [],
            page_count=1,
            components=[{"document_type": "PO", "page_start": 1, "page_end": 1}],
        ),
    ),
    162.0: ("truncated_http_body.pdf", ("body", '{"id":"x","choices":[{"mess', 200)),
    165.0: ("float_line_no.pdf", extraction("DN", "DN-F", "31337", [line(1.5, "10")])),
    168.0: ("bool_qty.pdf", extraction("DN", "DN-B", "31337", [line("1", True)])),
    171.0: ("dn_for_absent_po.pdf", extraction("DN", "DN-W", "31337", [line("1", "10")])),
    174.0: ("too_many_pages.pdf", extraction("PO", "88001", None, [line("1", "1")])),
    177.0: ("resume.pdf", extraction("UNKNOWN", None, None, [])),
    180.0: ("menu_read_as_si.pdf", extraction("SI", "MENU", None, [line("1", "2")])),
    183.0: ("html_error_reply.pdf", ("body", "<html>502 Bad Gateway</html>", 200)),
    186.0: ("number_type_po_ref.pdf", ("args", '{"document_type":"DN","po_reference":4500012345}')),
}
TOO_MANY_PAGES_W = 174.0


# ---------------------------------------------------------------- adversarial sets
# key -> (po_no printed on PO, dn ref, si ref, po_lines, dn_lines, si_lines)
def _l(*pairs):
    return [line(no, q, desc) for no, q, desc in pairs]


STD = _l(("1", "10", "BOLT M10"), ("2", "5", "NUT M10"))

SETS: dict[str, tuple] = {
    "good": (GOOD_PO, GOOD_PO, GOOD_PO, STD, STD, STD),
    "negative": (
        "7000101",
        "7000101",
        "7000101",
        STD,
        _l(("1", "-10", "BOLT M10"), ("2", "5", "NUT M10")),
        STD,
    ),
    "zero": (
        "7000102",
        "7000102",
        "7000102",
        STD,
        _l(("1", "0", "BOLT M10"), ("2", "5", "NUT M10")),
        STD,
    ),
    "huge": (
        "7000103",
        "7000103",
        "7000103",
        STD,
        _l(("1", "1e13", "BOLT M10"), ("2", "5", "NUT M10")),
        STD,
    ),
    "garbled": (
        "7000104",
        "7000104",
        "7000104",
        STD,
        _l(("1", "1O", "BOLT M10"), ("2", "5", "NUT M10")),
        STD,
    ),
    "uom_in_qty": (
        "7000105",
        "7000105",
        "7000105",
        STD,
        _l(("1", "10 EA", "BOLT M10"), ("2", "5", "NUT M10")),
        STD,
    ),
    "european": (
        "7000106",
        "7000106",
        "7000106",
        _l(("1", "1.234,00", "BOLT M10")),
        _l(("1", "1234", "BOLT M10")),
        _l(("1", "1234", "BOLT M10")),
    ),
    "fractional": (
        "7000107",
        "7000107",
        "7000107",
        _l(("1", "2.5", "CABLE M")),
        _l(("1", "2.50", "CABLE M")),
        _l(("1", "2.500", "CABLE M")),
    ),
    "three_decimals": (
        "7000108",
        "7000108",
        "7000108",
        _l(("1", "2.555", "CABLE M")),
        _l(("1", "2.555", "CABLE M")),
        _l(("1", "2.555", "CABLE M")),
    ),
    "dup_dn_line": (
        "7000109",
        "7000109",
        "7000109",
        STD,
        _l(("1", "4", "BOLT M10"), ("1", "6", "BOLT M10"), ("2", "5", "NUT M10")),
        STD,
    ),
    "po_total_row": (
        "7000110",
        "7000110",
        "7000110",
        STD + _l((None, "15", "TOTAL")),
        STD,
        STD,
    ),
    "dn_total_row": (
        "7000111",
        "7000111",
        "7000111",
        STD,
        STD + _l((None, "15", "TOTAL QUANTITY")),
        STD,
    ),
    "desc_only_vendor": (
        "7000112",
        "7000112",
        "7000112",
        STD,
        _l((None, "10", "BOLT M10"), (None, "5", "NUT M10")),
        _l((None, "10", "BOLT M10"), (None, "5", "NUT M10")),
    ),
    "po_no_lines": ("7000113", "7000113", "7000113", [], STD, STD),
    "dn_no_lines": ("7000114", "7000114", "7000114", STD, [], STD),
    "leading_zero_lines": (
        "7000115",
        "7000115",
        "7000115",
        _l(("01", "10", "BOLT M10"), ("002", "5", "NUT M10")),
        STD,
        STD,
    ),
    "po_spellings": ("PO-7000-116", "7000116", "P.O. 7000116 Rev 1", STD, STD, STD),
    "letters_conflict": (
        "TOGA-PO-25-26-117",
        "ARM-PO-25-26-117",
        "TOGA-PO-25-26-117",
        STD,
        STD,
        STD,
    ),
}
# A second, separately-scanned copy of the same delivery note (same DN number,
# different bytes) for the "dup_dn_number" set: PO 10, DN-9 delivers 5, scanned twice.
DUP_DN = {
    "po": ("7000118", _l(("1", "10", "BOLT M10"))),
    "dn": ("DN-9", _l(("1", "5", "BOLT M10"))),
    "si": ("INV-118", _l(("1", "10", "BOLT M10"))),
}


def _set_widths():
    w = 200.0
    out = {}
    for key in [*list(SETS), "dup_dn_number"]:
        out[key] = (w, w + 3, w + 6)
        w += 9
    out["dup_dn_number_scan2"] = w
    return out


SET_W = _set_widths()


def _responses():
    resp: dict[float, object] = {w: spec for w, (_name, spec) in SINGLES.items()}
    # A PDF in input/sub/ is read now (R-17): a DN that cites no PO, so it is held unattached.
    resp[396.0] = extraction("DN", "DN-SUB", None, [line("1", "10")])
    for key, (po_no, dn_ref, si_ref, po_l, dn_l, si_l) in SETS.items():
        wp, wd, ws = SET_W[key]
        resp[wp] = extraction("PO", po_no, None, po_l)
        resp[wd] = extraction("DN", f"DN-{key}", dn_ref, dn_l)
        resp[ws] = extraction("SI", f"INV-{key}", si_ref, si_l)
    wp, wd, ws = SET_W["dup_dn_number"]
    resp[wp] = extraction("PO", DUP_DN["po"][0], None, DUP_DN["po"][1])
    resp[wd] = extraction("DN", DUP_DN["dn"][0], DUP_DN["po"][0], DUP_DN["dn"][1])
    resp[SET_W["dup_dn_number_scan2"]] = extraction(
        "DN", DUP_DN["dn"][0], DUP_DN["po"][0], DUP_DN["dn"][1]
    )
    resp[ws] = extraction("SI", DUP_DN["si"][0], DUP_DN["po"][0], DUP_DN["si"][1])
    return resp


def _old(path: Path):
    t = time.time() - 3600
    os.utime(path, (t, t))


LONG_UNICODE = "Bestellung Lieferschein ü é ñ 发票 " + "x" * 120 + " (copy 1).pdf"


@pytest.fixture(scope="module")
def batch(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("batch")
    with pytest.MonkeyPatch.context() as mp:
        cfg = make_cfg(tmp, mp)
        vlm = HttpVLM(mp, _responses())
        inp = Path(cfg.paths.input_folder)

        for w, (name, _spec) in SINGLES.items():
            write_pdf(inp / name, w, pages=35 if w == TOO_MANY_PAGES_W else 1)
        for key in SETS:
            wp, wd, ws = SET_W[key]
            write_pdf(inp / f"{key}_PO.pdf", wp)
            write_pdf(inp / f"{key}_DN.pdf", wd)
            write_pdf(inp / f"{key}_SI.pdf", ws)
        wp, wd, ws = SET_W["dup_dn_number"]
        write_pdf(inp / "dupdn_PO.pdf", wp)
        write_pdf(inp / "dupdn_DN_scanA.pdf", wd)
        write_pdf(inp / "dupdn_DN_scanB.pdf", SET_W["dup_dn_number_scan2"])
        write_pdf(inp / "dupdn_SI.pdf", ws)

        # Non-VLM file faults.
        (inp / "zero.pdf").write_bytes(b"")
        (inp / "not_a_pdf.pdf").write_text(
            "Dear supplier, please find attached...", encoding="utf-8"
        )
        good_bytes = (inp / "good_PO.pdf").read_bytes()
        (inp / "truncated_old.pdf").write_bytes(good_bytes[: len(good_bytes) // 2])
        _old(inp / "truncated_old.pdf")
        (inp / "truncated_recent.pdf").write_bytes(good_bytes[: len(good_bytes) // 2])
        enc = PdfWriter()
        enc.add_blank_page(width=399, height=200)
        enc.encrypt(user_password="secret", owner_password="owner")
        with open(inp / "encrypted.pdf", "wb") as fh:
            enc.write(fh)
        _old(inp / "encrypted.pdf")
        (inp / "sub").mkdir()
        write_pdf(inp / "sub" / "in_subfolder.pdf", 396)
        (inp / "notes.txt").write_text("not a pdf", encoding="utf-8")
        # The good PO again, byte-identical, under a long unicode name with spaces.
        (inp / LONG_UNICODE).write_bytes(good_bytes)

        result = run_sync()
        yield {
            "cfg": cfg,
            "vlm": vlm,
            "result": result,
            "docs": {d.original_filename: d for d in all_docs(cfg)},
            "sets": {
                s.po_no_normalized + ("" if s.po_no_letters is None else f"/{s.po_no_letters}"): s
                for s in sets(cfg)
            },
            "input": sorted(p.name for p in inp.rglob("*") if p.is_file()),
            "qdocs": sorted(
                p.name
                for p in (Path(cfg.paths.quarantine_folder) / "_documents").rglob("*")
                if p.is_file()
            )
            if (Path(cfg.paths.quarantine_folder) / "_documents").exists()
            else [],
            "audit_rows": _audit_rows(cfg),
        }


def _audit_rows(cfg) -> list[tuple[str, int | None]]:
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import AuditLog

    with Session(get_engine(cfg)) as s:
        return [(str(a.action), a.po_set_id) for a in s.query(AuditLog).all()]


def test_every_automatic_quarantine_writes_an_audit_row(batch):
    """Every automatic quarantine (set or single document) leaves an audit row
    holding the QUARANTINE.txt text, so the trail outlives the folder."""
    quarantined = [s for s in batch["sets"].values() if s.status == POSetStatus.quarantined]
    assert len(quarantined) >= 10
    rows = batch["audit_rows"]
    assert rows and all(action.endswith("quarantined") for action, _ in rows), rows
    assert {ps_id for _, ps_id in rows if ps_id is not None} >= {s.id for s in quarantined}


def _set(batch, key):
    po_no = SETS[key][0] if key in SETS else DUP_DN["po"][0]
    from app.services.grouping import normalize_po_no

    norm = normalize_po_no(po_no)
    matches = [s for k, s in batch["sets"].items() if k.split("/")[0] == norm]
    assert len(matches) == 1, (key, list(batch["sets"]))
    return matches[0]


# ======================================================================= containment


def test_sync_returns_a_summary_and_never_raises(batch):
    res = batch["result"]
    assert set(res) >= {"processed", "extracted", "errors"}
    assert res["errors"] > 0, "the bad inputs must be counted as errors, not hidden"


def test_fake_received_no_request_for_an_unknown_file(batch):
    assert batch["vlm"].unknown == []


def test_good_set_still_merges_beside_seventy_bad_neighbours(batch):
    ps = _set(batch, "good")
    assert ps.status == POSetStatus.merged, ps.reconcile_reason
    names = sorted(d.original_filename for d in ps.documents)
    # The byte-identical copy LONG_UNICODE sorts first, so ITS name is the one
    # the single PO row carries (first name seen wins; see dedup test).
    assert names == sorted([LONG_UNICODE, "good_DN.pdf", "good_SI.pdf"]), names
    out = list(Path(batch["cfg"].paths.output_folder).glob("*.pdf"))
    assert "INV-good.pdf" in [p.name for p in out]


def test_byte_identical_copy_with_long_unicode_name_is_deduplicated_and_cleared(batch):
    # Same SHA as good_PO.pdf: ONE document row (named after whichever copy
    # sorted first), and BOTH input copies are removed with the merge
    # (delete_input_files matches by hash).
    assert "good_PO.pdf" not in batch["docs"]
    assert LONG_UNICODE in batch["docs"]
    assert LONG_UNICODE not in batch["input"] and "good_PO.pdf" not in batch["input"]


# ======================================================================= VLM replies


def _doc(batch, name):
    d = batch["docs"].get(name)
    assert d is not None, f"{name} has no document row"
    return d


def test_all_null_unknown_is_held_valid_and_unattached(batch):
    d = _doc(batch, "allnull_unknown.pdf")
    assert (d.extraction_status, d.doc_type, d.po_set_id) == (
        ExtractionStatus.valid,
        DocType.UNKNOWN,
        None,
    )


@pytest.mark.parametrize(
    "name",
    [
        "null_type.pdf",
        "invoice_type.pdf",
        "prose.pdf",
        "truncated_args.pdf",
        "http400.pdf",
        "truncated_http_body.pdf",
        "float_line_no.pdf",
        "bool_qty.pdf",
        "too_many_pages.pdf",
        "html_error_reply.pdf",
        "number_type_po_ref.pdf",
    ],
)
def test_schema_invalid_or_unreadable_reply_fails_doc_and_quarantines_it(batch, name):
    d = _doc(batch, name)
    assert d.extraction_status == ExtractionStatus.failed
    assert d.po_set_id is None
    assert name not in batch["input"], "a failed document is removed from input"
    stem = Path(d.stored_path).stem
    assert any(q.startswith(stem) or q == "QUARANTINE.txt" for q in batch["qdocs"])
    # The quarantined copy keeps the operator's original filename.
    assert (
        Path(batch["cfg"].paths.quarantine_folder)
        / "_documents"
        / stem
        / Path(d.original_filename).name
    ).is_file()


def test_attempt_cap_bounds_vlm_calls_for_a_content_error(batch):
    # 3 counted attempts; the 4th Prefect try only stamps `failed`.
    w = next(w for w, (n, _s) in SINGLES.items() if n == "prose.pdf")
    assert batch["vlm"].calls[w] == 3


def test_oversized_pdf_is_refused_without_any_request(batch):
    assert batch["vlm"].calls[TOO_MANY_PAGES_W] == 0


@pytest.mark.parametrize("name", ["http402.pdf", "http500.pdf", "timeout.pdf"])
def test_provider_fault_leaves_document_pending_in_input(batch, name):
    d = _doc(batch, name)
    assert d.extraction_status == ExtractionStatus.pending
    assert d.extraction_attempt_count == 0
    assert name in batch["input"]


def test_provider_fault_amplification_per_document(batch):
    """Observed: one document on a dead provider costs 8 HTTP calls per sync.

    4 Prefect tries in the ingest loop, then the recovery step re-extracts the
    same still-pending document with another 4. With a 60 s timeout that is
    ~8 minutes of a nightly window per document when the provider hangs; a
    200-document night on a hung endpoint is ~27 hours. Pinned as observed.
    """
    w = next(w for w, (n, _s) in SINGLES.items() if n == "timeout.pdf")
    assert batch["vlm"].calls[w] == 8


def test_unknown_with_good_po_number_is_not_attached(batch):
    d = _doc(batch, "unknown_with_good_po.pdf")
    assert d.doc_type == DocType.UNKNOWN and d.po_set_id is None


def test_ambiguous_flag_wins_over_a_populated_po_reference(batch):
    d = _doc(batch, "dn_ambiguous_but_ref_set.pdf")
    assert d.po_reference_ambiguous is True
    assert d.po_set_id is None


def test_po_without_number_is_held(batch):
    d = _doc(batch, "po_without_number.pdf")
    assert (d.extraction_status, d.doc_type, d.po_set_id, d.po_no_normalized) == (
        ExtractionStatus.valid,
        DocType.PO,
        None,
        None,
    )


def test_skip_becomes_unknown_and_is_held(batch):
    d = _doc(batch, "skip_cover.pdf")
    assert d.doc_type == DocType.UNKNOWN and d.po_set_id is None


def test_combined_is_quarantined_whole_and_held(batch):
    d = _doc(batch, "combined.pdf")
    assert d.doc_type == DocType.COMBINED and d.po_set_id is None and not d.line_items
    assert "combined.pdf" not in batch["input"]


@pytest.mark.parametrize("name", ["resume.pdf", "dn_for_absent_po.pdf"])
def test_junk_or_orphan_document_waits_unattached(batch, name):
    d = _doc(batch, name)
    assert d.extraction_status == ExtractionStatus.valid and d.po_set_id is None


def test_menu_misread_as_si_without_po_is_held(batch):
    d = _doc(batch, "menu_read_as_si.pdf")
    assert d.doc_type == DocType.SI and d.po_set_id is None and not d.po_no_normalized


# ======================================================================= file faults


def test_limitation_zero_byte_file_is_skipped_every_night(batch):
    """Decided 2026-10-24: a zero-byte .pdf is skipped for now (a later module may use it).

    No row, no quarantine, not counted; it stays in input. `is_file_stable` refuses
    size 0, so the ingest-time rejection is never reached. Nothing is lost.
    """
    assert "zero.pdf" in batch["input"]
    assert "zero.pdf" not in batch["docs"]


def test_files_in_subfolders_are_read(batch):
    """R-17 (decided 2026-10-24): input/sub/*.pdf is ingested like a top-level file."""
    assert "in_subfolder.pdf" in batch["docs"], "a PDF in a subfolder must be read"


def test_recently_written_truncated_pdf_stays_in_input_for_retry(batch):
    assert "truncated_recent.pdf" in batch["input"]
    assert "truncated_recent.pdf" not in batch["docs"]


@pytest.mark.parametrize("name", ["not_a_pdf.pdf", "truncated_old.pdf", "encrypted.pdf"])
def test_rejected_file_is_moved_to_quarantine_with_a_reason(batch, name):
    assert name not in batch["input"]
    stem = Path(name).stem
    assert any(q.startswith(stem + "_") and q.endswith(".reason.txt") for q in batch["qdocs"]), (
        batch["qdocs"]
    )


@pytest.mark.parametrize("name", ["not_a_pdf.pdf", "truncated_old.pdf", "encrypted.pdf"])
def test_rejected_file_is_visible_to_the_operator(batch, name):
    assert name not in batch["input"]  # precondition: it really was taken out
    assert name in batch["docs"], "no row -> not on the Unclassified page"


# ======================================================================= sets


@pytest.mark.parametrize(
    "key", ["negative", "zero", "huge", "garbled", "uom_in_qty", "european", "three_decimals"]
)
def test_unparseable_or_non_positive_quantity_quarantines_the_set(batch, key):
    ps = _set(batch, key)
    assert ps.status == POSetStatus.quarantined, (key, ps.status, ps.reconcile_reason)
    assert "zero, negative, or unreadable" in (ps.reconcile_reason or "")
    assert ps.merged_output_path is None


@pytest.mark.parametrize(
    "key", ["fractional", "dup_dn_line", "desc_only_vendor", "leading_zero_lines", "po_spellings"]
)
def test_legitimate_variant_merges(batch, key):
    ps = _set(batch, key)
    assert ps.status == POSetStatus.merged, (key, ps.reconcile_reason)


def test_description_fallback_is_flagged_on_the_merged_set(batch):
    ps = _set(batch, "desc_only_vendor")
    assert ps.description_match_count == 4
    assert "matched by description" in (ps.reconcile_reason or "")


def test_po_totals_row_holds_the_set_pending_as_partial_delivery(batch):
    """Observed: a numberless TOTAL row on the PO becomes a PO line nobody
    delivers. The set never merges (safe) but is shown as "Waiting on more
    deliveries or invoices" forever, which is misleading."""
    ps = _set(batch, "po_total_row")
    assert ps.status == POSetStatus.pending
    assert "Waiting on more deliveries" in (ps.reconcile_reason or "")


def test_dn_totals_row_quarantines_as_unmatched(batch):
    ps = _set(batch, "dn_total_row")
    assert ps.status == POSetStatus.quarantined
    assert "could not be matched" in (ps.reconcile_reason or "")


def test_po_without_lines_quarantines(batch):
    ps = _set(batch, "po_no_lines")
    assert ps.status == POSetStatus.quarantined
    assert "no line items" in (ps.reconcile_reason or "")


def test_dn_without_lines_quarantines(batch):
    ps = _set(batch, "dn_no_lines")
    assert ps.status == POSetStatus.quarantined
    assert "no line items" in (ps.reconcile_reason or "")


def test_dn_without_lines_reason_names_the_file(batch):
    ps = _set(batch, "dn_no_lines")
    assert ps.status == POSetStatus.quarantined
    assert "dn_no_lines_DN.pdf" in (ps.reconcile_reason or "")


def test_letters_conflict_holds_the_dn_and_the_set_waits(batch):
    ps = _set(batch, "letters_conflict")
    assert ps.status == POSetStatus.pending
    dn = _doc(batch, "letters_conflict_DN.pdf")
    assert dn.po_set_id is None


def test_same_dn_number_twice_in_one_set_is_not_summed_into_a_merge(batch):
    ps = _set(batch, "dup_dn_number")
    dn_numbers = [d.dn_no for d in ps.documents if d.doc_type == DocType.DN]
    assert dn_numbers == ["DN-9", "DN-9"]  # precondition: both scans are in the set
    assert ps.status == POSetStatus.quarantined, "two copies of one DN were summed"
    assert ps.reconcile_reason and "DN-9" in ps.reconcile_reason
