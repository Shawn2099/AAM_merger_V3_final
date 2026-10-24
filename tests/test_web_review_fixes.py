"""Opus web review (2026-10-24): each defect the reviewers proved, now guarded."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import AppConfig, load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
from app.models.base import Base


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "web.db"
    for name in ("input", "output", "quarantine", "stored", "logs"):
        (tmp_path / name).mkdir()
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.log_folder = tmp_path / "logs"
    cfg.schedule.poll_enabled = False
    Base.metadata.create_all(get_engine(cfg))
    for target in (
        "app.api.routes.sync.load_config",
        "app.api.routes.po_sets.load_config",
        "app.api.routes.dashboard.load_config",
        "app.flows.sync.load_config",
        "app.services.sync_lock.load_config",
        "app.main.load_config",
    ):
        monkeypatch.setattr(target, lambda path=None: cfg)
    from app.main import app

    with TestClient(app) as client:
        yield client, cfg


def _held_dn(cfg) -> int:
    with Session(get_engine(cfg)) as s:
        doc = Document(
            sha256_hash="d" * 64,
            original_filename="held-dn.pdf",
            stored_path=str(Path(cfg.paths.stored_documents_folder) / "held-dn.pdf"),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.valid,
        )
        s.add(doc)
        s.commit()
        return doc.id


# ------------------------------------------------------------------ reclassify vs a busy set
def test_reclassify_is_refused_with_409_while_the_target_set_is_locked(env):
    from datetime import UTC, datetime

    client, cfg = env
    doc_id = _held_dn(cfg)
    with Session(get_engine(cfg)) as s:
        ps = POSet(
            po_no_normalized="7000999",
            status=POSetStatus.pending,
            locked_by_action="force_merge",
            locked_at=datetime.now(UTC),
        )
        s.add(ps)
        s.commit()
    r = client.post(
        f"/unclassified/{doc_id}/reclassify",
        data={"doc_type": "DN", "po_no": "7000999"},
        follow_redirects=False,
    )
    assert r.status_code == 409 and "busy" in r.text.lower()
    with Session(get_engine(cfg)) as s:
        doc = s.get(Document, doc_id)
        assert doc.po_set_id is None, "nothing may be attached to a set mid-merge"
        assert doc.doc_type == DocType.UNKNOWN, "and nothing about the document changed"


# ------------------------------------------------------------------ config
def test_a_per_po_lock_timeout_shorter_than_one_extraction_is_raised_not_refused():
    """An older config (default was 300 s) keeps working after an upgrade, with a warning."""
    data = load_config("config.example.yaml").model_dump(mode="json")
    data["concurrency"]["po_set_lock_timeout_seconds"] = 300  # below the 742 s worst case
    lg = logging.getLogger("app.core.config")
    lg.disabled = False
    seen: list[str] = []

    class _H(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    h = _H(logging.WARNING)
    lg.addHandler(h)
    try:
        cfg = AppConfig.model_validate(data)
    finally:
        lg.removeHandler(h)
    assert cfg.concurrency.po_set_lock_timeout_seconds == 742 + 60
    assert any("po_set_lock_timeout_seconds" in m for m in seen)


def test_a_zero_or_tiny_lock_timeout_is_refused():
    data = load_config("config.example.yaml").model_dump(mode="json")
    for bad in (0, 30, -5):
        data["concurrency"]["po_set_lock_timeout_seconds"] = bad
        with pytest.raises(ValueError):
            AppConfig.model_validate(data)


def test_the_example_config_lock_timeout_is_valid_and_long_enough():
    assert load_config("config.example.yaml").concurrency.po_set_lock_timeout_seconds == 900


# ------------------------------------------------------------------ startup: orphaned sync sidecar
def test_a_sidecar_left_by_a_stopped_service_is_cleared_at_startup(tmp_path, monkeypatch):
    from app.services import sync_lock

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "orphan.db"
    for name in ("input", "output", "quarantine", "stored", "logs"):
        (tmp_path / name).mkdir()
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.log_folder = tmp_path / "logs"
    cfg.schedule.poll_enabled = False
    Base.metadata.create_all(get_engine(cfg))
    for target in ("app.services.sync_lock.load_config", "app.main.load_config"):
        monkeypatch.setattr(target, lambda path=None: cfg)
    lock = sync_lock.get_sync_lock()
    sidecar = sync_lock._sidecar_for(Path(lock.lock_file))
    sidecar.write_text(str(time.time()))  # a "running" marker nobody owns
    assert sync_lock._is_sync_running() is True
    from app.main import app

    with TestClient(app):
        assert sync_lock._is_sync_running() is False


# ------------------------------------------------------------------ set page while a sync runs
def test_the_set_page_disables_actions_and_says_why_while_a_sync_runs(env, monkeypatch):
    client, cfg = env
    with Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized="7000998", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        ps_id = ps.id
    page = client.get(f"/po_sets/{ps_id}/view").text
    assert "sync-running-note" not in page

    monkeypatch.setattr("app.api.routes.dashboard._sync_running_state", lambda: True)
    page = client.get(f"/po_sets/{ps_id}/view").text
    assert "sync-running-note" in page
    for testid in (
        "btn-toggle-customs",
        "btn-merge-now",
        "btn-rematch",
        "btn-reextract",
        "btn-force-merge",
    ):
        # The opening tag can contain "=>" inside an hx-on handler, so take the whole element
        # and look only at the part before its first child.
        element = re.search(
            rf'<button(?:(?!</button>).)*?data-testid="{testid}".*?</button>', page, re.S
        )
        assert element, testid
        tag = re.match(r"(.*?)<span", element.group(0), re.S)
        assert "disabled" in tag.group(1).split(), f"{testid} must be disabled while a sync runs"


# ------------------------------------------------------------------ dashboard poll refreshes the cards
def test_the_table_poll_also_refreshes_the_kpi_cards_and_tabs(env):
    client, cfg = env
    with Session(get_engine(cfg)) as s:
        s.add(POSet(po_no_normalized="7000997", status=POSetStatus.merged))
        s.commit()
    body = client.get("/dashboard/table").text
    assert re.search(r'id="kpi-grid"[^>]*hx-swap-oob="outerHTML"', body)
    assert re.search(r'id="status-tabs"[^>]*hx-swap-oob="outerHTML"', body)
    # the full page carries the same elements, but not marked out-of-band
    full = client.get("/dashboard").text
    assert 'id="kpi-grid"' in full and "hx-swap-oob" not in full.split('id="kpi-grid"', 1)[1][:80]
    assert full.count('id="kpi-grid"') == 1 and full.count('id="status-tabs"') == 1
