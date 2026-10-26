# Opus whole-project review, 2026-10-25

**Scope:** branch `fix/mvp-high-priority` at `4689a53`, plus the uncommitted working tree (the `_oldest_first`
sort in `ingestion.py`, the changed `missing_dn_document` text, the template `data-testid` hooks, and the
WS1/WS6 tests).

**Method:** I read the code against `docs/CORE_LOGIC.md`, `AAM_merger_V3_PRODUCT.md`, `HANDOFF_PLAN.md`,
`docs/DELIVERY.md` and `docs/RUNBOOK.md`. I also ran `ruff check`, `ruff format --check`, `ty check src`
and the non-UI pytest suite.

**What is not executed:** every finding marked *(read)* comes from reading the code. My scratch probes
for findings B2 and B3 could not run because the shell permission check kept failing, so neither is
proven by an executed test. The exact reproduction for each is given below.

---

## 1. Verdict

**GO WITH FIXES** for "install on this PC and run the 695-PDF end-to-end run".

No path was found that merges a set when PO ≠ ΣDN or PO ≠ ΣSI on the line keys the engine compares.
The comparison is integer-exact and shared by the engine and the set page. Every merge goes through
`reconcile_po_set` → `merge_po_set`, apart from Force Merge and the stand-alone manual merger.

The run should not start until these are dealt with:

- **B1**: the test suite is red.
- **B2**: the duplicate-DN/SI guard can be bypassed. This is a wrong-merge class.
- **B3**: a PO-bearing DN can be placed by DN-number collision into an unrelated set. It quarantines
  that set and strands the DN. The code pins this as an accepted limitation; I disagree that it is
  acceptable.
- **B4**: this PC's `config.yaml` makes `Install.cmd` refuse.
- **B5**: run preconditions (sleep, key/credit smoke test, copy-then-scan).

---

## 2. Blockers (fix before the install / e2e run)

### B1. The suite is red because of the uncommitted `_oldest_first` change

`src/app/services/ingestion.py:280-327` now returns files oldest mtime first, and three tests still
assume path order.

**Evidence** (full non-UI run: `3 failed, 6505 passed, 8 skipped, 43 xfailed`, 9 min 36 s):

- **Deterministic failures.** Both fail again when their file is run on its own:
  - `tests/test_robustness_batch_sync.py::test_good_set_still_merges_beside_seventy_bad_neighbours`
    (`:393`)
  - `tests/test_robustness_batch_sync.py::test_byte_identical_copy_with_long_unicode_name_is_deduplicated_and_cleared`
    (`:384`)
  - What changed: the test expects the byte-identical copy that sorts first by path
    (`Bestellung…`) to become the document, with `good_PO.pdf` de-duplicated. Oldest-first now ingests
    `good_PO.pdf` first.
- **Flaky.** `tests/test_best_case_pipeline.py:280` (`found == sorted(found)`) failed in the first run
  and passed in the second. Whether it fails depends on mtime ties.
- **Order-dependent.** `tests/test_settings_page_more.py:291` (`test_b_08_…`, file modified in the
  working tree) failed in the full run and passes on its own. The likely cause is global
  `_REPORTED_BAD` / `_LAST_GOOD` state left in `config.py:473-474` by an earlier test.

**Why it matters:** the commit rule (HANDOFF section 8.5) is a green suite first.

**Minimal fix (tests only, no product change):**

- Assert the new contract in the two batch tests and in `test_best_case_pipeline`: oldest mtime
  first, path as the tie-break (as `test_ws6_input_order_is_oldest_first` already does). Set explicit
  mtimes with `os.utime`.
- Give `test_b_08` an autouse fixture that clears `_LAST_GOOD`, `_REPORTED_BAD` and `_CONFIG_CACHE`.

**Product note:** which filename a byte-identical pair keeps now depends on mtime. With an Explorer or
robocopy copy, mtime is the *source's* last-write time, so "oldest first" means "oldest original
document", not "first to arrive". That is harmless, but PRODUCT and D5 should say so.

### B2. The duplicate-document guard misses a rescan whose number is unreadable or confusable (wrong-merge class) *(read)*

