"""Robustness: failure containment, crash/restart, disk and filesystem faults (2026-10-24).

Passing tests pin acceptable behaviour; `xfail(strict=True)` marks a defect.
"""

from __future__ import annotations

import errno
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session
from test_robustness_harness import (
    HttpVLM,
    all_docs,
    doc_by_name,
    extraction,
    line,
    make_cfg,
    run_sync,
    sets,
    write_pdf,
)

from app.core.database import get_engine
from app.models import AuditLog, Document, ExtractionStatus, POSet, POSetStatus


@pytest.fixture()
def rcfg(tmp_path, monkeypatch):
    return make_cfg(tmp_path, monkeypatch)


PO_NO = "4500077777"
W_PO, W_DN, W_SI = 111.0, 222.0, 333.0
STD = [line("1", "10", "BOLT"), line("2", "5", "NUT")]


def _good(dn_lines=None):
    return {
        W_PO: extraction("PO", PO_NO, None, STD),
        W_DN: extraction("DN", "DN-1", PO_NO, dn_lines or STD),
        W_SI: extraction("SI", "INV-1", PO_NO, STD),
    }


def _drop(cfg, *which):
    inp = Path(cfg.paths.input_folder)
    widths = {"PO": W_PO, "DN": W_DN, "SI": W_SI}
    for t in which or ("PO", "DN", "SI"):
        write_pdf(inp / f"{t}.pdf", widths[t])


def _ingest_and_extract(cfg, name, width):
    from app.services.extraction import extract_document
    from app.services.ingestion import ingest_file

    p = write_pdf(Path(cfg.paths.input_folder) / name, width)
    doc = ingest_file(p, cfg)
    return doc.id, (lambda: extract_document(doc.id, cfg))


def _status(cfg, doc_id):
    with Session(get_engine(cfg)) as s:
        d = s.get(Document, doc_id)
        return d.extraction_status, d.extraction_attempt_count, d.po_set_id


# ======================================================================= DB-level faults


def test_a_stray_unit_price_in_the_vlm_reply_is_ignored(rcfg, monkeypatch):
    stray = line("1", "10")
    stray["unit_price"] = "100000000000000000"  # price is not extracted; a stray key is ignored
    HttpVLM(monkeypatch, {W_DN: extraction("DN", "DN-1", PO_NO, [stray])})
    doc_id, extract = _ingest_and_extract(rcfg, "dn.pdf", W_DN)
    extract()
    status, _attempts, _ = _status(rcfg, doc_id)
    assert status == ExtractionStatus.valid
    from app.models.models import LineItem

    assert not hasattr(LineItem, "unit_price")


def test_db_error_at_extraction_commit_ends_as_a_counted_failure(rcfg, monkeypatch):
    """A flush-time DB error must not leave the row 'processing' with no attempt counted."""
    import app.services.extraction as ext

    HttpVLM(monkeypatch, {W_DN: extraction("DN", "DN-1", PO_NO, STD)})
    doc_id, extract = _ingest_and_extract(rcfg, "dn.pdf", W_DN)

    def boom(doc, result, s, cfg):
        doc.extraction_status = ExtractionStatus.valid
        s.flush()
        raise OverflowError("Python int too large to convert to SQLite INTEGER")

    monkeypatch.setattr(ext, "populate_from_raw_dict", boom)
    with pytest.raises(OverflowError):
        extract()
    status, attempts, _ = _status(rcfg, doc_id)
    assert (status, attempts) == (ExtractionStatus.failed, 1)


def test_overflow_quantity_is_bounded_by_the_sanitizer(rcfg, monkeypatch):
    # The quantity path has a 1e12 ceiling, so it cannot overflow the column.
    HttpVLM(monkeypatch, {W_DN: extraction("DN", "DN-1", PO_NO, [line("1", "999999999999999999")])})
    _doc_id, extract = _ingest_and_extract(rcfg, "dn.pdf", W_DN)
    extract()
    doc = doc_by_name(rcfg, "dn.pdf")
    assert doc.extraction_status == ExtractionStatus.valid
    assert [li.quantity for li in doc.line_items] == [0]  # -> non_positive_quantity quarantine


# ======================================================================= stability / ingest


