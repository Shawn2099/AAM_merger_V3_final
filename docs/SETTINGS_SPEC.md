# Settings page (WS3 / D11)

Built 2026-10-25 at the owner's request, ahead of the pilot. Code: `src/app/services/settings_admin.py`,
`src/app/api/routes/settings.py`, `templates/settings.html`. Tests: `tests/test_settings_page.py`.

## What the operator gets

`/settings` (nav: Settings) shows the settings an operator may change, grouped by purpose, each with
a plain-language label, a help line, its unit and allowed range, and a badge saying when a change takes
effect:

| Badge | Meaning |
|---|---|
| Live | within seconds, no restart (folder watcher, settle minutes, lock timeout, merge-tool limits) |
| Next sync | the next sync, or the next document read, uses it (AI model, matching rules, copy check) |
| Needs restart | after the Windows service is restarted (log file, the merge tool's upload size guard) |

Below the form, a **Fixed by IT** table shows (read only) the folders, database file, allowed hosts,
midnight-sweep switch and zone, packet page order, number locale and the API-key variable name. It also
says whether the API key is set; the key itself is never shown, stored or posted.

## What can be changed (28 settings)

| Group | Keys |
|---|---|
| Watching the input folder | `schedule.poll_enabled`, `poll_interval_seconds`, `merge_settle_minutes`; `ingestion.stability_poll_interval_seconds`, `stability_poll_count` |
| Reading documents (AI) | `vlm.model`, `request_timeout_seconds`, `raster_dpi`, `reasoning_effort`, `max_pages`; `extraction.max_retries`, `stale_processing_seconds`, `split_combined` |
| Matching and merging rules | `reconciliation.single_po_document`; `matching.fuzzy_description_threshold`; the four `matching.line_number_fallback.*` switches |
| Locks | `concurrency.po_set_lock_timeout_seconds` |
| Merge PDFs tool | `manual_merge.max_files`, `max_pages`, `max_total_mb`, `sweep_after_hours`, `sweep_on_startup` |
| Log file | `logging.level`, `max_file_size_mb`, `backup_count` |

Deliberately **not** editable here (decision, deviates from the earlier Q1 answer that listed paths as
"restart-needed"): all `paths.*`, `server.*` and `allowed_hosts`, `vlm.api_key_env_var`, `merge.legal_order`,
`matching.locale`, `prefect.*`, `schedule.nightly_enabled`, `manual_merge.temp_folder`,
`extraction.retry_backoff_seconds`. A wrong path silently shows an empty system, a wrong host list locks
everyone out, and the nightly switch is read only by `scripts/deploy_prefect.py`, so a switch on this page would
not act. They stay in `config.yaml`, changed by IT.

## Why a bad value cannot kill the program

1. **Typed and bounded on the page.** Every field is parsed (whole numbers only, listed choices only,
   `provider/name` for the model). The limits shown are read from the same pydantic models the service starts
   with (`_model_bounds`), so the page and the models cannot drift; a test proves each limit.
2. **Validated on a candidate copy.** The edited text is written to `.config.yaml.candidate` and loaded with the
   service's own loader (`load_config_file`, all validators, including cross-field rules such as "stale limit must
   exceed the worst-case reading"). A failure is shown beside the field (or as one banner for a cross-field rule)
   and **the real file is never touched**.
3. **No silent adjustment.** `po_set_lock_timeout_seconds` is quietly raised by the loader if too short. The page
   refuses the value instead, so it never shows a number the service is not running on.
4. **Atomic replace with backup.** The previous file is copied to `config_backups/config-<UTC time>.yaml`
   (newest 20 kept), then the candidate replaces the file with `os.replace` (retried on Windows sharing errors).
5. **Read-back check.** The saved file is loaded again and every changed value must read back. If not, the backup is
   restored and the page says so.
6. **One writer at a time, no lost updates.** A file lock serialises saves, and the form carries the version (hash) of
   the file it was built from; a save against a file that has since changed is refused with "changed by someone else".
7. **Comments survive.** The file is edited line by line (not re-dumped), so comments, order and Windows line endings
   are kept. The edited text is parsed again and must hold exactly the intended values, or nothing is written. A layout
   the editor cannot handle safely (for example a one-line `{...}` section) is refused with "ask IT".
8. **Only listed keys.** The route reads only the keys in `GROUPS`; a forged field for a path or the API key is ignored.
9. **Every save is audited** (`settings_changed`: old and new value of each key, backup name), shown on the Audit page.

### A file broken outside the page (hand edit, bad restore)

`load_config` keeps the last config that loaded and validated, per file. If the file later becomes unreadable or
invalid, the service logs one error per edit and **keeps running on the last good settings**; the dashboard and every
other page keep working. The Settings page shows a red banner with the reason, and refuses saves until IT repairs the
file or restores a copy from `config_backups`. A service **started** on a broken file still refuses to start
(SPEC 13.1 fail-fast is unchanged: there is no earlier good copy to trust).

## How a change reaches the running service

The service re-reads `config.yaml` on every request, poller tick and sync (cached by file time stamp), so Live and
Next-sync settings need no restart. One exception is handled by the page: if the service started with folder watching
off, no watcher thread exists; saving `poll_enabled = On` starts it at once (`start_if_enabled`). Switching it off makes
the existing watcher idle at its next tick.

## Security

No login, as everywhere else (PRODUCT section 8); the Origin guard and the allowed-host list apply to the POST, and every
save is audited. The API key is never read into a response.

## Tests

Backend: `tests/test_settings_page.py` (36 tests, written with the code). Browser and extra backend cases: planned in
`docs/OPENCODE_SETTINGS_TEST_PLAN.md` (ST-01..ST-20 in `tests/ui/test_ui_settings.py`, B-01..B-13 in
`tests/test_settings_page_more.py`), written by a DeepSeek worker and reviewed by Opus.

## Not covered / known limits

- Moving a data folder, the database, the allowed hosts or the midnight schedule is an IT task (see RUNBOOK).
- The page cannot tell a wrong-but-well-formed model id from a right one; the badge and help say a wrong id makes
  documents fail to read (they are kept and retried).
- `logging.*` and `manual_merge.max_total_mb` need a service restart; the page says so but does not restart it.
- Two browser tabs: the second save is refused (version check) and the page reloads the current values.
