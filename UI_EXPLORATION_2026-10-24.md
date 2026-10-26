# UI exploration report — AAM Merger V3 (2026-10-24)

Exploratory pass over the running app using a real Chromium browser (OpenCode browser
tools) plus a headless Playwright script for the global checks. **No product code was
changed.** Everything below was observed on the throwaway harness
(`scripts/ui_test_env.py --root data/ui_test`, server on `127.0.0.1:8765`), never on
`data/aam_merger.db` or the real `config.yaml`.

Environment: Python 3.12 venv, Chromium, viewport 1440×900 (plus 1024/1366/768),
harness statuses at build: merged 2 · pending 6 · mismatched 3 · quarantined 4 ·
blocked customs 1 · 7 held documents.

---

## 1. Findings (product)

Ordered by how much they cost an operator. "Evidence" is what I actually saw, with the
route/selector/DB value; no speculation about causes beyond what the code shows.

### F-01 — Re-Extract reports success and does not extract; the set changes state anyway · HIGH

**Page:** PO Set detail → `⚡ Re-Extract with AI` (`[data-testid=btn-reextract]`),
`POST /po_sets/{id}/redo_extract`.

**Seen (no provider key):** the toast says **"✅ ⚡ Document extraction queued."**
(`static/js/app.js` `path.endsWith('/redo_extract')`). After ~90 s (3 documents ×
retry backoff 2/5/15 s) the set page reloads and:

- status `quarantined` → `pending`, reason changes from
  "A delivery or invoice line could not be matched to any PO line" to
  "A document in this set has not been read yet";
- the three documents are left `extraction_status = pending`, `extraction_attempt_count = 0`,
  still attached;
- no error is shown anywhere. Server log has
  `VLM infrastructure fault for doc 36 …: RuntimeError` and
  `Prefect flow … Completed()`.

**Seen (with a live key, which the harness accidentally has — see H-01):** all three
documents were re-read by the VLM (blank seeded pages → `UNKNOWN`), **detached** from the
set (`documents.po_set_id = NULL`, by design in `extraction.py:649` when a re-read loses
the PO), and the set was left with **0 documents**, `pending`,
"No purchase order document in this set yet". Same success toast.

**Expected** (`docs/UI_TEST_PLAN.md` S-16): a clear error when extraction cannot run, and
the set unchanged. The route returns per-document `extractions: [{doc_id, error}]`
(`src/app/api/routes/po_sets.py:504`), but the UI ignores the result entirely.

**Evidence:** `sessionStorage` toast log `["✅ ⚡ Document extraction queued. ×"]`;
DB rows for set 13 before/after; 90 s lock on `po_sets.locked_by_action = redo_extract`;
server log lines above.

**Suggested direction (not applied):** report the extraction outcome (count read/failed)
and refuse to leave the set's status changed when zero documents were read; keep the
button disabled while the request runs (it stays enabled because the page was rendered
before the lock).

---

### F-02 — Previewing a non-previewable document renders raw JSON inside the drawer · MEDIUM

**Page:** Unclassified → Preview of `fake-not-a-pdf.pdf` (doc 52). Same for any doc whose
`/documents/{id}/preview` returns 404/422.

**Seen:** the drawer opens (title correct) and the iframe shows Chrome's JSON viewer with
`{"detail":"Rejected on arrival, no preview: no %PDF- header in the first 1024 bytes; it is
not a PDF"}`, unstyled and clipped by the panel width. No friendly message, no "this file
cannot be previewed" state.

**Expected (U-07):** a clear error, not a blank/raw drawer.
The error path exists in `src/app/api/routes/dashboard.py:637` (422) and `:642` (404),
but `base.html` puts the URL straight into an `<iframe>` (`:93`).

**Evidence:** screenshot of the open drawer; `GET /documents/52/preview` → 422; iframe
URL `about:blank` for the 200-but-blank case (doc 49).

---

### F-03 — Audit log dumps raw JSON (with escaped `\n`) into one table cell · MEDIUM

**Page:** `/audit`.

**Seen:** each `quarantined` row prints the whole `detail` JSON — a multi-KB quarantine
report with literal `\n` escapes — inside `<code>` (`templates/audit.html:55`). Four such
rows make the page hundreds of pixels tall per row with unreadable text; the timestamp and
action cells are vertically centred next to the blob. The raw enum names (`quarantined`,
`line_mapping_applied`, `manual_status_change`) are also shown for actions without a
badge branch (`:43`).

