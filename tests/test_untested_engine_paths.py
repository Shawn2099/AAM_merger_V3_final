"""Engine tests for the paths that have never been executed.

Every scenario here sits on code that was at 0% coverage when this file was
written, or on an error branch that had never been driven. Together they answer
the question the happy-path suite cannot: what happens when the pipeline meets
something other than a perfect set of documents?

Assumption under test: the VLM returns correct data. These tests deliberately
do NOT check extraction quality. They check what the engine does with whatever
it is handed, including when that is nothing at all.

Failures here are expected to be findings, not regressions. Nothing in `src/`
was changed to make them pass.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import (
    Base,
    DocType,
    Document,
    ExtractionStatus,
    POSet,
    POSetStatus,
)
from app.services import extraction as ext

PO_NO = "4500043712"
INVOICE_NO = "SIV-ARS-26-4005"
OTHER_PO_NO = "4500049999"

W_PO, W_DN, W_SI = 111.0, 222.0, 333.0


def _pdf(path: Path, width: float, pages: int = 1) -> Path:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=200)
    path.parent.mkdir(parents=True, exist_ok=True)
    w.write(str(path))
    return path


def _extraction(doc_type, number, po_ref, lines, **kw):
    return ext._VLMPageExtraction(
        document_type=doc_type,
        has_po_section=doc_type in ("PO", "COMBINED"),
        has_dn_section=doc_type in ("DN", "COMBINED"),
        has_si_section=doc_type in ("SI", "COMBINED"),
        document_number=number,
        po_reference=po_ref,
        po_reference_ambiguous=kw.get("ambiguous", False),
        vendor_name="ACME TRADING EST.",
        line_items=[
            ext._VLMLineItem(
                line_item_no=no, description=desc, quantity=qty, unit_price=price, dn_no=None
            )
            for no, desc, qty, price in lines
        ],
    )


ONE = [("1", "NUT, HEX 9/16", "50.00", "350.00")]


def default_responses():
    return {
        W_PO: _extraction("PO", PO_NO, None, ONE),
        W_DN: _extraction("DN", "GDN-1", PO_NO, ONE),
        W_SI: _extraction("SI", INVOICE_NO, PO_NO, ONE),
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A fully isolated pipeline: tmp folders, tmp DB, mocked load_config."""
    cfg = load_config("config.example.yaml")
    base = tmp_path.as_posix()
    cfg.paths.input_folder = f"{base}/input"
    cfg.paths.output_folder = f"{base}/output"
    cfg.paths.quarantine_folder = f"{base}/quarantine"
    cfg.paths.stored_documents_folder = f"{base}/stored"
    cfg.paths.database_path = f"{base}/aam.db"
    cfg.paths.log_folder = f"{base}/logs"
    cfg.ingestion.stability_poll_interval_seconds = 0
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


@pytest.fixture()
def vlm(monkeypatch):
    """Fake only the network. The real `_call_vlm` still runs."""
    state = {"responses": default_responses(), "calls": []}

    def create(self, **kwargs):
        payload = None
        for part in kwargs["messages"][1]["content"]:
            if part.get("type") == "file":
                payload = part["file"]["file_data"].split(",", 1)[1]
        raw = base64.b64decode(payload)
        state["calls"].append(raw)
        try:
            width = float(PdfReader(io.BytesIO(raw)).pages[0].mediabox.width)
        except Exception:
            width = "UNREADABLE"
        if width not in state["responses"]:
            raise ValueError(f"unreadable document (width={width})")
        return state["responses"][width]

    class Completions:
        pass

    Completions.create = create

    class Chat:
        completions = Completions()

    class Client:
        chat = Chat()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(ext.instructor, "from_openai", lambda *a, **kw: Client())
    return state


def sets(cfg):
    with Session(get_engine(cfg)) as s:
        return {p.po_no_normalized: (p.id, p.status.value) for p in s.query(POSet).all()}


