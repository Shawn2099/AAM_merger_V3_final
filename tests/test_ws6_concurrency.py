"""WS6 — Force Merge + poller + manual Scan on one PO Set: exactly one writes the packet.

The set is a real mismatch (PO 10, DN 7, SI 10), so it is a set an operator may Force
Merge but no automatic pass may merge. Every check is an invariant that holds whoever
wins the race; none asserts which thread wins.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from _ws6_helpers import (
    CannedVLM,
    Gate,
    SyncRecorder,
    audit_count,
    bind_poller_config,
    document_row,
    drop_set,
    hidden_output_files,
    join_new_sync_threads,
    output_pdfs,
    payload,
    plain_extract_task_for,
    set_row,
    sync_run_threads,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_robustness_harness import line, make_cfg, run_sync, write_pdf

from app.core.database import get_engine
from app.models import AuditAction, AuditLog, ExtractionStatus, POSet, POSetStatus
from app.services import sync_lock
from app.services.poller import SyncPoller

JUST = "WS6: operator accepts 7 of 10 delivered."
PO = "4500011111"


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    c = make_cfg(tmp_path, monkeypatch)
    # config.example.yaml has a 5-minute settle time; a poller-started sync would
    # then hold every set. Tests that want the settle rule set it themselves (5.3).
    c.schedule.merge_settle_minutes = 0
    # Keep the app's own background poller out of these tests: they drive ticks by hand,
    # so every tick in the test is one the test asked for.
    c.schedule.poll_enabled = False
    monkeypatch.setattr("app.flows.sync._extract_task_for", plain_extract_task_for)
    # `with TestClient(app)` runs main.lifespan, which calls main's own load_config():
    # without this it would read the developer's config.yaml.
    monkeypatch.setattr("app.main.load_config", lambda *a, **k: c)
    bind_poller_config(monkeypatch, c)
    return c


@pytest.fixture()
def vlm(monkeypatch):
    return CannedVLM(monkeypatch)


@pytest.fixture()
def other_pdf(cfg):
    """The path of an unrelated document the test writes when it needs one."""
    return Path(cfg.paths.input_folder) / "unrelated_dn.pdf"


def _call(fn, *args, **kwargs):
    """Call a route function directly; ("ok", value), ("http", status, detail) or ("exc", repr)."""
    try:
        return ("ok", fn(*args, **kwargs))
    except HTTPException as e:
        return ("http", e.status_code, str(e.detail))
    except Exception as e:  # a 500 the route did not turn into an HTTPException
        return ("exc", repr(e))


def _mismatched_set(cfg, vlm) -> int:
    drop_set(cfg, vlm, PO, 300.0, dn_qty="7")
    result = run_sync()
    assert result["errors"] == 0, result
    ps = set_row(cfg, PO)
    assert ps.status == POSetStatus.mismatched, ps.reconcile_reason
    return ps.id


def _refusals(results: list[tuple], po: str = "") -> list[tuple]:
    """The non-ok results, after proving none of them was an unhandled exception."""
    crashed = [r for r in results if r[0] == "exc"]
    assert crashed == [], f"{po}: the route raised instead of answering {crashed}"
    return [r for r in results if r[0] != "ok"]


def _audit_details(cfg, po_set_id: int) -> list[str]:
    with Session(get_engine(cfg)) as s:
        rows = s.scalars(
            select(AuditLog).where(
                AuditLog.po_set_id == po_set_id, AuditLog.action == AuditAction.force_merge
            )
        ).all()
        return [r.detail or "" for r in rows]


# --------------------------------------------------------------------------- 6.1


def test_ws6_force_merge_holds_the_set_while_poller_scan_and_a_second_merge_try(
    cfg, vlm, other_pdf, monkeypatch
):
    from app.api.routes import po_sets as po_routes
    from app.main import app

    recorder = SyncRecorder(monkeypatch)
    ps_id = _mismatched_set(cfg, vlm)

    import app.services.merge as merge

    gate = Gate()
    monkeypatch.setattr(merge, "_write_merged", gate.wrap(merge._write_merged))

    results: list[tuple] = []

    def run_force_merge() -> None:
        results.append(_call(po_routes.force_merge, ps_id, justification=JUST))

    thread = threading.Thread(target=run_force_merge, name="ws6-held-force-merge")
    thread.start()
    try:
        assert gate.entered.wait(15), "Force Merge never reached the packet write"

        # 1. The per-PO lock is held, in the DB, while the write is in flight.
        with Session(get_engine(cfg)) as s:
            assert s.get(POSet, ps_id).locked_by_action == "force_merge"
        locked_stamp = set_row(cfg, PO).updated_at

        with TestClient(app) as client:
            # 2. A second Force Merge is refused.
            second = client.post(f"/po_sets/{ps_id}/force_merge", data={"justification": JUST})
            assert second.status_code == 409, second.text
            assert "already in progress" in second.text.lower()
            # 3. Merge Now and Re-Match are refused by the same lock.
            assert client.post(f"/po_sets/{ps_id}/merge").status_code == 409
            assert client.post(f"/po_sets/{ps_id}/redo_match").status_code == 409

            # 4. A poller pass runs, and skips the locked set instead of waiting on it.
            # poll_enabled is switched on only for the manual tick below: the lifespan
            # above must not find it on and start a real background poller.
            write_pdf(other_pdf, 350.0)
            vlm.add(other_pdf, payload("DN", "DN-ORPHAN", None, [line("1", "1", "SPARE")]))
            recorder.clear()
            cfg.schedule.poll_enabled = True
            poller = SyncPoller(load_cfg=lambda: cfg)
            assert poller.tick() == "ran"
            cfg.schedule.poll_enabled = False
            # A swallowed "database is locked" is exactly what this test must not hide.
            recorder.assert_clean()
            locked = set_row(cfg, PO)
            assert locked.status == POSetStatus.mismatched
            assert locked.merged_output_path is None
            assert locked.updated_at == locked_stamp, "the sweep reconciled a locked set"
            orphan = document_row(cfg, "unrelated_dn.pdf")
            assert orphan is not None, "the poller pass never read the new document"
            assert orphan.extraction_status == ExtractionStatus.valid

            # 5. A manual Scan runs, and also leaves the locked set alone.
            recorder.clear()
            before = sync_run_threads()
            scan = client.post("/sync")
            assert scan.status_code == 200, scan.text
            join_new_sync_threads(before)
            recorder.assert_clean()
            scanned = set_row(cfg, PO)
            assert scanned.status == POSetStatus.mismatched
            assert scanned.merged_output_path is None
            assert scanned.updated_at == locked_stamp, "the Scan reconciled a locked set"
    finally:
        gate.release()
        thread.join(30)

    assert not thread.is_alive()
    assert len(results) == 1
    _refusals(results)
    kind, value = results[0]
    assert kind == "ok", value
    assert value["status"] == "merged"

    ps = set_row(cfg, PO)
    assert ps.status == POSetStatus.merged
    packets = output_pdfs(cfg)
    assert len(packets) == 1, [p.name for p in packets]
    assert hidden_output_files(cfg) == []
    assert len(PdfReader(str(packets[0])).pages) == 3
    assert audit_count(cfg, ps_id, AuditAction.force_merge) == 1
    with Session(get_engine(cfg)) as s:
        row = s.scalars(
            select(AuditLog).where(
                AuditLog.po_set_id == ps_id, AuditLog.action == AuditAction.force_merge
            )
        ).one()
        assert row.justification == JUST
        assert "already_merged" not in (row.detail or "")
    assert ps.locked_by_action is None


# --------------------------------------------------------------------------- 6.2


def test_ws6_a_running_sync_refuses_force_merge_and_scan_and_queues_the_poller(
    cfg, vlm, other_pdf, monkeypatch
):
    from app.main import app

    recorder = SyncRecorder(monkeypatch)
    ps_id = _mismatched_set(cfg, vlm)
    write_pdf(other_pdf, 351.0)
    vlm.add(other_pdf, payload("DN", "DN-ORPHAN-2", None, [line("1", "1", "SPARE")]))
    queued_stamp = set_row(cfg, PO).updated_at

    holder = sync_lock.acquire_sync_lock()
    assert holder is not None
    # ONE poller for both ticks: the "ran" tick proves its own _queued flag was honoured.
    poller = SyncPoller(load_cfg=lambda: cfg)
    try:
        # poll_enabled stays off while the client is open, so the lifespan starts no
        # background poller; it is switched on only around the manual tick below.
        with TestClient(app) as client:
            refused = client.post(f"/po_sets/{ps_id}/force_merge", data={"justification": JUST})
            assert refused.status_code == 409, refused.text
            assert "a sync is currently running" in refused.text.lower()
            assert client.post(f"/po_sets/{ps_id}/redo_extract").status_code == 409
            scan = client.post("/sync")
            assert scan.status_code == 409, scan.text
            assert "sync already running" in scan.text.lower()

            recorder.clear()
            cfg.schedule.poll_enabled = True
            assert poller.tick() == "queued"
            cfg.schedule.poll_enabled = False
            assert poller.runs == 0
        # "queued" is a deferral, not a pass: nothing was read and nothing reconciled.
        recorder.assert_clean()
        assert recorder.results == [], "a queued tick ran a sync"
        assert set_row(cfg, PO).updated_at == queued_stamp
    finally:
        sync_lock.release_sync_lock(holder)

    recorder.clear()
    cfg.schedule.poll_enabled = True
    assert poller.tick() == "ran", "the queued pass must run once the lock is free"
    cfg.schedule.poll_enabled = False
    assert poller.runs == 1
    recorder.assert_clean()

    assert set_row(cfg, PO).status == POSetStatus.mismatched
    assert set_row(cfg, PO).merged_output_path is None
    orphan = document_row(cfg, "unrelated_dn.pdf")
    assert orphan is not None, "the deferred pass never read the new document"
    assert orphan.extraction_status == ExtractionStatus.valid

    with TestClient(app) as client:
        merged = client.post(f"/po_sets/{ps_id}/force_merge", data={"justification": JUST})
        assert merged.status_code == 200, merged.text
        assert merged.json()["status"] == "merged"

    assert set_row(cfg, PO).status == POSetStatus.merged
    assert len(output_pdfs(cfg)) == 1


# --------------------------------------------------------------------------- 6.3


def test_ws6_two_simultaneous_force_merges_write_one_packet(cfg, vlm):
    """Two Force Merges of the same set, ten rounds: one packet and one real merge each.

    The race is not steered: both threads are released by one barrier and the invariants
    hold whichever one wins. A round can also end with both requests succeeding — the
    second one finds the set merged and re-reports it (O-2) — so the number of rounds
    that produce a 409 is recorded and printed, not asserted: 409 is a scheduling
    outcome, and requiring it would make the test depend on which thread was faster.
    """
    from app.api.routes import po_sets as po_routes

    pids: list[tuple[int, str]] = []
    for i in range(10):
        po = f"45000200{i:02d}"
        drop_set(cfg, vlm, po, 400.0 + 10 * i, dn_qty="7")
        pids.append((i, po))
    result = run_sync()
    assert result["errors"] == 0, result
    ids: list[tuple[int, str]] = []
    for _i, po in pids:
        row = set_row(cfg, po)
        assert row.status == POSetStatus.mismatched, row.reconcile_reason
        ids.append((row.id, po))

    refused_rounds: list[str] = []
    for ps_id, po in ids:
        barrier = threading.Barrier(2, timeout=10)
        results: list[tuple] = []

        def attempt(set_id: int, sink: list[tuple], sync_point: threading.Barrier) -> None:
            sync_point.wait()
            sink.append(_call(po_routes.force_merge, set_id, justification=JUST))

        threads = [
            threading.Thread(
                target=attempt, args=(ps_id, results, barrier), name=f"ws6-race-{ps_id}-a"
            ),
            threading.Thread(
                target=attempt, args=(ps_id, results, barrier), name=f"ws6-race-{ps_id}-b"
            ),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert all(not t.is_alive() for t in threads)
        assert len(results) == 2

        # 1. One winner; every loser was refused by the lock, never crashed.
        winners = [r for r in results if r[0] == "ok"]
        assert winners, f"{po}: nobody merged the set: {results}"
        losers = _refusals(results, po)
        if losers:
            refused_rounds.append(po)
        for _kind, status, detail in losers:
            assert status == 409, f"{po}: unexpected {status} {detail}"
            assert "already in progress" in detail.lower(), f"{po}: {detail}"

        # 2. One packet, and the set points at it.
        row = set_row(cfg, po)
        assert row.status == POSetStatus.merged, row.reconcile_reason
        assert row.merged_output_path is not None
        packet = Path(row.merged_output_path)
        assert packet.is_file()
        assert packet.name == f"SI-{po}.pdf", packet.name

        # 3. Every success left exactly one audit row; only one row is a real merge.
        details = _audit_details(cfg, ps_id)
        assert len(details) == len(winners), f"{po}: {details}"
        assert sum(1 for d in details if "already_merged" not in d) == 1, f"{po}: {details}"

        # 4. The packet is complete.
        assert len(PdfReader(str(packet)).pages) == 3

    print(
        f"\nWS6-OBSERVED 6.3 rounds refused with 409: {len(refused_rounds)}/{len(ids)} "
        f"({refused_rounds or 'none'})"
    )
    assert hidden_output_files(cfg) == []
    assert len(output_pdfs(cfg)) == len(ids)
