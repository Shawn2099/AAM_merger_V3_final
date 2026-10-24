# AAM Merger V3: pre-deployment plan, decision log and delegation packets

Written 2026-10-25. This file is the single plan for getting from "MVP code complete" to "running on the office PC".
It is written so that **any session or tool** (Claude Opus, DeepSeek via OpenCode, a human) can pick up one packet
without the rest of the history. `HANDOFF_PLAN.md` stays the live log; this file is the forward plan and the decision record.

## 0. Operating model (user decision 2026-10-25)

The user has little time. Roles from now on:

| Role | Who | Does | Never does |
|---|---|---|---|
| **Architect + reviewer** | Claude (Opus for reviews and plans) | Designs, writes the packet briefs, reviews every diff, decides pass/fail, keeps this file and the decision log current, talks to the user | Token-heavy bulk work (writing many tests, running long loops, soak runs) when a worker can do it |
| **Worker** | DeepSeek via OpenCode (`opencode:opencode-rescue` agent or `/opencode:rescue`) | Writes tests, runs them, runs long/iterative jobs, mechanical edits inside a packet's allowed paths | Decides behaviour, edits outside its allowed paths, commits, touches real data or paid APIs |
| **Owner** | The user | Answers the decisions in section 3, gives go-ahead for the real-data run, does the click-through on the target PC | |

Rules for every packet:
1. A packet names its **allowed paths**, **forbidden paths**, **exact commands**, and an **acceptance test**. The worker stops and reports on ambiguity.
2. The worker leaves changes uncommitted. **Claude reviews the diff, re-runs the suite, then commits by naming files.**
3. Anything touching reconciliation, grouping, merge, migrations or the Prefect deployment gets an **Opus review before commit** (CLAUDE.md).
4. Worker output is never trusted on its own word: the reviewer re-runs the named commands and reads the diff. A test made green by weakening it is rejected.
5. No real corpus, no paid provider, no `data/` folder (except the throwaway harness `data/ui_test`), no `config.yaml` in any packet. The one real run is WS9, last, on a NEW database, with the user's go-ahead.
6. Standing user rules still apply: precision over recall (doubt means hold, not merge); do not delete anything without asking; no force-push, never push `main`; keep this file and `HANDOFF_PLAN.md` updated.

## 1. Where we are (2026-10-25)

- Branch `fix/mvp-high-priority`. The Opus review fixes are committed and pushed (`905d974`; full suite 6355 passed, ruff and ty clean). WS0 is DONE. The worker is on WS1b in the OpenCode desktop app.
- Done: pipeline, fallbacks, guards, quarantine report, subfolder reading, 400 circuit breaker, poller + queue + settle + nightly switch, UI test harness (20 reference tests), Opus review of the whole branch and its fixes.
- **Readiness verdict:** ready for a *supervised pilot* on the target PC once WS1b, WS1 (triaged) and WS4 are done and Q7 is answered. **Not ready for unattended production**: WS2 (misread hold), WS3 (settings page, if the user still wants it before go-live), WS5 (hardening) and WS9 (real run on the target) are open. See section 5 for the gates.

## 2. Decision log (every decision so far; newest facts win; "Where" says which doc holds the detail)

