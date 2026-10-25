"""Settings page: a change is validated before it is written, and a bad one never reaches the file.

The promises under test (docs/SETTINGS_SPEC.md): every shown value is the running value; a value
the models would refuse is refused on the page with a message beside the field; the file on disk
is replaced only by a verified copy and a backup is kept; comments in config.yaml survive; a
config that breaks on disk does not take the running service down.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import config as config_module
from app.core.database import get_engine
from app.models.models import AuditAction, AuditLog
from app.services import settings_admin as svc

REPO = Path(__file__).resolve().parents[1]
SECRET = "sk-test-secret-123"


@pytest.fixture
def cfg_file(tmp_path, monkeypatch) -> Path:
    """A real config beside nothing else: relative paths resolve into tmp_path."""
    path = tmp_path / "config.yaml"
    text = (REPO / "config.example.yaml").read_text(encoding="utf-8")
    path.write_text(text.replace("poll_enabled: true", "poll_enabled: false"), encoding="utf-8")
    monkeypatch.setenv("AAM_CONFIG_PATH", str(path))
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    return path


@pytest.fixture
def client(cfg_file):
    from app.main import create_app

    with TestClient(create_app(allowed_hosts=["*"])) as c:
        yield c


def _form(client: TestClient, cfg_file: Path, **overrides: object) -> dict[str, str]:
    """What the browser would post: the page's own values, with some changed."""
    model = svc.page_model(config_module.load_config_file(cfg_file, remember=False))
    data = {"config_version": svc.file_version(cfg_file)}
    for group in model["groups"]:
        for s in group["settings"]:
            value = s["value"]
            data[s["key"]] = str(value).lower() if isinstance(value, bool) else str(value)
    for key, value in overrides.items():
        data[key.replace("__", ".")] = str(value).lower() if isinstance(value, bool) else str(value)
    return data


def _audit_rows(cfg_file: Path) -> list[AuditLog]:
    cfg = config_module.load_config_file(cfg_file, remember=False)
    with Session(get_engine(cfg)) as s:
        return list(s.query(AuditLog).filter(AuditLog.action == AuditAction.settings_changed).all())


# ------------------------------------------------------------------------ the page


def test_page_shows_every_setting_and_never_the_api_key(client):
    html = client.get("/settings").text
    keys = [s.key for g in svc.GROUPS for s in g.settings]
    assert html.count('data-testid="setting-row"') == len(keys)
    for key in keys:
        assert f'data-key="{key}"' in html
    assert SECRET not in html
    assert "The API key" in html and "is set" in html
    assert html.count('data-testid="readonly-row"') == len(svc.READ_ONLY)
    assert 'data-testid="settings-disk-problem"' not in html


def test_the_page_values_are_the_running_values(client, cfg_file):
    data = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    html = client.get("/settings").text
    assert f'value="{data["schedule"]["poll_interval_seconds"]}"' in html
    assert f'value="{data["vlm"]["raster_dpi"]}"' in html


def test_posting_the_page_unchanged_changes_nothing(client, cfg_file):
    before = cfg_file.read_bytes()
    r = client.post("/settings", data=_form(client, cfg_file))
    assert r.status_code == 200
    assert "Nothing was changed" in r.text
    assert cfg_file.read_bytes() == before
    assert not (cfg_file.parent / svc.BACKUP_FOLDER_NAME).exists()
    assert _audit_rows(cfg_file) == []


def test_every_number_accepts_its_own_bounds_and_refuses_one_beyond(cfg_file):
    """The limits on the page are the limits of the models, so a form can never trip a model."""
    for group in svc.GROUPS:
        for setting in group.settings:
            if setting.kind != "int":
                continue
            lo, hi = svc._model_bounds(setting)
            if lo is not None:
                assert svc._parse(setting, str(lo))[1] is None, setting.key
                assert svc._parse(setting, str(lo - 1))[1] is not None, setting.key
            if hi is not None:
                assert svc._parse(setting, str(hi))[1] is None, setting.key
                assert svc._parse(setting, str(hi + 1))[1] is not None, setting.key


