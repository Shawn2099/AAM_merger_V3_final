"""The UI harness must fail closed on the provider key (H-01).

`load_config()` reads `.env` files itself, after import, with `override=False`.
Popping `OPENROUTER_API_KEY` from the child environment therefore did nothing:
a `.env` (the repository's own, or one beside the harness config) put the key
straight back and the "no network, no cost" harness made real VLM calls.

`override=False` respects a variable that is *present*, and an empty string is
present. The harness now sets the variable to `""` and writes an empty `.env`
beside its config. These tests hold that mechanism in place without a network
call: the probe only loads the config and reads the environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]

#: Runs in a clean interpreter (pytest's `tests/conftest.py` dotenv guard does
#: not apply there), exactly as `scripts/ui_test_env.py` runs when built by hand.
_PROBE = """
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
import ui_test_env  # noqa: E402

ui_test_env.prepare_provider_free_environment()

from app.core.config import load_config  # noqa: E402

load_config(sys.argv[2])
print("RESULT=" + json.dumps({"key": os.getenv("OPENROUTER_API_KEY")}))
"""


def _harness_config_with_a_dotenv(tmp_path: Path) -> Path:
    """A harness-shaped config whose sibling `.env` holds a key, as a dev has."""
    root = tmp_path / "ui_test"
    root.mkdir()
    data = yaml.safe_load((REPO / "config.example.yaml").read_text(encoding="utf-8"))
    data["paths"] = {
        "input_folder": str(root / "input"),
        "output_folder": str(root / "output"),
        "quarantine_folder": str(root / "quarantine"),
        "stored_documents_folder": str(root / "stored"),
        "database_path": str(root / "aam_merger.db"),
        "log_folder": str(root / "logs"),
        "combined_folder": str(root / "combined"),
        "archive_folder": str(root / "archive"),
    }
    data["manual_merge"]["temp_folder"] = str(root / "manual_merge_tmp")
    data["schedule"]["poll_enabled"] = False
    data["server"]["allowed_hosts"] = ["*"]
    (root / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    (root / ".env").write_text("OPENROUTER_API_KEY=sk-from-the-file\n", encoding="utf-8")
    return root / "config.yaml"


def test_the_harness_process_sees_no_provider_key(tmp_path):
    """The real bug: the harness environment let a `.env` supply a live key."""
    cfg_path = _harness_config_with_a_dotenv(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    env["PYTHONPATH"] = str(REPO / "src")
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, str(REPO), str(cfg_path)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    result = [line for line in proc.stdout.splitlines() if line.startswith("RESULT=")]
    assert result, proc.stdout
    assert json.loads(result[-1].removeprefix("RESULT="))["key"] == ""
    assert "not set" in proc.stderr, (
        "the harness must log OPENROUTER_API_KEY not set, or it is not failing closed"
    )


def test_the_vlm_refuses_to_run_with_the_harness_key(tmp_path, monkeypatch):
    """With the harness environment, extraction must stop before any client call."""
    cfg_path = _harness_config_with_a_dotenv(tmp_path)
    clean_cwd = tmp_path / "clean"
    clean_cwd.mkdir()
    monkeypatch.chdir(clean_cwd)  # no `.env` of its own to fall back to
    monkeypatch.setenv("OPENROUTER_API_KEY", "")

    from app.core.config import load_config
    from app.services.extraction import _call_vlm

    cfg = load_config(str(cfg_path))
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        _call_vlm(str(tmp_path / "missing.pdf"), "PO", cfg)