| ID | Decision | Date | Status | Where |
|---|---|---|---|---|
| P1 | Product is the 2-layer core: PO/DN/SI reconcile exactly, one merged PDF per PO. Extras judged against it. | earlier | final | memory core-product-intent |
| P2 | Precision over recall: a wrong merge is the worst outcome; doubt means hold/quarantine | earlier | final | memory precision-over-recall |
| P3 | Normal flow assumes one doc type and one PO per PDF; combined/fragments are a separate future module | earlier | final | memory scope-single-doc-single-po |
| P4 | Manual merger is a KEEP feature | earlier | final | memory manual-merger-keep |
| P5 | Never trust the embedded PDF text layer; images go to the model | earlier | final | memory distrust-embedded-pdf-text |
| P6 | Commit and push regularly by naming files; no force-push; never push `main` | earlier | final | memory |
| D1 | One-digit PO misread: do not merge a suspected misread; hold it and let the operator choose (move page to the right PO, "not a misread", or Force Merge). No second read, no extra API calls | 2026-10-24 | decided, **not built (WS2)** | HANDOFF 6c |
| D2 | "1 of 6" POs with only page 1 supplied: match and merge if the PDF has everything the program needs; the packet uses the ORIGINAL PDF pages, never the PNGs sent to the model | 2026-10-24 | decided; verify in WS1/WS6 | HANDOFF 6c |
| D3 | R-13 (split-gate reason overwritten) deferred to the future combined-file module | 2026-10-24 | deferred | HANDOFF |
| D4 | Extraction validation / hallucination defence is NOT in the initial MVP; revisit after real-world data | 2026-10-24 | deferred | HANDOFF 7 |
| D5 | Poll the input folder every 1 minute; all waiting files, oldest first, nothing skipped, overlapping ticks coalesced; each set reconciled and merged immediately (settle time default ON, 5 min); nightly sweep stays as a switch; merge only if every condition is satisfied; no parallel extraction (no big refactor now) | 2026-10-24 | built | PRODUCT 8, `tests/test_poller.py` |
| D6 | pymupdf AGPL licence decision | later | deferred to production phase | HANDOFF |
| D7 | Real-corpus run only at the very end, on a NEW database, after all dev and testing, with the user's go-ahead (paid provider) | 2026-10-24 | final | WS9 |
| D8 | Read subfolders of the input folder; skip zero-byte files for now (a future module may use them) | 2026-10-24 | built | PRODUCT |
| D9 | A PO-less DN found only by its number (not by PO) is never merged on that alone: the set holds (`placed_by_number_only`) until the operator confirms or Force Merges | 2026-10-24 (Opus review) | built | PRODUCT 8 |
| D10 | Per-PO lock timeout default 900 s; an older shorter value is raised with a warning, 0/tiny refused | 2026-10-24 | built | config.py |
| D11 | Config must become fully editable from the UI; auto-run timings and folder polling to be finalised; then production deployment decisions and full testing | 2026-10-24 | **open, needs the answers in section 3 (WS3)** | HANDOFF 6b |
| D12 | **Operating model: Opus as architect/reviewer, DeepSeek via OpenCode as worker for token-heavy work; keep this decision log; plan before deployment so other sessions/tools can implement and test** | 2026-10-25 | this file | section 0 |
| D16 | Q7: a supervised pilot may run WITHOUT the misread hold for the first week only (operator checks every merged packet); after that WS2 must be live (no unattended running without it). Q8: after a manual Move the target set is NOT exempted from the single-PO guard; it holds as `multiple_po_documents` and the operator Force Merges (audited). No guard exemption is built | 2026-10-25 | final (owner) | WS2 spec |
| D17 | **Scope freeze (owner, 2026-10-25): no new features; make what exists work.** The manual merger already covers a wrongly read PO (build the right packet by hand). Therefore: PF-11 (status pill) is DROPPED; the WS2 "Move to another PO" action is DROPPED. Fixes to existing behaviour (PF-01..PF-10, bugs, tests, install, docs) continue. Whether the WS2 misread HOLD itself is built is an open owner question (Q9); until answered it is NOT built | 2026-10-25 | final (owner); Q9 open | specs |
| D15 | Opus review of the plans (2026-10-25): WS2 spec rejected and rewritten (rev 2); PF-11 now persists run results in `last_sync.json` because the nightly run is another process; packets that share files run serially; PF brief got binding corrections | 2026-10-25 | applied | specs, brief |
| D14 | Pre-deploy UI fixes PF-01..PF-10 approved (WS1b); archive justification optional; Re-Extract state machine unchanged; add PF-11 system-status pill | 2026-10-25 | approved, in progress | WS1b |
| D13 | Not fixed on purpose, documented: Origin guard compares host names only; upload has no size middleware; unused config keys; per-minute re-hash of stuck input files; split-parent cleanup by name; no installer DependOnService for Prefect | 2026-10-24 | open (WS5) | PRODUCT 8 "operating limits" |

## 3. Decisions still needed from the user (answer in one line each; defaults are my recommendation)