**Expected:** a short human-readable summary (reason, file names), with the report behind a
details/collapse or a link, and human labels for every action.

**Evidence:** screenshot of `/audit`; page text shows
`{"reason": "unmatched_vendor_line", "report": "QUARANTINED PO SET\n====…`.
(Force Merge rows are fine: badge + justification + short JSON.)

---

### F-04 — Unknown status filter and unknown set id return bare JSON, not an app page · MEDIUM

Seen (browser navigation, all `Content-Type: application/json`, empty `<title>`, no nav):

| URL | Response body |
|---|---|
| `/dashboard?status=nonsense` | `{"detail":"Unknown status filter: nonsense"}` (422) |
| `/po_sets/999999/view` | `{"detail":"POSet 999999 not found"}` (404) |
| `/po_sets/abc/view` | Pydantic `int_parsing` JSON (422) |
| `/documents/999999/preview` | `{"detail":"Document not found"}` (404) |
| `/po_sets/999999/merged_pdf` | `{"detail":"Merged PDF not available"}` (404) |

**Expected (D-07, S-17):** the page renders with a friendly message and a way back to the
dashboard. `dashboard()` raises `HTTPException` for an unknown status at
`src/app/api/routes/dashboard.py:316`; the view route does the same for a missing set.

---

### F-05 — Saving a held document's type hides both the confirmation and the saved type · MEDIUM

**Page:** `/unclassified`, row `DN-NOPO.pdf`.

**Seen:** select `DN` → **Save & Route** → `POST /unclassified/46/reclassify` returns 200
with the intended row:
`<tr id="doc-row-46"><td …>Type set to DN, but no open PO Set exists for this document; it
remains in the holding area.</td></tr>` **and `HX-Refresh: true`**
(`dashboard.py:1514-1529`). The browser reloads immediately, so the explanation is never
read, and the type dropdown re-renders at `-- Select Type --` even though
`documents.doc_type = 'DN'` (`unclassified.html:85-93` is not initialised from the stored
type).

**Expected (U-09):** the row stays and says why; the operator can see the type they set.

**Evidence:** response body + header captured with `fetch`; DB `doc_type='DN'`; select
value `""` after reload.

---

### F-06 — An ambiguous-PO document has no per-row marker · MEDIUM

**Page:** `/unclassified`.

**Seen:** `DN-AMBIG.pdf` (`documents.po_reference_ambiguous = 1`) renders exactly like every
other held row: `Extracted PO: 7000001 (key 7000001)`. Only the aggregate badge
"1 need a PO number" hints at it — with 7 rows the operator cannot tell which one.