- **Where:** `src/app/services/reconciliation.py:680-708`.
  - `_number_key(None)` is `""` and `_number_key("DN-77")` is `"DN77"`, so the two copies land in
    different `by_number` buckets.
  - The overlap check only runs inside a bucket (`if len(group) < 2: continue`).
  - `_number_key` does not fold confusables: `GDN-RHO-25-513` and `GDN-RH0-25-513` (letter O vs
    digit 0) are different keys.
- **Scenario:**
  - PO line 1 is 10.
  - DN-77 (5) was scanned twice; the two scans are different bytes.
  - On one scan the VLM returns `document_number: null`. The prompt tells it to return null when any
    character is unreadable, which is likely on a poor scan.
  - SI invoices 10.
  - Result: ΣDN = 5 + 5 = 10 = PO = SI, so the set **merges**. The packet carries the same delivery note
    twice and the real second delivery is missing.
- **Tests:** `tests/test_core_review_fixes.py:149` covers two number-less copies (None, None) only. No
  test covers (None, "DN-77") or the O/0 pair.
- **Probe to add (expected to fail today):**
  `_dups(cfg, [("DN-77", [("1", 5000)]), (None, [("1", 5000)])])` must not return `merged`.
- **Minimal fix (about 10 lines, safe direction):**
  - A DN (or SI) whose number key is `""` clashes with *every* other same-pool document that shares a
    normalised line number, not only with other number-less documents.
  - Build the duplicate key with confusables folded (O→0; I and L→1). Use this fold only for this
    guard, never for grouping.
  - The cost is more holds, never fewer. That is the owner's rule.

### B3. A DN that prints its own PO can be pulled into a different set by DN-number collision *(read; I disagree with the pinned limitation)*

- **Where:**
  - `src/app/services/grouping.py:511-520`: the query has no `po_no_normalized` filter.
  - `grouping.py:538-554` (strategy 1, by DN number).
  - `src/app/flows/sync.py:528-539`: `resolve_unattached_documents` runs **before**
    `attach_unattached_to_open_sets`.
  - Pinned as current behaviour by `tests/test_known_limitations.py:82`
    (`test_known_limitation_dn_number_collision_attaches`).
- **Scenario (normal for the 695 run, which reads files oldest first):**
  1. The DN for PO 777 is read before its PO, so it has no open set and waits unattached.
  2. PO 777 is read later in the same pass and mints set 777.
  3. In recovery, step 2 runs first. If any document in another open set (say 456) carries the same
     DN number (short numeric DN numbers repeat across vendors), the DN is attached to **456**.
     Step 3, which would have put it in 777, never sees it.
- **Effect:**
  - No wrong merge: `po_reference_mismatch` (`reconciliation.py:573-608`) quarantines 456.
  - But 456 is innocent and its quarantine is sticky.
  - 777 waits forever on "No delivery note attached…".
  - There is no UI path to detach an attached document (`dashboard.py:1494-1504`). The operator has to
    archive 456 and re-add its files.
  - This pollutes exactly what the e2e run is meant to measure.
- **Minimal fix (one line, no new feature):** in `resolve_unattached_documents`, skip documents that
  carry their own PO key (`if doc.po_no_normalized: continue`). Those are `attach_unattached_to_open_sets`'
  job, and the DN-number route exists for documents with *no* PO printed. Alternatively, swap steps 2
  and 3 in `recovery_flow`.
  - Either way, update `test_known_limitations.py:82` and PRODUCT section 8 entry M.
  - Re-run `make business-logic`; `tests/test_per_line_dn_association.py` and
    `tests/test_mvp_blockers.py` exercise this sweep.

### B4. `Install.cmd` will refuse on this PC with the current `config.yaml`

- **Where:**
  - `config.yaml` (this PC) has no `server.allowed_hosts`.
  - The auto-fill in `scripts/setup_test_rig.ps1:131-149` only runs when `config.yaml` is **missing**.
  - `scripts/install_nssm_services.ps1:151-161` treats a missing list as unsafe and exits 1.
  - `Install.cmd` does not pass `-AllowAnyHost`.
- **This config is also stale in other ways:**
  - No `schedule:` section, so `poll_enabled` defaults to **False** (`config.py:214`) and the D5 folder
    watcher is **off** on this PC.
  - `po_set_lock_timeout_seconds: 300` is silently raised to 802 with a warning (`config.py:340-353`).
  - No `stale_processing_seconds`, `max_pages` or `line_number_fallback` (the defaults apply).
  - The `single_po_document` comment says "quarantines"; the code holds the set pending.