| # | Question | Recommended default | Blocks |
|---|---|---|---|
| Q1 | Which settings may be edited live from the UI? | Live: polling interval/enable, settle minutes, nightly switch, manual-merge limits that are read per request. Restart-needed: paths, model id, lock timeout. Never in UI: API key, allowed_hosts. Every edit writes an audit row; invalid values are refused with a message and the old file stays | WS3 |
| Q2 | Does the settings page need a login? | No login (LAN, same as the rest), but only reachable from the allowed hosts, and each edit audited | WS3 |
| Q3 | Keep the midnight Prefect sweep on after polling is live? | Yes, on, as a safety net (cheap: nothing new means nothing to do) | WS3/WS4 |
| Q4 | Is the settings page required for the FIRST go-live, or can the pilot run with `config.yaml` edited by IT? | Pilot first with `config.yaml`; settings page after the pilot | order of WS3 |
| Q5 | pymupdf AGPL: accept (internal use only, never distributed), replace the rasteriser, or buy a licence? | Accept for internal use, record the decision, revisit if the software is ever shipped to others | WS8 |
| Q6 | Who is the on-site contact for the Windows Server click-through and the first midnight run? | needed | WS9 |
| Q7 (ANSWERED 2026-10-25: yes for the first week only; afterwards WS2 is required) | May a SUPERVISED pilot run before WS2 exists? Until then a one-digit PO misread can still produce a wrong merge (precision rule P2). | Yes, only with the operator checking every merged packet's page count for the first week; no unattended run | G2 |
| Q9 | With the scope freeze (D17): build the misread HOLD at all (a new guard + one migration + "Not a misread" button), or rely on the manual merger plus first-week supervision? Recommendation: do NOT build it now; the first real week shows how often a misread occurs; revisit with data. This overrides the G3 line that made WS2 mandatory | recommended: skip for now | G3 |
| Q8 (ANSWERED 2026-10-25: accept option a: a hand-placed page does not exempt the guard; the operator Force Merges) | After "Move to another PO" the target has two PO documents and the single-PO guard holds it. Accept (operator Force Merges) or exempt a hand-placed PO page with zero line items? | Exempt (spec WS2 option b) | WS2 Move action |

## 4. Work packets

Order (Opus review 2026-10-25): packets that share files run SERIALLY: **WS1b -> PF-11 -> WS2 -> WS1** (they all touch `scripts/ui_test_env.py`, `tests/ui/test_ui_set_detail.py`, `templates/po_set_detail.html`, `dashboard.py` or `poller.py`). WS6 (new test files only) may run in parallel with any of them. WS5 starts after WS1b and each item names its own allowed paths. Each packet ends with a Claude review; reconciliation hunks are written by Claude, not the worker.

### WS0. Land the review fixes (Claude, now)
- Run the full suite once more (`uv run python -m pytest -q -W ignore -o addopts= -p no:cacheprovider`), ruff, ruff format, `ty check src`.
- GitNexus detect-changes, staged-tree check in a `git checkout-index -a --prefix=<tmp>/` copy, commit by naming files, push.
- Update test counts in HANDOFF. Acceptance: green suite on the pushed commit.

### WS1. Complete the UI test suite (DeepSeek, then Claude review)
- Brief: `docs/OPENCODE_UI_TEST_BRIEF.md` (already written, rules included). Work list: `docs/UI_TEST_PLAN.md` (~45 TODO cases).
- Add cases for the two newest operator states: `settling` (set held during the settle window) and `placed_by_number_only` ("Confirm it belongs here" button, `btn-confirm-placement`; needs a seeded scenario in `scripts/ui_test_env.py`).
- Allowed: `tests/ui/**`, `scripts/ui_test_env.py`, and `data-testid`-only edits in `templates/` (the hook `btn-confirm-placement` exists already; check before asking). Forbidden: `src/`, `alembic/`, configs, other tests. Runs AFTER WS1b, PF-11 and WS2 so the cases are written once against the final pages.
- Acceptance: all cases implemented or marked `xfail(strict)` with "SUSPECTED PRODUCT BUG UI-xx"; whole `tests/ui` suite green twice in a row and in reverse file order; report lists findings. Claude triages each finding into fix / won't-fix / test error.

> **Scope freeze D17: PF-11 below is DROPPED. Do not build it.**

