"""S1: a fault that cannot clear within a run (HTTP 401/402/403, no API key) stops the pass.

One document is tried, the rest of the input is left untouched, nothing is retried or read again
in recovery, and the next sync (after the fault is fixed) reads everything without a restart.
"""

from __future__ import annotations

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

from app.models import ExtractionStatus

WIDTHS = [111.0, 122.0, 133.0, 144.0, 155.0]
STD = [line("1", "10", "BOLT")]


@pytest.fixture()
def rcfg(tmp_path, monkeypatch):
    return make_cfg(tmp_path, monkeypatch)


def _good() -> dict:
    return {w: extraction("PO", f"45000{int(w)}", None, STD) for w in WIDTHS}


def _drop_five(cfg) -> None:
    for i, w in enumerate(WIDTHS):
        write_pdf(Path(cfg.paths.input_folder) / f"PO{i}.pdf", w)


def _input_names(cfg) -> list[str]:
    return sorted(p.name for p in Path(cfg.paths.input_folder).iterdir())


@pytest.mark.parametrize("code", [401, 402, 403])
def test_fatal_http_status_stops_new_work_after_the_first_response(rcfg, monkeypatch, code):
    bad = ("status", code, {"error": {"message": f"HTTP {code}", "code": code}})
    responses = dict.fromkeys(WIDTHS, bad)
    vlm = HttpVLM(monkeypatch, responses)
    _drop_five(rcfg)

    res = run_sync()

    assert res["vlm_halted"] is True
    assert "rejected the key or has no credit" in res["vlm_halt_reason"]
    # The whole stable batch is ingested first (so workers never touch input
    # files), but only the in-flight window may reach the provider. No Prefect
    # retry, no recovery re-read, and no document beyond that window is called.
    first_calls = sum(vlm.calls.values())
    assert 1 <= first_calls <= rcfg.prefect.max_concurrent_extraction_tasks, dict(vlm.calls)
    docs = all_docs(rcfg)
    assert len(docs) == 5
    assert {d.extraction_status for d in docs} == {ExtractionStatus.pending}
    assert {d.extraction_attempt_count for d in docs} == {0}, "no attempt may be charged"
    assert len(_input_names(rcfg)) == 5, "every file is still in input"

    # The fault is fixed (no restart): the next sync reads everything.
    responses.update(_good())
    res2 = run_sync()
    assert res2["vlm_halted"] is False
    assert res2["vlm_halt_reason"] is None
    docs = all_docs(rcfg)
    assert len(docs) == 5
    assert {d.extraction_status for d in docs} == {ExtractionStatus.valid}
    assert sum(vlm.calls.values()) == first_calls + 5


def test_missing_api_key_stops_after_the_first_document(rcfg, monkeypatch):
    vlm = HttpVLM(monkeypatch, _good())
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("app.core.config.load_env", lambda *a, **kw: None)
    _drop_five(rcfg)

    res = run_sync()

    assert res["vlm_halted"] is True
    assert "not configured" in res["vlm_halt_reason"]
    assert sum(vlm.calls.values()) == 0
    docs = all_docs(rcfg)
    assert len(docs) == 5
    assert {d.extraction_status for d in docs} == {ExtractionStatus.pending}
    assert {d.extraction_attempt_count for d in docs} == {0}
    assert len(_input_names(rcfg)) == 5

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    res2 = run_sync()
    assert res2["vlm_halted"] is False
    docs = all_docs(rcfg)
    assert len(docs) == 5
    assert {d.extraction_status for d in docs} == {ExtractionStatus.valid}
    assert sum(vlm.calls.values()) == 5


def test_a_transient_failure_is_not_read_again_by_recovery_in_the_same_pass(rcfg, monkeypatch):
    """A 500 is retried by the Prefect envelope only; recovery must not repeat it."""
    w = WIDTHS[0]
    vlm = HttpVLM(monkeypatch, {w: ("status", 500)})
    write_pdf(Path(rcfg.paths.input_folder) / "PO0.pdf", w)

    run_sync()

    assert vlm.calls[w] == rcfg.extraction.max_retries + 1, dict(vlm.calls)
    (doc,) = all_docs(rcfg)
    assert doc.extraction_status == ExtractionStatus.pending
