# Manual PDF Merger — Deep Audit (Backend + Frontend)

**Date:** 2026-10-23
**Scope:** the isolated manual PDF merge tool only —
`src/app/api/routes/manual_merger.py`, `src/app/services/quarantine.py::manual_merge`,
`templates/manual_merger.html`, `src/app/core/limits.py`, plus its tests and the
requirements it implements (SPEC §7.12 / FR-14.11–FR-14.13).
**Out of scope:** the automated pipeline's merge (`services/merge.py`), quarantine
archive/delete, and the rest of the dashboard.

**Method:** line-by-line review, graph impact check, targeted pytest runs, HTTP-level
probes through `TestClient`, and a real browser end-to-end run against a local
`uvicorn` instance (upload → reorder → merge → download → inspect the PDF).

**Environment:** Python 3.12 venv, FastAPI 0.141.1, Starlette 1.6.0, pypdf 6.16.1,
uvicorn 0.52.3, Windows.

---

## 1. Verdict

The feature is **small, cleanly separated at the route level, and works end-to-end on a
modern browser**: no DB row, no PO association, correct page order, temp cleanup on the
success path, XSS-safe DOM construction, and a CSRF guard from the app middleware.

It is **not release-clean**. There is one reproducible frontend data-integrity bug, one
parse-time resource-control gap, and a cluster of validation/UX gaps that quietly produce
wrong or empty packets. Requirements traceability is **2 fully met, 1 partially met** —
FR-14.12's "output destination selectable in the UI" is not implemented at all (only the
download *filename* is).

No P0. Four issues I would treat as P1/P2 blockers for calling this section "done".

---

## 2. Requirement traceability

> Status below is the **pre-remediation** state recorded by the audit; the fixed
> state is in §8. Limits and the output-name rules are in §2.1.

| Requirement | Status | Evidence |
|---|---|---|
| **FR-14.11** — separate tool, upload, drag-reorder, download, no DB row, no `po_no` | ✅ Met (with one caveat) | `manual_merger.py:18-161`; `manual_merge` has no session/engine; browser run merged `c,a,b` → widths `300,100,200`. Caveat: it only accepts PDFs, while the text says "arbitrary files" (defensible for a PDF merger). |
| **FR-14.12** — output **destination and filename** user-selectable in the UI | ⚠️ **Not met** | The page has a filename field only (`manual_merger.html:198-202`). The route always writes to `tempfile.mkstemp()` (`manual_merger.py:140`); no user path ever reaches `manual_merge(output_path=…)`. The test that claims this (`test_quarantine.py:328`) exercises the service API, not the UI. |
| **FR-14.13** — visually and navigationally isolated | ⚠️ Partial | Standalone amber page, no base template, no external assets. But it is a normal fifth item in the pipeline's own navbar (`base.html:52-54`) and links back to `/dashboard`. Visually distinct; not *navigationally* isolated in the strict sense. |

---

## 2.1 Limits and output-name rules

| Limit | Value | Enforced in |
|---|---|---|
| PDFs per merge | **20** (default) | `manual_merge.max_files`, UI while files are added |
| Total upload size | **100 MB** (default) | `manual_merge.max_total_mb`, server + UI |
| One file | **100 MB** (default) | server |
| Pages in the merged packet | **5,000** (default) | `manual_merge.max_pages` |
| Request body | total + 2 MB multipart overhead | `BodySizeLimitMiddleware`, before parsing |
| Output name | non-empty stem, **≤100 chars**, `.pdf` | server (`validate_pdf_name`) + live UI |
| Staging folder | `manual_merge.temp_folder` (default `<database folder>/manual_merge_tmp`) | server; must be separate from every pipeline folder |
| Stale temp sweep | `sweep_after_hours` (6) on startup and before each merge | only `mm-*.pdf` in that folder |

Limits are configured under `manual_merge:` in `config.yaml`; the defaults live
in `src/app/core/limits.py`. The page receives them from the route, so changing
`max_files`/`max_total_mb` changes the UI text and the checks together (restart
required: the request-body guard is built at startup). The page cap is
server-side only.

**Output-name rules.** Allowed: letters, digits, spaces, Unicode, and ordinary
punctuation. Rejected with a clear message: `<>:"/\|?*`, control characters,
leading/trailing dots or spaces, and Windows device names (`CON`, `PRN`, `AUX`,
`NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`, with or without an extension). `.pdf` is
appended when omitted. The UI shows the final name, marks the field invalid and
disables **Merge** while the name is invalid; the server repeats the check and
returns HTTP 422 with code `bad_filename` if a client bypasses it.

**Encryption policy.** Every encrypted PDF is refused, including one with an
empty user password (owner-password-only bank statements). The message is:
"*name* is password-protected. Please upload an unlocked copy (remove the
password and save the file again)." The tool never decrypts and never silently
drops permission flags.

