"""Settings page in a browser: ST-01..ST-20 (docs/OPENCODE_SETTINGS_TEST_PLAN.md section 2).

A settings test CHANGES `config.yaml`, so this module builds its own throwaway harness
environment (exactly like `tests/ui/conftest.py::_start_env`) instead of mutating the
shared `ui_env`. `reset_config` puts the file, the backup folder, the save lock and the
`settings_changed` audit rows back between tests, so the cases stay independent.

Run it with:

    RUN_E2E=1 uv run python -m pytest tests/ui/test_ui_settings.py -o addopts= -p no:cacheprovider
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from playwright.sync_api import Page, expect

from app.core.config import load_config_file
from app.services import settings_admin as svc

REPO = Path(__file__).resolve().parents[2]

SAVE = '[data-testid="settings-save"]'
RESET = '[data-testid="settings-reset"]'
FORM = '[data-testid="settings-form"]'
SAVED = '[data-testid="settings-saved"]'
NOCHANGE = '[data-testid="settings-nochange"]'
ERROR = '[data-testid="settings-error"]'
DISK = '[data-testid="settings-disk-problem"]'
POLLER = '[data-testid="poller-state"]'
INTERVAL = "schedule.poll_interval_seconds"

BADGE_TEXT = {"live": "Live", "next_sync": "Next sync", "restart": "Needs restart"}


# ------------------------------------------------------------------------ environment


def _load_env_module():
    spec = importlib.util.spec_from_file_location(
        "settings_ui_test_env", REPO / "scripts" / "ui_test_env.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stop(proc) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - a wedged server
        proc.kill()
    if proc.stdout is not None:
        proc.stdout.close()


def _drain(proc) -> None:
    """Read the child's output: a full pipe would block the server mid-run."""
    stream = proc.stdout
    if stream is None:
        return

    def pump() -> None:
        with contextlib.suppress(ValueError, OSError):
            for _ in stream:
                pass

    threading.Thread(target=pump, name="settings-env-log", daemon=True).start()


@pytest.fixture(scope="session")
def settings_env(tmp_path_factory):
    """A separate throwaway environment: its own config, DB and server."""
    root = tmp_path_factory.mktemp("settings_ui_test_env")
    env = {**os.environ, "OPENROUTER_API_KEY": ""}
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "ui_test_env.py"), "--root", str(root)],
        cwd=str(REPO),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    module = _load_env_module()
    proc, base = module.serve(root)
    _drain(proc)
    state = SimpleNamespace(
        root=root,
        db_path=root / "aam_merger.db",
        proc=proc,
        base=base,
        module=module,
        config_bytes=(root / "config.yaml").read_bytes(),
    )
    try:
        yield state
    finally:
        _stop(state.proc)


def _bump_mtime(path: Path) -> None:
    stat_now = path.stat()
    os.utime(path, ns=(stat_now.st_atime_ns, stat_now.st_mtime_ns + 1_000_000_000))


def _delete_settings_audit_rows(env) -> None:
    con = sqlite3.connect(env.db_path, timeout=10)
    try:
        con.execute("delete from audit_log where action = 'settings_changed'")
        con.commit()
    finally:
        con.close()


