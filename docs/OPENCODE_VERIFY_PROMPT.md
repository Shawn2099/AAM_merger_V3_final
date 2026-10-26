# Verification worker prompt (DeepSeek). Report only. Claude fixes.

You are a VERIFICATION worker for the repo at C:\Users\malliath\Desktop\aam_merger (Windows, PowerShell, run Python only through `uv run`).

## Hard rules
1. You may create or overwrite exactly ONE file: `docs/VERIFY_REPORT_2026-10-25.md`. Do not edit, create, move or delete anything else (no src/, tests/, templates/, scripts/, docs/ other than that report, no config.yaml, no .env, no data/ folder). No git commands that change anything (no add, commit, push, stash, checkout, reset). `git status --short` and `git diff --stat` are allowed.
2. Do not fix anything. If something fails, record it exactly and keep going. Fixing is Claude's job.
3. Do not print or copy the contents of `.env`.
4. Run commands one at a time, in the order below, and write each result into the report right after the command finishes (so the report exists even if you are interrupted). Each pytest run can take 3-12 minutes: use a long timeout (600000 ms) and wait. Do not start a second pytest while one is running. If a command times out, re-run it once with output redirected to a file (`> $env:TEMP\x.out 2>&1; echo exit=$LASTEXITCODE`) and read the file.
5. The pytest summary line ("N passed, M failed") is sometimes lost in the noisy output. The real verdict is the exit code plus the list of FAILED/ERROR lines. Always capture both:
   `uv run pytest <args> > $env:TEMP\pt.out 2>&1; echo exit=$LASTEXITCODE; Select-String -Path $env:TEMP\pt.out -Pattern '^(FAILED|ERROR)','passed','failed' | Select-Object -Last 40`

## Step 0: precondition
Run `git status --short` and record it. Then check these files exist: `tests/test_s1_provider_breaker.py`, `tests/test_s2_copy_safety.py`, `tests/test_s3_startup_fallback.py`, `tests/test_s4_s5_s8_ui_truth.py`. Record which are missing. If the S1/S2 worker is still editing, files under src/app/services/extraction.py, src/app/services/ingestion.py or src/app/flows/sync.py may change while you run; record the last-modified time (`(Get-Item <file>).LastWriteTime`) of those three files at the start and at the end of your run.

## Step 1: lint and types
- `uv run ruff check . ; echo exit=$LASTEXITCODE`
- `uv run ruff format --check . ; echo exit=$LASTEXITCODE`
- `uv run ty check src ; echo exit=$LASTEXITCODE`
Record the full text of any error (rule, file:line, message).

## Step 2: full backend suite (no browser)
`uv run pytest -q --no-cov -p no:warnings --ignore=tests/ui` with the capture pattern from rule 5.
Record: exit code, summary line if present, every FAILED/ERROR test id with its one-line error. For each failure also run that single test alone once (`uv run pytest "<test id>" -q --no-cov -p no:warnings`) and record whether it passes alone (an order-dependent failure is important information).

## Step 3: business-logic subset (the Makefile's list, `make` is not installed)
`uv run pytest -q --no-cov -p no:warnings tests/test_business_scenarios_ui.py tests/test_reconciliation.py tests/test_edge_reconciliation.py tests/test_matching.py tests/test_grouping.py tests/test_merge.py tests/test_customs.py tests/test_quarantine.py tests/test_dashboard.py tests/test_best_case_pipeline.py tests/test_untested_engine_paths.py` with the capture pattern.

## Step 4: browser suite, three runs (Chromium; needs RUN_E2E=1)
Run A: `$env:RUN_E2E='1'; uv run pytest tests/ui -q --no-cov -p no:warnings` (capture pattern).
Run B: the same again.
Run C: reversed file order: list the files with `Get-ChildItem tests/ui -Filter 'test_ui_*.py' | Sort-Object Name -Descending | ForEach-Object { "tests/ui/$($_.Name)" }` and pass them to pytest in that order.
Run D: `$env:RUN_E2E='1'; uv run pytest tests/ui/test_ui_settings.py -q --no-cov -p no:warnings` three times in a row.
Record for each run: exit code, passed/failed/xfailed counts if visible, every FAILED test id and its error. List every XFAIL/XPASS (use `-rxX`) with its reason text, because XPASS(strict) means a suspected bug was fixed and the marker must be removed.

## Step 5: extra checks
- `uv run pytest -q --no-cov -p no:warnings tests/test_ws6_concurrency.py tests/test_ws6_d21_counts.py tests/test_ws6_d2_original_pages.py tests/test_ws6_poller_soak.py tests/test_ws6_restart_recovery.py` three times in a row (flakiness check). Record any run that differs.
- PowerShell parse check of every script (record the error count, must be 0): for each of `scripts/setup_test_rig.ps1`, `scripts/install_nssm_services.ps1`, `scripts/manage_services.ps1`, `scripts/backup_db.ps1`, `scripts/verify_backup.ps1`:
  `$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("C:\Users\malliath\Desktop\aam_merger\<file>",[ref]$t,[ref]$e); $e.Count`
- `.\scripts\install_nssm_services.ps1 -WithPrefectServer -DryRun` (a dry run: changes nothing, needs no admin). Record the exit code and whether the Web service line shows NO DependOnService and the Worker line shows DependOnService AAMMerger-PrefectServer.

## Step 6: write the final report
At the top of `docs/VERIFY_REPORT_2026-10-25.md` put a table: step, command, exit code, result (PASS / FAIL / ERROR), counts. Then:
- "Failures": for each, test id, exact error text (first 15 lines of the traceback), whether it passes alone, which files in `git status` look related.
- "XPASS / changed xfails".
- "Flaky" (anything that differed between repeated runs).
- "File times" from Step 0 (start vs end) so Claude can tell if code moved during the run.
- "Not run / could not run" with the reason.
Be factual and brief. Do not guess causes: write "cause unknown" when you do not know. Do not suggest code changes.
