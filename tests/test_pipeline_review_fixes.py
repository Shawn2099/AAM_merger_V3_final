"""Opus pipeline review (2026-10-24): each defect the reviewers proved, now guarded."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_robustness_harness import (
    HttpVLM,
    all_docs,
    make_cfg,
    run_sync,
    write_pdf,
)

from app.models import ExtractionStatus
from app.services import poller as poller_mod
from app.services.poller import SyncPoller


@pytest.fixture()
def rcfg(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, monkeypatch)
    cfg.schedule.poll_enabled = True
    return cfg


BAD400 = ("status", 400, {"error": {"message": "x is not a valid model ID", "code": 400}})


# ------------------------------------------------------------------ P1: breaker + refunded docs
def test_a_persistent_400_never_condemns_documents_across_passes(rcfg, monkeypatch):
    """Pass 1 trips and refunds three documents; pass 2 adds three more. Nothing is quarantined."""
    widths = {111.0: "a", 222.0: "b", 333.0: "c", 444.0: "d", 555.0: "e", 666.0: "f"}
    HttpVLM(monkeypatch, dict.fromkeys(widths, BAD400))
    inp = Path(rcfg.paths.input_folder)
    for w, n in list(widths.items())[:3]:
        write_pdf(inp / f"{n}.pdf", w)
    assert run_sync()["vlm_halted"] is True
    for w, n in list(widths.items())[3:]:
        write_pdf(inp / f"{n}.pdf", w)
    assert run_sync()["vlm_halted"] is True
    docs = all_docs(rcfg)
    assert {d.extraction_status for d in docs} == {ExtractionStatus.pending}, [
        (d.original_filename, d.extraction_status) for d in docs
    ]
    qdocs = Path(rcfg.paths.quarantine_folder) / "_documents"
    assert not qdocs.exists() or not list(qdocs.iterdir()), "no document may be quarantined"
    assert len(list(inp.glob("*.pdf"))) == 6, "every input file is still in place"


# ------------------------------------------------------------------ poller backoff and retries
class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _poller(cfg, run_sync_fn, clock=None, lock="LOCK"):
    return SyncPoller(
        load_cfg=lambda: cfg, run_sync=run_sync_fn, acquire=lambda: lock, clock=clock or _Clock()
    )


def _file(cfg, name, size=300):
    return write_pdf(Path(cfg.paths.input_folder) / name, size)


def test_a_halted_provider_pauses_the_poller_then_one_run_is_owed(rcfg):
    clock = _Clock()
    calls = []

    def sync(lock):
        calls.append(1)
        return {"vlm_halted": True, "errors": 3}

    poller = _poller(rcfg, sync, clock)
    _file(rcfg, "a.pdf")
    assert poller.tick() == "ran" and len(calls) == 1
    _file(rcfg, "b.pdf", 320)  # a new file during the pause must not trigger a run
    assert poller.tick() == "idle" and len(calls) == 1
    clock.t += poller_mod.HALT_BACKOFF_SECONDS + 1
    assert poller.tick() == "ran" and len(calls) == 2  # exactly one owed run afterwards


def test_a_failing_sync_backs_off_and_the_files_are_retried_later(rcfg):
    clock = _Clock()
    outcomes = [RuntimeError("prefect down"), {"errors": 0}]

    def sync(lock):
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    poller = _poller(rcfg, sync, clock)
    _file(rcfg, "a.pdf")
    assert poller.tick() == "ran"
    assert poller.tick() == "idle"  # backing off, not hammering the server every minute
    clock.t += poller_mod.FAILURE_BACKOFF_SECONDS + 1
    assert poller.tick() == "ran"  # retried with NO new file: the snapshot was not committed
    assert poller.tick() == "idle"


def test_unstable_files_are_retried_a_few_times_then_committed(rcfg):
    calls = []

    def sync(lock):
        calls.append(1)
        return {"errors": 0, "unstable": 1}

    poller = _poller(rcfg, sync)
    _file(rcfg, "growing.pdf")
    outcomes = [poller.tick() for _ in range(6)]
    assert outcomes[:4] == ["ran"] * 4 and outcomes[4:] == ["idle", "idle"]
    assert len(calls) == 4


class _Capture(logging.Handler):
    """caplog can miss records when another test's logging setup disabled the logger."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.text = ""

    def emit(self, record):
        self.text += record.getMessage() + " "


def _capture(logger_name):
    lg = logging.getLogger(logger_name)
    h = _Capture()
    lg.disabled = False
    lg.addHandler(h)
    return lg, h


def test_a_long_queue_behind_another_sync_is_logged(rcfg):
    clock = _Clock()
    poller = SyncPoller(
        load_cfg=lambda: rcfg, run_sync=lambda lock: {}, acquire=lambda: None, clock=clock
    )
    _file(rcfg, "a.pdf")
    lg, cap = _capture("app.services.poller")
    try:
        assert poller.tick() == "queued"
        clock.t += poller_mod.QUEUED_WARN_SECONDS + 1
        assert poller.tick() == "queued"
    finally:
        lg.removeHandler(cap)
    assert "still waiting" in cap.text