**Messages.** Unreadable/truncated files say so and suggest re-saving; wrong
extensions say "Please add PDF files only"; limit errors name the configured
number ("Too many files — you can merge up to 20 at a time", "These files add up
to more than 100 MB. Please remove one or use smaller files"); the failing
filename is attached to the card. Name errors say "Please remove \"/\" from the
file name." No raw pypdf text reaches the operator.

---

## 3. Backend walkthrough — what actually happens

> This walkthrough describes the code **as audited** (pre-remediation); §2.1 and
> §8 describe the current implementation.

`POST /manual/merge` (`manual_merger.py:24`), a **sync** handler (correct: it runs in
FastAPI's threadpool, so the blocking reads/writes are legal):

1. **FastAPI parses the whole multipart body first** (`routing.py:430`,
   `await request.form()`) — before any line of the route runs.
2. Route checks `not files` and `len(files) > 20` (`:83-92`).
3. Per file: suffix `.pdf` (`:101`), `read(100MB+1)` (`:103`), per-file cap (`:104`),
   running total cap (`:110`), `startswith(b"%PDF")` (`:119`), write to an `mkstemp`
   temp (`:121-125`).
4. Parse `order` as ints (`:128-131`) → `manual_merge(tmp_paths, order, out_tmp)`
   (`:147`).
5. `manual_merge` (`quarantine.py:871`): validates the permutation, opens each PDF,
   `add_page` for every page, writes the output (`:900-911`).
6. `FileResponse(..., background=BackgroundTask(_cleanup, inputs + output))`
   (`:150-155`).
7. Any `HTTPException` → JSON, or a minimal HTML page when `Accept` contains
   `text/html` (`:33-36`); any other `Exception` → 422 `"manual merge failed: {e}"`
   (`:159-161`).

**What is correct and should not be regressed**

- No DB/session/`audit_log` anywhere on this path; no `po_no`.
- `OriginGuardMiddleware` covers this POST (`main.py:169`), so a cross-origin browser
  form cannot trigger a merge.
- Temp files are unique (`mkstemp`) and are deleted on success and on handler errors;
  the existing test proves 3/3 removed (`test_dashboard.py:636`).
- The filename is sanitised and **only** used as a `Content-Disposition` value — the
  on-disk name is always a temp path, so there is no path traversal.
- DOM construction uses `textContent`, never `innerHTML` (`manual_merger.html:252`).
- Ordering works: the browser rebuilds `FileList` in display order
  (`:336-340`) and the server merges in that order (verified end-to-end).

---

## 4. Findings

Severity: **P1** = fix before calling the feature done; **P2** = should fix this cycle;
**P3** = hardening / quality. "Observed" = reproduced; "Code path" = established from
the code plus library source.

| ID | Sev | Area | Finding | Evidence |
|---|---|---|---|---|
| MM-01 | **P1** | Frontend | Dropping a file from the OS **onto an existing row** discards the dropped file and silently moves the *first* staged file to that position | Observed in a real browser |
| MM-02 | **P1** | Backend | The 20-file / 100 MB caps run **after** the whole request body is parsed and spooled; there is no request-size bound | Code path + library defaults; 1001-file probe |
| MM-03 | P2 | Backend | `%PDF` is required at **byte 0**, so valid PDFs with a BOM/leading bytes are rejected — while the ingestion path deliberately accepts the header within the first 1024 bytes | Observed (header at ~890 → 422) |
| MM-04 | P2 | Backend | A zero-page PDF is accepted and produces a **0-page "merged" packet with HTTP 200**; the dashboard upload path rejects exactly this | Observed |
| MM-05 | P2 | Both | Any failure loses the **entire staged list**; messages are raw pypdf text with **no filename**; some failures return raw JSON to the browser | Observed |
| MM-06 | P2 | Spec | FR-14.12 destination selection is not implemented | Code path |
| MM-07 | P2 | Security | Page-level JavaScript/actions **survive the merge**; encrypted PDFs with an empty user password are silently decrypted (permissions stripped) | Observed |
| MM-08 | P2 | Frontend | No client-side validation of type/count/size; >20 files or >100 MB only fail at submit with a full-page error | Code path + observed corrupt file staged |
| MM-09 | P2 | Backend | Filename sanitiser silently mangles names (`Invoice 1234 (final).pdf` → `Invoice1234final.pdf`); no length cap; `.pdf`-only name accepted | Observed |
| MM-10 | P3 | Frontend | "Choose PDF Files" invokes `fileInput.click()` **twice** per click | Observed (`opens: 2`) |
| MM-11 | P3 | Frontend | A drop anywhere outside the dropzone/rows falls through to the browser default (navigates to the PDF, losing the staged list) | Code path |
| MM-12 | P3 | Backend | No logging of merge failures; broad `except Exception` turns internal bugs into 422; raw exception text can leak temp paths | Code path |
| MM-13 | P3 | Frontend | Order integrity depends entirely on `fileInput.files` assignment succeeding; `order` is always identity (`0,1,2`) with no fallback | Observed |
| MM-14 | P3 | Architecture | `manual_merge` lives in `quarantine.py`; the "fully separate" tool imports the DB stack on first use | Code path |
| MM-15 | P3 | Security | Download has no `Cache-Control: no-store`/`nosniff`; PDFs sit in the system `%TEMP%` | Code path |
| MM-16 | P3 | A11y | Dropzone is a non-focusable `div`; the upload label has no `for`; no `aria-live` for the staged list | Code path |
| MM-17 | P3 | Tests | No behavioural JS tests; the "no DB" test is vacuous; route branches uncovered (81% on the route file) | `pytest --cov` |
| MM-18 | P3 | Docs | `AAM_merger_V3_PRODUCT.md` (declared source of truth) has **no manual-merger section** | grep |
| MM-19 | P3 | Config | 20-file / 100 MB limits are hardcoded constants, not `config.yaml` | `limits.py:11,17` |
| MM-20 | P3 | Output | Merged PDF drops document metadata (Title/Author) and outlines | Observed (`{'/Producer': 'pypdf'}`) |
| MM-21 | P3 | Robustness | `PdfReader` handles are not explicitly closed; Starlette skips the cleanup background task if the send raises (not reproduced) | Code path; 35 MB abort test left 0 temp files |
| MM-22 | P3 | Packaging | Template dir resolves from repo root; a wheel install would not contain `templates/` (same as the dashboard) | Code path |

---

## 5. Detail

### MM-01 — Dropping a PDF onto a row corrupts the list (P1, observed)

`manual_merger.html:295-306`:

```js
li.addEventListener('drop', e => {
  e.preventDefault();
  const from = parseInt(e.dataTransfer.getData('text/plain'), 10);
  const to = idx;
  if (from !== to) {
    const item = files.splice(from, 1)[0];   // from === NaN for an OS file drop
    files.splice(to, 0, item);
    ...
```

An OS file drop carries no `text/plain` index, so `parseInt("")` is `NaN`.
`Array.prototype.splice(NaN, 1)` is `splice(0, 1)` — it removes **the first staged
file**, moves it to the drop row, and the dropped file is never added.

Reproduced in Chrome against the live page with staged `[c.pdf, a.pdf, b.pdf]`
and a drop of `dropped-on-row.pdf` on row 2:

```
before: ["c.pdf","a.pdf","b.pdf"]
after : ["a.pdf","c.pdf","b.pdf"]   // dropped-on-row.pdf absent
```

That is a **silent wrong page order in a document packet**, which is exactly the class
of error this product exists to avoid. Fix:

- In the row handler, if `e.dataTransfer.files?.length`, delegate to the same
  `addFiles()` used by the dropzone and return.
- Only treat a drop as a reorder when `getData('text/plain')` parses to a valid index
  in range.
- Add a document-level `dragover`/`drop` `preventDefault()` so a near-miss cannot
  navigate the tab (MM-11).

A regression test must be a real browser test (Playwright), not a source grep.

### MM-02 — Caps do not bound the request body (P1, code path)

`manual_merger.py:83-118` checks `len(files)` and bytes **after** FastAPI has parsed
the whole form. With Starlette 1.6 the parser defaults are:

- `max_files = 1000`, `max_fields = 1000`;
- `max_part_size = 1 MB` — applied only to **non-file** parts;
- files spill to `SpooledTemporaryFile` after 1 MB;
- **no total-body limit.**

Consequences on the 2-core Win2016 host (NSSM starts uvicorn directly, so no reverse
proxy body cap — `DEPLOY.md:117`):

- 21 × 100 MB is fully received and written to `%TEMP%` before the route returns the
  friendly "too many files"; a 5 GB POST is spooled to disk first.
- A 1001-file request is rejected by Starlette with **HTTP 400 JSON**
  (`{"detail":"Too many files. Maximum number of files is 1000."}`) — the probe
  reproduced this, which also proves the route's 20-file cap is not the gate.
- A LAN host (no auth is accepted risk) can fill the system drive; repeated oversized
  posts also burn threadpool workers.

Fix, in order of preference:

1. A small ASGI middleware that rejects `Content-Length > ~110 MB` for
   `/manual/merge` **before** the app reads the body (still note chunked transfer).
2. Read the form yourself in the route:
   `await request.form(max_files=MAX_MANUAL_MERGE_FILES + 1, max_part_size=…)`
   and stream each `UploadFile` in bounded chunks, aborting once the running total
   exceeds the cap.
3. Optionally mirror the same guard in the dashboard upload route.

Also fix the response shape: a parse error should return the HTML error page for
browsers, not raw JSON (MM-05).

### MM-03 — Magic-header check is stricter than reality and inconsistent with ingestion (P2, observed)

`manual_merger.py:119` uses `data.startswith(b"%PDF")`. The product's own ingestion
path deliberately does it differently:

- `ingestion.py:143`: `if b"%PDF-" not in data[:1024]` — accepts a header anywhere in
  the first 1024 bytes (the documented viewer tolerance), and the comment explains why.
- `ingestion.py:41-55`: additionally opens the bytes with pypdf and rejects zero-page /
  unreadable files.

Probe results:

| Input | Manual merger | Ingestion rule |
|---|---|---|
| PDF with `\r\n\r\n` before `%PDF` | **422 "not a PDF file"** | accepted |
| PDF with UTF-8 BOM | **422** | accepted |
| PDF with 890 bytes of padding then `%PDF-1.4` | **422** | accepted |
| PDF truncated by 10–400 bytes | 422 (pypdf message) | rejected with a clear reason |

Recommendation: extract the ingestion validation into a shared
`services/pdf_validation.py` and use it in both places. The manual merger should reject
the same things ingestion rejects *and* accept the same things, with a message that
names the file and says what to do.

### MM-04 — Zero-page input yields a zero-page "success" (P2, observed)

`manual_merge` has an explicit "still write the file" branch (`quarantine.py:910`).
Probe: posting a valid 0-page PDF (`PdfWriter` with no pages) returns **200**,
`Content-Disposition: attachment`, and a 311-byte PDF whose `/Count` is 0. A CA user
gets an empty download with no warning. The dashboard upload path explicitly rejects
this (`dashboard.py:771`, "PDF has no pages"), so the two upload paths disagree.

Fix: before merging, read each input's page count; reject a 0-page file with a named
error. After merging, assert the output page count equals the sum of the inputs (cheap
defence against a corrupted write).

### MM-05 — Failure UX loses the work and speaks in pypdf (P2, observed)

Real browser run with a corrupt `.pdf`:

```
Manual merge was not completed
manual merge failed: Stream has ended unexpectedly
← Back to the manual merger
```

- No filename → the operator cannot tell which of 20 files is bad.
- The text is an internal pypdf exception, not an instruction.
- The normal form POST navigated away; clicking Back returned to a **fresh page with
  `files.length === 0`**. A 20-file, hand-ordered packet has to be rebuilt.
- A missing `files` field returns raw JSON even with `Accept: text/html`
  (`{"detail":[{"type":"missing",...}]}`), because FastAPI validation runs before the
  route's `try/except`. The same is true for >1000 files (400 JSON). Only errors raised
  *inside* `_manual_merge_impl` get the HTML page.

Fix:

- Validate client-side before submit and show an in-page error; keep the staged list.
- Or submit with `fetch`/HTMX and render the error in the page.
- Include the offending filename and a human message ("`a.pdf` is not a readable
  PDF — it may be truncated or password-protected").
- Add a global exception/validation handler for `/manual/merge` that honours
  `Accept: text/html`.

### MM-06 — FR-14.12 destination is not implemented (P2, spec gap)

The code and tests are honest about the service (`output_path` works), but the UI
exposes only `output_filename` (`manual_merger.html:198-202`), and the route always
writes to `mkstemp` (`manual_merger.py:140`). SPEC FR-14.12 says the **destination** is
user-selectable "in the UI at merge time", including "the same output folder". A browser
download cannot express that.

Choose one and record it:

- **Implement:** a "save to server folder" option with a configured allow-list/root,
  defaulting to download-only.
- **Amend:** change FR-14.12 (and add to PRODUCT §8) to "the file is downloaded; the
  browser chooses the folder", and rewrite `test_manual_merge_output_user_selectable`
  to test the UI contract.

Do not leave the requirement and the product disagreeing.

### MM-07 — PDF payloads and permission restrictions propagate (P2, observed)

Merging a PDF that carries a page-level JavaScript action and a link annotation
produced an output that still contains both:

```
merged page keys: ['/AA', '/Annots', ...]
annots: [{'/Subtype': '/Link', '/A': {'/S': '/JavaScript', '/JS': "app.alert('pwned')"}}]
page /AA: {'/O': {'/S': '/JavaScript', 'JS': "..."}}
```

pypdf copies page dictionaries, so `/Annots`, `/AA`, launch/URI actions and embedded
files travel with the page. A vendor PDF is untrusted input; this tool then hands the
payload to the CA as a merged packet.

Separately, a PDF encrypted with an **owner password and an empty user password**
merged successfully and the output was **unencrypted** (`is_encrypted: False`), i.e.
"no copy / no extract" permissions are silently dropped.

Recommendation: either strip `/AA`, `/OpenAction`, `/Launch`, `/JavaScript` actions and
optional-content/embedded files during the copy, or document the limitation explicitly
in PRODUCT §8 and pin it with `test_known_limitation_*`. Handle `reader.is_encrypted`
explicitly: fail with a clear message unless the operator opts into decrypting.

### MM-08 — No client-side validation of the documented limits (P2)

The UI never checks the 20-file or 100 MB limits (`manual_merger.html:342-348` only
appends; `:355-364` filters the *drop* path by type). A user can stage 25 files or
150 MB, click Merge, wait for the upload, and get a full-page error after the work is
lost. It also stages a `.txt` chosen through the picker's "All files" filter, while the
drop path silently ignores non-PDFs with no message.

Fix: a shared `addFiles()` that filters/validates and shows a row-level message, a
running total size + count in the hint, and a disabled Merge button with a reason when
over limit. Use the same `accept` decision on both paths.

### MM-09 — Filename sanitiser silently rewrites the name (P2, observed)

`safe_name` (`manual_merger.py:134`) keeps only `isalnum`, `-`, `_`, `.`. Results:

| Typed | Download name |
|---|---|
| `Invoice 1234 (final).pdf` | `Invoice1234final.pdf` |
| `../../etc/passwd` | `....etcpasswd.pdf` |
| `.pdf` | `.pdf` |
| 3000 chars | 3027-char `Content-Disposition` |
| `发票2024.pdf` | `filename*=utf-8''…` ✅ |

The UI promises "Result will download with this name" (`manual_merger.html:201`).
Spaces and parentheses are legal in a quoted `Content-Disposition`; the aggressive
filter is only needed for header injection, and CR/LF are already impossible via
quoting. Recommendation: reject CR/LF/NUL (or quote properly), keep spaces/parentheses,
cap at ~120 characters, never allow a name that starts with a dot, and show the final
name in the UI before submit.

### MM-10 … MM-22 (P3, condensed)

- **MM-10** Clicking "Choose PDF Files" calls `fileInput.click()` twice: the inline
  `onclick` (`manual_merger.html:189`) and the dropzone bubble handler (`:351`, because
  the button's `parentElement` is the dropzone). Measured `opens: 2`. Keep one handler.
- **MM-11** Only the dropzone (`:355`) and rows (`:295`) cancel `dragover`/`drop`.
  Dropping a file on the page background is handled by the browser: it navigates to the
  PDF and destroys the staged list. Add a document-level guard.
- **MM-12** The module has no logger; `except Exception as e: raise HTTPException(422,
  f"manual merge failed: {e}")` (`:159-161`) hides bugs as user errors and may echo a
  temp path/username from `PermissionError`/`OSError`. Log with `exc_info`, return a
  generic message, and map known cases (encrypted, truncated, no pages).
- **MM-13** `syncOrder()` writes `0,1,2,…` (identity) at `:333`; correctness relies
  entirely on `fileInput.files = dt.files` (`:339`). If `DataTransfer`/assignment is
  unavailable, the UI shows one order and the packet merges in another, silently. Either
  send the display→upload permutation in `order`, or build the `FormData` explicitly
  from the display order. An identity `order` is dead weight today.
- **MM-14** `manual_merge` lives in `services/quarantine.py:871`, and the route imports
  it lazily (`manual_merger.py:73`). That module imports the engine/models at import
  time. Move the function to `services/manual_merge.py` (or `services/pdf_tools.py`) so
  "fully separate" is structural, not just behavioural.
- **MM-15** `FileResponse` sets no `Cache-Control`/`X-Content-Type-Options`. Add
  `no-store` for a financial document and `nosniff`. Uploads also land in the system
  `%TEMP%`, which may be readable by other local users; prefer a configured, ACL'd
  temp/work dir.
- **MM-16** Accessibility: the dropzone is a `div` with a click handler (`:184`) — not
  focusable, no role; the label at `:183` has no `for`; the hint (`:208`) has no
  `aria-live`; arrow buttons rely on glyphs + `title`. Add `role="button" tabindex="0"`,
  keyboard activation, a real `<label for="file-input">`, and `aria-live="polite"` on
  the hint/list.
- **MM-17** Tests: `test_manual_merge_isolated` (`test_quarantine.py:236-242`) is
  vacuous (`count() == 0 or True`, then asserts a fresh empty DB is empty). It would
  pass if the function wrote rows. Route coverage is 81%; uncovered branches include
  the `.pdf` suffix check (`:102`), the single-file byte cap (`:105`), the magic check
  (`:120`), the empty-order path (`:131`), the sanitiser fallbacks (`:136-138`) and the
  whole HTML error page (`:39-54`). There are **no JS behaviour tests** —
  `test_audit_frontend.py:168` only greps for `innerHTML`.
- **MM-18** PRODUCT is declared the source of truth, but has no manual-merger section;
  the only normative text is SPEC §7.12 and business §14.2, and AGENTS says the SPEC's
  merge sections are retired history. Add a short PRODUCT section (or explicitly point
  to §7.12) so the next agent does not "fix" this feature against stale text.
- **MM-19** `MAX_UPLOAD_BYTES`/`MAX_MANUAL_MERGE_FILES` are module constants
  (`limits.py:11,17`). If they are product decisions, say so in PRODUCT; if they are
  environment knobs, move them to `config.yaml` per AGENTS §5.
- **MM-20** Merged output metadata is `{'/Producer': 'pypdf'}` — the source Title/Author
  are dropped. If the CA files packets by title, preserve metadata (or add a cover
  page / manifest). At minimum document it.
- **MM-21** `PdfReader` objects in the loop (`quarantine.py:907-909`) are not closed.
  CPython refcounting makes the existing cleanup test pass on Windows, but explicit
  `reader.close()` in a `finally` is cheap insurance. Starlette runs the cleanup
  background only after a successful send; a 35 MB client abort on uvicorn 0.52 left
  **0** temp files, so this is theoretical — but a startup sweep of `tmp*.pdf` older
  than N hours would close the hole permanently.
- **MM-22** `parents[4] / "templates"` (`manual_merger.py:15`) works from the repo
  (the documented NSSM command runs there), but `pyproject.toml` packages only
  `src/app`, so a wheel install has no templates. Same latent issue as the dashboard;
  low priority because of the deployment model.

---

## 6. Test-quality assessment

Targeted run (not the full suite; the worktree has pre-existing uncommitted changes):

```
pytest tests/test_dashboard.py tests/test_audit_config_security.py \
       tests/test_quarantine.py tests/test_layer2_gaps.py
→ 7 manual-merger tests pass
→ src/app/api/routes/manual_merger.py: 81% (79 stmts, 11 miss)
```

What the tests prove: bad order → 422; success + 3/3 temp cleanup; file-count and
total-byte caps; the service merges in the requested order; a user-supplied
`output_path` works at the service level; the page links back to the dashboard; the
template contains no `innerHTML`.

What they do **not** prove (and should):

1. No DB row is created — the current test is vacuous. Create a DB with one `po_sets`
   row, call the endpoint, assert the row and count are unchanged, and assert no
   `audit_log` row.
2. The UI destination requirement (FR-14.12) — the current test bypasses the UI.
3. The row-drop bug, the double file-picker call, the drop-outside behaviour, the
   staged-list survival on error — all need a real browser (Playwright) test.
4. `.pdf` suffix rejection, the per-file cap, the magic check, the empty-order path,
   the HTML error page — 11 uncovered route lines.
5. Zero-page, leading-BOM/padding, truncated, encrypted PDFs — the real-world cases.
6. A concurrent-merge smoke test (two simultaneous posts → two complete PDFs, no shared
   state) — not a lock issue, just isolation.

---

## 7. Recommended order of work

1. **MM-01** row-drop corruption + document-level drop guard. This is the only finding
   that silently produces a wrong packet from normal use.
2. **MM-02** request-size gate before parsing (Content-Length middleware and/or bounded
   form parsing). Protect the prod host's disk and worker pool.
3. **MM-03/04** unify PDF validation with ingestion (1024-byte header window, pypdf
   open, ≥1 page) and name the offending file. This also fixes half of MM-05.
4. **MM-05** in-page error handling so a failed merge does not destroy the staged list;
   HTML mapping for validation/parse errors.
5. **MM-06** decide FR-14.12 (implement destination or amend the spec + PRODUCT +
   tests).
6. **MM-07** strip page actions (or pin the limitation in PRODUCT §8 with a test) and
   handle encrypted inputs explicitly.
7. **MM-08/09** client-side limits and a non-destructive filename rule.
8. **MM-17** add the missing browser and route tests; then close MM-10–MM-22 as
   hardening.

---

## Appendix A — Evidence log (commands actually run)

| Check | Result |
|---|---|
| `pytest` on the 4 relevant files | 7 passed |
| Route coverage | `manual_merger.py 81%`, missing lines 35, 41-43, 78, 84, 102, 105, 120, 131, 136, 138 |
| Baseline `POST /manual/merge`, 2 files `order=1,0` | 200, pages `[200, 100]` (order honoured) |
| Browser E2E: stage `a,b,c` → click ↑ on row 3 twice → merge | 200, 3 pages, widths `[300, 100, 200]`; `FileList` order `c,a,b`; `order` field `0,1,2` |
| OS-file drop on a row (synthetic `DragEvent` + `File`) | dropped file ignored; `[c,a,b]` → `[a,c,b]` |
| 0-page PDF | 200; output pages = 0 |
| PDF with `\r\n\r\n` / BOM / header at byte ~890 | 422 "not a PDF file" |
| Truncated PDF (10–400 bytes cut) | 422 `manual merge failed: Stream has ended unexpectedly` |
| Encrypted (owner pw, empty user pw) | merges; output `is_encrypted: False` |
| Page `/AA` + Link `/JavaScript` in input | both present in output |
| `Invoice 1234 (final).pdf` | `Content-Disposition: … filename="Invoice1234final.pdf"` |
| 3000-char filename | 3027-char `Content-Disposition` accepted |
| No files + `Accept: text/html` | 422 JSON, not the HTML page |
| 21 files + `Accept: text/html` | 422 HTML page (raised inside the route) |
| 1001 files | 400 JSON from Starlette's `max_files` |
| Clicking "Choose PDF Files" with `fileInput.click` instrumented | `opens: 2` |
| Corrupt PDF via browser → submit | error page; Back → `files.length === 0` |
| 35 MB download, client closed after response headers | 0 leftover temp files (cleanup ran) |

## Appendix B — Probe scripts

Throwaway harnesses used for the observations above (outside the repo):

- `%TEMP%\opencode\mm_probe.py`, `mm_probe2.py`, `mm_probe3.py`, `mm_probe4.py`
- `%TEMP%\opencode\disconnect_probe.py`
- `%TEMP%\opencode\make_pdfs.py`, `%TEMP%\opencode\mmui\{a,b,c,corrupt}.pdf`

No repository source file was modified during the audit phase. The remediation is
recorded in §8.

---

## 8. Remediation status (2026-10-23)

The tool was rebuilt to the contract in this report. The spec amendments (FR-14.12
download-only, FR-14.13 same shell with a standalone marker) were approved by the
product owner before the change.

| ID | Status | What changed |
|---|---|---|
| MM-01 | **Fixed** | Row drop branches on `dataTransfer.files.length` before any reorder; files are added. Re-tested in a browser: a dropped PDF is appended, the existing order is untouched. |
| MM-02 | **Fixed** | New `BodySizeLimitMiddleware` rejects an oversized `Content-Length` before the app, and counts chunked bytes as they arrive. The form parser is also bounded (`max_files=21`, 64 KB fields). |
| MM-03 | **Fixed** | The header may sit anywhere in the first 1024 bytes (`b"%PDF-" in data[:1024]`), matching `ingestion.py:143`. The truncated / unreadable cases are named. |
| MM-04 | **Fixed** | Zero-page files are rejected with code `no_pages`; the merged output's page count is asserted against the sum of the inputs. |
| MM-05 | **Fixed** | XHR + in-page alert; the failing filename is marked on its card; the staged list survives. Validation/parse failures return the same JSON shape, and a plain form post gets the themed `manual_merger_error.html` page. |
| MM-06 | **Resolved by amendment** | SPEC FR-14.12 and PRODUCT §6.2 now state download-only; the UI names the download and the browser chooses the folder. |
| MM-07 | **Fixed** | `/AA`, `/JS`, `/JavaScript` and `/Launch` actions are stripped; ordinary `/URI` links survive. **Every** encrypted PDF is now refused (including owner-password-only files) with a message asking for an unlocked copy; the tool never decrypts and never drops permission flags. |
| MM-08 | **Fixed** | Type, count and total-size validation runs as files are staged; over-limit files are not added and the reason is shown; the merge button is disabled with a reason. |
| MM-09 | **Fixed** | Strict validation instead of silent rewriting: spaces, parentheses and Unicode are preserved; `<>:"/\|?*`, control characters, leading/trailing dots/spaces and reserved Windows names are **rejected** with a named message (`bad_filename`); `.pdf` is appended; the stem is capped at 100 chars. The UI validates live, previews the final name and disables Merge while invalid. |
| MM-10 | **Fixed** | One click handler; measured one `fileInput.click()` per action. |
| MM-11 | **Fixed** | Document-level `dragover`/`drop` guards; a file dropped anywhere is added, never navigated to. |
| MM-12 | **Fixed** | `logger.exception` on unexpected failures; 4xx messages are specific and name the file; the raw pypdf text is no longer shown. |
| MM-13 | **Fixed** | `FormData` is built by iterating the display list; no `DataTransfer`/`FileList` mutation. The legacy `order` field is still accepted by the API. |
| MM-14 | **Fixed** | Logic moved to `src/app/services/manual_merge.py`; `quarantine.manual_merge` remains as a re-export for existing callers. |
| MM-15 | **Fixed** | `Cache-Control: no-store` + `X-Content-Type-Options: nosniff` on the download; uploads stage in the configured `manual_merge.temp_folder` with a startup/per-merge sweep of only `mm-*.pdf`. |
| MM-16 | **Fixed** | `role="button"` + `tabindex` + Enter/Space on the dropzone, a real `<label for>`, `aria-live` on the list/alert, `role="progressbar"` with `aria-valuenow`, and accessible names on every icon button. |
| MM-17 | **Fixed** | `tests/test_manual_merger.py` (86 backend), `tests/test_manual_merger_ui.py` (20 source contracts) and `tests/test_manual_merger_e2e.py` (6 Playwright tests, opt-in with `RUN_E2E=1`). The vacuous no-DB assertion is removed; the route proof is a `get_engine` trap. |
| MM-18 | **Fixed** | PRODUCT §6.2 added; SPEC §7.12 amended. |
| MM-19 | **Fixed** | Limits are config-driven under `manual_merge:` (`max_files`, `max_total_mb`, `max_pages`) with defaults from `core/limits.py`; the page reads them from the route. |
| MM-20 | **Deferred** | Source Title/Author and outlines are still dropped; explicitly out of scope (product decision). |
| MM-21 | **Fixed** | Readers are closed in a `finally`; the dedicated folder is swept at startup and before each merge (only `mm-*.pdf`, older than `sweep_after_hours`). The 35 MB disconnect probe left no temp files. |
| MM-22 | **Deferred** | Templates remain repo-root assets; the deployment model runs from the repo, so this is not live. |

### Verification performed

| Check | Result |
|---|---|
| Manual suites (`test_manual_merger.py` + `test_manual_merger_ui.py`) | **106 passed, 0 failed** (86 backend + 20 UI) |
| Playwright E2E (`RUN_E2E=1`, Chromium) | **6 passed, 0 failed** |
| Manual + manual-adjacent legacy suites in one run | **238 passed, 6 skipped (E2E opt-in), 0 failed** |
| Full suite | Not used as the gate here — other parts of the app are being edited in parallel. The last clean run was 980 passed; a later run hit one unrelated reconciliation test that passes in isolation. |
| `ruff check` on every changed Python file | clean |
| Playwright: stage `a,b,c` → move `c` to the top → merge | 3 pages, widths `300, 100, 200`; suggested download name `E2E packet.pdf`; success status; no navigation |
| Playwright: OS PDF dropped on a row | appended, existing order unchanged (the MM-01 regression) |
| Playwright: encrypted PDF → merge | inline alert "encrypted or password-protected", card marked, list preserved, no download, URL unchanged |
| Playwright: 21 files | 20 staged, 1 rejected with "not added" |
| Playwright: invalid output name (`bad/name.pdf`) | inline message, Merge disabled; valid `Invoice 1234 (final)` enables it |
| Playwright: 420 px viewport | `.mm-grid` is a single column |
| Earlier manual browser run | corrupt PDF → inline alert and list preserved; `发票2024` accepted; `Quarterly packet` downloaded as `Quarterly packet.pdf`; no console errors |
| Live server after the final refactor | pages ordered `a,b,c` → `100,200,300`; zero-page → `no_pages`; encrypted (user or owner password) → `encrypted`; `a/b.pdf`/`CON.pdf`/101 chars → `bad_filename`; missing name → `merged-document.pdf` |
| Screenshots | not available in this environment (the desktop browser window was not visible to the capture tool); Playwright covers the interaction and the responsive layout |

### Running the browser suite

Dependencies are in the `dev` group (`playwright`, `pytest-playwright`). On a
fresh machine, install the browser once:

```powershell
uv sync --dev
.venv\Scripts\python.exe -m playwright install chromium
$env:RUN_E2E = "1"
.venv\Scripts\python.exe -m pytest tests/test_manual_merger_e2e.py -q
```

The suite starts its own uvicorn with a temporary `config.yaml` (via
`AAM_CONFIG_PATH`), database, staging folder and data paths, so it never touches
the real data folder. Without `RUN_E2E=1` those six tests are skipped and the
normal suite stays browser-free.

### Files added or changed

- Added: `src/app/services/manual_merge.py`, `src/app/core/http_errors.py`,
  `templates/manual_merger_error.html`, `static/css/manual_merger.css`,
  `static/js/manual_merger.js`, `tests/test_manual_merger.py`,
  `tests/test_manual_merger_ui.py`, `tests/test_manual_merger_e2e.py`.
- Rewritten: `src/app/api/routes/manual_merger.py`, `templates/manual_merger.html`.
- Edited: `src/app/main.py` (body guard + startup sweep), `src/app/core/config.py`
  (`manual_merge` section), `src/app/core/limits.py`,
  `src/app/services/quarantine.py` (re-export), `config.example.yaml`,
  `templates/base.html`, `templates/dashboard.html`, `AAM_merger_V3_SPEC.md`,
  `AAM_merger_V3_PRODUCT.md`, `pyproject.toml` + `uv.lock` (Playwright),
  `tests/conftest.py` (redirect the staging folder), `tests/test_audit_frontend.py`,
  `tests/test_audit_config_security.py`, `tests/test_quarantine.py`.