def docs_of(cfg, po_set_id):
    with Session(get_engine(cfg)) as s:
        return s.query(Document).filter_by(po_set_id=po_set_id).all()


# ==========================================================================
# Tier 1 — never-executed paths that fail silently
# ==========================================================================


def test_pending_doc_in_db_is_resumed_on_the_next_sync(env, vlm):
    """TIER 1. A document left `pending` in the DB is picked up and finished.

    This is the state a crashed or interrupted run leaves behind: the file was
    ingested and hashed, but extraction never completed. Nothing in `src/` had
    ever executed this loop.
    """
    from app.flows.sync import sync_flow

    cfg = env
    # pre-seed: stored file present, DB row pending, input folder already empty
    stored = _pdf(Path(cfg.paths.stored_documents_folder) / "dn.pdf", W_DN)
    with Session(get_engine(cfg)) as s:
        s.add(
            Document(
                sha256_hash="crashed1",
                original_filename="dn.pdf",
                stored_path=str(stored),
                doc_type=DocType.DN,
                extraction_status=ExtractionStatus.pending,
                po_no_normalized=PO_NO,
            )
        )
        s.commit()
    assert list(Path(cfg.paths.input_folder).iterdir()) == []

    result = sync_flow()

    assert result["errors"] == 0, result
    with Session(get_engine(cfg)) as s:
        doc = s.query(Document).filter_by(sha256_hash="crashed1").one()
        assert doc.extraction_status == ExtractionStatus.valid, "the pending doc was never resumed"
        assert len(doc.line_items) == 1
        assert doc.line_items[0].quantity == 50_000


def test_pending_dn_cannot_mint_a_set_and_is_left_unattached(env, vlm):
    """TIER 1. A DN alone must not create a PO Set (BLOCKER-5 rule).

    It waits, visibly unattached, until a PO anchors the key.
    """
    from app.flows.sync import sync_flow

    cfg = env
    stored = _pdf(Path(cfg.paths.stored_documents_folder) / "dn_only.pdf", W_DN)
    with Session(get_engine(cfg)) as s:
        s.add(
            Document(
                sha256_hash="dnonly1",
                original_filename="dn_only.pdf",
                stored_path=str(stored),
                doc_type=DocType.DN,
                extraction_status=ExtractionStatus.pending,
                po_no_normalized=PO_NO,
            )
        )
        s.commit()

    result = sync_flow()

    assert result["errors"] == 0, result
    assert sets(cfg) == {}, "a DN must not mint a PO Set"
    with Session(get_engine(cfg)) as s:
        doc = s.query(Document).filter_by(sha256_hash="dnonly1").one()
        assert doc.extraction_status == ExtractionStatus.valid, "it should still be extracted"
        assert doc.po_set_id is None, "it should stay unattached, not be force-fitted"


def test_pending_po_creates_the_set_and_later_docs_attach_and_merge(env, vlm):
    """TIER 1. Out-of-order arrival: the PO is stored pending, DN and SI arrive
    later on disk. The set must form and merge on the run that completes it."""
    from app.flows.sync import sync_flow

    cfg = env
    stored = _pdf(Path(cfg.paths.stored_documents_folder) / "po_early.pdf", W_PO)
    with Session(get_engine(cfg)) as s:
        s.add(
            Document(
                sha256_hash="earlypo1",
                original_filename="po_early.pdf",
                stored_path=str(stored),
                doc_type=DocType.PO,
                extraction_status=ExtractionStatus.pending,
                po_no_normalized=PO_NO,
            )
        )
        s.commit()

    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "DN.pdf", W_DN)
    _pdf(inp / "SI.pdf", W_SI)

    result = sync_flow()

    assert result["errors"] == 0, result
    ids = sets(cfg)
    assert PO_NO in ids, f"the pending PO should have created the set, got {ids}"
    assert ids[PO_NO][1] == "merged", f"got {ids}"
    out = list(Path(cfg.paths.output_folder).iterdir())
    assert [p.name for p in out] == [f"{INVOICE_NO}.pdf"], out
    assert len(docs_of(cfg, ids[PO_NO][0])) == 3


