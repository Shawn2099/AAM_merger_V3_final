"""Settings page, backend cases B-01..B-13 (docs/OPENCODE_SETTINGS_TEST_PLAN.md section 3).

The promises under test (docs/SETTINGS_SPEC.md): a failed write changes nothing and is
explained; the text editor keeps every byte it was not asked to change, including
non-ASCII comments and Windows line endings; one save wins a race; a forged or oversized
form changes nothing; values are only accepted when the models accept them; the page
never shows a number the service is not running on.
"""

from __future__ import annotations

import copy
import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core import config as config_module
from app.core.config import AppConfig
from app.core.database import get_engine
from app.models.models import AuditAction, AuditLog
from app.services import settings_admin as svc

REPO = Path(__file__).resolve().parents[1]
SECRET = "sk-test-secret-123"


# ------------------------------------------------------------------------ fixtures


@pytest.fixture
def cfg_file(tmp_path, monkeypatch) -> Path:
    """A real config beside nothing else: relative paths resolve into tmp_path."""
    path = tmp_path / "config.yaml"
    text = (REPO / "config.example.yaml").read_text(encoding="utf-8")
    path.write_text(text.replace("poll_enabled: true", "poll_enabled: false"), encoding="utf-8")
    monkeypatch.setenv("AAM_CONFIG_PATH", str(path))
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    return path


@pytest.fixture(autouse=True)
def _isolated_config_memory(monkeypatch):
    """Start and finish each test with no last-good config or bad-file markers left by
    earlier tests in the suite (module-level state in app.core.config)."""
    monkeypatch.setattr(config_module, "_LAST_GOOD", {})
    monkeypatch.setattr(config_module, "_REPORTED_BAD", set())
    yield
    monkeypatch.setattr(config_module, "_LAST_GOOD", {})
    monkeypatch.setattr(config_module, "_REPORTED_BAD", set())


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


def _example_text() -> str:
    return (REPO / "config.example.yaml").read_text(encoding="utf-8")


def _example_data() -> dict:
    return yaml.safe_load(_example_text())


def _get(data: dict, dotted: str):
    node = data
    for part in dotted.split("."):
        node = node[part]
    return node


def _set(data: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def _backups(cfg_file: Path) -> list[Path]:
    return sorted((cfg_file.parent / svc.BACKUP_FOLDER_NAME).glob("config-*.yaml"))


def config_version(html: str) -> str:
    match = re.search(r'name="config_version" value="([0-9a-f]+)"', html)
    assert match, "the page carries no config_version"
    return match.group(1)


# ------------------------------------------------------------------------ B-01


def test_b_01_an_unwritable_file_is_refused_without_a_500(client, cfg_file):
    """A read-only config must be explained, not crash, and must stay untouched."""
    before = cfg_file.read_bytes()
    os.chmod(cfg_file, stat.S_IREAD)
    try:
        if os.access(cfg_file, os.W_OK):
            pytest.skip("this platform/user can still write a read-only file")
        r = client.post(
            "/settings", data=_form(client, cfg_file, schedule__poll_interval_seconds=90)
        )
        assert r.status_code == 409, r.status_code
        assert 'data-testid="settings-error"' in r.text
        assert "could not be written" in r.text
        assert cfg_file.read_bytes() == before
        assert not (cfg_file.parent / ".config.yaml.candidate").exists()
        assert not list(cfg_file.parent.glob("*.candidate"))
        assert _audit_rows(cfg_file) == []
    finally:
        os.chmod(cfg_file, stat.S_IREAD | stat.S_IWRITE)


# ------------------------------------------------------------------------ B-02


def test_b_02_non_ascii_comments_survive_a_save(client, cfg_file):
    """The editor works on text, not a re-dump, so a ₹ comment must come back byte-identical."""
    comment = "# rupee ₹ and dash — and é"
    text = comment + "\n" + cfg_file.read_text(encoding="utf-8")
    cfg_file.write_bytes(text.encode("utf-8"))

    r = client.post("/settings", data=_form(client, cfg_file, schedule__poll_interval_seconds=90))
    assert r.status_code == 200, r.text

    after = cfg_file.read_bytes()
    assert comment.encode("utf-8") in after
    after.decode("utf-8")  # still valid UTF-8
    assert (
        config_module.load_config_file(cfg_file, remember=False).schedule.poll_interval_seconds
        == 90
    )


# ------------------------------------------------------------------------ B-03


def test_b_03_eight_racers_cannot_both_win(cfg_file):
    """One version, eight savers: exactly one wins, the rest are told the file moved."""
    from app.models.base import Base

    Base.metadata.create_all(get_engine(config_module.load_config_file(cfg_file, remember=False)))
    version = svc.file_version(cfg_file)
    intervals = list(range(100, 108))

    def attempt(value: int):
        return svc.save(
            {"config_version": version, "schedule.poll_interval_seconds": str(value)},
            version=version,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, intervals))

    winners = [r for r in results if r.ok and r.changed]
    assert len(winners) == 1, [r.general for r in results]
    for loser in (r for r in results if r is not winners[0]):
        assert loser.general, "a refused save must say why"
        assert (
            "changed by someone else" in loser.general
            or "Another save is in progress" in loser.general
        )

    saved = config_module.load_config_file(cfg_file, remember=False)
    assert saved.schedule.poll_interval_seconds == winners[0].changed[0]["new"]
    assert len(_backups(cfg_file)) == 1
    assert len(_audit_rows(cfg_file)) == 1