### WS1b. Pre-deploy UI fixes from the DeepSeek exploration (reviewed 2026-10-25, APPROVED)
- Evidence: `UI_EXPLORATION_2026-10-24.md` (20 product findings F-01..F-20, 2 harness findings H-01/H-02, verified-working list). Plan: `docs/UI_PREDEPLOY_PLAN_2026-10-25.md` (PF-01..PF-10). Worker brief with the reviewer's overrides: `docs/OPENCODE_PF_BRIEF.md`.
- Reviewer verdict: findings are accurate and well evidenced. **H-01 is the important one**: the UI harness silently reloads the developer's `.env` and spends real money on Re-Extract; PF-01 fixes it (empty-string env var). Approved: PF-01..PF-10 (PF-08 focus trap last). Not changed: Re-Extract status machine (pinned by tests, only the message was dishonest), the type select is never pre-selected. Archive justification stays optional.
- Added by the reviewer, not yet in the plan: **PF-11 operator-visible system status** (is polling on, time of last poll, last sync result incl. "all extractions failed"). Reason: with the 1-minute poller nobody clicks Scan, so F-16 ("a scan's outcome is never shown") becomes the only way an operator learns the system is stuck. Needs a short design (Claude) before the worker builds it; candidate source is `SyncPoller` state plus the last sync result. Tracked as the first item after PF-10.
- Acceptance: whole suite + `tests/ui` x3 green, ruff/ty clean, Claude re-runs and reads the diff before commit.

### WS2. D1 misread hold (T13): Opus designs, DeepSeek builds, Opus reviews
> Specs written 2026-10-25: docs/SPEC_WS2_MISREAD_HOLD.md (this packet) and docs/SPEC_PF11_SYSTEM_STATUS.md (PF-11). Both are ready for the worker once approved.

1. **Claude writes the design spec** (HANDOFF 6c "D1 design notes"): the exact rule (a PO-only set one digit away from a set that has DN/SI), its false-positive risk, the new reason `possible_po_misread`, the three operator actions (move a document to another PO, "not a misread" flag, Force Merge), audit rows, and the tests that must exist. No code before the spec is approved.
2. **Claude writes the reconciliation hunk; DeepSeek implements** the routes (`po_sets.py`), `models.py`, the migration (`alembic/versions/`, `scripts/migrate_db.py` marker), `templates/po_set_detail.html`, the seed scenario and `tests/test_misread_hold.py`. Spec rev 2: `docs/SPEC_WS2_MISREAD_HOLD.md`; the Move action waits for owner question Q8. The "move a document to another PO" action is new: no UI path moves an attached document today.
3. **Claude reviews as Opus** (reconciliation change). Acceptance: the false-positive cases in the spec each have a test (two real POs that differ by one digit and both complete must still merge); `make business-logic` passes; hold is visible on the dashboard and set page.

### WS3. Settings page (D11): build only after Q1 to Q4 are answered
- Spec first (Claude): list of keys with live/restart class, validation, atomic write of `config.yaml` with a backup copy, rollback on invalid, audit row, re-register the Prefect deployment when the nightly switch or cron changes, secrets excluded.
- Build (DeepSeek): route `src/app/api/routes/settings.py`, template `templates/settings.html`, tests `tests/test_settings_page.py`, UI cases in `tests/ui/test_ui_settings.py`.
- Acceptance: a bad value never leaves the app unable to start; every edit audited; round-trip test (edit, restart, value kept).

### WS4. Windows installation and operations (DeepSeek drafts scripts and checks; Claude reviews; user runs on the target)
- Make the web service start after the Prefect service (NSSM `DependOnService`), in `scripts/*.ps1`.
- Make step 6 of `docs/DELIVERY.md` enforceable: the install script refuses to finish without `server.allowed_hosts` set to the PC's real name/address.
- Backup task, restore drill (write the steps and a verification script that compares row counts and a sample file hash), power-loss recovery notes.
- Acceptance: scripts pass PowerShell syntax check (`[System.Management.Automation.Language.Parser]::ParseFile`) and a dry-run mode that prints actions without changing anything.

### WS5. Hardening backlog (D13): small, independent, DeepSeek with Claude review
Pick in this order; each is its own commit:
1. Request-size limit for the upload route (reject before spooling).
2. Origin guard: also compare the port and require the scheme to match.
3. Remove or wire the unused config keys (`server.host`, `server.port`, `prefect.max_concurrent_extraction_tasks`): remove from the model and example config, with a one-time "ignored key" warning for old files.
4. Skip re-hashing unchanged stuck input files (cache by path, size, mtime) so the 1-minute poll stays cheap as stuck files accumulate.
- Acceptance: a `test_*` for each; no change to merge behaviour (full suite green).