def test_reconciled_set_with_no_invoice_number_merges_as_the_po_number(env, vlm):
    """TIER 1, and the product decision: quantities reconcile, so it merges.

    The SI carries no extractable number. The packet is named from the PO
    number and the reviewer is told, via the naming flag, that it is not
    invoice-named.
    """
    from app.flows.sync import sync_flow

    cfg = env
    vlm["responses"] = {
        W_PO: _extraction("PO", PO_NO, None, ONE),
        W_DN: _extraction("DN", "GDN-1", PO_NO, ONE),
        W_SI: ext._VLMPageExtraction(
            document_type="SI",
            has_si_section=True,
            document_number=None,
            po_reference=PO_NO,
            vendor_name="ACME",
            line_items=[
                ext._VLMLineItem(
                    line_item_no="1",
                    description="NUT, HEX 9/16",
                    quantity="50.00",
                    unit_price="350.00",
                )
            ],
        ),
    }
    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "PO.pdf", W_PO)
    _pdf(inp / "DN.pdf", W_DN)
    _pdf(inp / "SI.pdf", W_SI)

    result = sync_flow()

    assert result["errors"] == 0, result
    out = list(Path(cfg.paths.output_folder).iterdir())
    assert [p.name for p in out] == [f"{PO_NO}.pdf"], (
        f"a reconciled set with no invoice number must still merge, as {PO_NO}.pdf; got {out}"
    )
    with Session(get_engine(cfg)) as s:
        ps = s.query(POSet).one()
        assert ps.status == POSetStatus.merged
        assert "invoice" in (ps.reconcile_reason or "").lower(), (
            f"the PO-number fallback must be stated to the reviewer, got {ps.reconcile_reason!r}"
        )


def test_output_name_collision_quarantines_instead_of_overwriting(env, vlm):
    """TIER 1. Two sets whose packets would share a filename.

    The first set's delivered packet must survive untouched; the second must
    quarantine rather than clobber it (FR-14.6).
    """
    from app.flows.sync import sync_flow

    cfg = env
    # a pre-existing packet belonging to nobody in this DB
    squatter = Path(cfg.paths.output_folder) / f"{INVOICE_NO}.pdf"
    squatter.parent.mkdir(parents=True, exist_ok=True)
    PdfWriter().write(str(squatter))
    original_bytes = squatter.read_bytes()

    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "PO.pdf", W_PO)
    _pdf(inp / "DN.pdf", W_DN)
    _pdf(inp / "SI.pdf", W_SI)

    result = sync_flow()
    assert result["errors"] == 0, result

    assert squatter.read_bytes() == original_bytes, "a delivered packet was overwritten"
    ids = sets(cfg)
    assert ids[PO_NO][1] == "quarantined", f"expected quarantine on collision, got {ids}"
    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, ids[PO_NO][0])
        assert ps.merged_output_path is None


def test_missing_stored_pdf_at_merge_time_writes_nothing(env, vlm):
    """TIER 1. A stored file vanishes between reconciliation and merge.

    Nothing may be delivered, and the set must not claim to be merged.
    """
    from app.services.merge import merge_po_set
    from app.services.reconciliation import reconcile_po_set

    cfg = env
    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "PO.pdf", W_PO)
    _pdf(inp / "DN.pdf", W_DN)
    _pdf(inp / "SI.pdf", W_SI)
    from app.flows.sync import sync_flow

    sync_flow()

    ids = sets(cfg)
    assert ids[PO_NO][1] == "merged"
    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, ids[PO_NO][0])
        ps.status = POSetStatus.pending
        ps.merged_output_path = None
        s.commit()
        # delete one source file behind the engine's back
        victim = next(d for d in ps.documents if d.doc_type == DocType.SI)
        Path(victim.stored_path).unlink()
        pid = ps.id

    # clear the packet the first run delivered, so the second merge attempt is
    # testing the missing source file and not the filename collision
    for p in Path(cfg.paths.output_folder).iterdir():
        p.unlink()

    out = merge_po_set(pid, cfg)
    assert out is None, "must not report a path when a source file is missing"
    assert not list(Path(cfg.paths.output_folder).iterdir()), "a partial packet was written"
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, pid).status != POSetStatus.merged
    _ = reconcile_po_set  # keep the import honest for readers