- **Minimal fix:** move the old `config.yaml` aside and let the installer regenerate it from
  `config.example.yaml` (it holds no custom paths), or add `allowed_hosts` and `schedule:` by hand. Then
  decide explicitly whether the poller runs during the e2e run.

### B5. Preconditions for the paid 695-file run (runbook, no code)

1. **Power plan: never sleep.** Win10/11 defaults to sleeping after 15-30 minutes. A sleeping PC
   suspends the poller, the midnight run and LAN access, and can pause a ~2 h pass (200 files took
   33 min, so 695 files is about 2 h). Nothing in the installer sets this.
2. **One-file smoke test with the paid key first**, and keep watching the credit balance. An auth,
   credit or config failure does not stop the pass (see S1). With the key missing or the credits used
   up, each document costs about 44 s of retries in both the main loop and the recovery loop. For 695
   files that is roughly 8.5 hours of a pass that achieves nothing, and operator actions answer 409 the
   whole time.
3. **Copy first, then scan.** Copy `real_samples/*.pdf` into `data/input` while no sync can run (the
   poller is off on this config; otherwise stop `AAMMerger-Web`), then press Scan. This sidesteps S2
   for the run.
4. **Expect 409s for the whole first pass.** Every operator action returns 409 while a sync runs
   (`po_sets.py:73`, `dashboard.py:784,1433`). Review the results after the pass ends. If the poller
   is on, switch it off on the Settings page during review; otherwise each new-file pass re-hashes,
   re-parses and re-stability-polls every unmerged file (about `ceil(n/8)*4` s) and blocks actions
   again.
5. **Do not run `Install.cmd` on this dev PC unless you mean to.** `uv sync --no-dev`
   (`setup_test_rig.ps1:101`) removes pytest and playwright from the shared `.venv`.

---

## 3. Should-fix before the client deployment

### S1. Infrastructure errors that will not clear in this run do not stop the pass *(read)*

- **Where:** `extraction.py:807-837`. 401, 402, 403 and 404, plus `RuntimeError("... not configured")`,
  are all treated as infrastructure errors. Each one refunds the attempt and re-raises.
- **How the cost adds up:**
  - The Prefect retry envelope runs 4 times with 2/5/15 s backoff (`flows/sync.py:285-295`).
  - Then `recovery_flow` step 1 (`sync.py:453-472`) reads every still-pending document **again** in
    the same pass.
- **Effect:** about 8 calls and 44 s per document per pass, unbounded across passes. There is no
  breaker like the HTTP-400 one, so a missing `.env` on a fresh install, or exhausted credits, turns a
  pass into hours of 409s.
- **Timeouts:** a document that always times out (180 s × 8 = 24 min per pass) is retried on every
  pass that any new file triggers. Whether OpenRouter bills client-timed-out requests is **uncertain**.
- **Minimal fix:**
  - Trip the existing breaker (`_note_provider_400` path) on 401, 402 and 403 and on "not configured";
    those cannot succeed on the next document either.
  - Do not re-extract in `recovery_flow` a document the main loop already tried in this pass.

### S2. The copy-safety check is weaker than it looks on Windows (owner priority 3) *(read; verify on the target)*

- **Where:** `ingestion.py:24-34` samples the size twice, 2 s apart. The samples for the whole batch are
  taken up front (`sync.py:736`), and a file is ingested up to hours later.
- **The problem:**
  - Explorer, robocopy and SMB copies usually pre-size the destination (`CopyFileEx`). The size is
    final from the first byte while the tail is still zeros, so the size test passes mid-copy.
  - What remains is pypdf (`_incomplete_pdf_problem`) plus the 300 s recent-mtime rule
    (`ingestion.py:63-83`). The mtime rule applies **only when pypdf fails**.
  - pypdf in `strict=False` mode scans back past a zero tail and can open an earlier `%%EOF`. This
    happens with linearised ("fast web view") or incrementally saved PDFs, which are common for signed
    invoices. Such a partial file passes.
  - I traced the outcomes: these end as holds, quarantines or `packet_source_unreadable`, not as wrong
    merges. But a partial copy can be stored and read, which breaks the owner's rule.
