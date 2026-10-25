# UI test plan (browser tests)

Audience: a test-writing agent (DeepSeek through OpenCode, or a person). Read
`docs/OPENCODE_UI_TEST_BRIEF.md` first for the rules. This file is the work list.

## 1. How the harness works

| Piece | What it is |
|---|---|
| `scripts/ui_test_env.py` | Builds a throwaway environment with the REAL pipeline code and a canned VLM reply (no network, no cost) and serves it. Writes `manifest.json`. |
| `tests/ui/conftest.py` | Starts the environment once per run, gives you `page`, `manifest`, `db` (read-only SQLite) and `base_url` (so `page.goto("/dashboard")` works). Fails any test that raises a JavaScript error. |
| `data-testid` hooks | Added to the templates. Use `page.get_by_test_id(...)`. Never select by CSS class or text position. |
| Reference tests | `tests/ui/test_ui_dashboard.py`, `test_ui_set_detail.py`, `test_ui_global.py`, `test_ui_unclassified.py` (30 passing tests). Copy their style. |

Known `data-testid` gaps: the preview buttons on the unclassified and set pages have no id
(select them by test id on the row first), the toast stack has no id (use `.toast-<type>`,
e.g. `.toast-warning`). The quarantine table's archive button is `btn-archive-row`.
A toast survives a reload only when the action that showed it reloads the page (the message
is parked in `sessionStorage`); do not assert a toast for an action that stays on the page.

Run (PowerShell):

```powershell
$env:RUN_E2E = "1"
uv run python -m pytest tests/ui -o addopts= -p no:cacheprovider -W ignore -q
```

Useful flags: `-k ui_s_06` runs one case (case ids are in the function names), `--headed` to watch, `--screenshot only-on-failure --tracing retain-on-failure --output data/ui_test_artifacts`
to keep evidence, `-x` to stop at the first failure.

Inspect the environment by hand: `uv run python scripts/ui_test_env.py --root data/ui_test --serve`
then open the printed URL (`data/ui_test` is gitignored and wiped on every build).

## 2. The seeded data (the manifest)

`manifest["scenarios"][name]` has `id`, `po_no`, `status`, `reason`, `mutable`, `line_mapping_rule`,
`merged_output_path`, `docs[{id,type,file,lines}]`. `manifest["held"][name]` is the Unclassified page.
`manifest["counts"]` is sets per status. Statuses and reasons are what the engine really produced.

| Scenario | Status | What it shows |
|---|---|---|
| `merged_basic` | merged | 2 lines, PO = DN = SI, real packet PDF |
| `merged_mapped_x10` | merged | PO 10,20 vs DN/SI 1,2: "lines mapped (x10)" |
| `pending_no_dn` | pending | "No delivery note attached to this set yet (not received, still being read, or waiting on the Unclassified page)" |
| `pending_partial_delivery` | pending | line 1 not delivered: "Awaiting" verdict |
| `pending_vendor_line_not_on_po` | pending | SI line 2 matches no PO line: "Not on the PO" note |
| `pending_two_po_documents` | pending | "holds 2 PO documents" |
| `needs_review_quantity` | mismatched | DN 8 of 10 |
| `force_merge_target` (mutable) | mismatched | one test may force merge it |
| `awaiting_customs` | blocked_customs | customs ON, no customs documents |
| `quarantined_po_no_lines` | quarantined | PO read with no line items |
| `quarantined_unmatched_vendor_line` | quarantined | DN line 9 matches nothing |
| `reextract_target` (mutable) | quarantined | Re-Extract's own target (S-16); re-extracting resets its verdict |
| `archive_target` (mutable) | quarantined | one test may archive it |
| `force_merge_validation_target` (mutable) | mismatched | for the justification-validation test |
| `archive_modal_target` (mutable) | quarantined | for the quarantine-table delete modal test |
| `customs_toggle_target` (mutable) | pending | one test may toggle customs |
| `upload_customs_target` (mutable) | pending | one test may upload customs/shipping PDFs |
| `customs_remove_target` (mutable) | pending | customs ON, a CUSTOMS and a SHIPPING file attached, no DN; S-21 removes the CUSTOMS file |