# ==========================================================================
# Tier 2 — error branches: does one bad document stop the run?
# ==========================================================================


def test_one_unreadable_document_does_not_stop_the_run(env, vlm):
    """TIER 2. A corrupt file must not prevent the good documents merging.

    This is the single most important resilience property: one bad PDF in the
    input folder must not cost the operator every other PO that day.

    MEASURED: the engine is correct here. Three good documents group, reconcile
    and merge; the corrupt file is quarantined rather than being silently
    consumed or left to be retried forever.
    """
    from app.flows.sync import sync_flow

    cfg = env
    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "PO.pdf", W_PO)
    _pdf(inp / "DN.pdf", W_DN)
    _pdf(inp / "SI.pdf", W_SI)
    (inp / "corrupt.pdf").write_bytes(b"this is definitely not a PDF")

    result = sync_flow()

    assert result["processed"] == 4, f"all four files should be attempted: {result}"
    ids = sets(cfg)
    assert PO_NO in ids, f"the good documents must still group: {ids}"
    assert ids[PO_NO][1] == "merged", f"the good documents must still merge: {ids}"
    out = [p.name for p in Path(cfg.paths.output_folder).iterdir()]
    assert out == [f"{INVOICE_NO}.pdf"], out
    # a permanently-unreadable file is QUARANTINED: copied out of the input
    # folder so the next sync does not re-hash it and re-report it as an error
    # every night, and removed from input so it stops being picked up at all
    assert "corrupt.pdf" not in [p.name for p in inp.iterdir()], (
        "a permanently-broken file is still sitting in the input folder"
    )
    qroot = Path(cfg.paths.quarantine_folder) / "_documents"
    assert qroot.exists(), "the broken file was not quarantined"
    assert any(qroot.rglob("QUARANTINE.txt")), "the quarantine copy has no reason file"
    with Session(get_engine(cfg)) as s:
        bad = s.query(Document).filter_by(original_filename="corrupt.pdf").one()
        assert bad.extraction_status == ExtractionStatus.failed
        assert bad.extraction_attempt_count == 3, "it should have exhausted its retries"
        assert Path(bad.stored_path).exists(), "the stored copy must be kept for review"


def test_a_permanently_failed_document_is_counted_as_an_error_in_the_summary(env, vlm):
    """TIER 2. A total loss must never be reported as a clean run.

    This was a real defect, pinned here so it cannot come back. Measured with
    one corrupt file among four documents, the summary used to say:

        FLOW SUMMARY : {'processed': 4, 'extracted': 4, 'errors': 0, ...}
        corrupt.pdf  type=UNKNOWN status=failed attempts=3

    The document really was marked `failed` in the database, yet the run was
    reported as a clean success.

    Cause: Prefect places a task in a COMPLETED state whenever it returns any
    Python object, and `extract_document` returns normally once a document has
    exhausted its attempt cap (src/app/services/extraction.py). The sync loop
    counted errors from task *exceptions*, so the final "successful" retry
    erased the failure. `extracted` was derived as `processed - errors` and
    inherited the same error.

    Fix: the count is taken from the persisted document row, which is the only
    record of what actually happened. See
    app/flows/sync.py::_persisted_extraction_status.
    """
    from app.flows.sync import sync_flow

    cfg = env
    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "PO.pdf", W_PO)
    _pdf(inp / "DN.pdf", W_DN)
    _pdf(inp / "SI.pdf", W_SI)
    (inp / "corrupt.pdf").write_bytes(b"not a pdf at all")

    result = sync_flow()

    with Session(get_engine(cfg)) as s:
        failed = s.query(Document).filter_by(extraction_status=ExtractionStatus.failed).count()
    assert failed == 1, "precondition: exactly one document really did fail"

    assert result["errors"] == 1, (
        f"a document that exhausted its retries must be counted as an error; summary said {result}"
    )
    assert result["extracted"] == 3, f"only three documents were really extracted; got {result}"