def test_one_unstat_able_file_does_not_abort_the_sync(rcfg, monkeypatch):
    import app.services.ingestion as ing

    HttpVLM(monkeypatch, _good())
    _drop(rcfg)
    write_pdf(Path(rcfg.paths.input_folder) / "locked.pdf", 444)
    real = ing.is_file_stable

    def flaky(p, interval, count):
        if p.name == "locked.pdf":
            raise PermissionError(13, "Access is denied", str(p))
        return real(p, interval, count)

    monkeypatch.setattr(ing, "is_file_stable", flaky)
    run_sync()
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.merged


def test_transient_read_error_leaves_a_good_file_for_the_next_sync(rcfg, monkeypatch):
    HttpVLM(monkeypatch, _good())
    _drop(rcfg)
    real_read = Path.read_bytes
    state = {"tripped": False}

    def read_once_denied(self):
        if self.name == "DN.pdf" and not state["tripped"]:
            state["tripped"] = True
            raise PermissionError(13, "The process cannot access the file", str(self))
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", read_once_denied)
    run_sync()
    assert state["tripped"]
    assert (Path(rcfg.paths.input_folder) / "DN.pdf").exists(), "good DN moved out of input"


def test_file_deleted_between_poll_and_ingest_is_counted_and_sync_continues(rcfg, monkeypatch):
    import app.services.ingestion as ing

    HttpVLM(monkeypatch, _good())
    _drop(rcfg)
    ghost = write_pdf(Path(rcfg.paths.input_folder) / "AAA_ghost.pdf", 444)
    real = ing.is_file_stable

    def vanish(p, interval, count):
        ok = real(p, interval, count)
        if p.name == "AAA_ghost.pdf":
            ghost.unlink()
        return ok

    monkeypatch.setattr(ing, "is_file_stable", vanish)
    res = run_sync()
    assert res["errors"] >= 1
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.merged


def test_file_still_growing_is_left_for_the_next_sync(rcfg, monkeypatch):
    import app.services.ingestion as ing

    HttpVLM(monkeypatch, _good())
    _drop(rcfg)
    monkeypatch.setattr(
        ing,
        "is_file_stable",
        lambda p, interval, count: p.name != "SI.pdf",
    )
    res = run_sync()
    assert res["errors"] == 0
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.pending
    assert (Path(rcfg.paths.input_folder) / "SI.pdf").exists()
    assert doc_by_name(rcfg, "SI.pdf") is None


# ======================================================================= provider faults


def test_402_mid_sync_then_key_restored_merges_on_the_next_sync(rcfg, monkeypatch):
    responses = _good()
    good_dn = responses[W_DN]
    responses[W_DN] = ("status", 402, {"error": {"message": "Key limit exceeded", "code": 402}})
    HttpVLM(monkeypatch, responses)
    _drop(rcfg)
    first = run_sync()
    assert first["errors"] >= 1
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.pending and ps.merged_output_path is None
    assert doc_by_name(rcfg, "DN.pdf").extraction_status == ExtractionStatus.pending

    responses[W_DN] = good_dn
    run_sync()
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.merged, ps.reconcile_reason


def test_a_provider_wide_400_does_not_condemn_the_whole_batch(rcfg, monkeypatch):
    bad = ("status", 400, {"error": {"message": "x is not a valid model ID", "code": 400}})
    HttpVLM(monkeypatch, {W_PO: bad, W_DN: bad, W_SI: bad})
    _drop(rcfg)
    run_sync()
    docs = all_docs(rcfg)
    assert len(docs) == 3
    assert all(d.extraction_status != ExtractionStatus.failed for d in docs)


def test_the_400_breaker_refunds_attempts_and_reports_the_halt(rcfg, monkeypatch):
    bad = ("status", 400, {"error": {"message": "x is not a valid model ID", "code": 400}})
    vlm = HttpVLM(monkeypatch, {W_PO: bad, W_DN: bad, W_SI: bad})
    _drop(rcfg)
    res = run_sync()
    assert res["vlm_halted"] is True
    docs = all_docs(rcfg)
    assert {d.extraction_status for d in docs} == {ExtractionStatus.pending}
    assert {d.extraction_attempt_count for d in docs} == {0}, "no attempt may be charged"
    # The verdict was withheld, so nothing was moved or recorded as quarantined.
    qdocs = Path(rcfg.paths.quarantine_folder) / "_documents"
    assert not qdocs.exists() or not list(qdocs.iterdir())
    with Session(get_engine(rcfg)) as s:
        assert s.query(AuditLog).count() == 0
    assert sorted(p.name for p in Path(rcfg.paths.input_folder).iterdir()) == [
        "DN.pdf",
        "PO.pdf",
        "SI.pdf",
    ]
    # The tripping call is not retried by Prefect.
    assert max(vlm.calls.values()) <= rcfg.extraction.max_retries + 1