### WS6. Regression, concurrency and soak tests (DeepSeek: token-heavy; Claude reviews)
All with the canned VLM, no network:
- Poller soak: 500 synthetic documents arriving over simulated minutes, assert oldest-first order, no document skipped, no duplicate merges.
- Concurrent Force Merge + poller + manual Scan on the same set: exactly one wins, others get 409, no corrupt output.
- Restart recovery: kill mid-sync (simulated), restart, assert no stuck lock and no lost document.
- D2 check: a "1 of 6" PO with the first page only merges using the ORIGINAL PDF pages (compare page bytes/count against the stored upload).
- Allowed: `tests/**` new files only, `scripts/soak_*.py`. Acceptance: deterministic (3 runs identical), documented runtime.

### WS7. Documentation and runbook (DeepSeek drafts, Claude edits)
- Operator runbook: what each status means, what to click for each hold reason (table already in `quarantine.py` WHAT_TO_DO), daily checks, what to do when the VLM halts, how to read the quarantine report.
- Update `docs/DELIVERY.md` and `AAM_merger_V3_PRODUCT.md` for anything changed by WS2 to WS5.

### WS8. Licence and policy decisions (user, with Claude's one-page summary)
- Q5 (pymupdf). Record the outcome in D6.

### WS9. Final proof on the target (LAST; user go-ahead; Claude supervises, user clicks)
1. Fresh install on the Windows Server 2016 PC from `docs/DELIVERY.md` on a **NEW database**.
2. Click-through of every screen (UI_TEST_PLAN as the checklist).
3. One midnight Prefect run observed.
4. **Real-corpus run on the NEW database** (paid provider, user go-ahead): compare results with the earlier real run explained in HANDOFF (24 pending sets are known causes; requeue dry-run predicted sets 39, 40, 42, 48, 50 would merge).
5. Backup, restore drill, reboot test.
- Acceptance: nothing merged that a human would not sign; every non-merged set has a visible reason and an action.

## 5. Gates

| Gate | Needs | Allows |
|---|---|---|
| **G1 Committed** | WS0 | Anyone can pull the branch and run the suite |
| **G2 Pilot-ready (supervised)** | G1, WS1b done (the UI harness no longer spends real API money), WS1 triaged, WS4, Q7 answered | Install on the target PC, operator watches, `config.yaml` edited by IT |
| **G3 Production-ready (unattended; owner: WS2 mandatory after the first pilot week)** | G2, WS2, WS5, WS6, WS7, Q5, and WS3 if Q4 says it is required | Leave it running; the nightly sweep and poller run without a watcher |
| **G4 Accepted** | WS9 | Handover signed |

## 6. Delegation packet template (copy for each OpenCode session)

```
ROLE: You are the worker. Claude is the reviewer. Do exactly this packet and nothing else.
PACKET: <WSn title>
READ FIRST: CLAUDE.md "Conventions that bite", docs/CORE_LOGIC.md, <packet-specific files>
ALLOWED PATHS: <list>      FORBIDDEN: src/ (unless listed), alembic/, config*.yaml, data/, .env, git commands
NO NETWORK, NO API KEYS, NO REAL DATA.
STEPS: <numbered>
COMMANDS TO RUN: <exact, PowerShell, repo root>
ACCEPTANCE: <testable statements>
DO NOT: weaken a test to pass it, add skip/xfail except as the brief allows, change expected values, commit or push.
REPORT: files changed, commands run with the final pass/fail lines, assumptions you made, anything suspicious in the product (finding, not a fix).
```

## 7. Reviewer checklist (Claude, per returned packet)
1. `git diff --stat`: only allowed paths touched?
2. Re-run the packet's commands; compare with the worker's report.
3. Read the diff for weakened assertions, added sleeps/retries, new skip/xfail, hard-coded expectations.
4. Full suite, ruff, ruff format, `ty check src`.
5. For core areas: Opus review, then GitNexus detect-changes and the staged-tree check before committing by named files.
6. Update this file (status, decisions) and `HANDOFF_PLAN.md`.
