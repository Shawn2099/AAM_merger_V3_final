r"""Test bootstrap — the suite must never write into a real data folder.

Two problems this file removes, both of which only show up when nobody is
watching:

1. **The suite wrote into the repository's data folder.** The paths in
   `config.example.yaml` are relative (`./data/output`), so a test that
   loads it without overriding them writes merged packets — and stored
   documents — into the folder the product uses. On a developer machine
   that is clutter; on the production host it is the live output folder.
   Every configuration the suite loads is now redirected into one
   temporary directory, so a test run leaves nothing behind.

2. **The suite could not start on a fresh clone.** The app refuses to run
   without a `config.yaml` (SPEC 13.1) and `load_config()` reads the
   working directory, so a clone without one failed 54 tests with
   "Config not found". A developer's own `config.yaml` still wins; if
   there is none, the session writes a temporary one.

The redirect is applied by replacing `app.core.config.load_config` at
import time, before pytest imports the test modules — so the test modules
that do `from app.core.config import load_config` pick up the redirect
rather than the original.

**Only placeholder paths are redirected.** A path that points into the
repository's own data folder is a placeholder from `config.example.yaml`
and is moved into the session directory. A path that points anywhere else —
a test's own `tmp_path`, a deployment's `C:\AAM` — is left exactly as it
is, so an explicitly provided configuration keeps the paths its owner
chose.

A test that needs a specific path still sets it: `cfg.paths.output_folder
= tmp_path / "output"` overrides the redirect, exactly as before.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import dotenv
import yaml

from app.core import config as config_module

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: One temporary directory for the whole session. Every path the suite
#: touches lives under it, and it is removed when the session ends.
_SESSION_DIR = Path(tempfile.mkdtemp(prefix="aam-merger-tests-"))

#: The suite must never pick up the developer's own secrets. A real
#: `.env` at the repository root holds a live API key, and the config
#: loader reads it — so a test that asserts "no key -> fail closed" would
#: find the real key and pass straight through the guard it is testing.
#: Tests that need a key set one themselves.
#:
#: The guard blocks the repository's own `.env` and nothing else: a `.env`
#: a test writes in its tmp_path is still loaded, so the loader can be
#: tested for what it does.
_real_load_dotenv = dotenv.load_dotenv


def _guarded_load_dotenv(dotenv_path=None, *args, **kwargs):
    """Load `.env` files the suite wrote; never the repository's own."""
    candidate = Path(dotenv_path) if dotenv_path else Path.cwd() / ".env"
    try:
        resolved = candidate.resolve()
    except OSError:  # pragma: no cover - an unresolvable path is not ours
        return False
    if resolved == _REPO_ROOT or _REPO_ROOT in resolved.parents:
        return False
    return _real_load_dotenv(dotenv_path, *args, **kwargs)


dotenv.load_dotenv = _guarded_load_dotenv
dotenv.dotenv_values = lambda *a, **kw: {}

#: The loader as it was before this file replaced it.
_original_load_config = config_module.load_config


def _session_paths() -> dict[str, str]:
    return {
        "input_folder": str(_SESSION_DIR / "input"),
        "output_folder": str(_SESSION_DIR / "output"),
        "quarantine_folder": str(_SESSION_DIR / "quarantine"),
        "stored_documents_folder": str(_SESSION_DIR / "stored"),
        "combined_folder": str(_SESSION_DIR / "combined"),
        "log_folder": str(_SESSION_DIR / "logs"),
        "database_path": str(_SESSION_DIR / "aam_merger.db"),
    }


def _is_placeholder(path) -> bool:
    """True when a path points into the repository's own data folder.

    Those are the relative placeholders of `config.example.yaml`
    (`./data/output` and friends). They are unsafe: the suite would write
    into the folder the product uses. A path anywhere else — a test's
    `tmp_path`, a deployment's `C:\AAM` — belongs to whoever set it and is
    left alone.
    """
    try:
        resolved = Path(path).resolve()
    except Exception:
        return False
    return resolved == _REPO_ROOT or _REPO_ROOT in resolved.parents


def _redirect_paths(cfg):
    """Move placeholder paths into the session directory."""
    for name, value in _session_paths().items():
        current = getattr(cfg.paths, name, None)
        if current is not None and _is_placeholder(current):
            setattr(cfg.paths, name, value)
    return cfg


def _load_config(path=None):
    """The real loader, with every path redirected into the session dir."""
    return _redirect_paths(_original_load_config(path))


config_module.load_config = _load_config


def _ensure_a_config_file() -> None:
    """Make sure `load_config()` (no argument) has a file to read.

    The app refuses to start without a config (SPEC 13.1) and the loader
    reads the working directory. A developer's own `config.yaml` wins; if
    there is none — a fresh clone — the session writes a temporary one
    whose paths point into the session directory.
    """
    if os.getenv("AAM_CONFIG_PATH"):
        return
    if (_REPO_ROOT / "config.yaml").exists():
        return
    data = yaml.safe_load((_REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    data["paths"] = _session_paths()
    target = _SESSION_DIR / "config.yaml"
    target.write_text(yaml.safe_dump(data), encoding="utf-8")
    os.environ["AAM_CONFIG_PATH"] = str(target)


_ensure_a_config_file()