def test_a_document_the_breaker_already_refunded_is_judged_on_its_own(rcfg, monkeypatch):
    """Night two: the same three 400s no longer halt the run; each fails as usual."""
    bad = ("status", 400, {"error": {"message": "image too large", "code": 400}})
    HttpVLM(monkeypatch, {W_PO: bad, W_DN: bad, W_SI: bad})
    _drop(rcfg)
    assert run_sync()["vlm_halted"] is True  # night one: all refunded, marked
    res = run_sync()  # night two: the marker makes each rejection its own verdict
    assert res["vlm_halted"] is False
    assert {d.extraction_status for d in all_docs(rcfg)} == {ExtractionStatus.failed}


def test_a_good_read_between_400s_does_not_trip_the_breaker(rcfg, monkeypatch):
    """Only an unbroken run of rejected DIFFERENT documents is taken as a bad request."""
    bad = ("status", 400, {"error": {"message": "bad pdf", "code": 400}})
    good = _good()
    HttpVLM(monkeypatch, {W_PO: good[W_PO], W_DN: bad, W_SI: bad})
    _drop(rcfg)
    res = run_sync()
    assert res["vlm_halted"] is False
    by_name = {d.original_filename: d for d in all_docs(rcfg)}
    assert by_name["PO.pdf"].extraction_status == ExtractionStatus.valid
    assert by_name["DN.pdf"].extraction_status == ExtractionStatus.failed
    assert by_name["SI.pdf"].extraction_status == ExtractionStatus.failed


# ======================================================================= crash / restart / concurrency


def test_document_stuck_in_processing_after_a_crash_is_reclaimed_and_read(rcfg, monkeypatch):
    HttpVLM(monkeypatch, _good())
    from app.services.ingestion import ingest_file

    _drop(rcfg)
    inp = Path(rcfg.paths.input_folder)
    for name in ("PO.pdf", "DN.pdf", "SI.pdf"):
        ingest_file(inp / name, rcfg)
    old = datetime.now(UTC) - timedelta(hours=2)
    with Session(get_engine(rcfg)) as s:
        s.execute(
            update(Document)
            .where(Document.original_filename == "DN.pdf")
            .values(extraction_status=ExtractionStatus.processing, updated_at=old)
            .execution_options(synchronize_session=False)
        )
        s.commit()
    run_sync()
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.merged, ps.reconcile_reason


def test_a_recent_processing_claim_is_respected_and_counted_as_extracted(rcfg, monkeypatch):
    """Observed: a fresh `processing` row (another worker) is not touched, but the
    sync summary counts it under `extracted` (flows/sync.py:648) - cosmetic."""
    HttpVLM(monkeypatch, _good())
    from app.services.ingestion import ingest_file

    _drop(rcfg, "DN")
    doc = ingest_file(Path(rcfg.paths.input_folder) / "DN.pdf", rcfg)
    with Session(get_engine(rcfg)) as s:
        s.execute(
            update(Document)
            .where(Document.id == doc.id)
            .values(extraction_status=ExtractionStatus.processing)
            .execution_options(synchronize_session=False)
        )
        s.commit()
    res = run_sync()
    assert res["extracted"] == 1 and res["errors"] == 0
    assert _status(rcfg, doc.id)[0] == ExtractionStatus.processing


def test_second_sync_while_one_holds_the_lock_is_skipped(rcfg, monkeypatch):
    from app.flows.sync import sync_flow
    from app.services.sync_lock import acquire_sync_lock, release_sync_lock

    HttpVLM(monkeypatch, _good())
    _drop(rcfg)
    lock = acquire_sync_lock(None)
    assert lock is not None
    try:
        res = sync_flow()
    finally:
        release_sync_lock(lock)
    assert res["status"] == "skipped"
    assert all_docs(rcfg) == []


# ======================================================================= merge-time faults


def _reconciled_set_ready_to_merge(cfg, monkeypatch, *, merge_patch=None):
    """Run a sync with the merge write patched; return the set."""
    HttpVLM(monkeypatch, _good())
    _drop(cfg)
    if merge_patch is not None:
        import pypdf

        monkeypatch.setattr(pypdf.PdfWriter, "write", merge_patch)
    res = run_sync()
    (ps,) = sets(cfg)
    return ps, res


