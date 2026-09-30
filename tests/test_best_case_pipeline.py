"""Best-case end-to-end test of the core engine.

The happy path, run for real. Everything is genuine except the network call to
the VLM: real PDFs on disk, real SHA-256 dedup, real `stored_path` writes, the
real `_call_vlm` response mapping, real grouping, real reconciliation, a real
`pypdf` merge, and real input-folder clearing.

That boundary is deliberate. Every earlier extraction test mocked `_call_vlm`
outright, which is how `_call_vlm` came to read four attributes the schema never
declared and crash on every real response for months without a single failure.
Here the real `_call_vlm` runs; only `instructor.from_openai` is replaced, so
the mapping code is executed on every run of this file.

The VLM is faked by dispatching on each PDF's first-page width, so the fake
needs no knowledge of hashes, filenames, or call order.
"""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import Base, Document, POSet, POSetStatus
from app.services import extraction as ext

PO_NO = "4500043712"
INVOICE_NO = "SIV-ARS-26-4005"

# One page width per document type, used by the fake VLM to tell them apart.
W_PO, W_DN, W_SI = 111.0, 222.0, 333.0

# The business case: one PO line delivered across two DNs and invoiced across
# two SIs, every figure reconciling exactly. This is the worked example from
# SPEC 7.6 / business doc section 10.
PO_LINES = [
    ("1", "NUT, HEX 9/16 IN-12 UNC GRADE B YELLOW ZINC PLATED", "50.00", "350.00"),
    ("2", "WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED CS", "100.00", "1620.00"),
    ("3", "BOLT, HEX 3/4 X 2-1/2 GRADE 8", "200.00", "48.50"),
]
DN_LINES = [
    ("1", "NUT, HEX 9/16 IN-12 UNC GRADE B Line Item - 1", "20.00", "350.00"),
    ("1", "NUT, HEX 9/16 IN-12 UNC GRADE B Line Item - 1", "30.00", "350.00"),
    ("2", "WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED Line Item - 2", "100.00", "1620.00"),
    ("3", "BOLT, HEX 3/4 X 2-1/2 GRADE 8 Line Item - 3", "200.00", "48.50"),
]
SI_LINES = [
    ("1", "NUT, HEX 9/16 IN-12 UNC GRADE B Line Item - 1", "25.00", "350.00"),
    ("1", "NUT, HEX 9/16 IN-12 UNC GRADE B Line Item - 1", "25.00", "350.00"),
    ("2", "WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED Line Item - 2", "100.00", "1620.00"),
    ("3", "BOLT, HEX 3/4 X 2-1/2 GRADE 8 Line Item - 3", "200.00", "48.50"),
]


def _extraction(doc_type, number, po_ref, lines):
    return ext._VLMPageExtraction(
        document_type=doc_type,
        has_po_section=(doc_type in ("PO", "COMBINED")),
        has_dn_section=(doc_type in ("DN", "COMBINED")),
        has_si_section=(doc_type in ("SI", "COMBINED")),
        document_number=number,
        po_reference=po_ref,
        po_reference_ambiguous=False,
        vendor_name="ACME TRADING EST.",
        line_items=[
            ext._VLMLineItem(
                line_item_no=no, description=desc, quantity=qty, unit_price=price, dn_no=None
            )
            for no, desc, qty, price in lines
        ],
    )


RESPONSES = {
    W_PO: _extraction("PO", PO_NO, None, PO_LINES),
    W_DN: _extraction("DN", "GDN-ARS-26-4619", PO_NO, DN_LINES),
    W_SI: _extraction("SI", INVOICE_NO, PO_NO, SI_LINES),
}


def _write_pdf(path: Path, width: float, pages: int = 1) -> Path:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=200)
    path.parent.mkdir(parents=True, exist_ok=True)
    w.write(str(path))
    return path


@pytest.fixture()
def fake_vlm(monkeypatch):
    """Replace only `instructor.from_openai`; the real `_call_vlm` still runs."""

    def create(self, **kwargs):
        payload = None
        for part in kwargs["messages"][1]["content"]:
            if part.get("type") == "file":
                payload = part["file"]["file_data"].split(",", 1)[1]
        assert payload, "the request must carry the PDF"
        reader = PdfReader(io.BytesIO(base64.b64decode(payload)))
        width = float(reader.pages[0].mediabox.width)
        assert width in RESPONSES, f"unexpected PDF width {width}"
        return RESPONSES[width]

    class Completions:
        pass

    Completions.create = create

    class Chat:
        completions = Completions()

    class Client:
        chat = Chat()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(ext.instructor, "from_openai", lambda *a, **kw: Client())
    return Client