- **Minimal fix:**
  - Also require `now - st_mtime >= N` seconds (N ≈ 30-60) and an unchanged mtime across the samples.
  - Re-check the `(size, mtime)` signature immediately before `ingest_file`, not only at batch start.
  - Prove it with the planned slow-copy test over the SMB share (DELIVERY section 5).

### S3. A bad `config.yaml` still kills the program after a restart or reboot (owner priority 4) *(read)*

- **Where:** the last-good fallback (`config.py:466-503`) is in-process only.
  - `main.py:265` calls `load_config()` at startup with no last-good copy, so it raises.
  - NSSM then restarts the web service every 2 s indefinitely.
  - The nightly Prefect run is a fresh process each time, so a bad file also kills every nightly run
    while the web service keeps going on its in-memory copy.
- **Minimal fix (owner decides; arguably a small feature):** at startup, if `config.yaml` is invalid,
  load the newest file in `config_backups/` that validates, log it loudly, and show the existing
  "disk problem" banner. The worker can do the same.

### S4. "Age" and the dashboard order show the last system touch, not the set's age (UI truth) *(read)*

- **Where:**
  - `acquire_lock` and `release_lock` are Core `update()` calls (`locking.py:45-58`, `91-100`), so the
    Column `onupdate` on `po_sets.updated_at` fires (`models.py:233-235`).
  - Every sweep locks and unlocks every open set (`sync.py:404-450`, `559-581`).
  - The dashboard sorts and labels "Age" from `updated_at` (`dashboard.py:138-159`,
    `_dashboard_table.html:96-110`), and so does the quarantine table.
- **Effect:** after each pass every open set reads "just now" and the order reshuffles.
- **Minimal fix:** show "Last checked" for `updated_at`, or use `created_at` for "Age".

### S5. A merged set's matrix is recomputed with today's settings (UI truth; new exposure through the Settings page) *(read)*

- **Where:** `dashboard.py:472-478` calls `compare_po_set_lines(..., line_fallback=cfg.matching.line_number_fallback)`
  with the live config, and `fuzzy_description_threshold` is live too.
- **Effect:** turning off a line-number rule or raising the threshold on the Settings page makes
  existing *merged* sets show "Mismatch" or "Identification Mismatch" rows under a "Merged" badge.
- **Minimal fix:** for `merged` sets, compute with the stored `line_mapping_rule`'s rule (or the full
  default rule set). At least print "computed with current settings" on merged sets.

### S6. Settings keys that should not be one click away for any LAN user (no login) *(read)*

- `reconciliation.single_po_document` (`settings_admin.py:210-217`): **Off** is half-dead.
  - The engine sums both POs, but `merge_po_set` still refuses more than one PO (`merge.py:361-362`).
  - `_reconcile_po_set_inner` then returns `pending` with no reason code, and `_persist_reason` keeps
    whatever older sentence was there (`reconciliation.py:1086-1091`, `392-394`). The page then shows a
    stale reason forever.
  - Fix: make it read-only.
- `extraction.split_combined` (`settings_admin.py:196-203`) re-enables the out-of-scope splitter module
  (HANDOFF section 1) from a browser. Make it read-only.
- `matching.fuzzy_description_threshold` accepts 0-100 (`config.py:133`). A low value lets any
  number-less row attach to the nearest PO line. Show a page minimum of 80.
- `logging.backup_count > 0` with two processes (web and worker) writing one `RotatingFileHandler` file
  (`logging_setup.py:45-50`) fails rollover on Windows with "file in use". Keep it read-only.

### S7. Install and boot (Windows 10/11 fresh folder)

- **No VC++ runtime check:** `setup_test_rig.ps1` never checks for the VC++ runtime (DELIVERY line 76
  says PyMuPDF needs it; **uncertain** for 1.28.2 wheels). Add `& $venvPython -c "import pymupdf"` after
  the sync, with a clear message.
- **Store alias:** the Microsoft Store `python.exe` alias passes `Get-Command python`
  (`setup_test_rig.ps1:90`). `python -m venv` then fails with a confusing message. Prefer uv, or test
  `python -c "import sys"`.
- **uv's Python lives in the installing user's profile:** uv-managed Python is downloaded into the
  installing user's `%APPDATA%`. The LocalSystem services then run a venv whose base interpreter is in
  that profile; it works, but is fragile if the profile is removed (**uncertain**). Over-the-shoulder
  UAC with a different admin account means uv is not on PATH at all.