def test_disk_full_at_merge_quarantines_with_packet_write_failed_and_no_partial_file(
    rcfg, monkeypatch
):
    def enospc(self, stream):
        Path(stream).write_bytes(b"%PDF-1.4 partial")
        raise OSError(errno.ENOSPC, "No space left on device")

    ps, _ = _reconciled_set_ready_to_merge(rcfg, monkeypatch, merge_patch=enospc)
    assert ps.status == POSetStatus.quarantined
    assert "could not be written" in (ps.reconcile_reason or "") or "No space" in (
        ps.reconcile_reason or ""
    )
    assert list(Path(rcfg.paths.output_folder).iterdir()) == [], "no partial/tmp packet left"
    assert (Path(rcfg.paths.input_folder) / "PO.pdf").exists(), "inputs kept when nothing merged"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DEFECT R-07: an infrastructure failure at write time (disk full, output share "
    "offline, 60 s output-lock timeout -> filelock.Timeout is an OSError) quarantines a "
    "set whose documents are CONSISTENT, and quarantine is sticky under every sweep. "
    "After the disk is fixed the set stays quarantined until an operator acts.",
)
def test_set_recovers_after_disk_full_is_fixed(rcfg, monkeypatch):
    import pypdf

    real_write = pypdf.PdfWriter.write

    def enospc(self, stream):
        raise OSError(errno.ENOSPC, "No space left on device")

    ps, _ = _reconciled_set_ready_to_merge(rcfg, monkeypatch, merge_patch=enospc)
    assert ps.status == POSetStatus.quarantined  # precondition
    monkeypatch.setattr(pypdf.PdfWriter, "write", real_write)
    run_sync()
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.merged


def test_pypdf_error_during_packet_write_quarantines_the_set(rcfg, monkeypatch):
    from pypdf.errors import PdfReadError

    def broken(self, stream):
        raise PdfReadError("Could not read object 12 0 R")

    ps, res = _reconciled_set_ready_to_merge(rcfg, monkeypatch, merge_patch=broken)
    assert ps.merged_output_path is None  # precondition: nothing was delivered
    assert ps.status == POSetStatus.quarantined, (ps.status, ps.reconcile_reason, res)


def test_output_folder_that_is_a_file_quarantines_as_write_failure(rcfg, monkeypatch):
    out = Path(rcfg.paths.output_folder)
    out.rmdir()
    out.write_text("not a folder", encoding="utf-8")
    ps, _res = _reconciled_set_ready_to_merge(rcfg, monkeypatch)
    assert ps.status == POSetStatus.quarantined
    assert ps.merged_output_path is None


def test_stored_file_vanished_before_merge_quarantines_as_source_unreadable(rcfg, monkeypatch):
    from app.services.reconciliation import reconcile_po_set

    HttpVLM(monkeypatch, _good())
    import app.services.merge as merge_mod

    real = merge_mod.merge_po_set
    monkeypatch.setattr(merge_mod, "merge_po_set", lambda *a, **k: None)  # hold the merge
    _drop(rcfg)
    run_sync()
    monkeypatch.setattr(merge_mod, "merge_po_set", real)
    (ps,) = sets(rcfg)
    si = doc_by_name(rcfg, "SI.pdf")
    Path(si.stored_path).unlink()
    res = reconcile_po_set(ps.id, rcfg)
    assert res["status"] == "quarantined" and res["reason"] == "packet_source_unreadable"
    assert list(Path(rcfg.paths.output_folder).glob("*.pdf")) == []


def test_quarantine_folder_failure_still_records_the_reason(rcfg, monkeypatch):
    HttpVLM(monkeypatch, _good(dn_lines=[line("1", "-10", "BOLT"), line("2", "5", "NUT")]))
    q = Path(rcfg.paths.quarantine_folder)
    q.rmdir()
    q.write_text("share offline", encoding="utf-8")
    _drop(rcfg)
    run_sync()
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.quarantined  # precondition
    assert ps.reconcile_reason, "quarantined with no reason"


# ======================================================================= silent loss