def test_no_attempt_ever_leaves_an_invalid_file(client, cfg_file):
    """Whatever is submitted, the file on disk still starts the service."""
    attempts = []
    for group in svc.GROUPS:
        for s in group.settings:
            if s.kind == "int":
                lo, hi = svc._model_bounds(s)
                attempts += [(s.key, lo), (s.key, hi)]
            if s.kind == "choice":
                attempts += [(s.key, c) for c in s.choices]
            if s.kind == "bool":
                attempts += [(s.key, "true"), (s.key, "false")]
    for key, value in attempts:
        if value is None:
            continue
        r = client.post(
            "/settings", data=_form(client, cfg_file, **{key.replace(".", "__"): value})
        )
        assert r.status_code in (200, 409, 422), (key, value, r.status_code)
        assert svc.disk_problem(cfg_file) is None, (key, value)
        assert client.get("/settings").status_code == 200


# ------------------------------------------------------------------------ saving


def test_a_valid_change_is_saved_with_comments_backup_and_audit(client, cfg_file):
    before = cfg_file.read_text(encoding="utf-8")
    r = client.post("/settings", data=_form(client, cfg_file, schedule__poll_interval_seconds=90))
    assert r.status_code == 200
    assert 'data-testid="settings-saved"' in r.text and "60 → <strong>90" in r.text
    after = cfg_file.read_text(encoding="utf-8")
    assert "poll_interval_seconds: 90" in after
    # Only that line differs: every comment and every other value is exactly as it was.
    assert [a for a in after.splitlines() if a not in before.splitlines()] == [
        "  poll_interval_seconds: 90"
    ]
    assert len(after.splitlines()) == len(before.splitlines())
    backups = list((cfg_file.parent / svc.BACKUP_FOLDER_NAME).glob("config-*.yaml"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == before
    rows = _audit_rows(cfg_file)
    assert len(rows) == 1
    detail = json.loads(rows[0].detail)
    assert detail["changes"] == {"schedule.poll_interval_seconds": {"old": 60, "new": 90}}
    assert detail["backup"] == backups[0].name
    assert "Settings Changed" in client.get("/audit").text
    assert config_module.load_config(cfg_file).schedule.poll_interval_seconds == 90


def test_several_kinds_of_value_round_trip(client, cfg_file):
    r = client.post(
        "/settings",
        data=_form(
            client,
            cfg_file,
            vlm__reasoning_effort="medium",
            vlm__model="openai/gpt-6-mini",
            extraction__split_combined=True,
            matching__line_number_fallback__step10=False,
            logging__level="DEBUG",
        ),
    )
    assert r.status_code == 200, r.text
    saved = config_module.load_config_file(cfg_file, remember=False)
    assert saved.vlm.reasoning_effort == "medium"
    assert saved.vlm.model == "openai/gpt-6-mini"
    assert saved.extraction.split_combined is True
    assert saved.matching.line_number_fallback.step10 is False
    assert saved.matching.line_number_fallback.step10_reverse is True
    assert saved.logging.level == "DEBUG"
    assert 'level: "DEBUG"' in cfg_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"schedule__poll_interval_seconds": 5}, "at least 15"),
        ({"schedule__poll_interval_seconds": 99999}, "at most 3600"),
        ({"schedule__poll_interval_seconds": "abc"}, "whole number"),
        ({"schedule__poll_interval_seconds": ""}, "Enter a number"),
        ({"schedule__poll_interval_seconds": "1.5"}, "whole number"),
        ({"vlm__raster_dpi": 10}, "at least 72"),
        ({"vlm__reasoning_effort": "extreme"}, "Choose one of"),
        ({"vlm__model": "gpt"}, "provider/name"),
        ({"vlm__model": ""}, "cannot be empty"),
        ({"extraction__split_combined": "maybe"}, "on or off"),
        ({"extraction__max_retries": 9}, "at most 5"),
        ({"logging__max_file_size_mb": 0}, "at least 1"),
    ],
)
def test_a_bad_value_is_refused_beside_its_field_and_nothing_is_written(
    client, cfg_file, override, fragment
):
    before = cfg_file.read_bytes()
    r = client.post("/settings", data=_form(client, cfg_file, **override))
    assert r.status_code == 422
    assert 'data-testid="field-error"' in r.text and fragment in r.text
    assert 'data-testid="settings-saved"' not in r.text
    assert cfg_file.read_bytes() == before
    assert not (cfg_file.parent / svc.BACKUP_FOLDER_NAME).exists()
    assert _audit_rows(cfg_file) == []