# ------------------------------------------------------------------------ B-04


def test_b_04_a_form_larger_than_ours_is_refused(client, cfg_file):
    before = cfg_file.read_bytes()
    data = {f"field_{i}": "1" for i in range(201)}
    r = client.post("/settings", data=data)
    assert r.status_code == 413
    assert cfg_file.read_bytes() == before


# ------------------------------------------------------------------------ B-05

INJECTIONS = (
    "a/b\n  evil: 1",
    "a/b #c",
    'a/b"',
    "a/b' x",
    "{a: b}/c",
    " ",
    "x" * 5000,
)


@pytest.mark.parametrize("raw", INJECTIONS, ids=[f"inject-{i}" for i in range(len(INJECTIONS))])
def test_b_05_a_text_setting_cannot_carry_structure(client, cfg_file, raw):
    """`vlm.model` goes into a YAML line: anything that could add a key is refused."""
    before = cfg_file.read_bytes()
    r = client.post("/settings", data=_form(client, cfg_file, vlm__model=raw))
    assert r.status_code == 422, r.status_code
    assert 'data-testid="field-error"' in r.text
    assert cfg_file.read_bytes() == before
    assert svc.disk_problem(cfg_file) is None


# ------------------------------------------------------------------------ B-06


@pytest.mark.parametrize("raw", ["True", "1", "yes", "ON", ""])
def test_b_06_a_boolean_is_only_on_or_off(client, cfg_file, raw):
    before = cfg_file.read_bytes()
    r = client.post("/settings", data=_form(client, cfg_file, extraction__split_combined=raw))
    assert r.status_code == 422, (raw, r.status_code)
    assert "Choose on or off" in r.text
    assert cfg_file.read_bytes() == before


def test_b_06_a_choice_is_case_sensitive(client, cfg_file):
    before = cfg_file.read_bytes()
    r = client.post("/settings", data=_form(client, cfg_file, logging__level="INFO".lower()))
    assert r.status_code == 422, r.status_code
    assert "Choose one of" in r.text
    assert cfg_file.read_bytes() == before


# ------------------------------------------------------------------------ B-07

REFUSED_INTS = ("٩٠", "9 0", "0x10", "1e3", "1_000", "--5", "", "   ", "1.0", "9" * 5000)


@pytest.mark.parametrize("raw", REFUSED_INTS, ids=[f"int-{i}" for i in range(len(REFUSED_INTS))])
def test_b_07_integers_are_strict(client, cfg_file, raw):
    before = cfg_file.read_bytes()
    r = client.post("/settings", data=_form(client, cfg_file, schedule__poll_interval_seconds=raw))
    assert r.status_code == 422, (raw[:20], r.status_code)
    assert 'data-testid="field-error"' in r.text
    if len(raw) > 12 and raw.strip().isdigit():
        assert "too large" in r.text
    assert cfg_file.read_bytes() == before


@pytest.mark.parametrize("raw", [" 90 ", "+90", "090"])
def test_b_07_a_plain_integer_is_accepted_and_written_plain(client, cfg_file, raw):
    original = cfg_file.read_bytes()
    cfg_file.write_bytes(original)  # a fresh version for each spelling
    stat_now = cfg_file.stat()
    os.utime(cfg_file, ns=(stat_now.st_atime_ns, stat_now.st_mtime_ns + 1_000_000_000))

    r = client.post("/settings", data=_form(client, cfg_file, schedule__poll_interval_seconds=raw))
    assert r.status_code == 200, (raw, r.text)
    data = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert data["schedule"]["poll_interval_seconds"] == 90
    assert "poll_interval_seconds: 90" in cfg_file.read_text(encoding="utf-8")


