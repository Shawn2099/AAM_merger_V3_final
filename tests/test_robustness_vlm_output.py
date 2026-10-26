"""Robustness: what the VLM sends back (2026-10-24 adversarial audit).

Every test runs the real `_call_vlm` -> real `openai` client -> real `instructor`
parser; only the HTTP response is invented (see test_robustness_harness.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_robustness_harness import (
    HttpVLM,
    doc_by_name,
    extraction,
    line,
    make_cfg,
    run_sync,
    sets,
    write_pdf,
)

from app.models import ExtractionStatus, POSetStatus


@pytest.fixture()
def rcfg(tmp_path, monkeypatch):
    return make_cfg(tmp_path, monkeypatch)


PO_NO = "4500012345"
W_PO, W_DN, W_SI = 111.0, 222.0, 333.0


def _good_set(po_lines=None, dn_lines=None, si_lines=None):
    po_lines = po_lines or [line("1", "10"), line("2", "5")]
    dn_lines = dn_lines or [line("1", "10"), line("2", "5")]
    si_lines = si_lines or [line("1", "10"), line("2", "5")]
    return {
        W_PO: extraction("PO", PO_NO, None, po_lines),
        W_DN: extraction("DN", "DN-1", PO_NO, dn_lines),
        W_SI: extraction("SI", "INV-1", PO_NO, si_lines),
    }


def _drop_set(cfg):
    inp = Path(cfg.paths.input_folder)
    write_pdf(inp / "PO.pdf", W_PO)
    write_pdf(inp / "DN.pdf", W_DN)
    write_pdf(inp / "SI.pdf", W_SI)


def test_harness_smoke_best_case_merges_through_real_http_stack(rcfg, monkeypatch):
    vlm = HttpVLM(monkeypatch, _good_set())
    _drop_set(rcfg)
    res = run_sync()
    assert res["errors"] == 0, res
    (ps,) = sets(rcfg)
    assert ps.status == POSetStatus.merged, ps.reconcile_reason
    assert vlm.calls == {W_PO: 1, W_DN: 1, W_SI: 1}


# ======================================================================= single-document reads
# extract_document is called directly (no Prefect) for speed; the HTTP stack is real.


def _extract(cfg, monkeypatch, reply, width=W_DN, name="doc.pdf"):
    from app.services.extraction import extract_document
    from app.services.ingestion import ingest_file

    vlm = HttpVLM(monkeypatch, {width: reply})
    p = write_pdf(Path(cfg.paths.input_folder) / name, width)
    doc = ingest_file(p, cfg)
    raised = None
    try:
        extract_document(doc.id, cfg)
    except Exception as e:
        raised = e
    return doc_by_name(cfg, name), raised, vlm


@pytest.mark.parametrize(
    "args",
    [
        '{"document_type":"DN","po_reference":"1","line_items":null}',
        '{"document_type":"DN","po_reference":"1","po_reference_ambiguous":null,"line_items":[]}',
        '{"document_type":"DN","po_reference":"1","components":null,"line_items":[]}',
        '{"document_type":"DN","po_reference":"1","page_count":null,"line_items":[]}',
        '{"document_type":"po","document_number":"1","line_items":[]}',
    ],
    ids=["line_items_null", "ambiguous_null", "components_null", "page_count_null", "lowercase"],
)
def test_explicit_nulls_or_case_drift_fail_the_whole_document(rcfg, monkeypatch, args):
    """Observed: an explicit JSON null in a non-optional field (the prompt says
    'omit nulls'; models do not always obey) or a lower-case enum fails schema
    validation; after 3 attempts the document is failed and quarantined. Safe
    (nothing wrong is merged) but a readable document is lost to the operator
    queue - a recall cost worth a lenient before-validator."""
    doc, raised, _ = _extract(rcfg, monkeypatch, ("args", args))
    assert raised is not None
    assert doc.extraction_status == ExtractionStatus.failed


def test_unknown_extra_fields_are_ignored(rcfg, monkeypatch):
    reply = extraction("DN", "DN-1", PO_NO, [line("1", "10")], confidence=0.3, notes="blurry")
    reply["line_items"][0]["uom"] = "EA"
    doc, raised, _ = _extract(rcfg, monkeypatch, reply)
    assert raised is None and doc.extraction_status == ExtractionStatus.valid


@pytest.mark.parametrize(
    ("qty_json", "scaled"),
    [
        ("10", 10_000),
        ("12.5", 12_500),
        ('"1,200.00"', 1_200_000),
        ("1e20", 0),
        ('"NaN"', 0),
        ('"Infinity"', 0),
        ('"-5"', 0),
        ('"0.001"', 0),
        ('"1 200"', 0),
        ('"1.234,56"', 0),
        ('"\\u00bd"', 0),
        ('"10 EA"', 0),
        ('"\\u0661\\u0660"', 10_000),
    ],
)
def test_quantity_shapes_never_crash_and_unreadable_ones_store_zero(
    rcfg, monkeypatch, qty_json, scaled
):
    """Any quantity the strict parser refuses is stored as 0 (never dropped), which
    quarantines the set as non_positive_quantity. Arabic-Indic digits are
    accepted by babel and parse as 10."""
    args = (
        '{"document_type":"DN","document_number":"D","po_reference":"1","line_items":'
        f'[{{"line_item_no":"1","description":"X","quantity":{qty_json}}}]}}'
    )
    doc, raised, _ = _extract(rcfg, monkeypatch, ("args", args))
    assert raised is None, raised
    assert [li.quantity for li in doc.line_items] == [scaled]


def test_line_rows_are_never_dropped_even_when_everything_is_null(rcfg, monkeypatch):
    reply = extraction(
        "DN",
        "DN-1",
        PO_NO,
        [{"line_item_no": None, "description": None, "quantity": None, "unit_price": None}] * 3,
    )
    doc, raised, _ = _extract(rcfg, monkeypatch, reply)
    assert raised is None
    assert len(doc.line_items) == 3
    assert all(li.quantity == 0 and li.description == "" for li in doc.line_items)


def test_huge_description_and_thousands_of_rows_are_stored(rcfg, monkeypatch):
    rows = [line(str(i), "1", "D" * 50) for i in range(1, 2001)]
    rows[0]["description"] = "Ü" * 100_000
    doc, raised, _ = _extract(rcfg, monkeypatch, extraction("DN", "DN-1", PO_NO, rows))
    assert raised is None
    assert len(doc.line_items) == 2000


def test_a_reread_that_now_reports_ambiguity_detaches_the_document(rcfg, monkeypatch):
    from sqlalchemy import update
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import Document
    from app.services.extraction import extract_document

    responses = _good_set()
    HttpVLM(monkeypatch, responses)
    import app.services.merge as merge_mod

    monkeypatch.setattr(merge_mod, "merge_po_set", lambda *a, **k: None)
    _drop_set(rcfg)
    run_sync()
    dn = doc_by_name(rcfg, "DN.pdf")
    assert dn.po_set_id is not None
    responses[W_DN] = extraction(
        "DN", "DN-1", PO_NO, [line("1", "10"), line("2", "5")], po_reference_ambiguous=True
    )
    with Session(get_engine(rcfg)) as s:
        s.execute(
            update(Document)
            .where(Document.id == dn.id)
            .values(extraction_status=ExtractionStatus.pending)
        )
        s.commit()
    extract_document(dn.id, rcfg)
    assert doc_by_name(rcfg, "DN.pdf").po_set_id is None


# ======================================================================= set-level reads (one sync)


def test_line_number_and_table_quirks_through_one_sync(rcfg, monkeypatch):
    """Four sets in one run:
    A  PO repeats line '1' (docket counter misread as a line no) -> rows are SUMMED
       on the PO side too, by design (CORE_LOGIC §3). Pinned as an accepted risk:
       'BOLT 5 + WASHER 5' on the PO merges against a DN of 'BOLT 10'.
    B  a repeated table header returned as a row on the DN -> qty 'Qty'
       unparseable -> 0 -> quarantined non_positive_quantity.
    C  a DN row numbered '3' that the PO does not carry -> orphan -> quarantined.
    D  ERP step-numbering PO 10/20 vs DN 1/2 -> merged by rule x10 (line-number fallback).
    """

    def po(n, lines):
        return extraction("PO", n, None, lines)

    def dn(n, lines):
        return extraction("DN", f"DN-{n}", n, lines)

    def si(n, lines):
        return extraction("SI", f"INV-{n}", n, lines)

    std = [line("1", "10", "BOLT"), line("2", "5", "NUT")]
    responses = {
        101.0: po("8100001", [line("1", "5", "BOLT M10"), line("1", "5", "WASHER M10")]),
        104.0: dn("8100001", [line("1", "10", "BOLT M10")]),
        107.0: si("8100001", [line("1", "10", "BOLT M10")]),
        110.0: po("8100002", std),
        113.0: dn("8100002", [*std, line("Sl No", "Qty", "Description")]),
        116.0: si("8100002", std),
        119.0: po("8100003", std),
        122.0: dn("8100003", [*std, line("3", "1", "FREE SAMPLE")]),
        125.0: si("8100003", std),
        128.0: po("8100004", [line("10", "10", "BOLT"), line("20", "5", "NUT")]),
        131.0: dn("8100004", std),
        134.0: si("8100004", std),
    }
    HttpVLM(monkeypatch, responses)
    inp = Path(rcfg.paths.input_folder)
    for w in responses:
        write_pdf(inp / f"w{int(w)}.pdf", w)
    run_sync()
    by_po = {s.po_no_normalized: s for s in sets(rcfg)}
    assert by_po["8100001"].status == POSetStatus.merged  # accepted-by-design risk
    assert by_po["8100002"].status == POSetStatus.quarantined
    assert "zero, negative, or unreadable" in by_po["8100002"].reconcile_reason
    assert by_po["8100003"].status == POSetStatus.quarantined
    assert "could not be matched" in by_po["8100003"].reconcile_reason
    assert by_po["8100004"].status == POSetStatus.merged  # rule x10
