# Settings page: test plan for the DeepSeek worker (Opus-written)

You write tests only. You do **not** change anything under `src/`, `templates/`, `alembic/`, `config*.yaml`,
`scripts/ui_test_env.py` or `tests/ui/conftest.py` (another worker is editing those two right now), and you do not
touch `tests/test_settings_page.py` (the existing 36 backend tests; read it for the fixture style). No git commands.
If a test shows the product is wrong, do **not** work around it: mark it `@pytest.mark.xfail(strict=True,
reason="SUSPECTED PRODUCT BUG <id>: <what>")`, keep the assertion that states the correct behaviour, and list it in
the report. A weakened assertion is a failed task.

Read first: `docs/SETTINGS_SPEC.md` (behaviour), `src/app/services/settings_admin.py` (rules), `src/app/api/routes/settings.py`,
`templates/settings.html` (all `data-testid`s), `tests/test_settings_page.py` (fixtures `cfg_file`, `client`, `_form`).

Deliverables

1. `tests/ui/test_ui_settings.py` , browser cases ST-01..ST-20 (section 2).
2. `tests/test_settings_page_more.py` , backend cases B-01..B-13 (section 3).
3. A report (section 5) as your final message.

Acceptance: `uv run pytest tests/test_settings_page.py tests/test_settings_page_more.py -q --no-cov -p no:warnings`
green; `RUN_E2E=1 uv run python -m pytest tests/ui/test_ui_settings.py -o addopts= -p no:cacheprovider` green **three
runs in a row**; `uv run ruff check . && uv run ruff format --check .` clean (line length 100). Only expected xfails.

---

## 1. Facts you need

Page: `/settings` (GET, POST). `data-testid`s: `nav-settings`, `settings-form`, `settings-group`, `setting-row` (attribute
`data-key="schedule.poll_interval_seconds"`), `field-<dotted key>` (input or select, `name` = the dotted key),
`field-error` (inside the row of a refused field), `apply-live` / `apply-next_sync` / `apply-restart` (badge text Live /
Next sync / Needs restart), `settings-save`, `settings-reset` (link "Discard changes"), `settings-saved` (green banner) with
`saved-row` items "label: old → new — when it applies", `settings-nochange`, `settings-error` (red banner),
`settings-disk-problem` (red banner), `poller-state` ("running" or "off"), `api-key-missing`, `settings-readonly` and its
`readonly-row`s. The hidden input `config_version` carries the file version. Numbers are `<input type=number min max>`;
booleans are `<select>` with values `true`/`false` (labels On/Off); choices are `<select>`.

Behaviour: Save button is disabled until a field changes (Alpine `dirty`). A valid save replaces `config.yaml` (comments kept,
only the changed line differs), copies the previous file to `config_backups/config-<UTC>.yaml` (newest 20 kept) next to it,
writes one `audit_log` row with `action = 'settings_changed'`, and re-renders the page with the new values (HTTP 200). A refused
value renders the page again with HTTP 422 and a message in that field's row, keeping what the operator typed; a problem with the
whole save (stale file version, invalid file on disk, cross-field rule, write failure) renders a red banner with HTTP 409 and
changes nothing. Numbers are whole numbers within the limits of the config models.

Cross-field rules you will meet (defaults in `config.example.yaml`): `extraction.stale_processing_seconds` (900) must exceed
`vlm.request_timeout_seconds * (extraction.max_retries + 1) + sum(retry_backoff)` (180*4+22 = 742 with the example); and
`concurrency.po_set_lock_timeout_seconds` must be at least that worst case plus 60, otherwise the page refuses it with
"Must be at least N".

Helper you will write in each file: `config_version(html)` = regex `name="config_version" value="([0-9a-f]+)"`.

## 2. Browser tests: `tests/ui/test_ui_settings.py`

