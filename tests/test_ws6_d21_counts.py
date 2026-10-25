"""WS6 / D21 — what one sync pass does with 0, 50 and 200 pending input files.

D21 (2026-10-25): no re-hash cache is built. The idle poller already compares path,
mtime and size per tick, so an unchanged folder starts nothing; what a pass costs is the
stability poll on every input file plus re-grouping and re-reconciling every open set.
This module counts that work — it never asserts seconds, so it is deterministic on any
machine. The timing table lives in `scripts/soak_d21_timing.py` (9.2).

A "pending input file" here is a document whose set is still open (PO + SI with no DN:
`missing_dn_document`), so the file stays in the input folder and every later pass sees
it again.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest
from _ws6_helpers import (
    CannedVLM,
    bind_poller_config,
    payload,
    plain_extract_task_for,
)
from sqlalchemy.orm import Session
from test_robustness_harness import line, make_cfg, run_sync, write_pdf

from app.core.database import get_engine
from app.models import Document, POSet, POSetStatus
from app.services import grouping, ingestion, reconciliation
from app.services.poller import SyncPoller

SOAK = os.getenv("RUN_SOAK") == "1"
NO_DN_REASON = (
    "No delivery note attached to this set yet "
    "(not received, still being read, or waiting on the Unclassified page)"
)


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
    bind_poller_config(monkeypatch, c)
    return c


@pytest.fixture()
def vlm(monkeypatch):
    return CannedVLM(monkeypatch)


def _open_set(cfg, vlm, index: int, wide: float) -> str:
    """One PO + one SI (no DN): the set waits, so both files stay in the input folder."""
    inp = Path(cfg.paths.input_folder)
    po = f"4500070{index:04d}"
    vlm.add(
        write_pdf(inp / f"po_{index:04d}.pdf", wide),
        payload("PO", po, None, [line("1", "10", "BOLT")]),
    )
    vlm.add(
        write_pdf(inp / f"si_{index:04d}.pdf", wide + 1),
        payload("SI", f"SI-{po}", po, [line("1", "10", "BOLT")]),
    )
    return po


def _open_sets(cfg) -> dict[str, str]:
    with Session(get_engine(cfg)) as s:
        rows = s.query(POSet).filter(POSet.status == POSetStatus.pending).all()
        return {r.po_no_normalized: r.reconcile_reason or "" for r in rows}


@pytest.mark.parametrize(
    "n_pending",
    [
        0,
        50,
        pytest.param(
            200,
            marks=pytest.mark.skipif(
                not SOAK, reason="set RUN_SOAK=1 to run the 200-file WS6 count"
            ),
        ),
    ],
)
def test_ws6_pending_input_files_cost_the_same_work_per_pass(cfg, vlm, monkeypatch, n_pending):
    for i in range(n_pending // 2):
        _open_set(cfg, vlm, i, 300.0 + 4 * i)

    cold = run_sync()
    assert cold["errors"] == 0, cold
    before_state = _open_sets(cfg)
    assert len(before_state) == n_pending // 2
    if n_pending:
        assert set(before_state.values()) == {NO_DN_REASON}

    # The warm pass adds one new PO for a new set; everything else is already known.
    new_po = "4500079999"
    vlm.add(
        write_pdf(Path(cfg.paths.input_folder) / "new_po.pdf", 900.0),
        payload("PO", new_po, None, [line("1", "10", "BOLT")]),
    )

    counts: Counter[str] = Counter()
    import app.flows.sync as flow_sync

    real_ingest = ingestion.ingest_file
    real_reconcile = reconciliation.reconcile_po_set
    real_resolve = grouping.resolve_unattached_documents
    real_attach = grouping.attach_unattached_to_open_sets
    real_stability = flow_sync._poll_stability

    def counting_ingest(src, cfg_):
        counts["ingest"] += 1
        return real_ingest(src, cfg_)

    def counting_reconcile(po_set_id, cfg_):
        counts["reconcile"] += 1
        return real_reconcile(po_set_id, cfg_)

    def counting_resolve(cfg_):
        counts["resolve"] += 1
        return real_resolve(cfg_)

    def counting_attach(cfg_):
        counts["attach"] += 1
        return real_attach(cfg_)

    def counting_stability(files, cfg_):
        counts["stability_files"] += len(files)
        return real_stability(files, cfg_)

    monkeypatch.setattr(ingestion, "ingest_file", counting_ingest)
    monkeypatch.setattr(reconciliation, "reconcile_po_set", counting_reconcile)
    monkeypatch.setattr(grouping, "resolve_unattached_documents", counting_resolve)
    monkeypatch.setattr(grouping, "attach_unattached_to_open_sets", counting_attach)
    monkeypatch.setattr("app.flows.sync._poll_stability", counting_stability)

    calls_before = sum(vlm.calls.values())
    with Session(get_engine(cfg)) as s:
        docs_before = s.query(Document).filter(Document.archived_at.is_(None)).count()

    warm = run_sync()

    assert warm["errors"] == 0, warm
    assert sum(vlm.calls.values()) - calls_before == 1, "only the new file may be read"
    assert counts["ingest"] == n_pending + 1, counts
    assert counts["stability_files"] == n_pending + 1, counts
    assert counts["reconcile"] == n_pending // 2 + 1, (
        "every open set is reconciled exactly once, plus the new one"
    )
    # Both grouping sweeps run once per pass, whatever is waiting: neither grows with
    # the backlog, which is why the growth is in the stability poll and the clock sweep.
    assert counts["resolve"] == 1, counts
    assert counts["attach"] == 1, counts
    with Session(get_engine(cfg)) as s:
        docs_after = s.query(Document).filter(Document.archived_at.is_(None)).count()
    assert docs_after - docs_before == 1

    after_state = _open_sets(cfg)
    for po, reason in before_state.items():
        assert after_state[po] == reason, f"{po} changed reason to {after_state[po]!r}"
    assert after_state[new_po] == NO_DN_REASON
    assert len(ingestion.input_pdfs(cfg)) == n_pending + 1

    # Idle: an unchanged input folder starts nothing, however many files are waiting.
    calls: list[int] = []

    def fake_sync(lock):
        calls.append(1)
        return {"errors": 0}

    cfg.schedule.poll_enabled = True
    poller = SyncPoller(load_cfg=lambda: cfg, run_sync=fake_sync, acquire=lambda: "LOCK")
    assert poller.tick() == "ran"  # the watcher starts empty: the waiting files look new
    assert poller.tick() == "idle"
    assert len(calls) == 1