def _audit_rows(env) -> list[sqlite3.Row]:
    con = sqlite3.connect(env.db_path, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        return list(
            con.execute(
                "select action, detail from audit_log where action = 'settings_changed' "
                "order by timestamp, id"
            )
        )
    finally:
        con.close()


def _served_interval(env) -> str | None:
    try:
        html = httpx.get(f"{env.base}/settings", timeout=5.0).text
    except httpx.HTTPError:
        return None
    match = re.search(
        r'data-testid="field-schedule\.poll_interval_seconds"[\s\S]{0,200}?value="(-?\d+)"',
        html,
    )
    return match.group(1) if match else None


def _wait_for_interval(env, cfg_bytes: bytes, timeout: float = 20.0) -> None:
    """Wait until the server answers with the restored file (it caches by stat signature)."""
    expected = str(yaml.safe_load(cfg_bytes)["schedule"]["poll_interval_seconds"])
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        seen = _served_interval(env)
        if seen == expected:
            return
        time.sleep(0.1)
    pytest.fail(f"the server did not serve the restored interval {expected!r} (saw {seen!r})")


@pytest.fixture(autouse=True)
def reset_config(settings_env):
    """Every case starts from the bytes the environment was built with."""
    yield
    cfg_path = settings_env.root / "config.yaml"
    cfg_path.write_bytes(settings_env.config_bytes)
    _bump_mtime(cfg_path)

    backups = settings_env.root / svc.BACKUP_FOLDER_NAME
    if backups.exists():
        shutil.rmtree(backups, ignore_errors=True)
    lock = Path(str(cfg_path) + ".settings.lock")
    if lock.exists():
        with contextlib.suppress(OSError):  # another handle may still hold it
            lock.unlink()
    _delete_settings_audit_rows(settings_env)
    _wait_for_interval(settings_env, settings_env.config_bytes)


# ------------------------------------------------------------------------ helpers


def config_version(html: str) -> str:
    match = re.search(r'name="config_version" value="([0-9a-f]+)"', html)
    assert match, "the page carries no config_version"
    return match.group(1)


def _url(env, path: str) -> str:
    return f"{env.base}{path}"


def _field(key: str) -> str:
    return f'[data-testid="field-{key}"]'


def _row(key: str) -> str:
    return f'[data-testid="setting-row"][data-key="{key}"]'


def _read_cfg(env) -> dict:
    return yaml.safe_load((env.root / "config.yaml").read_text(encoding="utf-8"))


def _effective(env):
    return load_config_file(env.root / "config.yaml", remember=False)


def _fill_and_save(page: Page, key: str, value: str):
    page.fill(_field(key), value)
    with page.expect_navigation() as nav:
        page.click(SAVE)
    return nav.value


def _submit_anyway(page: Page):
    """Turn the browser's own validation off, then submit (server validation only)."""
    page.evaluate(f"document.querySelector('{FORM}').noValidate = true")
    with page.expect_navigation() as nav:
        page.click(SAVE)
    return nav.value


def _full_form(env) -> dict[str, str]:
    """What the page would post: every setting at its current value, plus the version."""
    model = svc.page_model(_effective(env))
    data = {"config_version": svc.file_version(env.root / "config.yaml")}
    for group in model["groups"]:
        for setting in group["settings"]:
            value = setting["value"]
            data[setting["key"]] = str(value).lower() if isinstance(value, bool) else str(value)
    return data


def _highlighted_rows(page: Page) -> list[str]:
    rows = page.locator('[data-testid="setting-row"]')
    return [
        rows.nth(i).get_attribute("data-key")
        for i in range(rows.count())
        if rows.nth(i).get_attribute("style")
    ]


# ------------------------------------------------------------------------ ST-01


def test_st_01_navigation(page: Page, settings_env):
    page.goto(_url(settings_env, "/dashboard"))
    page.click('[data-testid="nav-settings"]')
    expect(page).to_have_url(re.compile(r"/settings$"))
    expect(page.locator('[data-testid="nav-settings"]')).to_have_class(re.compile(r"\bactive\b"))
    assert "Settings" in page.title()


# ------------------------------------------------------------------------ ST-02


def test_st_02_the_page_is_true(page: Page, settings_env):
    cfg = _effective(settings_env)
    page.goto(_url(settings_env, "/settings"))

    settings = [s for group in svc.GROUPS for s in group.settings]
    rows = page.locator('[data-testid="setting-row"]')
    expect(rows).to_have_count(len(settings))
    expect(page.locator('[data-testid="readonly-row"]')).to_have_count(len(svc.READ_ONLY))

    for setting in settings:
        row = page.locator(_row(setting.key))
        expect(row).to_have_count(1)

        shown = page.eval_on_selector(_field(setting.key), "el => el.value")
        expected = svc._get(cfg, setting.key)
        if isinstance(expected, bool):
            expected = "true" if expected else "false"
        assert shown == str(expected), (setting.key, shown, expected)

        assert row.locator("label").inner_text().strip(), setting.key

        badges = row.locator('[data-testid^="apply-"]')
        expect(badges).to_have_count(1)
        assert badges.get_attribute("data-testid") == f"apply-{setting.apply}"
        assert badges.inner_text().strip() == BADGE_TEXT[setting.apply], setting.key

    for index, (key, _label, _note) in enumerate(svc.READ_ONLY):
        value = svc._get(cfg, key)
        if isinstance(value, bool):
            expected = "On" if value else "Off"
        elif isinstance(value, list):
            expected = ", ".join(str(v) for v in value)
        elif value is None:
            expected = "—"
        else:
            expected = str(value)
        cells = page.locator('[data-testid="readonly-row"]').nth(index).locator("td")
        assert cells.nth(1).inner_text().strip() == expected, key


# ------------------------------------------------------------------------ ST-03


def test_st_03_dirty_tracking(page: Page, settings_env):
    page.goto(_url(settings_env, "/settings"))
    save = page.locator(SAVE)
    notice = page.get_by_text("You have unsaved changes.")

    expect(save).to_be_disabled()
    expect(notice).to_be_hidden()

    page.fill(_field(INTERVAL), "75")
    expect(save).to_be_enabled()
    expect(notice).to_be_visible()

    with page.expect_navigation():
        page.click(RESET)
    expect(save).to_be_disabled()
    expect(page.locator(_field(INTERVAL))).to_have_value("60")


# ------------------------------------------------------------------------ ST-04


def test_st_04_happy_path(page: Page, settings_env):
    cfg_path = settings_env.root / "config.yaml"
    old = _read_cfg(settings_env)["schedule"]["poll_interval_seconds"]
    new = old + 30
    before = cfg_path.read_bytes()

    page.goto(_url(settings_env, "/settings"))
    response = _fill_and_save(page, INTERVAL, str(new))
    assert response.status == 200

    saved = page.locator(SAVED)
    expect(saved).to_be_visible()
    rows = page.locator('[data-testid="saved-row"]')
    expect(rows).to_have_count(1)
    text = rows.first.inner_text()
    assert f"{old} → {new}" in text, text
    assert "within a few seconds" in text, text
    expect(page.locator(_field(INTERVAL))).to_have_value(str(new))

    after_lines = cfg_path.read_text(encoding="utf-8").splitlines()
    before_lines = before.decode("utf-8").splitlines()
    assert len(after_lines) == len(before_lines)
    diffs = [(a, b) for a, b in zip(before_lines, after_lines, strict=True) if a != b]
    assert len(diffs) == 1, diffs
    assert diffs[0][1] == f"  poll_interval_seconds: {new}"

    backups = sorted((settings_env.root / svc.BACKUP_FOLDER_NAME).glob("config-*.yaml"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before

    rows_db = _audit_rows(settings_env)
    assert len(rows_db) == 1
    detail = json.loads(rows_db[0]["detail"])
    assert detail["changes"] == {INTERVAL: {"old": old, "new": new}}


# ------------------------------------------------------------------------ ST-05


def test_st_05_browser_validation_stops_a_bad_number(page: Page, settings_env):
    cfg_path = settings_env.root / "config.yaml"
    before = cfg_path.read_bytes()
    posts: list[str] = []

    def watch(request):
        if request.method == "POST" and "/settings" in request.url:
            posts.append(request.url)

    page.on("request", watch)
    page.goto(_url(settings_env, "/settings"))
    page.fill(_field(INTERVAL), "5")

    assert page.eval_on_selector(_field(INTERVAL), "el => el.checkValidity()") is False
    expect(page.locator(SAVE)).to_be_enabled()
    page.click(SAVE)
    page.wait_for_timeout(300)

    assert posts == [], posts
    assert cfg_path.read_bytes() == before


# ------------------------------------------------------------------------ ST-06


def test_st_06_server_validation_bypassing_the_browser(page: Page, settings_env):
    cfg_path = settings_env.root / "config.yaml"
    before = cfg_path.read_bytes()
    page.goto(_url(settings_env, "/settings"))

    # A bad interval plus a valid second field: one failing field blocks everything.
    page.fill(_field(INTERVAL), "5")
    page.fill(_field("schedule.merge_settle_minutes"), "30")
    response = _submit_anyway(page)
    assert response.status == 422

    expect(page.locator(ERROR)).to_be_visible()
    bad_row = page.locator(_row(INTERVAL))
    field_error = bad_row.locator('[data-testid="field-error"]')
    expect(field_error).to_be_visible()
    assert "at least 15" in field_error.inner_text()
    assert _highlighted_rows(page) == [INTERVAL]
    expect(page.locator(_field(INTERVAL))).to_have_value("5")
    # The valid second field is NOT written, but the operator's typing is still shown.
    expect(page.locator(_field("schedule.merge_settle_minutes"))).to_have_value("30")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["schedule"]["merge_settle_minutes"] == 5

    page.fill(_field("vlm.model"), "gpt")
    response = _submit_anyway(page)
    assert response.status == 422
    assert (
        "provider/name"
        in page.locator(_row("vlm.model")).locator('[data-testid="field-error"]').inner_text()
    )

    page.fill(_field("vlm.raster_dpi"), "10")
    response = _submit_anyway(page)
    assert response.status == 422
    assert (
        "at least 72"
        in page.locator(_row("vlm.raster_dpi")).locator('[data-testid="field-error"]').inner_text()
    )

    assert cfg_path.read_bytes() == before
    assert not (settings_env.root / svc.BACKUP_FOLDER_NAME).exists()
    assert _audit_rows(settings_env) == []


# ------------------------------------------------------------------------ ST-07


def test_st_07_the_cross_field_rule_is_explained(page: Page, settings_env):
    cfg_path = settings_env.root / "config.yaml"
    before = cfg_path.read_bytes()
    page.goto(_url(settings_env, "/settings"))

    response = _fill_and_save(page, "vlm.request_timeout_seconds", "600")
    assert response.status == 409
    expect(page.locator(ERROR)).to_be_visible()
    assert "stale_processing_seconds" in page.inner_text("body")
    assert cfg_path.read_bytes() == before

    page.fill(_field("vlm.request_timeout_seconds"), "600")
    page.fill(_field("extraction.stale_processing_seconds"), "5000")
    page.fill(_field("concurrency.po_set_lock_timeout_seconds"), "5000")
    with page.expect_navigation() as nav:
        page.click(SAVE)
    assert nav.value.status == 200
    expect(page.locator('[data-testid="saved-row"]')).to_have_count(3)

    data = _read_cfg(settings_env)
    assert data["vlm"]["request_timeout_seconds"] == 600
    assert data["extraction"]["stale_processing_seconds"] == 5000
    assert data["concurrency"]["po_set_lock_timeout_seconds"] == 5000


# ------------------------------------------------------------------------ ST-08


def test_st_08_no_silent_adjustment(page: Page, settings_env):
    cfg_path = settings_env.root / "config.yaml"
    before = cfg_path.read_bytes()
    page.goto(_url(settings_env, "/settings"))

    response = _fill_and_save(page, "concurrency.po_set_lock_timeout_seconds", "120")
    assert response.status == 422
    message = page.locator(_row("concurrency.po_set_lock_timeout_seconds")).locator(
        '[data-testid="field-error"]'
    )
    expect(message).to_be_visible()
    assert "Must be at least" in message.inner_text()
    assert page.locator(_field("concurrency.po_set_lock_timeout_seconds")).input_value() == "120"
    assert cfg_path.read_bytes() == before


# ------------------------------------------------------------------------ ST-09


def test_st_09_select_fields_and_comments(page: Page, settings_env):
    cfg_path = settings_env.root / "config.yaml"
    # Give the editor a comment of its own to keep (the harness file is generated).
    cfg_path.write_bytes(cfg_path.read_bytes() + b"# kept by the settings editor\n")
    _bump_mtime(cfg_path)
    old_comments = [
        line for line in cfg_path.read_text(encoding="utf-8").splitlines() if line.startswith("#")
    ]

    page.goto(_url(settings_env, "/settings"))
    page.select_option(_field("vlm.reasoning_effort"), "medium")
    page.select_option(_field("extraction.split_combined"), "true")
    page.select_option(_field("matching.line_number_fallback.step10"), "false")
    with page.expect_navigation() as nav:
        page.click(SAVE)
    assert nav.value.status == 200
    expect(page.locator(SAVED)).to_be_visible()

    text = cfg_path.read_text(encoding="utf-8")
    assert 'reasoning_effort: "medium"' in text
    assert "split_combined: true" in text
    assert "step10: false" in text
    new_comments = [line for line in text.splitlines() if line.startswith("#")]
    assert new_comments == old_comments

    saved = _effective(settings_env)
    assert saved.vlm.reasoning_effort == "medium"
    assert saved.extraction.split_combined is True
    assert saved.matching.line_number_fallback.step10 is False


# ------------------------------------------------------------------------ ST-10


def test_st_10_two_tabs_and_a_stale_form(page: Page, settings_env):
    old = _read_cfg(settings_env)["schedule"]["poll_interval_seconds"]
    a, b = old + 10, old + 20

    page.goto(_url(settings_env, "/settings"))
    page2 = page.context.new_page()
    try:
        page2.goto(_url(settings_env, "/settings"))

        response = _fill_and_save(page, INTERVAL, str(a))
        assert response.status == 200
        expect(page.locator(SAVED)).to_be_visible()

        page2.fill(_field(INTERVAL), str(b))
        with page2.expect_navigation() as nav:
            page2.click(SAVE)
        assert nav.value.status == 409
        error = page2.locator(ERROR)
        expect(error).to_be_visible()
        assert "changed by someone else" in error.inner_text()

        assert _read_cfg(settings_env)["schedule"]["poll_interval_seconds"] == a
        expect(page2.locator(_field(INTERVAL))).to_have_value(str(a))
        expect(page2.locator(SAVE)).to_be_disabled()
    finally:
        page2.close()


# ------------------------------------------------------------------------ ST-11


def test_st_11_a_broken_file_while_running(page: Page, settings_env):
    cfg_path = settings_env.root / "config.yaml"
    good = cfg_path.read_bytes()
    broken = "paths: [broken: \n  x: {"
    try:
        cfg_path.write_text(broken, encoding="utf-8")
        _bump_mtime(cfg_path)

        page.goto(_url(settings_env, "/settings"))
        banner = page.locator(DISK)
        expect(banner).to_be_visible()
        assert "not valid" in banner.inner_text()

        response = page.goto(_url(settings_env, "/dashboard"))
        assert response is not None and response.status == 200
        expect(page.locator("#kpi-grid")).to_be_visible()

        page.goto(_url(settings_env, "/settings"))
        page.fill(_field(INTERVAL), "61")
        refused = _submit_anyway(page)
        assert refused.status == 409
        expect(page.locator(ERROR)).to_be_visible()
        assert cfg_path.read_text(encoding="utf-8") == broken
    finally:
        cfg_path.write_bytes(good)
        _bump_mtime(cfg_path)
    page.goto(_url(settings_env, "/settings"))
    expect(page.locator(DISK)).to_have_count(0)


# ------------------------------------------------------------------------ ST-12


def test_st_12_read_only_part_and_secrets(page: Page, settings_env, monkeypatch):
    page.goto(_url(settings_env, "/settings"))

    readonly = page.locator('[data-testid="settings-readonly"]')
    assert readonly.locator("input, select, textarea").count() == 0
    expect(page.locator('[data-testid="api-key-missing"]')).to_be_visible()
    assert "OPENROUTER_API_KEY" in page.inner_text("body")

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    html = page.request.get(_url(settings_env, "/settings")).text()
    assert "sk-" not in html


# ------------------------------------------------------------------------ ST-13


def test_st_13_forged_and_cross_site_posts(page: Page, settings_env):
    cfg_path = settings_env.root / "config.yaml"
    before = cfg_path.read_bytes()
    data = _full_form(settings_env)
    data["paths.input_folder"] = "C:/Windows"
    data["server.allowed_hosts"] = "evil"
    data["vlm.api_key_env_var"] = "X"

    forged = page.request.post(_url(settings_env, "/settings"), form=data)
    assert forged.status == 200, forged.text()
    assert "Nothing was changed" in forged.text()
    assert cfg_path.read_bytes() == before

    cross_site = page.request.post(
        _url(settings_env, "/settings"), form=data, headers={"Origin": "http://evil.example"}
    )
    assert cross_site.status == 403
    assert cfg_path.read_bytes() == before

    flood = dict(data)
    flood.update({f"extra_{i}": "x" for i in range(300)})
    too_big = page.request.post(_url(settings_env, "/settings"), form=flood)
    assert too_big.status == 413
    assert cfg_path.read_bytes() == before


# ------------------------------------------------------------------------ ST-14


def test_st_14_the_watcher_switch_is_true_and_live(page: Page, settings_env):
    cfg_path = settings_env.root / "config.yaml"
    # Never let the live poller meet real work: no PDFs, and an hour between looks.
    for pdf in (settings_env.root / "input").rglob("*.pdf"):
        pdf.unlink()

    page.goto(_url(settings_env, "/settings"))
    response = _fill_and_save(page, INTERVAL, "3600")
    assert response.status == 200
    assert _read_cfg(settings_env)["schedule"]["poll_interval_seconds"] == 3600

    page.select_option(_field("schedule.poll_enabled"), "true")
    with page.expect_navigation() as nav:
        page.click(SAVE)
    assert nav.value.status == 200
    expect(page.locator(POLLER)).to_have_text("running")
    page.reload()
    expect(page.locator(POLLER)).to_have_text("running")

    page.select_option(_field("schedule.poll_enabled"), "false")
    with page.expect_navigation() as nav:
        page.click(SAVE)
    assert nav.value.status == 200
    expect(page.locator(POLLER)).to_have_text("off")
    assert cfg_path.exists()


# ------------------------------------------------------------------------ ST-15


def test_st_15_layout(page: Page, settings_env):
    for width in (1280, 1024):
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(_url(settings_env, "/settings"))

        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        assert scroll_width <= client_width + 1, (width, scroll_width, client_width)

        save = page.locator(SAVE)
        expect(save).to_be_in_viewport()
        assert page.evaluate("window.scrollY") == 0

        boxes = page.eval_on_selector_all(
            '[data-testid^="field-"]',
            "els => els.map(e => { const r = e.getBoundingClientRect();"
            " return {left: r.left, right: r.right}; })",
        )
        clipped = [b for b in boxes if b["left"] < -1 or b["right"] > client_width + 1]
        assert not clipped, (width, clipped)

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(_url(settings_env, "/settings"))
    scroll_width = page.evaluate("document.documentElement.scrollWidth")
    client_width = page.evaluate("document.documentElement.clientWidth")
    print(f"\nST-15 at 390px: scrollWidth={scroll_width} clientWidth={client_width}", flush=True)

    page.fill(_field(INTERVAL), "75")  # enable Save so it is genuinely clickable
    save = page.locator(SAVE)
    save.scroll_into_view_if_needed()
    expect(save).to_be_visible()
    save.click(trial=True)  # actionability only: reachable and clickable, no submit


# ------------------------------------------------------------------------ ST-16


def test_st_16_the_audit_page_shows_it(page: Page, settings_env):
    old = _read_cfg(settings_env)["schedule"]["poll_interval_seconds"]
    new = old + 30
    page.goto(_url(settings_env, "/settings"))
    assert _fill_and_save(page, INTERVAL, str(new)).status == 200

    page.goto(_url(settings_env, "/audit"))
    first = page.locator('[data-testid="audit-row"]').first
    expect(first).to_be_visible()
    text = first.inner_text()
    assert "Settings Changed" in text
    assert f"{INTERVAL}: {old} → {new}" in text


# ------------------------------------------------------------------------ ST-17


def test_st_17_keyboard(page: Page, settings_env):
    page.goto(_url(settings_env, "/settings"))

    page.locator(_field(INTERVAL)).focus()
    page.keyboard.press("Control+A")
    page.keyboard.type("75")
    with page.expect_navigation():
        page.keyboard.press("Enter")
    expect(page.locator(SAVED)).to_be_visible()
    assert _read_cfg(settings_env)["schedule"]["poll_interval_seconds"] == 75

    # The Save button is disabled until something changes, and a disabled button is
    # not in the tab order: make the form dirty first, then walk the tab order.
    page.fill(_field(INTERVAL), "80")
    page.locator('[data-testid^="field-"]').first.focus()
    reached = False
    for _ in range(60):
        page.keyboard.press("Tab")
        active = page.evaluate(
            "() => { const el = document.activeElement;"
            " return {testid: el.getAttribute('data-testid'), inForm: !!el.closest('form')}; }"
        )
        assert active["inForm"], f"Tab left the form at {active['testid']!r}"
        if active["testid"] == "settings-save":
            reached = True
            break
    assert reached, "Tab never reached the Save button"


# ------------------------------------------------------------------------ ST-18


def test_st_18_backups_are_pruned(page: Page, settings_env):
    cfg_path = settings_env.root / "config.yaml"
    snapshots: list[bytes] = []
    for i in range(23):
        version = svc.file_version(cfg_path)
        snapshots.append(cfg_path.read_bytes())
        value = 100 if i % 2 == 0 else 101
        response = page.request.post(
            _url(settings_env, "/settings"),
            form={"config_version": version, INTERVAL: str(value)},
        )
        assert response.status == 200, response.text()

    backups = sorted((settings_env.root / svc.BACKUP_FOLDER_NAME).glob("config-*.yaml"))
    assert len(backups) == 20
    assert backups[-1].read_bytes() == snapshots[-1]  # the file as it was before the last save
    assert [b.read_bytes() for b in backups] == snapshots[-20:]
    assert len(_audit_rows(settings_env)) == 23


# ------------------------------------------------------------------------ ST-19


def test_st_19_restart_keeps_the_value(page: Page, settings_env):
    cfg_path = settings_env.root / "config.yaml"
    page.goto(_url(settings_env, "/settings"))
    assert _fill_and_save(page, INTERVAL, "90").status == 200

    _stop(settings_env.proc)
    proc, base = settings_env.module.serve(settings_env.root)
    _drain(proc)
    settings_env.proc, settings_env.base = proc, base

    page.goto(_url(settings_env, "/settings"))
    expect(page.locator(_field(INTERVAL))).to_have_value("90")
    response = page.goto(_url(settings_env, "/dashboard"))
    assert response is not None and response.status == 200

    good = cfg_path.read_bytes()
    cfg_path.write_text("paths: nonsense\n", encoding="utf-8")
    _bump_mtime(cfg_path)
    with pytest.raises(RuntimeError):
        settings_env.module.serve(settings_env.root)

    cfg_path.write_bytes(good)
    _bump_mtime(cfg_path)
    proc, base = settings_env.module.serve(settings_env.root)
    _drain(proc)
    settings_env.proc, settings_env.base = proc, base
    response = page.goto(_url(settings_env, "/settings"))
    assert response is not None and response.status == 200


# ------------------------------------------------------------------------ ST-20


def test_st_20_no_stray_files(page: Page, settings_env):
    root = settings_env.root
    page.goto(_url(settings_env, "/settings"))
    assert _fill_and_save(page, INTERVAL, "75").status == 200
    assert not list(root.glob("*.candidate"))
    assert not list(root.glob(".*.candidate"))
    assert not (root / ".config.yaml.candidate").exists()

    page.fill(_field(INTERVAL), "5")
    assert _submit_anyway(page).status == 422
    assert not list(root.glob("*.candidate"))
    assert not (root / ".config.yaml.candidate").exists()
