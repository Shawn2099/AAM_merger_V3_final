"""S2: never read a file that may still be copying.

A file is read only if its size and mtime are unchanged across the stability samples, it is at
least MIN_FILE_AGE_SECONDS old, and it is unchanged again immediately before it is ingested.
"""

from __future__ import annotations

import os
import time
import types
from pathlib import Path

import pytest
from test_robustness_harness import (
    HttpVLM,
    all_docs,
    extraction,
    line,
    make_cfg,
    run_sync,
    write_pdf,
)

from app.services import ingestion

W = 111.0


@pytest.fixture()
def rcfg(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, monkeypatch)
    # The suite-wide autouse fixture sets the age to 0; these tests exercise the real rule.
    monkeypatch.setattr(ingestion, "MIN_FILE_AGE_SECONDS", 30)
    # These tests age the modified time with os.utime. On Windows a file written now also has a
    # fresh creation time, which would hide the age, so they pin the rule to the modified time.
    # The creation-time rule has its own tests at the end of this file.
    monkeypatch.setattr(ingestion, "_last_touched", lambda st: st.st_mtime)
    HttpVLM(monkeypatch, {W: extraction("PO", "4500011111", None, [line("1", "10", "BOLT")])})
    return cfg


def _age(p: Path, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(p, (t, t))


def _drop(cfg, name="PO0.pdf") -> Path:
    return write_pdf(Path(cfg.paths.input_folder) / name, W)


def test_a_presized_file_with_a_fresh_mtime_is_not_ingested(rcfg):
    """Explorer/robocopy pre-size the target: right size, zero tail, but written a second ago."""
    p = _drop(rcfg)
    p.write_bytes(p.read_bytes() + b"\x00" * 4096)  # tail still zeros; mtime is now
    _age(p, 1)

    res = run_sync()

    assert all_docs(rcfg) == []
    assert p.exists()
    assert res["unstable"] == 1
    assert res["processed"] == 0


def test_a_normal_old_unchanged_file_is_ingested(rcfg):
    p = _drop(rcfg)
    _age(p, 120)

    run_sync()

    assert len(all_docs(rcfg)) == 1


def test_a_touched_file_waits_until_it_is_old_enough(rcfg):
    p = _drop(rcfg)
    _age(p, 120)
    os.utime(p, None)  # touched just now

    first = run_sync()
    assert all_docs(rcfg) == []
    assert first["unstable"] == 1

    _age(p, 31)
    run_sync()
    assert len(all_docs(rcfg)) == 1


def test_a_file_that_changes_after_the_stability_poll_is_skipped_then_ingested(rcfg, monkeypatch):
    p = _drop(rcfg)
    _age(p, 120)
    import app.flows.sync as flow_sync

    real_poll = flow_sync._poll_stability
    changed = {"done": False}

    def poll_then_change(files, cfg):
        result = real_poll(files, cfg)
        if not changed["done"]:
            changed["done"] = True
            # The copy resumes after the poll, before the file's turn: new mtime, new size.
            p.write_bytes(p.read_bytes() + b"\n%padding\n")
            _age(p, 120 - 5)  # still "old", but not the signature the poll saw
        return result

    monkeypatch.setattr(flow_sync, "_poll_stability", poll_then_change)

    first = run_sync()
    assert all_docs(rcfg) == [], "a file that changed since the poll must not be read"
    assert p.exists()
    assert first["unstable"] == 1
    assert first["processed"] == 0

    second = run_sync()  # stable now: same signature across the poll and the ingest
    assert len(all_docs(rcfg)) == 1
    assert second["unstable"] == 0


def test_is_file_stable_requires_unchanged_mtime_and_age(tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion, "MIN_FILE_AGE_SECONDS", 30)
    monkeypatch.setattr(ingestion, "_last_touched", lambda st: st.st_mtime)  # see rcfg
    p = tmp_path / "a.pdf"
    p.write_bytes(b"x" * 10)
    _age(p, 120)
    assert ingestion.is_file_stable(p, interval=0, count=2) is True
    os.utime(p, None)
    assert ingestion.is_file_stable(p, interval=0, count=2) is False
    _age(p, 120)
    p.write_bytes(b"")
    _age(p, 120)
    assert ingestion.is_file_stable(p, interval=0, count=2) is False  # zero bytes stay unstable


def test_last_touched_uses_the_later_of_modified_and_creation_time():
    assert (
        ingestion._last_touched(types.SimpleNamespace(st_mtime=1000.0, st_birthtime=5000.0))
        == 5000.0
    )
    assert (
        ingestion._last_touched(types.SimpleNamespace(st_mtime=7000.0, st_birthtime=5000.0))
        == 7000.0
    )
    assert ingestion._last_touched(types.SimpleNamespace(st_mtime=1000.0)) == 1000.0


def test_a_copied_old_file_with_a_fresh_creation_time_is_not_old_enough(rcfg, monkeypatch):
    monkeypatch.setattr(ingestion, "MIN_FILE_AGE_SECONDS", 30)
    p = _drop(rcfg)
    _age(p, 86400)  # the source's modified time is a day old, as after an extraction

    monkeypatch.setattr(ingestion, "_last_touched", lambda st: time.time())  # fresh creation time
    assert ingestion._old_enough(p) is False

    monkeypatch.setattr(ingestion, "_last_touched", lambda st: time.time() - 120)
    assert ingestion._old_enough(p) is True


def test_an_old_file_with_an_old_creation_time_is_old_enough(rcfg, monkeypatch):
    monkeypatch.setattr(ingestion, "MIN_FILE_AGE_SECONDS", 30)
    p = _drop(rcfg)
    _age(p, 86400)
    monkeypatch.setattr(ingestion, "_last_touched", lambda st: st.st_mtime)

    assert ingestion._old_enough(p) is True