Environment. The existing `ui_env` fixture is shared by other tests and must not be mutated, and a settings test **changes the config file**.
So the file defines its own session fixture `settings_env` that builds a **separate** throwaway environment exactly like
`tests/ui/conftest.py::_start_env` does (copy that function's logic into your file: run `scripts/ui_test_env.py --root <tmp>` with
`OPENROUTER_API_KEY=""`, load the module with importlib, `module.serve(root)`), and stops it at teardown. `settings_env.root / "config.yaml"` is the
live config of that server. Use absolute URLs `f"{settings_env.base}/settings"` (the `base_url` fixture belongs to the other env). Import the
autouse JS-error fixture implicitly through conftest (it applies automatically). Between tests a function-scoped autouse fixture `reset_config`
restores `config.yaml` to the bytes captured when the environment was built (write bytes back, delete `config_backups`, delete
`config.yaml.settings.lock` if present) and deletes `audit_log` rows with action `settings_changed` through a normal read-write sqlite3 connection
to `root/aam_merger.db`. After restoring the file wait until the server serves the restored values (GET `/settings` and compare the interval value)
because the config is cached by file time stamp; if the restore happens within the same timestamp tick the bytes differ in length or content anyway,
but do not rely on it: write the bytes back and then `os.utime` the file one second forward.

Never run the live poller against real work: before any case that turns folder watching on (ST-14) delete every `*.pdf` under `root/input` and set the
interval to 3600 first. Read helpers: `read_cfg()` = `yaml.safe_load(config.yaml)`; effective values = `app.core.config.load_config_file(path,
remember=False)` (import in the test process; it creates folders under the env root, which is fine).

Cases (each is a separate test function; name them `test_st_NN_<slug>`):

- **ST-01 navigation.** Open `/dashboard`, click `nav-settings`. URL ends `/settings`; `nav-settings` has class `active`; `<title>` contains "Settings".
- **ST-02 the page is true.** For every setting in `app.services.settings_admin.GROUPS`: exactly one `setting-row[data-key=...]` exists; its input value
  (bools: selected option value) equals the effective value from `load_config_file`; label text is non-empty; it has exactly one apply badge whose text is
  the one for its `apply` class. Row count equals the number of settings; `readonly-row` count equals `len(READ_ONLY)`; every read-only row's value text equals
  the effective value (paths as the loader resolved them).
- **ST-03 dirty tracking.** Fresh page: `settings-save` is disabled and "unsaved changes" is hidden. Change a number: Save enabled, the notice visible.
  Click `settings-reset`: page reloads, Save disabled again, the typed value is gone.
- **ST-04 happy path.** Read old interval from the file; set the field to old+30; click Save. Assert: HTTP 200 navigation result; `settings-saved` visible with one
  `saved-row` containing "old → new" and "within a few seconds"; the field now shows the new value; `config.yaml` on disk has the new value on exactly one line and
  every other line is byte-identical (compare `splitlines()` before/after, only that line differs, same line count); `config_backups` holds exactly one file equal to
  the previous bytes; the DB has exactly one `settings_changed` audit row whose detail JSON has `changes[<key>] == {"old": old, "new": new}`.
- **ST-05 browser validation stops a bad number before the server.** Type 5 into the interval field (min is 15). Register `page.on("request")` and click Save.
  Assert no POST to `/settings` was made, the input's `checkValidity()` is false, the file bytes are unchanged.
- **ST-06 server validation (bypass the browser).** In the page set `document.querySelector('[data-testid=settings-form]').noValidate = true`, type 5 into the interval
  field, click Save. Assert: response page shows `settings-error` and, inside the interval row, a `field-error` containing "at least 15"; only that row is
  highlighted; the field still contains 5; file bytes unchanged; no `config_backups` folder; no audit row. Repeat for the model field with `gpt`
  (error "provider/name") and for `vlm.raster_dpi` = 10 ("at least 72"). One failing field blocks the whole save: also change a valid second field in the same submit and
  assert it is **not** written (file unchanged) but still shows what was typed.
- **ST-07 cross-field rule is explained.** Set `vlm.request_timeout_seconds` = 600 alone and save: the red banner or the row text mentions
  `stale_processing_seconds`; file unchanged. Then in one submit set request timeout 600, stale 5000, lock timeout 5000: saved, three `saved-row`s, file holds all three.
- **ST-08 no silent adjustment.** Set the lock timeout to 120: `field-error` "Must be at least"; file unchanged (the page must never display 120 while the service runs on a raised value).
- **ST-09 select fields and comments.** Change `vlm.reasoning_effort` to `medium`, `extraction.split_combined` to On, one line-number switch to Off: saved;
  `config.yaml` contains `reasoning_effort: "medium"`, `split_combined: true`; every comment line (`#...`) of the old file is still present in the same order;
  the loader reads the three values back.