- **NSSM is not bundled:** no `tools/nssm` folder exists, so the first install downloads from nssm.cc
  (`install_nssm_services.ps1:214-233`). That is a known flaky host and offline LANs fail here. Bundle
  `nssm.exe`.
- **The firewall rule misses the Public profile:** it is `-Profile Domain,Private`
  (`setup_test_rig.ps1:201`), and a fresh Win10/11 network is often classified **Public**. Other PCs
  then cannot connect even though the rule "exists". Either detect the profile and print it, or tell
  IT to set the network to Private.
- **False success when Prefect is slow:** if the Prefect API is not healthy within 30 s
  (`setup_test_rig.ps1:323-334`), the pool and deployment are skipped with a warning. The script still
  prints "Setup Completed Successfully" and exits 0 (`389-391`). A first Prefect start (DB migrations)
  on a slow PC can exceed 30 s, so the midnight run silently never exists. Wait longer, and exit
  non-zero when the deployment is not registered.
- **Re-run safety:** `Install.cmd` and `setup_test_rig.ps1` do not stop the services before
  `uv sync` and `migrate_db.py`. A double-click re-run therefore migrates a live database and may hit
  locked `.pyd` files. DELIVERY section 8 says to stop first, but `Install.cmd` does not. Call
  `manage_services.ps1 -Action stop` at the top when the services exist.
- **Boot order:** Web and Worker `DependOnService AAMMerger-PrefectServer` (`install_nssm_services.ps1:328-331`).
  If the Prefect service fails at boot, the **dashboard does not start either**. The poller already
  backs off when Prefect is down (`poller.py:207-213`), so the hard dependency is not needed for the
  web service. Consider removing it for Web.
- **Logs:** `data\logs\nssm-*.log` and `aam_merger.log` grow forever by design. The Prefect server's
  stdout is the largest. Add a disk-space line to the morning check.
- **DHCP:** `allowed_hosts` is pinned to the IPs at install time (`setup_test_rig.ps1:138-146`). After a
  DHCP change, browsing by IP gets "Invalid host header", while browsing by hostname still works. Say
  this on the dashboard-URL line the installer prints.

### S8. Small truthfulness gaps in text

- **`missing_dn_document`** now says "not received, or still being read"
  (`reconciliation.py:218-220`). A DN can also be **waiting in Unclassified** (letters clash, missing
  PO, or held by B3). Add "or waiting on the Unclassified page".
- **`QUARANTINE.txt` for single documents** (`quarantine.py:498-512`):
  - It always prints `Attempts: n/3 (cap reached…)` and "This file could not be read". It is also
    written for COMBINED files that *were* read (`extraction.py:496-505`) and for ingest rejects (0/3).
  - The "/3" ignores `extraction.max_retries`.
- **Merge Now on a mismatched set** returns `reason: None` (xfail UI-S14b), so the toast does not say
  what disagrees. Return the explained reason.
- **`DELIVERY.md`:**
  - Section 6 says to restart the web service after changing the poll settings; the Settings page
    marks them Live (`settings_admin.py:85-111`).
  - Section 6 also says the sync "moves" input to `stored`. It copies; the input stays until the set
    merges.

---

## 4. Notes / nice-to-have

- **Accepted contract limits that still carry wrong-merge risk** (no new finding; listed so the e2e
  verification looks for them):
  - The strict xfail `test_same_family_different_size_does_not_match`
    (`tests/test_erp_description_quantity_invoice.py:91`): a 96.9 similarity score between different
    filter sizes. Only number-less rows are affected, and quantities must still balance. The
    "Merged • review" badge flags these sets.
  - One-sided letters (`test_erp_po_formats.py:144,153`).
  - x10 re-keying (`test_core_review_fixes.py:174`).
- **The "Needs restart" badge is static** (`settings.html:6-8`). After saving a restart-kind value, the
  page shows the new value while the service still runs the old one, until a restart. Low.
- **Daemon threads die on a service stop:** the Scan and poller syncs run in daemon threads
  (`routes/sync.py:56`, `poller.py:252`). A service stop mid-pass kills them; recovery is via the
  15-minute reclaim (O-4, accepted).
- **The nightly timezone defaults to `Asia/Kolkata`** (`deploy_prefect.py:37`). The code comments
  mention a UAE host. Confirm the office zone (DELIVERY section 4, item 6).