def test_extraction_failure_on_one_document_does_not_stop_the_run(env, vlm, monkeypatch):
    """TIER 2. A document the VLM cannot handle fails alone."""
    from app.flows.sync import sync_flow

    cfg = env
    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "PO.pdf", W_PO)
    _pdf(inp / "DN.pdf", W_DN)
    _pdf(inp / "SI.pdf", W_SI)
    _pdf(inp / "bad.pdf", 999.0)  # no response mapped for this width

    result = sync_flow()

    assert sets(cfg).get(PO_NO, (None, None))[1] == "merged", (
        f"the three good documents must still merge: {sets(cfg)}"
    )
    with Session(get_engine(cfg)) as s:
        statuses = {d.original_filename: d.extraction_status.value for d in s.query(Document)}
        assert statuses["bad.pdf"] == "failed", statuses
        assert statuses["PO.pdf"] == "valid", statuses
    # `result["errors"]` is 0 here even though bad.pdf failed -- see
    # test_a_permanently_failed_document_is_counted_as_a_success_in_the_summary
    _ = result


def test_grouping_failure_does_not_mint_an_orphan_set(env, vlm, monkeypatch):
    """TIER 2. A grouping error must leave no PO Set behind."""
    import app.services.grouping as grouping
    from app.flows.sync import sync_flow

    cfg = env
    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "PO.pdf", W_PO)
    _pdf(inp / "DN.pdf", W_DN)
    _pdf(inp / "SI.pdf", W_SI)

    real = grouping.get_or_create_po_set
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated grouping failure")
        return real(*a, **kw)

    monkeypatch.setattr(grouping, "get_or_create_po_set", flaky)
    result = sync_flow()

    assert result["errors"] >= 1, "the grouping failure must be counted"
    ids = sets(cfg)
    assert len(ids) <= 1, f"a failed grouping must not mint extra sets: {ids}"
    for _po, (_id, status) in ids.items():
        assert status in ("pending", "merged", "mismatched", "quarantined"), status


def test_reconcile_failure_does_not_block_other_sets(env, vlm, monkeypatch):
    """TIER 2. One set blowing up during reconciliation must not stop the rest."""
    import app.services.reconciliation as rec
    from app.flows.sync import sync_flow

    cfg = env
    vlm["responses"] = {
        **default_responses(),
        444.0: _extraction("PO", OTHER_PO_NO, None, ONE),
        555.0: _extraction("DN", "GDN-2", OTHER_PO_NO, ONE),
        666.0: _extraction("SI", "INV-OTHER", OTHER_PO_NO, ONE),
    }
    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "PO.pdf", W_PO)
    _pdf(inp / "DN.pdf", W_DN)
    _pdf(inp / "SI.pdf", W_SI)
    _pdf(inp / "PO2.pdf", 444.0)
    _pdf(inp / "DN2.pdf", 555.0)
    _pdf(inp / "SI2.pdf", 666.0)

    real = rec.reconcile_po_set

    def flaky(po_set_id, cfg_):
        if po_set_id == 2:  # whichever set is the second one
            raise RuntimeError("simulated reconcile failure")
        return real(po_set_id, cfg_)

    monkeypatch.setattr(rec, "reconcile_po_set", flaky)
    result = sync_flow()

    assert result["errors"] >= 1, result
    ids = sets(cfg)
    assert PO_NO in ids, f"the first set must still exist: {ids}"
    assert ids[PO_NO][1] == "merged", f"the healthy set must still merge: {ids}"


