# SPEC: replace a customs / shipping document before Merge Now

Status: **rev 2 (Claude, 2026-10-25, after the Opus review: APPROVE WITH CHANGES, all changes applied). Owner-requested (D19).
Not built; builds after the PF batch is reviewed (same files).** Reviewer: Opus again before commit.
Decision source: owner, 2026-10-25: "add the merge now button which gives the user the chance to replace any or both
the docs if needed".

## Why
An uploaded CUSTOMS/SHIPPING file is attached as-is and cannot be removed. A wrong file ends up in the packet. Merge Now already
exists and keeps every gate; what is missing is a way to take a wrong file out before pressing it. No auto-reconcile on upload or
remove (with the toggle OFF it would merge a packet without the replacement), and no Force Merge for customs.

## Behaviour
- Each attached CUSTOMS / SHIPPING document row on the set page gets a **Remove** button (testid
  `btn-remove-supporting-<doc_id>`), `hx-confirm`, page reload on success. Rendered only for CUSTOMS/SHIPPING rows, only when the
  set is not merged and not archived; disabled when `is_locked` (like the other row buttons). Then the operator uploads the right
  file with the existing form and presses **Merge Now**.
- Not for merged sets (409 "the packet is already delivered"): replacing inside a delivered packet is out of scope and goes in
  PRODUCT section 8 as a limitation (see Docs).
- Only CUSTOMS / SHIPPING documents (422 for PO/DN/SI).
- Help text on the upload box gains: "Upload both, check the file names, then press Merge Now. Keep Customs Check ON while
  replacing: with it OFF a later upload does not rebuild a packet and a sync may merge the set first."

## Route (`po_sets.py`, new): `POST /po_sets/{po_set_id}/documents/{doc_id}/remove_supporting`
1. Refuse while a sync is running (same message as the upload route). Acquire the per-PO lock (`_acquire_lock`, action
   `remove_supporting`): 409 before any write if busy; release in `finally`. The lock serialises this against Merge Now and the
   sweep (the sweep skips locked sets; `merge_po_set(expected_doc_ids=...)` is a second defence).
2. INSIDE the locked session, re-read and check (not before the lock; `_refuse_if_merged` runs before the lock and would let a
   finishing Merge Now slip through): set exists (404), not archived (409), status not `merged` (409), document belongs to this set
   (404, like `confirm_placement`), type is CUSTOMS or SHIPPING (422).
3. DETACH AND ARCHIVE the row: `po_set_id = None` AND `archived_at = now` (plus `archived_by` if the column exists). Detach keeps
   it out of the packet (the documents relationship does not filter archived rows). Archive keeps it out of the holding area,
   `unplaced_count`, the upload dedup and the sweeps, and stops an operator reclassifying it onto a merged set (reclassify attaches
   CUSTOMS/SHIPPING even to a merged set and rebuilds the delivered packet). The stored file is never deleted. The partial SHA
   index covers non-archived rows only, so the same bytes can be uploaded again as a NEW row.
4. Recount `customs_doc_count` from `Document.po_set_id == id AND archived_at IS NULL` (as the upload route does; `toggle_customs`
   counts without that filter, do not copy it).
5. Status is NOT changed in any case. Removing a document can only make the gate less satisfied; writing `pending` over
   `blocked_customs` would drop the "Awaiting Customs" header and leave a stale reason. `merge_po_set` re-checks `is_blocked`, so a
   short set cannot merge, and the next Merge Now / sync re-evaluates and writes `blocked_customs`.
6. Audit row, action `manual_status_change`, source `operator`, detail
   `{"note": "Removed <TYPE> <filename> (document #<id>)", "document_id": <id>, "doc_type": "<TYPE>", "filename": "<name>"}`
   (the audit summary renders only `note` or `filename`; no `dashboard.py` change).
7. Return JSON `{status, po_set_id, customs_doc_count, set_status}` so the page can show the new count.

## Tests (test first)
- Remove one of two attached docs on a `blocked_customs` set: doc detached AND archived, count 1, status unchanged, audit row with
  a readable note, file still on disk, not listed on the Unclassified page, not in `unplaced_count`.
- After removal a reconcile (toggle ON) returns `blocked_customs`; with the other doc missing, Merge Now refuses.
- Upload a replacement, Merge Now: the packet contains the replacement and not the removed file (marker pages / page counts).
- Re-upload of the SAME bytes after removal creates a new attached row.
- Merged set: 409, nothing changed, button absent in the page. PO/DN/SI document: 422. Document of another set: 404. Archived
  set: 409. Busy lock (another action holds it): 409 and nothing written. Sync running: 409.
- Remove with the toggle OFF succeeds (documented in the help text).
- The removed document never appears in `_ordered_docs` and cannot be reclassified onto a merged set.
- UI case in `tests/ui/test_ui_set_detail.py` plus a seed scenario.

## Allowed paths
`src/app/api/routes/po_sets.py` (route), `templates/po_set_detail.html`, `scripts/ui_test_env.py`, `tests/**`,
`docs/UI_TEST_PLAN.md`, `AAM_merger_V3_PRODUCT.md` (section 8 line). Forbidden: merge.py, reconciliation.py, models, migrations.
No schema change is needed. The "Customs Clearance" line 106 is NOT changed: the Shipping row is already shown separately on
line 107, and requiring both on line 106 would make it wrong.

## Docs
PRODUCT section 8: a delivered packet's customs or shipping file cannot be replaced (409); the only remedy is outside the app.
Pin with `test_known_limitation_delivered_packet_customs_cannot_be_replaced`.