def test_document_bytes_survive_somewhere_when_storage_is_broken(rcfg, monkeypatch):
    HttpVLM(monkeypatch, {W_DN: ("status", 503)})
    _drop(rcfg, "DN")
    run_sync()  # provider down: the document is stored and stays pending
    d = doc_by_name(rcfg, "DN.pdf")
    stored = Path(d.stored_path)
    payload = (Path(rcfg.paths.input_folder) / "DN.pdf").read_bytes()
    # The storage folder becomes unusable (deleted and replaced by a file:
    # a broken mount, a share remapped, an over-eager cleanup script).
    for f in stored.parent.iterdir():
        f.unlink()
    stored.parent.rmdir()
    stored.parent.write_text("unavailable", encoding="utf-8")
    run_sync()
    survivors = [
        p
        for p in Path(rcfg.paths.input_folder).parent.rglob("*")
        if p.is_file() and p.read_bytes() == payload
    ]
    assert survivors, "no copy of DN.pdf exists anywhere any more"


def test_quarantine_document_with_missing_stored_copy_keeps_a_copy_before_removing_input(rcfg):
    """Unit view of R-10: the input copy is only removed once a copy exists in quarantine."""
    from app.services.ingestion import ingest_file
    from app.services.quarantine import quarantine_document

    p = write_pdf(Path(rcfg.paths.input_folder) / "x.pdf", 500)
    payload = p.read_bytes()
    doc = ingest_file(p, rcfg)
    Path(doc.stored_path).unlink()
    folder = quarantine_document(doc.id, rcfg, reason="test")
    assert not p.exists(), "input copy removed"
    copies = [f for f in folder.iterdir() if f.suffix == ".pdf"]
    assert copies and copies[0].read_bytes() == payload


# ======================================================================= re-read drift


def test_reread_with_digitless_po_reference_detaches_the_document(rcfg, monkeypatch):
    from app.services.extraction import extract_document

    # Was defect R-11 when the audit started (the digit check in populate_from_raw_dict
    # was imported but unused); fixed in the working tree during the audit. Pinned.
    responses = _good()
    vlm = HttpVLM(monkeypatch, responses)
    _drop(rcfg)
    import app.services.merge as merge_mod

    monkeypatch.setattr(merge_mod, "merge_po_set", lambda *a, **k: None)
    run_sync()
    dn = doc_by_name(rcfg, "DN.pdf")
    assert dn.po_set_id is not None  # precondition: attached by the first read
    responses[W_DN] = extraction("DN", "DN-1", "N/A", STD)
    with Session(get_engine(rcfg)) as s:
        s.execute(
            update(Document)
            .where(Document.id == dn.id)
            .values(extraction_status=ExtractionStatus.pending)
        )
        s.commit()
    extract_document(dn.id, rcfg)
    assert vlm.calls[W_DN] == 2
    after = doc_by_name(rcfg, "DN.pdf")
    assert after.po_no_normalized == ""  # precondition: the new read has no key
    assert after.po_set_id is None, "still attached on the strength of the old read"


# ======================================================================= revisions


def test_revised_po_before_merge_holds_the_set_for_review(rcfg, monkeypatch):
    responses = _good()
    responses[444.0] = extraction("PO", PO_NO + " Rev 1", None, STD)
    HttpVLM(monkeypatch, responses)
    _drop(rcfg)
    write_pdf(Path(rcfg.paths.input_folder) / "PO_rev1.pdf", 444)
    run_sync()
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.pending
    assert "2 PO documents" in (ps.reconcile_reason or "")


def test_revised_po_after_merge_mints_a_fresh_set_and_never_touches_the_packet(rcfg, monkeypatch):
    responses = _good()
    responses[444.0] = extraction("PO", PO_NO, None, [line("1", "12", "BOLT")])
    HttpVLM(monkeypatch, responses)
    _drop(rcfg)
    run_sync()
    (first,) = sets(rcfg)
    assert first.status == POSetStatus.merged
    packet = Path(first.merged_output_path)
    before = packet.read_bytes()
    write_pdf(Path(rcfg.paths.input_folder) / "PO_rev1.pdf", 444)
    run_sync()
    a, b = sets(rcfg)
    assert a.status == POSetStatus.merged and b.status == POSetStatus.pending
    assert b.po_no_normalized == a.po_no_normalized
    assert packet.read_bytes() == before


# ======================================================================= split gate


