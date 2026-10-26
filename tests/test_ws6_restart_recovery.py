"""WS6 — restart recovery: no stuck lock and no lost document after a killed run.

The residue a killed process leaves behind is created the way the product leaves it: a
per-PO lock row, a sync sidecar, a document claimed `processing`. Nothing sleeps and no
real time passes — file times and timestamps are moved with `os.utime` and a Core
`update()`, so every test is deterministic.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _ws6_helpers import (
    CannedVLM,
    SimulatedCrash,
    backdate_documents,
    bind_poller_config,
    document_row,
    drop_set,
    join_new_sync_threads,
    output_pdfs,
    payload,
    plain_extract_task_for,
    set_row,
    sync_run_threads,
)
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import update
from sqlalchemy.orm import Session
from test_robustness_harness import line, make_cfg, run_sync, write_pdf

from app.core.database import get_engine
from app.models import Document, ExtractionStatus, POSet, POSetStatus
from app.services import sync_lock
from app.services.poller import SyncPoller
from app.services.reconciliation import REASON_TEXT, reconcile_po_set

JUST = "WS6: operator accepts 7 of 10 delivered."


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    c = make_cfg(tmp_path, monkeypatch)
    # config.example.yaml has a 5-minute settle time; a poller-started sync would
    # then hold every set. Tests that want the settle rule set it themselves (5.3).
    c.schedule.merge_settle_minutes = 0
    # Keep the app's own background poller out of these tests: every tick is one the
    # test asks for.
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


def _mismatched_set(cfg, vlm, po: str = "4500011111", width: float = 300.0) -> int:
    drop_set(cfg, vlm, po, width, dn_qty="7")
    result = run_sync()
    assert result["errors"] == 0, result
    ps = set_row(cfg, po)
    assert ps.status == POSetStatus.mismatched, ps.reconcile_reason
    return ps.id


def _set_lock(cfg, po_set_id: int, action: str, age_seconds: float) -> None:
    with Session(get_engine(cfg)) as s:
        s.execute(
            update(POSet)
            .where(POSet.id == po_set_id)
            .values(
                locked_by_action=action,
                locked_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
            )
        )
        s.commit()


# --------------------------------------------------------------------------- 7.1


def test_ws6_a_lock_left_by_a_dead_request_is_honoured_then_expires(cfg, vlm):
    """D10: a dead request's per-PO lock blocks until the configured timeout, then clears."""
    from app.main import app

    ps_id = _mismatched_set(cfg, vlm)
    _set_lock(cfg, ps_id, "force_merge", 30)

    with TestClient(app) as client:
        refused = client.post(f"/po_sets/{ps_id}/force_merge", data={"justification": JUST})
        assert refused.status_code == 409, refused.text
        assert "already in progress" in refused.text.lower()

        # A sync sweep must not clear or ignore a live lock: the set is left alone.
        result = run_sync()
        assert result["errors"] == 0, result
        assert set_row(cfg, "4500011111").status == POSetStatus.mismatched

        _set_lock(cfg, ps_id, "force_merge", cfg.concurrency.po_set_lock_timeout_seconds + 5)
        seen = client.get(f"/po_sets/{ps_id}")
        assert seen.status_code == 200
        assert seen.json()["is_locked"] is False

        merged = client.post(f"/po_sets/{ps_id}/force_merge", data={"justification": JUST})
        assert merged.status_code == 200, merged.text

    assert set_row(cfg, "4500011111").status == POSetStatus.merged
    assert len(output_pdfs(cfg)) == 1


# --------------------------------------------------------------------------- 7.2


def _orphan_sidecar(age_seconds: float) -> tuple[Path, Path]:
    """The residue of a dead lock holder: a lock file and an old sidecar, held by nobody."""
    lock_file = sync_lock._lock_file()
    lock_file.write_text("")
    sidecar = sync_lock._sidecar_for(lock_file)
    sidecar.write_text("0")
    stamp = time.time() - age_seconds
    os.utime(sidecar, (stamp, stamp))
    return lock_file, sidecar


def test_ws6_web_restart_clears_an_orphan_sidecar(cfg, vlm):
    from app.main import app

    ps_id = _mismatched_set(cfg, vlm)
    _lock_file, sidecar = _orphan_sidecar(60)
    assert sync_lock._is_sync_running() is True

    # Before the restart: every operator action is refused.
    client = TestClient(app)
    refused = client.post(f"/po_sets/{ps_id}/force_merge", data={"justification": JUST})
    assert refused.status_code == 409, refused.text
    assert "a sync is currently running" in refused.text.lower()

    # The restart (lifespan) takes and releases the sync lock once, removing the orphan.
    with TestClient(app) as client:
        assert sync_lock._is_sync_running() is False
        assert not sidecar.exists()
        merged = client.post(f"/po_sets/{ps_id}/force_merge", data={"justification": JUST})
        assert merged.status_code == 200, merged.text
    assert set_row(cfg, "4500011111").status == POSetStatus.merged