Held documents: `held_dn_no_po`, `held_ambiguous_po`, `held_not_read`, `held_failed`,
`held_hostile_name` (filename is HTML, must be escaped), `held_reclassify_target` (mutable),
`held_reclassify_no_po` (mutable: the no-PO reclassify path, whose message arrives with
`HX-Refresh`), `rejected_on_arrival` (file ingest refused).

**Independence rule:** a test that changes data owns exactly ONE `mutable` entry and never touches
another. If you need another mutable entry, add it to `SCENARIOS`/`HELD` in `scripts/ui_test_env.py`
(allowed: that file is part of the harness) and say so in your report. Tests must pass in any order.

## 3. Case matrix

Status: DONE = implemented in the repo and passing. TODO = write it. Each TODO is one test function
named `test_ui_<area>_<nn>_<what_it_checks>` in the file shown.

### Dashboard (`tests/ui/test_ui_dashboard.py`)
| ID | Status | Check |
|---|---|---|
| D-01 | DONE | every scenario row: PO, status text, `data-status`, doc count, reason |
| D-02 | DONE | KPI cards and tab counts equal the manifest |
| D-03 | DONE | a status tab shows only that status |
| D-04 | DONE | a PO link opens its set page |
| D-05 | DONE | a merged row offers a working PDF download |
| D-06 | TODO | the status dropdown (`#status-filter`) filters like the tabs and keeps the selection |
| D-07 | DONE | `?status=nonsense` renders the app shell with a 422 and a way back (not raw JSON) |
| D-08 | TODO | the table refreshes itself: a request to `/dashboard/table` happens within 7 s (HTMX poll) |
| D-09 | TODO | nav links go to the right pages; the Unclassified badge equals `len(manifest["held"])` |
| D-10 | TODO | `merged_mapped_x10` row shows "lines mapped (x10)" |
| D-11 | TODO | `awaiting_customs` row shows "Awaiting Customs" and the customs reason |
| D-12 | TODO | the Scan Input Folder button (`btn-scan-input`): clicking it does not break the page. Prefect is NOT running here, so assert a visible, non-crashing response (note what it says in your report) |

### Set page (`tests/ui/test_ui_set_detail.py`)
| ID | Status | Check |
|---|---|---|
| S-01..S-08 | DONE | merged matrix and buttons; mapped flag; waiting reason + "Not on the PO"; quarantined reason; preview PDF; force merge; archive; customs toggle |
| S-09 | TODO | data-driven: for EVERY scenario the matrix row count, and the PO/DN/SI totals (sum the three numeric cells) equal the DB (`db` fixture; sum `line_items.quantity/1000` per document type, accounting for unmatched vendor lines shown as flags) |
| S-10 | TODO | `pending_two_po_documents`: reason names both PO files |
| S-11 | TODO | `awaiting_customs`: reason card says both CUSTOMS and SHIPPING are required; the upload form is visible |
| S-12 | TODO | `upload_customs_target`: uploading a non-PDF shows an error and attaches nothing; uploading a valid PDF as CUSTOMS and one as SHIPPING (generate with pypdf) increases the document count by 2 |
| S-13 | TODO | Re-Match on a pending set reloads and keeps the same reason (use `pending_no_dn`, not mutable: it changes nothing) |
| S-14 | TODO | Merge Now on `needs_review_quantity` does not merge and explains why (the status stays mismatched in the DB) |
| S-15 | DONE | Force merge with a justification shorter than the minimum is refused with a visible message (one icon, not two) and nothing merges (`force_merge_validation_target`) |
| S-16 | DONE | Re-Extract with AI has no provider key here: the operator is told every read failed, and the message survives the reload the action triggers. The set's verdict afterwards is the engine's decision — a failed re-read leaves it `pending` with `document_not_yet_read` (pinned by `tests/test_mvp_blockers.py`), so "the set is unchanged" is NOT the requirement. Own scenario: `reextract_target` |
| S-17 | DONE | an unknown set id (`/po_sets/999999/view`) shows a friendly 404 page, not JSON or a stack trace |
| S-18 | TODO | the preview drawer closes with its close button and with Escape |
| S-19 | TODO | a merged set page has a working "download merged PDF" link |
| S-20 | TODO | an archived set (after S-07) is read-only: no action buttons |
| S-21 | DONE | `customs_remove_target`: Remove on the CUSTOMS row (confirm accepted) detaches and archives it in the DB, the row disappears after the reload, a success toast naming the file survives it, and the header shows Customs Clearance missing. S-21b: a merged set page has no remove buttons |

