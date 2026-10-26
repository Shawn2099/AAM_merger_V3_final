# Brief for the UI test agent (DeepSeek via OpenCode)

You are writing browser tests for AAM Merger V3, a PO/DN/SI reconciliation dashboard. Your job is
to implement the TODO cases in `docs/UI_TEST_PLAN.md`, prove each one is stable, and report what you
find. You are not asked to change the product.

## Read, in this order
1. `docs/UI_TEST_PLAN.md` (the work list and the harness)
2. `tests/ui/conftest.py`, then `tests/ui/test_ui_set_detail.py` (the style to copy)
3. `CLAUDE.md` sections "Conventions that bite" (integers x1000, ban list)

## Hard rules
1. **Edit only** `tests/ui/**` and, when you truly need a new seeded state, `scripts/ui_test_env.py`.
   Do NOT edit `src/`, `templates/`, `alembic/`, `config*.yaml`, or any other test file.
2. **Never run the real app against real data.** Only the harness environment (a temp folder). Never read or
   write `data/aam_merger.db`, `data/input*`, `data/stored`, `data/output`, or `config.yaml`.
3. **No network, no API keys.** The harness has no provider key on purpose. If a test seems to need one, stop and report.
4. **Do not make a test pass by weakening it.** Never change an expected value, loosen an assertion, add a
   retry loop, or add `skip`/`xfail` to get green. If the app disagrees with the plan, that is a finding.
5. **Product bugs are findings, not fixes.** When the page is wrong or a test cannot pass, mark the test
   `@pytest.mark.xfail(strict=True, reason="SUSPECTED PRODUCT BUG UI-xx: <one line>")` and describe it in the
   report with evidence. Do not edit the product.
6. **Do not commit, push, or touch git.** Leave changes in the working tree.
7. New test files in `tests/ui/` MUST be named `test_ui_<area>.py` (module names must be unique across `tests/`; a name like `test_dashboard.py` already exists elsewhere and breaks collection of the whole suite).
8. If something in the plan is ambiguous, say what you assumed in the report instead of guessing silently.

## How to work
- One case at a time. Write the test, run it alone (`-k ui_s_09`), then run the whole `tests/ui` suite.
- Use `page.get_by_test_id(...)` and `expect(...)` (it auto-waits). No `time.sleep`, no CSS class selectors,
  no XPath by position. If a needed element has no `data-testid`, report it (the maintainer will add one) and use
  the best role/label locator meanwhile.
- Tests that change data must `page.expect_response(...)` for the request they trigger, and each changes only the
  one `mutable` scenario assigned to it. Verify state with the read-only `db` fixture, not by trusting the page alone.
- Compare what the page shows with the DB or manifest, never with a number you typed from looking at the screen.
- You have no eyes: for layout, use measurements (`page.evaluate("document.documentElement.scrollWidth")`,
  bounding boxes) or `locator.aria_snapshot()`. Do not claim anything about colours or looks.
- Quantities are integers scaled x1000 in the DB; the pages show them divided by 1000.

## Commands (PowerShell, from the repository root)
```powershell
$env:RUN_E2E = "1"
uv run python -m pytest tests/ui -o addopts= -p no:cacheprovider -W ignore -q                 # whole suite
uv run python -m pytest tests/ui -o addopts= -p no:cacheprovider -W ignore -q -k ui_s_09        # one case
uv run python -m pytest tests/ui -o addopts= -p no:cacheprovider -W ignore -q --headed --slowmo 300 -k ui_g_02
uv run ruff format tests/ui scripts/ui_test_env.py ; uv run ruff check tests/ui scripts/ui_test_env.py
```
Before reporting: the whole suite passes 3 times in a row, ruff is clean, and
`uv run python -m pytest tests/test_manual_merger_e2e.py -o addopts= -p no:cacheprovider -q` still passes.

## Report format (reply with exactly this)
```
DONE:      <case ids implemented and stable>
FINDINGS:  <id> | <page> | <what you saw> | <expected> | <evidence: test name, request/response, DB value>
ASSUMED:   <anything you decided without being told>
NEEDS:     <missing data-testid / missing seeded state / anything blocking>
FILES:     <files changed>
RUNS:      <suite result x3, ruff, manual-merger e2e>
```
Keep it factual. Do not praise the code and do not speculate about causes you did not verify.