- **ST-10 two tabs, stale form.** In one browser context open the page twice (`page` and `page2`). Save interval A in `page`. In `page2` (still the old version) change the interval
  to B and Save: `settings-error` contains "changed by someone else"; file still has A; the page now shows A (not B) and Save is disabled.
- **ST-11 broken file while running.** Overwrite `config.yaml` with `paths: [broken: \n  x: {` (write the file, bump mtime). GET `/settings`: `settings-disk-problem` visible with the
  words "not valid"; GET `/dashboard`: status 200 and the KPI grid is visible (the service kept running); a Save attempt shows `settings-error` and leaves the broken text as it is.
  Restore the good bytes: banner gone. (`finally:` always restores.)
- **ST-12 read-only part and secrets.** `settings-readonly` contains no `input`, `select` or `textarea`. The harness blanks the key, so `api-key-missing` is visible and the page text
  contains the variable name `OPENROUTER_API_KEY`. Run the test with `os.environ["OPENROUTER_API_KEY"]` unset in the test process and assert the page HTML contains no `sk-`.
- **ST-13 forged and cross-site posts.** With `page.request.post(f"{base}/settings", form={...})`: a body with a valid `config_version` plus `paths.input_folder=C:/Windows`,
  `server.allowed_hosts=evil`, `vlm.api_key_env_var=X` and unchanged values for everything else returns 200 "Nothing was changed" and the file is unchanged. The same post with header
  `Origin: http://evil.example` returns 403 and the file is unchanged. A post with 300 extra fields returns 413 and the file is unchanged.
- **ST-14 watcher switch is true and live.** Pre-conditions in the text above. Set interval 3600 and save, then set `poll_enabled` On and save: `poller-state` reads "running" with no restart
  (re-GET the page and check again); set it Off and save: `poller-state` reads "off". (The page must not say "running" while watching is off.) Leave the env as found (the reset fixture restores the file).
- **ST-15 layout.** At 1280 px and 1024 px wide: `document.documentElement.scrollWidth <= clientWidth + 1`, the Save bar is visible without scrolling (it is sticky), and no row's input is clipped
  (bounding box inside the viewport). At 390 px: record `scrollWidth` and `clientWidth` in the test's output; assert only that the Save button is reachable by scrolling (`scroll_into_view_if_needed`) and clickable. Do not assert 390 px has no horizontal scroll.
- **ST-16 audit page shows it.** After ST-04's change, `/audit` has a row with badge text "Settings Changed" whose summary contains `schedule.poll_interval_seconds: 60 → 90` (use the real old/new). The row appears first (newest).
- **ST-17 keyboard.** Focus the interval field, type a new valid value, press Enter: the form saves (`settings-saved`). Tab order: from the first field, pressing Tab repeatedly reaches `settings-save` before any link outside the form.
- **ST-18 backups are pruned.** Using `page.request` with a fresh `config_version` each time, alternate the interval between 100 and 101 for 23 saves. `config_backups` then holds exactly 20 files; the
  newest equals the file as it was before the last save; the 3 oldest are gone; 23 audit rows exist.
- **ST-19 restart keeps the value.** Save interval 90. Stop the server process (`env.proc.terminate()`), start a new one with `module.serve(root)` (update `settings_env.proc` and `base` so teardown stops the new one),
  GET `/settings`: the interval shows 90 and `/dashboard` loads. Then make `config.yaml` invalid, restart the process again: the service must **refuse to start** (`serve` raises or the health check fails within its timeout);
  restore the file and restart once more so the session ends healthy.
- **ST-20 no stray files.** After the happy path and after a refused save, the config folder holds no `.config.yaml.candidate`; `config.yaml.settings.lock` may exist.

## 3. Backend tests: `tests/test_settings_page_more.py`

Copy the small fixtures `cfg_file`, `client` and `_form` from `tests/test_settings_page.py` (do not import from another test module). Cases:

- **B-01 unwritable file.** Make `config.yaml` read-only (`os.chmod(path, stat.S_IREAD)`; on Windows that sets the read-only attribute, which `os.replace` refuses). POST a valid change: HTTP 409 with `settings-error`
  "could not be written", file bytes unchanged, no `.candidate` left, no audit row, no 500. Restore write permission in `finally`. If the platform cannot make the file unwritable (running as an administrator), `pytest.skip` with the reason.
- **B-02 non-ASCII comments survive.** Insert a comment line `# rupee ₹ and dash — and é` into the config, save a change elsewhere: the comment is byte-identical after the save, the file still decodes as UTF-8.
- **B-03 eight racers.** 8 threads call `svc.save` with the same `version` and different intervals (100..107). Exactly one result is `ok and changed`, the other seven have `general` set (version or lock message), the file is valid and holds the winner's value,
  exactly one backup exists and exactly one audit row exists.
- **B-04 form size.** POST 201 fields: 413, file unchanged.
- **B-05 text injection.** For `vlm.model` submit each of: `a/b\n  evil: 1`, `a/b #c`, `a/b"` , `a/b' x`, `{a: b}/c`, ` ` (spaces), a 5000-character string. Every one is refused with a `field-error`; the file never changes and still loads.
- **B-06 booleans and choices are strict.** `True`, `1`, `yes`, `ON`, empty for a bool: refused "Choose on or off"; a choice not in the list (case changed, e.g. `HIGH`): refused.
- **B-07 integers are strict.** Refused: `٩٠` (Arabic-Indic digits), `9 0`, `0x10`, `1e3`, `1_000`, `--5`, empty, `   `, `1.0`, and a 5000-digit string (must not raise; message "too large"). Accepted and trimmed: ` 90 `, `+90`, `090`. For each accepted value the file holds the plain integer.
- **B-08 last good config.** Using `config_module.load_config(path)`: good file -> value V1; overwrite with invalid text -> five more `load_config` calls return V1 and the `app.core.config` logger emitted exactly **one** error containing "not valid" (use `caplog`);
  write a new valid file with V2 -> `load_config` returns V2. A path never loaded successfully raises on the first call.
- **B-09 wiring tripwire.** For every setting in `GROUPS`: (a) the dotted key exists in `config.example.yaml` (so IT can see it); (b) the last segment of the key appears (word boundary) in at least one `.py` file under `src/app`
  or `scripts` other than `core/config.py`, `services/settings_admin.py` and `api/routes/settings.py` (the setting is read somewhere). For `logging.level` search for `"level"` instead. List any key you could not match in the report; do not delete the check.
- **B-10 every setting round-trips on the real example file.** For every setting: take `config.example.yaml` text, `apply_changes` with a different valid value (toggle bool, next choice, value+1 or the minimum for numbers where +1 exceeds the maximum), parse, assert the value and that
  the number of lines and all comment lines are unchanged. Also with the file converted to CRLF.
- **B-11 repo config.** The same as B-10 against the repo's real `config.yaml` if it exists (`pytest.skip` if not): it must be editable without a `_LayoutError`.
- **B-12 bounds equal the models.** For every int setting, build a candidate config through `AppConfig` with the setting at its minimum and at its maximum (everything else as the example): the model accepts both unless a cross-field rule rejects it (then assert the rule's
  message names a field), and one below / one above are rejected by `svc._parse` before the model is consulted.
- **B-13 page values equal `load_config` for a file whose lock timeout is too short.** Use a config with `po_set_lock_timeout_seconds: 300`: the page shows the raised effective value (802 with the example), not 300, and posting the page unchanged writes nothing.

## 4. Hard rules

- Tests are deterministic: no sleeps longer than needed, use `expect(...)` / `wait_for` conditions, no real network, no real API key (the harness blanks it).
- Do not leave the repo's own `config.yaml` or `data/` changed: every test works on temp copies. `git status` must show only your two new files.
- Do not edit the product. A failing check is a finding.
- Keep each test independent; use the reset fixture, not ordering.
- Line length 100, no bare `except:`, `pathlib` for paths.

## 5. Report (your final message)

Per case: ST-01..ST-20, B-01..B-13: PASS / XFAIL(id) / SKIP(reason). Then: files created, the three-run result, ruff result, every deviation from this plan with the reason,
every suspected product bug with the exact steps and observed vs expected, and anything in the plan that you could not implement as written. Do not stop before printing it.