def test_a_config_error_inside_the_poller_run_still_releases_the_lock(monkeypatch):
    released = []
    monkeypatch.setattr(
        "app.services.sync_lock.release_sync_lock", lambda lock: released.append(lock)
    )

    def boom(path=None):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("app.core.config.load_config", boom)
    with pytest.raises(RuntimeError):
        poller_mod.run_poller_sync("LOCK")
    assert released == ["LOCK"]


# ------------------------------------------------------------------ sync: unstable count, offline input
def test_non_empty_unstable_files_are_counted_zero_byte_files_are_not(rcfg, monkeypatch):
    HttpVLM(monkeypatch, {})
    inp = Path(rcfg.paths.input_folder)
    a = _file(rcfg, "copying.pdf")
    (inp / "empty.pdf").write_bytes(b"")
    monkeypatch.setattr(
        "app.flows.sync._poll_stability", lambda files, cfg: dict.fromkeys(files, False)
    )
    result = run_sync()
    assert result["unstable"] == 1 and a.exists()


def test_an_unreachable_input_folder_does_not_abort_the_sync(rcfg, monkeypatch):
    HttpVLM(monkeypatch, {})

    def offline(cfg):
        raise OSError("the network name is no longer available")

    monkeypatch.setattr("app.flows.sync.input_pdfs", offline, raising=False)
    monkeypatch.setattr("app.services.ingestion.input_pdfs", offline)
    result = run_sync()  # must not raise
    assert result["errors"] >= 1


# ------------------------------------------------------------------ sync lock ordering
def test_release_removes_the_sidecar_before_it_releases_the_lock(tmp_path):
    from app.services.sync_lock import _sidecar_for, release_sync_lock

    lock_file = tmp_path / "sync.lock"
    lock_file.write_text("x")
    sidecar = _sidecar_for(lock_file)
    sidecar.write_text("1")
    seen = {}

    class _Lock:
        lock_file = str(tmp_path / "sync.lock")

        def release(self):
            seen["sidecar_exists_at_release"] = sidecar.exists()

    release_sync_lock(_Lock())
    assert seen == {"sidecar_exists_at_release": False}


# ------------------------------------------------------------------ data safety
def test_clearing_inputs_never_deletes_a_stored_copy_even_inside_the_input_folder(tmp_path):
    from app.models import POSetStatus
    from app.services.ingestion import delete_input_files

    inp = tmp_path / "input"
    (inp / "_stored").mkdir(parents=True)
    payload = b"%PDF-1.4 same bytes"
    original = inp / "po.pdf"
    stored = inp / "_stored" / "abc.pdf"
    original.write_bytes(payload)
    stored.write_bytes(payload)
    import hashlib

    sha = hashlib.sha256(payload).hexdigest()
    doc = SimpleNamespace(
        extraction_status=ExtractionStatus.valid,
        sha256_hash=sha,
        original_filename="po.pdf",
        stored_path=str(stored),
    )
    ps = SimpleNamespace(status=POSetStatus.merged, merged_output_path="x.pdf", documents=[doc])
    delete_input_files(ps, inp)
    assert not original.exists()
    assert stored.read_bytes() == payload, "the durable stored copy must survive"


def test_config_refuses_a_data_folder_inside_the_input_folder(tmp_path):
    from app.core.config import AppConfig, load_config

    data = load_config("config.example.yaml").model_dump(mode="json")
    data["paths"]["input_folder"] = str(tmp_path / "input")
    data["paths"]["stored_documents_folder"] = str(tmp_path / "input" / "stored")
    with pytest.raises(ValueError, match=r"separate from paths.input_folder"):
        AppConfig.model_validate(data)


def test_a_renamed_copy_of_a_quarantined_document_is_removed_from_input(rcfg):
    import hashlib

    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import DocType, Document
    from app.services.quarantine import quarantine_document

    inp = Path(rcfg.paths.input_folder)
    original = _file(rcfg, "scan.pdf", 410)
    renamed = inp / "scan (copy).pdf"
    renamed.write_bytes(original.read_bytes())
    sha = hashlib.sha256(original.read_bytes()).hexdigest()
    stored = Path(rcfg.paths.stored_documents_folder) / f"{sha}.pdf"
    stored.write_bytes(original.read_bytes())
    with Session(get_engine(rcfg)) as s:
        d = Document(
            sha256_hash=sha,
            original_filename="scan.pdf",
            stored_path=str(stored),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.failed,
        )
        s.add(d)
        s.commit()
        doc_id = d.id
    folder = quarantine_document(doc_id, rcfg, reason="extraction failed")
    assert (folder / "scan.pdf").is_file()
    assert not original.exists() and not renamed.exists(), "both input copies are cleared"