# ------------------------------------------------------------------------ B-08


def test_b_08_a_broken_file_keeps_the_last_good_settings(tmp_path):
    """`load_config` serves the last good copy, and says so once per edit."""
    import logging

    path = tmp_path / "config.yaml"
    good_v1 = _example_text().replace("poll_interval_seconds: 60", "poll_interval_seconds: 61")
    path.write_text(good_v1, encoding="utf-8")

    # A handler on the module's own logger: caplog relies on propagation, which an earlier
    # test in a full run may have switched off (the log setup does).
    seen: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    cfg_logger = logging.getLogger(config_module.__name__)
    keep = _Keep(level=logging.ERROR)
    was_disabled = cfg_logger.disabled
    cfg_logger.disabled = False  # dictConfig in an earlier test can disable existing loggers
    cfg_logger.addHandler(keep)
    try:
        assert config_module.load_config(path).schedule.poll_interval_seconds == 61

        path.write_text("paths: [broken: \n  x: {\n", encoding="utf-8")
        for _ in range(5):
            assert config_module.load_config(path).schedule.poll_interval_seconds == 61

        not_valid = [r for r in seen if "not valid" in r.getMessage()]
        assert len(not_valid) == 1, [r.getMessage() for r in not_valid]
    finally:
        cfg_logger.removeHandler(keep)
        cfg_logger.disabled = was_disabled

    good_v2 = _example_text().replace("poll_interval_seconds: 60", "poll_interval_seconds: 62")
    path.write_text(good_v2, encoding="utf-8")
    assert config_module.load_config(path).schedule.poll_interval_seconds == 62

    # A path that never loaded successfully is not excused: fail fast (SPEC 13.1).
    never_good = tmp_path / "never_good.yaml"
    never_good.write_text("paths: nonsense\n", encoding="utf-8")
    with pytest.raises(ValueError):
        config_module.load_config(never_good)


# ------------------------------------------------------------------------ B-09

#: The four vendor line-number switches are read only through
#: `LineNumberFallbackConfig.rules()` (core/config.py, which the plan excludes),
#: so their own attribute names never appear in a second file. They are still
#: proven read below, through the consumer of `rules()`.
INDIRECT_KEYS = frozenset(
    {
        "matching.line_number_fallback.step10",
        "matching.line_number_fallback.step10_reverse",
        "matching.line_number_fallback.release_suffix",
        "matching.line_number_fallback.release_suffix_reverse",
    }
)
EXCLUDED = (
    Path("src/app/core/config.py"),
    Path("src/app/services/settings_admin.py"),
    Path("src/app/api/routes/settings.py"),
)


def test_b_09_every_setting_is_visible_to_it_and_read_somewhere():
    """A setting that IT cannot see in the example, or that no code reads, is a trap."""
    example = _example_data()
    sources = [
        p
        for p in list((REPO / "src" / "app").rglob("*.py")) + list((REPO / "scripts").rglob("*.py"))
        if p.relative_to(REPO) not in EXCLUDED
    ]
    texts = {p: p.read_text(encoding="utf-8", errors="ignore") for p in sources}

    unmatched: list[str] = []
    for group in svc.GROUPS:
        for setting in group.settings:
            try:
                _get(example, setting.key)
            except KeyError:
                pytest.fail(f"{setting.key} is not in config.example.yaml")

            segment = "level" if setting.key == "logging.level" else setting.key.split(".")[-1]
            pattern = re.compile(r"\b" + re.escape(segment) + r"\b")
            if any(pattern.search(text) for text in texts.values()):
                continue
            unmatched.append(setting.key)

    # The known indirect reads still have to be read: `rules()` is what consumes them.
    unexpected = set(unmatched) - INDIRECT_KEYS
    assert not unexpected, f"settings read nowhere: {sorted(unexpected)}"
    if unmatched:
        readers = [p for p, text in texts.items() if ".rules()" in text]
        assert readers, f"no consumer of rules() for {sorted(unmatched)}"


# ------------------------------------------------------------------------ B-10


def _different_value(setting, current):
    """A different valid value for `setting` (toggle, next choice, another id, or +1)."""
    if setting.kind == "bool":
        return not bool(current)
    if setting.kind == "choice":
        choices = list(setting.choices)
        return choices[(choices.index(current) + 1) % len(choices)]
    if setting.kind == "text":
        return "openai/gpt-6-mini" if current != "openai/gpt-6-mini" else "openai/gpt-6-luna"
    lo, hi = svc._model_bounds(setting)
    value = int(current) + 1
    if hi is not None and value > hi:
        value = int(lo if lo is not None else hi - 1)
    return value