- **Duplicate scans keep their input files:** `_clear_resent_duplicate` (`sync.py:114-148`) only
  clears input for merged sets. Re-sent duplicates of pending or quarantined sets stay in input and are
  re-hashed every pass. This is part of the documented O(N) re-hash limitation.

---

## 5. What I verified and found sound

- **Lint and type checks:** `ruff check .` and `ruff format --check .` are clean (180 files), and
  `ty check src` is clean.
- **Test suite (non-UI):** `pytest --ignore=tests/ui` gave 6505 passed, 3 failed (all tests that
  assumed path order or shared global state, see B1), 8 skipped and 43 xfailed. I did not run
  `tests/ui` (Playwright, needs `RUN_E2E=1`).
- **The rule** (`reconciliation.py:415-926`, `matching.py`):
  - Exact integer ×1000 comparison per normalised line key, with the DN and SI pools independent.
  - Orphan vendor lines quarantine, and zero or unreadable quantities quarantine
    (`reconciliation.py:847-857`). A second PO document holds the set; it is never summed into a merge.
  - The order of the validity guards is right: failed, then unread, then foreign type, then missing
    side, then PO reference, then letters, then placed-by-number, then duplicate, then multiple PO.
  - The set page calls the same `compare_po_set_lines` as the engine.
- **The merge** (`merge.py:311-444`):
  - The document-id snapshot is re-checked inside the output-name lock (`MergeStaleError`).
  - The write is atomic (temp file, then `os.replace`), and an existing packet owned by another set is
    never overwritten.
  - A 0-page packet is refused, and so is a set with more than one PO document. A packet I/O failure
    leaves the set `pending` (R-07).
  - The packet uses the stored original PDFs, never the PNGs (D2 confirmed).
- **Sanitizer** (`sanitizer.py`): strict babel parse, non-finite values rejected, magnitude guard, at
  most 2 significant decimals, and `int(Decimal * 1000)` with no floats.
- **Extraction:**
  - The compare-and-swap claim (`pending → processing`) is in place, and a stale claim is reclaimed.
  - The task outcome is read back from the database row (`_persisted_extraction_status`).
  - The 400 breaker refunds documents; a document that is not COMBINED clears the split-parent flag.
- **Grouping:** digit key with the letters guard. A PO with no digits never mints a set, and an
  ambiguous PO is refused by all three placement paths. The unique open-set index referees racing
  creates.
- **Locks:**
  - The file sync lock uses `thread_local=False`, and the sidecar is removed before the lock is
    released.
  - The orphan sidecar is cleared at startup (`main.py:290-297`).
  - Per-set locks are conditional `UPDATE`s with action-scoped release.
  - Routes refuse while a sync runs, and the sweep skips locked sets.
- **Settings save** (`settings_admin.py:702-827`):
  - It edits through an allow-list only, validates a candidate copy with the real loader, and keeps a
    timestamped backup.
  - The file is replaced atomically with a retry, read back afterwards, and rolled back on failure.
  - Comments are kept, and the edited YAML is re-parsed and compared value by value.
  - Optimistic versioning, a file lock, and an audit row per save.
  - I could not construct an input that escapes the allow-list or writes YAML that fails to parse:
    values are written as JSON-quoted strings or bare ints and bools.
- **Copy safety, partially:** zero-byte files never pass, a file that pypdf cannot open and that was
  written in the last 300 s gets no row and is retried, and an older broken file is quarantined with a
  visible row (R-03). See S2 for the gap.
- **Xfails:**
  - No `tests/test_ws6_*` xfail remains; WS6-01 is fixed by `_oldest_first`.
  - UI-G01 (favicon 404) and UI-G03 (768 px overflow) are real but cosmetic. UI-S14b is a real minor
    truthfulness bug (S8).
  - The ERP-format and description xfails are known recall gaps on the safe side, apart from the three
    listed in section 4.
  - The two `test_po_normalization_matrix` legacy-migration xfails do not apply to a fresh database.
  - R-13 only applies with `split_combined: true`.
- **Test gaps that would let a regression through:** none covers B2 (a numbered plus a number-less
  rescan), B3 (a PO-bearing DN placed by number while its own set is open), the matrix of a merged set
  under changed settings (S5), the pass duration under 401/402/missing key (S1), or a pre-sized
  zero-tail copy (S2).