def test_ws6_a_scan_clears_an_orphan_left_by_a_crashed_nightly_worker(cfg, vlm):
    from app.main import app

    ps_id = _mismatched_set(cfg, vlm)
    with TestClient(app) as client:
        # The web service did NOT restart: the nightly worker died and left its sidecar.
        _lock_file, sidecar = _orphan_sidecar(60)
        assert sync_lock._is_sync_running() is True
        refused = client.post(f"/po_sets/{ps_id}/force_merge", data={"justification": JUST})
        assert refused.status_code == 409, refused.text
        assert "a sync is currently running" in refused.text.lower()

        before = sync_run_threads()
        scan = client.post("/sync")
        assert scan.status_code == 200, scan.text
        join_new_sync_threads(before)

        assert sync_lock._is_sync_running() is False
        assert not sidecar.exists()
        merged = client.post(f"/po_sets/{ps_id}/force_merge", data={"justification": JUST})
        assert merged.status_code == 200, merged.text


def test_ws6_an_old_orphan_sidecar_is_ignored(cfg, vlm):
    from app.main import app

    ps_id = _mismatched_set(cfg, vlm)
    _lock_file, _sidecar = _orphan_sidecar(sync_lock.SYNC_STALE_SECONDS + 60)
    assert sync_lock._is_sync_running() is False

    with TestClient(app) as client:
        merged = client.post(f"/po_sets/{ps_id}/force_merge", data={"justification": JUST})
        assert merged.status_code == 200, merged.text


# --------------------------------------------------------------------------- 7.3


def _crash_mid_extraction_then_restart(cfg, vlm):
    """7.3 up to (and including) the restart pass, shared by the test and its WS6-03 twin.

    Two complete sets, path order known. The second set's DN dies inside the VLM call:
    the process is gone, so the document row stays `processing`, the files after it are
    never ingested, and the sync sidecar stays behind. Returns the rows needed to judge
    what the restart pass did.
    """
    from app.flows.sync import _sync_flow_locked
    from app.main import app

    inp = Path(cfg.paths.input_folder)
    po1, po2 = "4500030001", "4500030002"
    files = {
        "a_po1.pdf": ("PO", po1, None, 200.0),
        "b_dn1.pdf": ("DN", f"DN-{po1}", po1, 201.0),
        "c_si1.pdf": ("SI", f"SI-{po1}", po1, 202.0),
        "d_po2.pdf": ("PO", po2, None, 203.0),
        "e_dn2.pdf": ("DN", f"DN-{po2}", po2, 204.0),
        "f_si2.pdf": ("SI", f"SI-{po2}", po2, 205.0),
    }
    shas: dict[str, str] = {}
    for name, (kind, number, ref, width) in files.items():
        shas[name] = vlm.add(
            write_pdf(inp / name, width), payload(kind, number, ref, [line("1", "10", "BOLT")])
        )
    vlm.crash_on[shas["e_dn2.pdf"]] = 1

    # The killed process: it held the sync lock and died inside the VLM call.
    lock = sync_lock.acquire_sync_lock()
    assert lock is not None
    with pytest.raises(SimulatedCrash):
        _sync_flow_locked(None)
    lock.release()  # a dead process cannot run release_sync_lock: the sidecar stays

    stuck = document_row(cfg, "e_dn2.pdf")
    assert stuck is not None
    assert stuck.extraction_status == ExtractionStatus.processing
    assert document_row(cfg, "f_si2.pdf") is None
    assert sync_lock._sidecar_for(sync_lock._lock_file()).exists()

    # Restart: the web service's lifespan clears the orphan, then the poller runs one pass.
    with TestClient(app):
        assert sync_lock._is_sync_running() is False
        cfg.schedule.poll_enabled = True
        poller = SyncPoller(load_cfg=lambda: cfg)  # a restarted service has an empty watcher
        assert poller.tick() == "ran"

        assert set_row(cfg, po1).status == POSetStatus.merged
        si2 = document_row(cfg, "f_si2.pdf")
        assert si2 is not None
        assert si2.extraction_status == ExtractionStatus.valid
        stuck = document_row(cfg, "e_dn2.pdf")
        assert stuck is not None
        assert stuck.extraction_status == ExtractionStatus.processing
        assert set_row(cfg, po2).status == POSetStatus.pending

    return po1, po2, shas, stuck, poller