@pytest.fixture()
def pipeline(tmp_path, monkeypatch):
    """A config pointed entirely at tmp_path, with every route/flow bound to it."""
    cfg = load_config("config.example.yaml")
    base = tmp_path.as_posix()
    cfg.paths.input_folder = f"{base}/input"
    cfg.paths.output_folder = f"{base}/output"
    cfg.paths.quarantine_folder = f"{base}/quarantine"
    cfg.paths.stored_documents_folder = f"{base}/stored"
    cfg.paths.database_path = f"{base}/aam.db"
    cfg.paths.log_folder = f"{base}/logs"
    cfg.ingestion.stability_poll_interval_seconds = 0  # no sleeping in tests
    cfg.ingestion.stability_poll_count = 1
    for sub in ("input", "output", "quarantine", "stored", "unclassified", "logs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(cfg))

    import app.api.routes.dashboard as dash
    import app.api.routes.po_sets as po_routes
    import app.api.routes.sync as sync_route
    import app.flows.sync as flow_sync
    import app.services.sync_lock as sync_lock

    for mod in (dash, po_routes, sync_route, flow_sync, sync_lock):
        monkeypatch.setattr(mod, "load_config", lambda *a, **kw: cfg)
    return cfg


def test_best_case_reaches_a_merged_packet(pipeline, fake_vlm, monkeypatch):
    """Input PDFs in, one reconciled merged PDF out, input folder emptied."""
    from app.flows.sync import sync_flow

    cfg = pipeline
    inp = Path(cfg.paths.input_folder)
    _write_pdf(inp / "PO.pdf", W_PO)
    _write_pdf(inp / "DN.pdf", W_DN)
    _write_pdf(inp / "SI.pdf", W_SI)
    assert len(list(inp.iterdir())) == 3

    result = sync_flow()

    assert result["errors"] == 0, f"pipeline reported errors: {result}"
    assert result["processed"] == 3

    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.query(POSet).one()
        assert ps.po_no_normalized == PO_NO
        assert ps.status == POSetStatus.merged, f"not merged: {ps.reconcile_reason}"
        assert ps.merged_at is not None
        assert ps.merged_output_path
        assert ps.reconcile_reason and "merged" in ps.reconcile_reason.lower()

        docs = s.query(Document).filter_by(po_set_id=ps.id).all()
        assert sorted(d.doc_type.value for d in docs) == ["DN", "PO", "SI"]
        assert all(d.extraction_status.value == "valid" for d in docs)
        assert all(d.stored_path and Path(d.stored_path).exists() for d in docs)

        # PO carries 3 lines; DN and SI carry 4 each (line 1 split across two)
        by_type = {d.doc_type.value: len(d.line_items) for d in docs}
        assert by_type == {"PO": 3, "DN": 4, "SI": 4}, by_type

        # the SI number and PO number are both captured
        si = next(d for d in docs if d.doc_type.value == "SI")
        assert si.si_no == INVOICE_NO and si.invoice_no == INVOICE_NO
        po = next(d for d in docs if d.doc_type.value == "PO")
        assert po.po_no_normalized == PO_NO

        # quantities stored scaled x1000, exactly as printed
        po_lines = {li.line_item_no: li for li in po.line_items}
        assert po_lines["1"].quantity == 50_000
        assert po_lines["2"].quantity == 100_000
        assert po_lines["3"].quantity == 200_000
        assert po_lines["1"].unit_price == 350_000

    # the merged packet exists, is named from the invoice, and has every page
    out = list(Path(cfg.paths.output_folder).iterdir())
    assert len(out) == 1, out
    assert out[0].name == f"{INVOICE_NO}.pdf", out[0].name
    reader = PdfReader(str(out[0]))
    assert len(reader.pages) == 3, "one page each from PO, DN and SI"
    widths = [float(p.mediabox.width) for p in reader.pages]
    # merge order is SI -> DN -> PO (config merge.legal_order)
    assert widths == [W_SI, W_DN, W_PO], f"wrong packet order: {widths}"

    # FR-4.8: input cleared only after a real merged output exists
    assert list(inp.iterdir()) == [], "input folder should be emptied after merge"
    # ...and the stored copies survive as the permanent audit trail
    assert len(list(Path(cfg.paths.stored_documents_folder).glob("*.pdf"))) == 3


def test_split_delivery_sums_to_the_po_quantity(pipeline, fake_vlm):
    """The core arithmetic, proven through the real sanitizer and matcher.

    Line 1 is delivered 20 + 30 across two DN rows and invoiced 25 + 25 across
    two SI rows. Every pool must sum to the PO's 50 for the set to merge.
    Quantities go through the real `parse_quantity_scaled`, exactly as the
    pipeline does, so the x1000 scaling is not hand-written here.
    """
    from app.services.matching import group_by_line_no
    from app.services.sanitizer import parse_quantity_scaled

    def rows(lines):
        return [
            {
                "line_item_no": no,
                "description": desc,
                "quantity": parse_quantity_scaled(qty),
            }
            for no, desc, qty, _price in lines
        ]

    po, dn, si = rows(PO_LINES), rows(DN_LINES), rows(SI_LINES)

    po_totals, dn_totals, dn_orphans, fail = group_by_line_no(po, dn, 85)
    assert fail is None and dn_orphans == []
    _, si_totals, si_orphans, _ = group_by_line_no(po, si, 85)
    assert si_orphans == []

    assert po_totals == {"1": 50_000, "2": 100_000, "3": 200_000}
    assert dn_totals == {"1": 50_000, "2": 100_000, "3": 200_000}, dn_totals
    assert si_totals == {"1": 50_000, "2": 100_000, "3": 200_000}, si_totals
    assert dn_totals == po_totals and si_totals == po_totals


def test_input_pdfs_are_discovered_exactly_once(tmp_path):
    """Guards the Windows double-glob that sent every document to the VLM twice.

    `glob("*.pdf") + glob("*.PDF")` returns each file twice on Windows because
    pathlib's glob is case-insensitive there. sync_flow extracts after
    ingesting, so the duplicate cost a second API call per document per sync.
    Only Windows reproduces it, and the production host is Windows.
    """
    from app.services.ingestion import find_input_pdfs

    folder = tmp_path / "in"
    folder.mkdir()
    for name in ("PO.pdf", "DN.pdf", "SI.pdf"):
        (folder / name).write_bytes(b"%PDF-1.4\n")
    (folder / "upper.PDF").write_bytes(b"%PDF-1.4\n")
    (folder / "notes.txt").write_bytes(b"ignore me")
    (folder / "sub").mkdir()

    found = find_input_pdfs(folder)

    assert sorted(p.name for p in found) == ["DN.pdf", "PO.pdf", "SI.pdf", "upper.PDF"]
    assert len(found) == len(set(found)), "a file was discovered twice"
    assert found == sorted(found), "order must be stable"
    assert "notes.txt" not in [p.name for p in found]
    assert "sub" not in [p.name for p in found]

    # the naive form this replaced, shown to be wrong on Windows
    naive = list(folder.glob("*.pdf")) + list(folder.glob("*.PDF"))
    if os.name == "nt":
        assert len(naive) == 8, "expected the naive form to double-count on Windows"
        assert len(found) == 4


def test_pipeline_is_idempotent_on_a_second_run(pipeline, fake_vlm):
    """Re-running must not double anything, and must not re-merge or re-ingest."""
    from app.flows.sync import sync_flow

    cfg = pipeline
    inp = Path(cfg.paths.input_folder)
    _write_pdf(inp / "PO.pdf", W_PO)
    _write_pdf(inp / "DN.pdf", W_DN)
    _write_pdf(inp / "SI.pdf", W_SI)

    first = sync_flow()
    assert first["errors"] == 0

    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.query(POSet).one()
        first_path, first_merged_at = ps.merged_output_path, ps.merged_at
        doc_count = s.query(Document).count()

    second = sync_flow()
    assert second["errors"] == 0, second

    with Session(eng) as s:
        assert s.query(POSet).count() == 1, "must not mint a second set for a merged PO"
        assert s.query(Document).count() == doc_count, "dedup must hold on re-run"
        ps = s.query(POSet).one()
        assert ps.merged_output_path == first_path
        assert ps.merged_at == first_merged_at, "merged_at is immutable"

    assert len(list(Path(cfg.paths.output_folder).iterdir())) == 1, "no second packet"


def test_a_short_delivery_stops_the_merge(pipeline, fake_vlm, monkeypatch):
    """Best case must actually be conditional: one short line blocks the merge.

    Proves the engine is not merging everything it is handed.
    """
    from app.flows.sync import sync_flow

    cfg = pipeline
    inp = Path(cfg.paths.input_folder)
    _write_pdf(inp / "PO.pdf", W_PO)
    _write_pdf(inp / "DN.pdf", W_DN)
    _write_pdf(inp / "SI.pdf", W_SI)

    # one DN row short by 1
    monkeypatch.setitem(
        ext._VLMLineItem.model_fields, "quantity", ext._VLMLineItem.model_fields["quantity"]
    )
    short = _extraction(
        "DN",
        "GDN-ARS-26-4619",
        PO_NO,
        [
            ("1", "NUT, HEX 9/16 IN-12 UNC GRADE B Line Item - 1", "20.00", "350.00"),
            ("2", "WASHER, FLAT SAE 1/4 IN YELLOW ZINC PLATED Line Item - 2", "100.00", "1620.00"),
            ("3", "BOLT, HEX 3/4 X 2-1/2 GRADE 8 Line Item - 3", "200.00", "48.50"),
        ],
    )
    monkeypatch.setitem(RESPONSES, W_DN, short)

    result = sync_flow()
    assert result["errors"] == 0

    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.query(POSet).one()
        assert ps.status == POSetStatus.mismatched, f"short delivery must not merge ({ps.status})"
        assert ps.merged_output_path is None
        # the reason must say a disagreement, not tell the reviewer to wait
        reason = (ps.reconcile_reason or "").lower()
        assert "awaiting" not in reason, f"mismatched set reads as a wait: {ps.reconcile_reason!r}"
        assert "quantit" in reason, f"unhelpful reason: {ps.reconcile_reason!r}"
        assert "20 of 50" in reason, f"the numbers must be shown: {ps.reconcile_reason!r}"

    assert list(Path(cfg.paths.output_folder).iterdir()) == [], "no packet may be written"
    assert len(list(inp.iterdir())) == 3, "input must NOT be cleared when nothing merged"