### Unclassified (`tests/ui/test_ui_unclassified.py`)
| ID | Status | Check |
|---|---|---|
| U-01..U-04 | DONE | all held docs listed; failed/rejected marked; route disabled until type chosen; reclassify places a DN |
| U-05 | TODO | typing `PO-7000003` in the PO box shows "Server key: 7000003" (debounced) |
| U-06 | DONE | `held_ambiguous_po` shows the ambiguous/multiple-PO marker on its own row |
| U-07 | DONE | previewing `rejected_on_arrival` answers with an HTML page in the drawer ("cannot be previewed" + the reason), never chrome's JSON viewer |
| U-08 | TODO | `rejected_on_arrival`: Save & Route returns an error mentioning "rejected on arrival" and the row stays |
| U-09 | DONE | reclassify with no PO number for a DN leaves it held and says why (the message survives the `HX-Refresh` reload; `held_reclassify_no_po`) |
| U-10 | TODO | the page count badge equals the number of rows after a reclassify removes one |
| U-11 | DONE | a held document's stored type is shown read-only below the select; the select is never pre-selected (guards U-03) |
| X-01 | DONE | a hostile HTML filename is escaped |
| X-02 | TODO | the same hostile filename is escaped on the set page after it is attached (add it to a set) |

### Quarantine and Audit
| ID | Status | Check |
|---|---|---|
| Q-01 | DONE | quarantine page lists exactly the quarantined sets |
| Q-02 | TODO | each row shows its reason; "Inspect & Resolve" opens the right set |
| Q-03 | TODO | the Archive modal from the table: Cancel does nothing, Confirm archives (use `archive_modal_target`) |
| Q-04 | DONE | the quarantine table's destructive control is labelled "Archive" (`btn-archive-row`), not "Delete" |
| A-01 | DONE | audit page shows the seeded row |
| A-02 | TODO | after S-06 force merge, the audit page shows a `force_merge` row with the justification text |
| A-03 | TODO | after S-07 archive, a "Quarantine Delete" row exists for that set |

### Merge PDFs tool
Covered by `tests/test_manual_merger_e2e.py` (opt-in, already passing). Do not duplicate; run it as part of the sweep.

### Global (`tests/ui/test_ui_global.py`, new)
| ID | Status | Check |
|---|---|---|
| G-01 | TODO | visit every page in the nav and 3 set pages: no console errors, no failed requests (4xx/5xx), no missing static files |
| G-02 | TODO | **offline LAN**: every network request on every page goes to the app's own host (no CDN, no fonts, no analytics) |
| G-03 | TODO | at widths 1024, 1366 and 768 there is no horizontal scroll on the dashboard and a set page |
| G-04 | DONE | keyboard: opening the Force Merge modal moves focus into it, Tab stays inside, Escape closes it and returns focus to the trigger |
| G-05 | TODO | a POST from a foreign Origin (use `page.request.post(..., headers={"Origin": "http://evil.example"})`) to `/po_sets/{id}/merge` is rejected with 403 |
| G-06 | TODO | each page has a non-empty, distinct `<title>` |
| G-07 | TODO | `aria_snapshot()` of the dashboard header/nav is stable (store the expected text inline) |

## 4. Beyond the functional suite (optional, in this order)

1. **Evidence screenshots:** a test that visits each page and state and saves a PNG to
   `data/ui_test_artifacts/` for a human or a vision model to review. Text-only models must not judge them.
2. **Failure injection:** use `page.route("**/dashboard/table", lambda r: r.abort())` to simulate a dead server
   and assert the page still shows the last data and does not throw.
3. **Two tabs, one action:** open the same set twice, Force Merge in both; the second must get a clear 409 message.
4. **Accessibility smoke:** every button has an accessible name; every form control has a label.

## 5. Definition of done for a case
- passes 3 times in a row (`for /L %i in (1,1,3) do ...` or a PowerShell loop);
- independent of order and of other tests' data changes;
- fails if the behaviour is removed (state in your report how you checked, for example by editing the expected value);
- uses `get_by_test_id` and `expect()` auto-waiting, with no `time.sleep`;
- every action that changes data waits for its HTTP response (`page.expect_response(...)`).