def test_ws6_crash_mid_extraction_loses_no_document(cfg, vlm):
    """A killed run loses nothing; O-4: a stuck claim waits for the nightly run.

    DEVIATION FROM THE PLAN (kept deliberately, reported as WS6-03): the plan expected
    the restarted set to report `document_not_yet_read` ("A document in this set has not
    been read yet"). It reports `missing_dn_document` instead, and that is the product
    as built: grouping runs after extraction, so a DN that died mid-read has no PO
    number, is never attached to a set, and the set can only see that its DN is absent.
    The code is therefore still `missing_dn_document`; its text says the note may still be
    being read (`test_ws6_03_stuck_document_message_says_it_may_still_be_read`).
    """
    po1, po2, shas, stuck, poller = _crash_mid_extraction_then_restart(cfg, vlm)
    inp = Path(cfg.paths.input_folder)

    assert stuck.po_set_id is None, "an unread document must never join a set"
    assert set_row(cfg, po2).reconcile_reason == REASON_TEXT["missing_dn_document"]
    assert poller.tick() == "idle", "the poller does not wake for a stuck document"

    # Time passes: the claim is older than stale_processing_seconds, so the next run re-reads it.
    backdate_documents(cfg, cfg.extraction.stale_processing_seconds + 5, column="updated_at")
    result = run_sync()
    assert result["errors"] == 0, result

    assert set_row(cfg, po1).status == POSetStatus.merged
    assert set_row(cfg, po2).status == POSetStatus.merged
    with Session(get_engine(cfg)) as s:
        rows = s.query(Document).filter(Document.archived_at.is_(None)).order_by(Document.id).all()
        assert [d.original_filename for d in rows] == sorted(
            [
                "a_po1.pdf",
                "b_dn1.pdf",
                "c_si1.pdf",
                "d_po2.pdf",
                "e_dn2.pdf",
                "f_si2.pdf",
            ]
        )
        assert {d.sha256_hash for d in rows} == set(shas.values())
    assert vlm.calls[shas["e_dn2.pdf"]] == 2, vlm.calls
    assert {name: vlm.calls[sha] for name, sha in shas.items() if name != "e_dn2.pdf"} == {
        name: 1 for name in shas if name != "e_dn2.pdf"
    }
    assert len(output_pdfs(cfg)) == 2
    assert list(inp.glob("*.pdf")) == []


def test_ws6_03_stuck_document_message_says_it_may_still_be_read(cfg, vlm):
    """WS6-03: a DN that died mid-read is never attached, so the code stays missing_dn_document.

    The operator-facing text must not claim the DN never arrived: it says "not received, or
    still being read", which is true for both cases.
    """
    _po1, po2, _shas, _stuck, _poller = _crash_mid_extraction_then_restart(cfg, vlm)
    verdict = reconcile_po_set(set_row(cfg, po2).id, cfg)
    assert verdict["reason"] == "missing_dn_document", verdict
    assert "still being read" in REASON_TEXT["missing_dn_document"]
    assert set_row(cfg, po2).reconcile_reason == REASON_TEXT["missing_dn_document"]


# --------------------------------------------------------------------------- 7.4


def test_ws6_a_stray_temp_file_from_a_killed_write_does_not_block_the_merge(cfg, vlm):
    stray = Path(cfg.paths.output_folder) / ".SI-4500040001.deadbeef.tmp"
    stray.write_bytes(b"partial")
    drop_set(cfg, vlm, "4500040001", 300.0)

    result = run_sync()
    assert result["errors"] == 0, result
    ps = set_row(cfg, "4500040001")
    assert ps.status == POSetStatus.merged, ps.reconcile_reason
    packet = Path(ps.merged_output_path)
    assert len(PdfReader(str(packet)).pages) == 3
    print(f"\nWS6-OBSERVED 7.4 stray .tmp after merge: {'exists' if stray.exists() else 'gone'}")


# --------------------------------------------------------------------------- 7.5


def test_ws6_an_orphan_packet_from_a_crash_before_commit_quarantines_the_set(cfg, vlm):
    """Power loss after os.replace, before the DB commit: the file is never overwritten."""
    from app.main import app

    orphan = Path(cfg.paths.output_folder) / "SI-4500050001.pdf"
    orphan.write_bytes(b"%PDF-1.4 orphan")
    orphan_bytes = orphan.read_bytes()
    drop_set(cfg, vlm, "4500050001", 300.0)

    result = run_sync()
    assert result["errors"] == 0, result
    ps = set_row(cfg, "4500050001")
    assert ps.status == POSetStatus.quarantined, ps.reconcile_reason
    assert "already exists" in (ps.reconcile_reason or "")
    assert orphan.read_bytes() == orphan_bytes, "the orphan packet was overwritten"

    orphan.unlink()
    with TestClient(app) as client:
        redone = client.post(f"/po_sets/{ps.id}/redo_match")
        assert redone.status_code == 200, redone.text
    ps = set_row(cfg, "4500050001")
    assert ps.status == POSetStatus.merged, ps.reconcile_reason
    assert len(PdfReader(ps.merged_output_path).pages) == 3