**Expected (U-06):** a per-row marker (e.g. "multiple PO numbers — cannot be placed
automatically"). The template only renders `hold_notes` (letters conflicts), not
`po_reference_ambiguous` (`templates/unclassified.html:53-55`).

---

### F-07 — Success toasts are destroyed by the immediate reload · MEDIUM

Every action that reloads on success loses its own confirmation, because Alpine toasts live
in memory:

- `toggle_customs` (set 15): button flips OFF→ON, page reloads, **no toast**;
- `force_merge` (set 8): merge + audit work, page reloads, toast gone;
- `redo_match`, `redo_extract`, `reclassify`: same;
- `merge` on a not-ready set: the warning is visible for the 1.2 s before
  `setTimeout(location.reload, 1200)`.

**Evidence:** a `MutationObserver` + `sessionStorage` log captured the toasts; after the
reload `document.querySelectorAll('.toast')` is empty on every one of these pages.
`static/js/app.js:146-188` adds the toast; the `hx-on::after-request` handlers in
`po_set_detail.html` reload.

---

### F-08 — Modals neither take nor trap keyboard focus · MEDIUM (accessibility)

**Page:** PO Set detail, `⚠️ Force Merge`; Quarantine table `Delete`.

**Seen:** after opening Force Merge, `document.activeElement` is still the trigger button
behind the modal. `Tab` then moves to `btn-preview` (×3), the upload `<input type=file>`
and the document-type `<select>` **behind** the backdrop — never into the modal first, and
nothing keeps focus inside it. `aria-modal="true"` is set, so assistive tech is told the
opposite of what happens. Escape closes it (works), as do Cancel and the `✕ Close` in the
drawer.

**Expected (G-04):** focus moves into the dialog on open, is trapped, and returns to the
trigger on close.

**Evidence:** focus sequence captured with `document.activeElement` after each Tab.

---

### F-09 — Missing form labels and low-contrast text (Lighthouse) · LOW (accessibility)

Lighthouse (Chromium, desktop):

| Page | Accessibility | Failing audits |
|---|---|---|
| `/dashboard` | **0.84** | `select-name` (`#status-filter` has no label); `color-contrast` ×8 (KPI `%` text, tab counts, table `📥 PDF` button) |
| `/po_sets/7/view` | **0.81** | `label` (upload `<input type=file>` has no `<label>`); `select-name` (upload `doc_type` select) |

Best-practices 1.0 on both. (`meta-description` also fails; irrelevant for a LAN tool.)

---

### F-10 — Every toast shows its icon twice · LOW

`templates/base.html:107-110` renders an icon span, and `static/js/app.js:121,174,176`
already prefixes the message. Observed text:
`"❌ ❌ Validation error: This file was rejected on arrival…"`,
`"⚠️ ⚠️ …"` for 409s.

### F-11 — A document in `pending` shows the raw enum on the set page · LOW

`po_set_detail.html:364-375` handles `valid` / `failed` / `processing`, else prints the raw
value. After a failed Re-Extract the document table reads
`PO-7000011.pdf  pending  1  7000011` instead of "Not read" / "Waiting to be read".

### F-12 — Upload refusal notice is the raw API string · LOW

Uploading `notpdf.txt` to a set redirects to
`/po_sets/16/view?notice=only%20.pdf%20uploads%20accepted`; the page shows a plain card with
`only .pdf uploads accepted` (the HTTP detail, `dashboard.py:778`), not a sentence.

### F-13 — "Service Online" is a link to raw JSON · LOW

The nav item links to `/health`; clicking leaves the app for
`{"status":"ok","version":"0.1.0"}` (no title, no nav, no way back except Back).
`templates/base.html:58`.

### F-14 — Unclassified subtitle has a duplicated phrase · LOW

`Preview one, then one, then assign its type…` (`templates/unclassified.html:25-26`).

### F-15 — Audit action chips show raw enum values for some actions · LOW

`quarantined`, `line_mapping_applied`, `manual_status_change` are rendered verbatim
(`templates/audit.html:43-45`).

### F-16 — A scan's outcome is never shown · LOW

`⚡ Scan Input Folder` toasts "Sync scan triggered"; the button disables and the spinner
appears within ≤5 s (the `/sync/status` poll). When the flow finished — in one run with
**every extraction failing** (missing key) and Prefect reporting `Completed()` — the UI
simply went idle: no success/failure summary, no last-scan time, nothing to distinguish
"0 new documents" from "everything failed".

### F-17 — "Merge Now" on a not-ready set gives a generic 1.2 s message · LOW

`/po_sets/7` (mismatched): toast `"⚠️ Not merged - still mismatched. Review the reason on
the set page."` — the server's `detail`/`reason` is not used, and the reload hides it almost
immediately. The page's reason card is correct, so this is a messaging gap.

### F-18 — Client-side search with no matches shows an empty table · INFO

Typing `zzzz-nomatch` hides all rows with no "No matching PO sets" message, unlike the
server-side empty state in `_dashboard_table.html:137-150`.
The filter itself is correct, including after the 5 s HTMX table swap
(`app.js` re-applies it on `htmx:afterSwap`).

### F-19 — The 3 s quarantine poll can replace the DOM under a click · INFO (test stability)

`/quarantine` swaps `#quarantine-table` every 3 s. One Playwright click on `Delete` failed
with a detached node because the table swapped between snapshot and click; a real mouse
click is much less likely to lose, but it shows the row set is not stable while the modal is
being opened. The poll's `[!…modal.open]` guard does keep an already-open modal intact.

### F-20 — Absolute server paths are shown on operator pages · INFO

The archived-set banner and the quarantine archive card print
`C:\Users\…\data\ui_test\archive\7000012_20261024T173802Z_14`, and `templates/audit.html`
detail JSON shows more. `_scrub_paths` exists for error text only. Intentional per the
template comments; listed for consistency with the "no server paths to operators" rule.

---

## 2. Findings (test harness) — read this before writing the S-16 test

### H-01 — The UI harness is **not** provider-free: it silently uses the developer's `OPENROUTER_API_KEY` · HIGH

`docs/OPENCODE_UI_TEST_BRIEF.md` states "No network, no API keys. The harness has no
provider key on purpose." That is not what happens.

- `scripts/ui_test_env.py` `build()`/`serve()` pop `OPENROUTER_API_KEY` from the child
  environment (`:273`, `:470`), and `tests/ui/conftest.py` does the same (`:52`).
- But `load_config()` loads `.env` files itself, including
  `<repo>/.env` (`src/app/core/config.py:404-421`), *after* import. `override=False` only
  preserves an already-set variable, so a popped variable is set again from the file.

**Proof (no product code touched):**

```text
config copied to a temp dir, no OPENROUTER_API_KEY in the environment:
before: None
after load_config(): SET len=73
```

**Observed consequence:** my first `Re-Extract with AI` on set 13 made real VLM calls
(the three blank seeded PDFs came back `UNKNOWN`), detached all three documents from the
set and left it `pending` with 0 documents. That is real network traffic and real cost in a
"no network, no cost" harness.

**Suggested fix (not applied):** put `OPENROUTER_API_KEY=""` in the child environment
instead of popping it (dotenv keeps an empty string), and/or write an empty `.env` next to
the harness `config.yaml`; add a startup assertion in `conftest.py` that the app process
cannot reach the provider. I placed `data/ui_test/.env` with an empty key for the second
half of this exploration and the server then logged
`OPENROUTER_API_KEY not set — VLM extraction will fail closed`, which is the state the
brief intends.

### H-02 — Brief/plan vs behaviour (for whoever writes the tests)

- `README` of the plan says the table polls every 2 s; the template polls every **5 s**
  (`_dashboard_table.html:1`, comment in `app.js:55` still says 2 s).
- `S-16` ("no provider key") cannot pass until H-01 is fixed, and even then the app's
  behaviour is F-01.
- `U-09`/`D-07`/`S-17`/`U-07` currently fail on behaviour, not on locators.
- `data-testid` gaps I hit: no test ids on the quarantine table's `Delete`/`Archive`
  buttons, on the preview buttons, or on the toast; `#status-filter` and the upload form
  controls have no labels.

---

## 3. What worked (verified, with evidence)

**Dashboard** — KPI counts equal the seeded manifest (16/2/3/1/4); all six tabs; the status
dropdown filters (`?status=quarantined` → 4 rows) and keeps the selection; the client-side
search filters by PO and by status text, and survives the 5 s table swap; the auto-poll
hits `/dashboard/table` every 5 s (6 calls in 30 s, all 200); nav badge `7` = holding-area
rows; `📥 PDF` downloads a valid 3-page packet (`SI-7000001.pdf`, 3 pages, widths
209/206/203 = the three source documents); `Scan Input Folder` toasts and disables the
button within one poll; `?status=nonsense` is the only dashboard failure (F-04).

**All 16 set pages** — correct status badge, reason card, flags and matrix for every seed:
merged (green, Force Merge/Merge Now/Re-Match/Re-Extract disabled, download link), x10
mapping (`Merged • review`, `lines mapped (x10)`, `← DN 1 · SI 1`), pending (awaiting
reason, "Not on the PO" for the SI line that matches nothing), two PO documents (reason
names both files), needs-review (`delivered 8 of 10`, red cell), quarantined (reason +
"does not merge even when every line matches"), no-PO-lines (empty state "The PO has no
extracted line items … vendor lines exist (2)"), blocked customs (reason names CUSTOMS +
SHIPPING, upload form visible), mutable targets all render their controls.

**Preview drawer** — opens the right document (`/documents/1/preview`, 200, title
`PO-7000001.pdf`), closes via `✕ Close` and via Escape.

**Force Merge** — justification `too short` → 422, toast
`❌ ❌ Validation error: Justification must be at least 20 characters (got 9)`, modal
closed, DB unchanged (set 9 still `mismatched`). Valid justification → merged, packet
written, audit row `force_merge` with the exact text, page shows `Force-merged - review` and
a download link.

**Archive** — from the quarantine table (set 10) and from the set page (set 14): set
disappears from the worklist (4 → 3 → 2 rows), `archive/<PO>_<ts>_<id>` written, audit rows
`quarantine_delete`, archived set page is read-only (banner, no action buttons, no upload
form, no modals, previews only).

**Customs** — toggle OFF → ON on a pending set (persisted, header shows
`ENABLED (CUSTOMS + SHIPPING Required)`, both "Missing"); upload of a non-PDF refused with
a notice and no change (2 docs before/after); one CUSTOMS + one SHIPPING upload → 4 docs,
`🛃 CUSTOMS` / `🛃 SHIPPING` rows.

**Re-Match** — pending set reloads and keeps the same reason and matrix.

**Manual merger (`/manual/merger`)** — two staged PDFs show `2 files · 982 B`, "Ready to
merge 2 files in this order"; `my packet` previews as `my packet.pdf`; merge downloads
`my packet.pdf` with **3 pages** (2+1, verified with pypdf); reserved name `CON` and illegal
`bad:name?.pdf` block the button with specific messages; a `.txt` is skipped with a warning;
a damaged `.pdf` returns `422` and the page shows
`The merge didn't finish — We couldn't read broken.pdf. It may be damaged or not a PDF.`

**Unclassified** — 7 rows with the four badges (7 awaiting / 2 failed / 1 need a PO /
4 waiting); rejected-on-arrival row carries the reason and 3/3 attempts; Save & Route is
disabled until a type is chosen; rejected doc → 422 toast naming "rejected on arrival" and
the row stays; PO-key debounce shows `Server key: 7000003` after one
`/unclassified/normalize?po_no=PO-7000003` (200); hostile filename `<img src=x
onerror=…><b>bold</b>.pdf` is rendered as text (`window.__xss` unset, no `<img>`/`<b>` in
the table).

**Global** (headless Playwright, 8 pages: dashboard, unclassified, quarantine, audit,
manual merger, 3 set pages) — **0** console errors, **0** page errors, **0** failed
requests, **0** 4xx/5xx, **0** requests to any host other than the app (offline LAN
requirement holds). Titles are non-empty and distinct. No horizontal scroll at 768, 1024
and 1366 px (`scrollWidth === clientWidth`) on the dashboard and a set page. Foreign
`Origin` on `POST /po_sets/9/merge` and `DELETE /po_sets/10/quarantine` → **403** with the
cross-origin message; a foreign `GET /dashboard` is allowed (by design).

---

## 4. Artifacts kept (nothing deleted)

- `data/ui_test/` — the built harness is **intact** (config, DB with WAL, stored PDFs,
  manifest.json, logs, the data I mutated). The server was restarted after the sweep and is
  currently serving **http://127.0.0.1:8765/dashboard** (stop it with
  `Get-NetTCPConnection -LocalPort 8765 | Stop-Process`). Rebuild only when you want a
  clean slate: `uv run python scripts/ui_test_env.py --root data/ui_test` (wipes the folder,
  including the empty-key `.env`).
- `scripts/ui_explore_global.py` — the reusable read-only Playwright sweep used for the
  global checks (console/page errors, failed + 4xx requests, cross-host requests, titles,
  responsive overflow, Origin guard). Run it against a live instance:
  `uv run python scripts/ui_explore_global.py --base http://127.0.0.1:8765 --json data/ui_test/explore_report.json`
- `data/ui_test/explore_report.json` — the last sweep output (8 pages, 0 errors, 403 on
  cross-origin POST/DELETE, no overflow at 768/1024/1366).
- `tests/ui/**`, `scripts/ui_test_env.py`, `src/`, `templates/`, `config*.yaml` — **unchanged**
  (`git status` shows only the pre-existing user modifications).

## 5. Not verified / limits

- No real vendor PDFs, no real DB, no NSSM/Prefect deployment work — harness only.
- I did not run the `pytest tests/ui` suite; I also did not write the TODO cases. Findings
  F-01…F-06 are exactly the cases that would need `xfail(strict=True)` today.
- Colours: I only report contrast from Lighthouse, not subjective appearance.