def test_one_bad_value_blocks_the_whole_save(client, cfg_file):
    before = cfg_file.read_bytes()
    r = client.post(
        "/settings",
        data=_form(client, cfg_file, schedule__poll_interval_seconds=120, vlm__raster_dpi=10),
    )
    assert r.status_code == 422
    assert cfg_file.read_bytes() == before
    # The operator's other input is still in the form.
    assert 'value="120"' in r.text


def test_a_timeout_the_stale_limit_cannot_cover_is_refused_with_the_reason(client, cfg_file):
    before = cfg_file.read_bytes()
    r = client.post("/settings", data=_form(client, cfg_file, vlm__request_timeout_seconds=600))
    assert r.status_code in (409, 422)
    assert "stale_processing_seconds" in r.text
    assert cfg_file.read_bytes() == before
    ok = client.post(
        "/settings",
        data=_form(
            client,
            cfg_file,
            vlm__request_timeout_seconds=600,
            extraction__stale_processing_seconds=5000,
            concurrency__po_set_lock_timeout_seconds=5000,
        ),
    )
    assert ok.status_code == 200, ok.text
    assert (
        config_module.load_config_file(cfg_file, remember=False).vlm.request_timeout_seconds == 600
    )


def test_a_lock_timeout_that_would_be_quietly_raised_is_refused_instead(client, cfg_file):
    """The page must not say 120 while the service runs on 802."""
    before = cfg_file.read_bytes()
    r = client.post(
        "/settings", data=_form(client, cfg_file, concurrency__po_set_lock_timeout_seconds=120)
    )
    assert r.status_code == 422
    assert "Must be at least" in r.text
    assert cfg_file.read_bytes() == before


def test_a_file_changed_by_someone_else_is_not_overwritten(client, cfg_file):
    form = _form(client, cfg_file, schedule__poll_interval_seconds=120)
    other = cfg_file.read_text(encoding="utf-8").replace(
        "poll_interval_seconds: 60", "poll_interval_seconds: 45"
    )
    cfg_file.write_text(other, encoding="utf-8")
    r = client.post("/settings", data=form)
    assert r.status_code == 409
    assert "changed by someone else" in r.text
    assert cfg_file.read_text(encoding="utf-8") == other


def test_a_failed_read_back_restores_the_previous_file(client, cfg_file, monkeypatch):
    before = cfg_file.read_bytes()
    real = config_module.load_config_file

    def fail_on_the_saved_file(path, *, remember=True):
        if remember and Path(path) == cfg_file:
            raise ValueError("simulated failure after the replace")
        return real(path, remember=remember)

    monkeypatch.setattr(svc.config_module, "load_config_file", fail_on_the_saved_file)
    r = client.post("/settings", data=_form(client, cfg_file, schedule__poll_interval_seconds=120))
    assert r.status_code == 409
    assert "previous settings were restored" in r.text
    assert cfg_file.read_bytes() == before
    assert _audit_rows(cfg_file) == []


def test_two_saves_at_once_cannot_both_win(cfg_file):
    """The second save sees a new file version and is refused, never merged blindly."""
    form_a = {"config_version": svc.file_version(cfg_file), "schedule.poll_interval_seconds": "100"}
    form_b = {"config_version": svc.file_version(cfg_file), "schedule.poll_interval_seconds": "200"}
    first = svc.save(form_a, version=form_a.pop("config_version"))
    second = svc.save(form_b, version=form_b.pop("config_version"))
    assert first.ok and first.changed
    assert second.general and "changed by someone else" in second.general
    assert (
        config_module.load_config_file(cfg_file, remember=False).schedule.poll_interval_seconds
        == 100
    )


def test_switching_the_watcher_on_starts_it_without_a_restart(client, cfg_file, monkeypatch):
    started = []
    sentinel = object()

    def fake_start(cfg):
        started.append(cfg)
        return sentinel

    monkeypatch.setattr("app.services.poller.start_if_enabled", fake_start)
    client.app.state.poller = None
    r = client.post("/settings", data=_form(client, cfg_file, schedule__poll_enabled=True))
    assert r.status_code == 200
    assert len(started) == 1 and client.app.state.poller is sentinel
    client.app.state.poller = None  # the lifespan must not try to stop a stand-in


# ------------------------------------------------------------------------ a broken file


