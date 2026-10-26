"""WS6 soak: 500 documents through the real poller — every document once, every set once.

D5: the poller lists the input folder every minute, starts one sync for everything
waiting, and nothing is skipped or repeated. The soak drives that path (real file lock,
real `run_poller_sync`, real Prefect flow) with documents arriving in ten batches over
simulated minutes, then re-sends five already-merged documents.

Extraction is canned (`CannedVLM`) and the Prefect task wrapper is unwrapped
(`plain_extract_task_for`) so the 500-document run fits in a test; 5.4 runs the same
flow through the real Prefect task path on a smaller batch and proves the two agree.

RUN_SOAK=1 raises the batch from 20 sets (100 documents) to 100 sets (500 documents).
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import pytest
from _ws6_helpers import (
    CannedVLM,
    backdate_documents,
    bind_poller_config,
    drop_set,
    hidden_output_files,
    output_pdfs,
    payload,
    plain_extract_task_for,
)
from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_robustness_harness import line, make_cfg, run_sync, sets, write_pdf

from app.core.database import get_engine
from app.models import AuditAction, AuditLog, Document, POSet, POSetStatus
from app.services import ingestion
from app.services.poller import SyncPoller
from app.services.reconciliation import SETTLING_PREFIX

SOAK = os.getenv("RUN_SOAK") == "1"
SETS = 100 if SOAK else 20
BATCHES = 10
BASE_MTIME = 1_760_000_000


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    c = make_cfg(tmp_path, monkeypatch)
    # config.example.yaml has a 5-minute settle time; a poller-started sync would
    # then hold every set. Tests that want the settle rule set it themselves (5.3).
    c.schedule.merge_settle_minutes = 0
    monkeypatch.setattr("app.flows.sync._extract_task_for", plain_extract_task_for)
    # `with TestClient(app)` runs main.lifespan, which calls main's own load_config():
    # without this it would read the developer's config.yaml.
    monkeypatch.setattr("app.main.load_config", lambda *a, **k: c)
    return c


@pytest.fixture()
def vlm(monkeypatch):
    return CannedVLM(monkeypatch)


# --------------------------------------------------------------------------- 5.1


def _five_documents(po: str, width: float) -> list[tuple[str, str, str | None, list[dict], float]]:
    """The five documents of one set: only the sums reconcile (PO == DN == SI per line)."""
    return [
        ("PO", po, None, [line("1", "10", "BOLT"), line("2", "6", "NUT")], width),
        ("DN", f"DN-{po}-A", po, [line("1", "4", "BOLT"), line("2", "6", "NUT")], width + 1),
        ("DN", f"DN-{po}-B", po, [line("1", "6", "BOLT")], width + 2),
        ("SI", f"INV-{po}-A", po, [line("1", "5", "BOLT"), line("2", "6", "NUT")], width + 3),
        ("SI", f"INV-{po}-B", po, [line("1", "5", "BOLT")], width + 4),
    ]


def test_ws6_soak_every_document_once_every_set_merged_once(cfg, vlm, monkeypatch):
    cfg.schedule.poll_enabled = True
    bind_poller_config(monkeypatch, cfg)

    # All documents of all sets, then shuffled so each batch mixes sets.
    planned: list[tuple[str, str, str | None, list[dict], float]] = []
    for i in range(SETS):
        planned.extend(_five_documents(f"45{i:08d}", 200 + 5 * i))
    rng = random.Random(20261025)
    rng.shuffle(planned)
    batches = [planned[k::BATCHES] for k in range(BATCHES)]

    inp = Path(cfg.paths.input_folder)
    used_names: set[str] = set()
    shas: dict[str, str] = {}  # filename -> sha
    poller = SyncPoller(load_cfg=lambda: cfg, clock=lambda: 0.0)
    for batch_no, batch in enumerate(batches):
        for j, (kind, number, ref, lines, width) in enumerate(batch):
            while True:
                name = f"{rng.getrandbits(32):08x}.pdf"
                if name not in used_names:
                    used_names.add(name)
                    break
            path = write_pdf(inp / name, width)
            shas[name] = vlm.add(path, payload(kind, number, ref, lines))
            stamp = BASE_MTIME + 60 * batch_no + j
            os.utime(path, (stamp, stamp))
        assert poller.tick() == "ran", f"batch {batch_no} must start a pass"

    # Re-sends: five documents of already merged sets, copied in under new names.
    assert poller.tick() == "idle"
    merged = [ps for ps in sets(cfg) if ps.status == POSetStatus.merged]
    assert merged, "no set merged, so there is nothing to re-send"
    re_sent = 0
    for ps in merged[:5]:
        doc = sorted(ps.documents, key=lambda d: d.id)[0]
        name = f"{rng.getrandbits(32):08x}.pdf"
        used_names.add(name)
        (inp / name).write_bytes(Path(doc.stored_path).read_bytes())
        re_sent += 1
    assert re_sent == 5
    assert poller.tick() == "ran", "a re-sent copy must start a pass"
    assert poller.tick() == "idle"

    with Session(get_engine(cfg)) as s:
        docs = s.query(Document).filter(Document.archived_at.is_(None)).all()
        doc_count = len(docs)
        distinct_hashes = {d.sha256_hash for d in docs}
        unread = [d.original_filename for d in docs if d.extraction_status.value != "valid"]
        unattached = [d.original_filename for d in docs if d.po_set_id is None]
        all_sets = s.query(POSet).all()
        merged_paths = [ps.merged_output_path for ps in all_sets]
        statuses = {ps.status for ps in all_sets}
        force_rows = s.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == AuditAction.force_merge)
        )

    # 1. One row per input document; the re-sends made none.
    assert doc_count == 5 * SETS, f"expected {5 * SETS} document rows, found {doc_count}"
    assert len(distinct_hashes) == 5 * SETS
    # 2. Every document read and placed.
    assert unread == []
    assert unattached == []
    # 3. Every set merged once, one packet each.
    assert statuses == {POSetStatus.merged}, statuses
    assert len(all_sets) == SETS
    assert len(set(merged_paths)) == SETS, "two sets share a merged_output_path"
    assert len(output_pdfs(cfg)) == SETS, [p.name for p in output_pdfs(cfg)][:5]
    assert hidden_output_files(cfg) == []
    for ps in all_sets:
        assert len(PdfReader(ps.merged_output_path).pages) == 5, ps.merged_output_path
    # 4. No document was read twice.
    assert {sha: vlm.calls[sha] for sha in shas.values() if vlm.calls[sha] != 1} == {}
    # 5. Nothing left in input.
    assert ingestion.input_pdfs(cfg) == []
    # 6. Nothing was force-merged, and one pass per batch plus the re-send pass.
    assert force_rows == 0
    assert poller.runs == BATCHES + 1, poller.runs


# --------------------------------------------------------------------------- 5.2


def test_ws6_input_order_is_oldest_first(cfg, vlm, monkeypatch):
    """D5: "one pass reads every waiting file, oldest first"."""
    inp = Path(cfg.paths.input_folder)
    order: list[str] = []
    for name, width, po in (("a.pdf", 400.0, "4500110001"), ("b.pdf", 410.0, "4500110002")):
        vlm.add(write_pdf(inp / name, width), payload("PO", po, None, [line("1", "10")]))
    vlm.add(write_pdf(inp / "c.pdf", 420.0), payload("PO", "4500110003", None, [line("1", "10")]))
    # c is the oldest waiting file (largest age), a the newest.
    for name, age in (("c.pdf", 300), ("b.pdf", 200), ("a.pdf", 100)):
        path = inp / name
        os.utime(path, (BASE_MTIME - age, BASE_MTIME - age))

    real = ingestion.ingest_file
    monkeypatch.setattr(
        ingestion,
        "ingest_file",
        lambda src, cfg_: (order.append(Path(src).name), real(src, cfg_))[1],
    )
    result = run_sync()
    assert result["errors"] == 0, result
    assert order == ["c.pdf", "b.pdf", "a.pdf"], order


# --------------------------------------------------------------------------- 5.3


def test_ws6_soak_settle_wake_merges_everything_in_one_pass(cfg, vlm, monkeypatch):
    cfg.schedule.poll_enabled = True
    cfg.schedule.merge_settle_minutes = 5
    bind_poller_config(monkeypatch, cfg)
    for i in range(5):
        drop_set(cfg, vlm, f"45001200{i:02d}", 200 + 10 * i)

    poller = SyncPoller(load_cfg=lambda: cfg)
    assert poller.tick() == "ran"
    for ps in sets(cfg):
        assert ps.status == POSetStatus.pending, ps.status
        assert (ps.reconcile_reason or "").startswith(SETTLING_PREFIX), ps.reconcile_reason
    assert poller.tick() == "idle", "a set inside its settle window must not merge"

    backdate_documents(cfg, 600)
    assert poller.tick() == "ran", "the settle time has passed; the poller must wake itself"
    assert {ps.status for ps in sets(cfg)} == {POSetStatus.merged}
    assert poller.tick() == "idle"


# --------------------------------------------------------------------------- 5.4


def test_ws6_soak_small_real_prefect_path(tmp_path, monkeypatch):
    """The real Prefect task path (not unwrapped) merges the same way, on 3 sets."""
    cfg = make_cfg(tmp_path, monkeypatch)
    cfg.schedule.poll_enabled = True
    cfg.schedule.merge_settle_minutes = 0
    monkeypatch.setattr("app.main.load_config", lambda *a, **k: cfg)
    bind_poller_config(monkeypatch, cfg)
    vlm = CannedVLM(monkeypatch)
    for i in range(3):
        drop_set(cfg, vlm, f"45001300{i:02d}", 200 + 10 * i)

    poller = SyncPoller(load_cfg=lambda: cfg)
    assert poller.tick() == "ran"
    assert {ps.status for ps in sets(cfg)} == {POSetStatus.merged}
    assert sorted(vlm.calls.values()) == [1, 1, 1, 1, 1, 1, 1, 1, 1]
    assert len(output_pdfs(cfg)) == 3