def _other_value(setting):
    return _different_value(setting, _get(_example_data(), setting.key))


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_b_10_every_setting_round_trips_on_the_real_example_file(newline):
    text = _example_text()
    if newline == "\r\n":
        text = text.replace("\n", "\r\n")
    lines_before = text.splitlines()
    comments_before = [line for line in lines_before if line.lstrip().startswith("#")]

    for group in svc.GROUPS:
        for setting in group.settings:
            value = _other_value(setting)
            out = svc.apply_changes(text, {setting.key: value})
            parsed = yaml.safe_load(out)
            assert _get(parsed, setting.key) == value, setting.key
            lines_after = out.splitlines()
            assert len(lines_after) == len(lines_before), setting.key
            assert [
                line for line in lines_after if line.lstrip().startswith("#")
            ] == comments_before, setting.key


# ------------------------------------------------------------------------ B-11


def test_b_11_the_repo_config_is_editable_if_it_exists():
    path = REPO / "config.yaml"
    if not path.exists():
        pytest.skip("the repo has no config.yaml (it is gitignored)")
    text = path.read_text(encoding="utf-8")
    for newline in ("\n", "\r\n"):
        variant = text if newline == "\n" else text.replace("\r\n", "\n").replace("\n", "\r\n")
        lines_before = variant.splitlines()
        comments_before = [line for line in lines_before if line.lstrip().startswith("#")]
        for group in svc.GROUPS:
            for setting in group.settings:
                try:
                    current = _get(yaml.safe_load(variant), setting.key)
                except KeyError:
                    pytest.skip(f"the repo config lacks {setting.key}")
                value = _different_value(setting, current)
                out = svc.apply_changes(variant, {setting.key: value})  # never a _LayoutError
                assert _get(yaml.safe_load(out), setting.key) == value, setting.key
                lines_after = out.splitlines()
                assert len(lines_after) == len(lines_before), setting.key
                assert [
                    line for line in lines_after if line.lstrip().startswith("#")
                ] == comments_before, setting.key


# ------------------------------------------------------------------------ B-12


def test_b_12_the_page_bounds_are_the_model_bounds(tmp_path, monkeypatch):
    """Every limit the page shows must be a limit the models agree with."""
    monkeypatch.chdir(tmp_path)
    base = _example_data()

    for group in svc.GROUPS:
        for setting in group.settings:
            if setting.kind != "int":
                continue
            lo, hi = svc._model_bounds(setting)

            for value in (lo, hi):
                if value is None:
                    continue
                data = copy.deepcopy(base)
                _set(data, setting.key, value)
                try:
                    AppConfig.model_validate(data)
                except ValidationError as exc:
                    # Only a cross-field rule may refuse a value the page allows.
                    assert setting.key in str(exc) or "field" in str(exc).lower(), (
                        setting.key,
                        value,
                        str(exc),
                    )

            if lo is not None:
                assert svc._parse(setting, str(lo - 1))[1] is not None, setting.key
            if hi is not None:
                assert svc._parse(setting, str(hi + 1))[1] is not None, setting.key


# ------------------------------------------------------------------------ B-13


def test_b_13_a_raised_lock_timeout_is_what_the_page_shows(client, cfg_file):
    """The loader raises a too-short lock timeout; the page must not show 300."""
    text = cfg_file.read_text(encoding="utf-8").replace(
        "po_set_lock_timeout_seconds: 900", "po_set_lock_timeout_seconds: 300"
    )
    cfg_file.write_text(text, encoding="utf-8")

    html = client.get("/settings").text
    assert config_version(html) == svc.file_version(cfg_file)
    row = re.search(
        r'data-testid="field-concurrency\.po_set_lock_timeout_seconds"[\s\S]{0,200}?value="(\d+)"',
        html,
    )
    assert row is not None
    assert row.group(1) == "802", row.group(1)
    assert 'value="300"' not in html

    before = cfg_file.read_bytes()
    r = client.post("/settings", data=_form(client, cfg_file))
    assert r.status_code == 200
    assert "Nothing was changed" in r.text
    assert cfg_file.read_bytes() == before
    assert not (cfg_file.parent / svc.BACKUP_FOLDER_NAME).exists()
    assert _audit_rows(cfg_file) == []