def test_a_file_broken_while_running_keeps_the_service_up_and_says_so(client, cfg_file):
    assert client.get("/dashboard").status_code == 200
    good = cfg_file.read_bytes()
    cfg_file.write_text("paths: [this is: not valid\n  vlm: {", encoding="utf-8")
    assert client.get("/dashboard").status_code == 200
    page = client.get("/settings")
    assert page.status_code == 200
    assert 'data-testid="settings-disk-problem"' in page.text
    r = client.post("/settings", data={"config_version": svc.file_version(cfg_file)})
    assert r.status_code == 409
    assert "not valid" in r.text
    assert cfg_file.read_text(encoding="utf-8").startswith("paths: [this is")
    cfg_file.write_bytes(good)
    assert 'data-testid="settings-disk-problem"' not in client.get("/settings").text


def test_a_service_started_on_a_broken_file_still_refuses_to_start(tmp_path):
    """Fail fast at start-up is kept (SPEC 13.1): there is no earlier good copy to run on."""
    broken = tmp_path / "never_good.yaml"
    broken.write_text("paths: nonsense\n", encoding="utf-8")
    with pytest.raises(ValueError):
        config_module.load_config(broken)


# ------------------------------------------------------------------------ the text editor


BASE = """\
# top comment
paths:
  input_folder: "./in"   # inline note
schedule:
  # keep this comment
  poll_enabled: false
  poll_interval_seconds: 60  # seconds
matching:
  fuzzy_description_threshold: 85
  line_number_fallback:
    step10: true
    step10_reverse: true
logging:
  level: "INFO"
"""


def test_editor_changes_only_the_target_line_and_keeps_comments():
    out = svc.apply_changes(BASE, {"schedule.poll_interval_seconds": 90})
    assert out == BASE.replace(
        "poll_interval_seconds: 60  # seconds", "poll_interval_seconds: 90  # seconds"
    )


def test_editor_does_not_confuse_a_key_with_one_that_starts_the_same():
    out = svc.apply_changes(BASE, {"matching.line_number_fallback.step10": False})
    parsed = yaml.safe_load(out)["matching"]["line_number_fallback"]
    assert parsed == {"step10": False, "step10_reverse": True}


def test_editor_quotes_strings_and_writes_booleans_and_ints():
    out = svc.apply_changes(BASE, {"logging.level": "DEBUG", "schedule.poll_enabled": True})
    parsed = yaml.safe_load(out)
    assert parsed["logging"]["level"] == "DEBUG" and parsed["schedule"]["poll_enabled"] is True
    assert 'level: "DEBUG"' in out and "poll_enabled: true" in out


def test_editor_adds_a_missing_key_to_its_section_and_a_missing_section():
    out = svc.apply_changes(BASE, {"logging.max_file_size_mb": 20, "vlm.raster_dpi": 200})
    parsed = yaml.safe_load(out)
    assert parsed["logging"] == {"level": "INFO", "max_file_size_mb": 20}
    assert parsed["vlm"] == {"raster_dpi": 200}
    assert out.startswith("# top comment\n")


def test_editor_adds_a_missing_nested_block():
    text = "matching:\n  locale: en_IN\n"
    out = svc.apply_changes(text, {"matching.line_number_fallback.step10": False})
    assert yaml.safe_load(out)["matching"] == {
        "locale": "en_IN",
        "line_number_fallback": {"step10": False},
    }


def test_editor_keeps_windows_line_endings():
    text = BASE.replace("\n", "\r\n")
    out = svc.apply_changes(text, {"schedule.poll_interval_seconds": 90, "vlm.raster_dpi": 200})
    assert "\r\n" in out and "\n" not in out.replace("\r\n", "")
    assert yaml.safe_load(out)["schedule"]["poll_interval_seconds"] == 90


def test_editor_refuses_a_layout_it_cannot_edit_safely():
    flow = "schedule: {poll_enabled: false, poll_interval_seconds: 60}\n"
    with pytest.raises(svc._LayoutError):
        svc.apply_changes(flow, {"schedule.poll_interval_seconds": 90})


def test_only_the_listed_settings_can_be_posted(client, cfg_file):
    """A forged field for a path or the API key is ignored, never written."""
    before = cfg_file.read_bytes()
    data = _form(client, cfg_file)
    data["paths.input_folder"] = "C:/Windows"
    data["vlm.api_key_env_var"] = "SOMETHING_ELSE"
    data["server.allowed_hosts"] = "evil"
    r = client.post("/settings", data=data)
    assert r.status_code == 200
    assert cfg_file.read_bytes() == before