def test_failed_split_gate_quarantines_parent_and_records_the_split_reason(rcfg, monkeypatch):
    """With split_combined ON, a page_count the PDF does not have fails the gate."""
    rcfg.extraction.split_combined = True
    HttpVLM(
        monkeypatch,
        {
            444.0: extraction(
                "COMBINED",
                None,
                PO_NO,
                [],
                page_count=9,
                components=[{"document_type": "PO", "page_start": 1, "page_end": 9}],
            )
        },
    )
    write_pdf(Path(rcfg.paths.input_folder) / "combined.pdf", 444, pages=3)
    run_sync()
    d = doc_by_name(rcfg, "combined.pdf")
    assert d.extraction_status == ExtractionStatus.failed and d.po_set_id is None
    assert not list(Path(rcfg.paths.input_folder).glob("*_p*.pdf")), "no child was cut"
    report = (
        Path(rcfg.paths.quarantine_folder)
        / "_documents"
        / Path(d.stored_path).stem
        / "QUARANTINE.txt"
    ).read_text(encoding="utf-8")
    assert "Attempts" in report


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DEFECT R-13 (low): the split-gate reason ('split_failed:page_count_mismatch') "
    "written by _handle_combined is overwritten in the same sync by "
    "_quarantine_broken_document's generic 'attempt cap reached' text (same "
    "QUARANTINE.txt). The operator loses the actual cause; the report also claims 3 "
    "attempts although the VLM was called once.",
)
def test_failed_split_gate_reason_survives_in_the_report(rcfg, monkeypatch):
    rcfg.extraction.split_combined = True
    HttpVLM(
        monkeypatch,
        {
            444.0: extraction(
                "COMBINED",
                None,
                PO_NO,
                [],
                page_count=9,
                components=[{"document_type": "PO", "page_start": 1, "page_end": 9}],
            )
        },
    )
    write_pdf(Path(rcfg.paths.input_folder) / "combined.pdf", 444, pages=3)
    run_sync()
    d = doc_by_name(rcfg, "combined.pdf")
    report = (
        Path(rcfg.paths.quarantine_folder)
        / "_documents"
        / Path(d.stored_path).stem
        / "QUARANTINE.txt"
    ).read_text(encoding="utf-8")
    assert "page_count_mismatch" in report, report


def _merge_then_resend_identical(cfg, monkeypatch):
    HttpVLM(monkeypatch, _good())
    _drop(cfg)
    run_sync()
    (first,) = sets(cfg)
    assert first.status == POSetStatus.merged  # precondition
    _drop(cfg)  # the vendor (or a script) re-sends the identical files
    res = run_sync()
    return res, sets(cfg)


def test_resent_identical_files_after_merge_observed_state(rcfg, monkeypatch):
    """R-16 fixed: no error, no second EMPTY set, the packet is untouched and the
    three re-sent byte-identical files are cleared from input (they are duplicates of
    documents already in the merged packet)."""
    res, all_sets = _merge_then_resend_identical(rcfg, monkeypatch)
    assert res["errors"] == 0
    assert [(s.status, len(s.documents)) for s in all_sets] == [(POSetStatus.merged, 3)]
    assert sorted(p.name for p in Path(rcfg.paths.input_folder).iterdir()) == []


def test_resent_identical_po_after_merge_does_not_mint_a_phantom_set(rcfg, monkeypatch):
    _res, all_sets = _merge_then_resend_identical(rcfg, monkeypatch)
    assert len(all_sets) == 1, [(s.id, s.status, len(s.documents)) for s in all_sets]


def test_a_failing_attach_sweep_does_not_stop_reconciliation(rcfg, monkeypatch):
    import app.services.grouping as grouping

    HttpVLM(monkeypatch, _good())
    _drop(rcfg)

    def boom(cfg):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(grouping, "resolve_unattached_documents", boom)
    run_sync()
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.merged


def test_file_over_the_size_cap_is_rejected_and_listed_as_failed(rcfg, monkeypatch):
    """'Huge file': over limits.MAX_UPLOAD_BYTES it is moved to quarantine/_documents
    with a reason file and a `failed` Document row (R-03: visible to the operator)."""
    from app.core import limits
    from app.services.ingestion import ingest_file

    p = write_pdf(Path(rcfg.paths.input_folder) / "huge.pdf", 500, pages=50)
    monkeypatch.setattr(limits, "MAX_UPLOAD_BYTES", 1024)
    with pytest.raises(ValueError, match="upload cap"):
        ingest_file(p, rcfg)
    assert not p.exists()
    qdir = Path(rcfg.paths.quarantine_folder) / "_documents"
    assert any(f.name.endswith(".reason.txt") for f in qdir.iterdir())
    (row,) = all_docs(rcfg)
    assert row.extraction_status == ExtractionStatus.failed
    assert row.po_set_id is None and "upload cap" in (row.ingest_reject_reason or "")


def test_unused_imports_guard():
    # keep linters quiet about helpers imported for symmetry
    assert os and time and POSet
