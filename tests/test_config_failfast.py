"""Config fail-fast (SPEC 13.1, BLOCKER-9, W-13).

A missing config.yaml must raise, never silently fall back to the committed
example. A missing VLM API key must log a warning, never pass silently.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest


def test_missing_config_raises(tmp_path, monkeypatch):
    """No config.yaml + only a decoy example present → FileNotFoundError."""
    from app.core.config import load_config

    shutil.copy(Path("config.example.yaml"), tmp_path / "config.example.yaml")
    monkeypatch.setenv("AAM_CONFIG_PATH", str(tmp_path / "nope.yaml"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        load_config()


def test_missing_api_key_warns(tmp_path, monkeypatch, caplog):
    """Unset API key env var → warning logged (not silent pass)."""
    from app.core.config import load_config

    cfg_path = tmp_path / "cfg.yaml"
    shutil.copy(Path("config.example.yaml"), cfg_path)
    monkeypatch.setenv("AAM_CONFIG_PATH", str(cfg_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        load_config()
    assert any("OPENROUTER_API_KEY" in r.message for r in caplog.records)


def test_phase1_defaults_from_example():
    """Phase 1: example ships team decisions (gpt-6-luna, en_IN, current order)."""
    from app.core.config import load_config

    cfg = load_config("config.example.yaml")
    assert cfg.vlm.model == "openai/gpt-6-luna"
    assert cfg.matching.locale == "en_IN"
    assert cfg.merge.legal_order == ["SI", "DN", "PO", "COMBINED", "SHIPPING", "CUSTOMS"]


def test_bad_locale_fails_fast(tmp_path, monkeypatch):
    """Unknown babel locale → validation error, never silent fallback."""
    import yaml

    from app.core.config import load_config

    data = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    data["matching"]["locale"] = "xx_FAKE"
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("AAM_CONFIG_PATH", str(cfg_path))
    with pytest.raises(Exception, match="locale"):
        load_config()


def test_bad_legal_order_fails_fast(tmp_path, monkeypatch):
    """Unknown doc type in legal_order → validation error."""
    import yaml

    from app.core.config import load_config

    data = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    data["merge"]["legal_order"] = ["SI", "NOPE"]
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("AAM_CONFIG_PATH", str(cfg_path))
    with pytest.raises(Exception, match="legal_order"):
        load_config()


def test_ordered_docs_honors_custom_order():
    """Phase 1: merge order editable via config without code change."""
    from types import SimpleNamespace

    from app.services.merge import _ordered_docs

    docs = [
        SimpleNamespace(doc_type="PO", original_filename="po.pdf"),
        SimpleNamespace(doc_type="CUSTOMS", original_filename="c.pdf"),
        SimpleNamespace(doc_type="SI", original_filename="si.pdf"),
    ]
    ps = SimpleNamespace(documents=docs, id=1)
    default = [d.original_filename for d in _ordered_docs(ps)]
    assert default == ["si.pdf", "po.pdf", "c.pdf"]

    cfg = SimpleNamespace(merge=SimpleNamespace(legal_order=["PO", "SI", "CUSTOMS"]))
    custom = [d.original_filename for d in _ordered_docs(ps, cfg)]
    assert custom == ["po.pdf", "si.pdf", "c.pdf"]