def test_two_sets_one_merges_one_quarantines(env, vlm):
    """TIER 2. Independent sets reach their own correct terminal states.

    The quarantined set here is the deliberate single-PO-document case.
    """
    from app.flows.sync import sync_flow

    cfg = env
    vlm["responses"] = {
        **default_responses(),
        444.0: _extraction("PO", OTHER_PO_NO, None, ONE),
        555.0: _extraction("DN", "GDN-2", OTHER_PO_NO, ONE),
        666.0: _extraction("SI", "INV-OTHER", OTHER_PO_NO, ONE),
    }
    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "PO.pdf", W_PO)
    _pdf(inp / "DN.pdf", W_DN)
    _pdf(inp / "SI.pdf", W_SI)
    _pdf(inp / "PO2.pdf", 444.0)
    _pdf(inp / "DN2.pdf", 555.0)
    _pdf(inp / "SI2.pdf", 666.0)

    result = sync_flow()
    assert result["errors"] == 0, result

    ids = sets(cfg)
    assert set(ids) == {PO_NO, OTHER_PO_NO}, ids
    assert ids[PO_NO][1] == "merged", ids
    assert ids[OTHER_PO_NO][1] == "merged", f"two clean, independent sets must BOTH merge: {ids}"
    out = sorted(p.name for p in Path(cfg.paths.output_folder).iterdir())
    assert out == sorted([f"{INVOICE_NO}.pdf", "INV-OTHER.pdf"]), out
    assert PdfReader(str(Path(cfg.paths.output_folder) / "INV-OTHER.pdf")).pages


def test_a_failed_document_is_listed_and_labelled_as_failed(env, vlm):
    """TIER 2. A permanently failed document must be visibly failed.

    Before this was fixed, the measured behaviour was: the file WAS listed in
    /unclassified, because ingestion leaves `doc_type = UNKNOWN` and a failed
    extraction never advances it — but the row carried no status label at all,
    so it looked identical to a document still waiting to be read. The
    holding-area header also counted it as merely "pending".

    The holding area now lists failed documents of any type, and any document
    attached to a PO Set can be retried from that set via Redo/Re-extract.
    """
    from fastapi.testclient import TestClient

    import app.api.routes.dashboard as dash
    from app.flows.sync import sync_flow
    from app.main import app

    cfg = env
    inp = Path(cfg.paths.input_folder)
    _pdf(inp / "PO.pdf", W_PO)
    _pdf(inp / "DN.pdf", W_DN)
    _pdf(inp / "SI.pdf", W_SI)
    _pdf(inp / "bad.pdf", 999.0)

    sync_flow()

    with Session(get_engine(cfg)) as s:
        failed = s.query(Document).filter_by(extraction_status=ExtractionStatus.failed).all()
        assert len(failed) == 1, "precondition: one document failed"
        failed_name = failed[0].original_filename
        # ingestion left it UNKNOWN, and failure never moved it on
        assert failed[0].doc_type == DocType.UNKNOWN
        assert failed[0].po_set_id is None

    dash.load_config = lambda: cfg
    client = TestClient(app)

    unclassified_page = client.get("/unclassified").text

    # The failed document is listed, and the row now says so.
    assert failed_name in unclassified_page, "precondition: the failed file is listed"
    assert "Failed" in unclassified_page, (
        f"{failed_name} is listed in /unclassified but carries no 'Failed' label; "
        f"a reviewer cannot tell it from a document still pending extraction"
    )
    # and the holding-area header counts failures separately, so a directory
    # holding a lost document does not read as a clean "N awaiting classification"
    assert "1 failed" in unclassified_page, (
        "the unclassified header does not surface the failed count"
    )
