# SPEC: replace a customs / shipping document before Merge Now

Status: **rev 1 (Claude, 2026-10-25). Owner-requested (D19). Not built; builds after the PF batch is reviewed (same files).**
Decision source: owner, 2026-10-25: "add the merge now button which gives the user the chance to replace any or both
the docs if needed". Reviewer: Opus before commit (touches the merge path's inputs).

## Why
An uploaded CUSTOMS/SHIPPING file is attached as-is and cannot be removed. A wrong file ends up in the packet. Merge Now already
exists and keeps every gate; what is missing is a way to take a wrong file out before pressing it.

## Behaviour
- Each attached CUSTOMS / SHIPPING document on the set page gets a **Remove** button (testid `btn-remove-supporting-<doc_id>`),
  with a confirm. The operator then uploads the right file with the existing form and presses **Merge Now**.
- Allowed only while the set is NOT merged and NOT archived (pending, mismatched, quarantined, blocked_customs). A merged
  set is refused with 409 "the packet is already delivered": replacing inside a delivered packet stays out of scope (it would
  overwrite a packet that may have been sent).
- Only documents of type CUSTOMS or SHIPPING can be removed this way. PO/DN/SI are refused (422).

## Route (`po_sets.py`, new): `POST /po_sets/{po_set_id}/documents/{doc_id}/remove_supporting`
1. Acquire the per-PO lock (`_acquire_lock`, action `remove_supporting`); 409 before any write if busy; release in `finally`.
2. Refuse: sync running (same message as upload), set archived/merged, document not in this set, wrong type.
3. DETACH, do not delete and do not archive: set `po_set_id = None` (documents relationship does not filter archived rows, so
   an archived row would still be merged into the packet). The stored file is never deleted (standing rule).
4. Recompute `customs_doc_count` from the remaining attached CUSTOMS/SHIPPING types.
5. If the set is `pending` or `blocked_customs` it is left for reconcile to re-evaluate (status set to `pending`; reconcile
   returns `blocked_customs` again when the gate is unsatisfied). A quarantined/mismatched set keeps its status.
6. Write an AuditLog row, action `manual_status_change`, detail `{"removed_supporting_document": <name>, "doc_type": ...}`,
   source `operator`.
7. Re-uploading the identical bytes works: the existing upload path already re-attaches a document whose `po_set_id` is None
   (rows with pipeline data or a reject reason are refused, which a customs row never has).

## Tests (test first)
- Remove one of two attached docs on a `blocked_customs`/pending set: doc detached, count 1, audit row, file still on disk.
- Upload a replacement, Merge Now: the packet contains the replacement, not the removed file (compare page counts / text-free
  marker pages).
- Re-upload of the SAME bytes after removal re-attaches it.
- Merged set: 409, nothing changed. PO/DN/SI document: 422. Document of another set: 404/409. Busy lock: 409, nothing written.
- Archived-document leak check: a removed document never appears in `_ordered_docs`.
- UI case in `tests/ui/test_ui_set_detail.py` plus a seed scenario; button absent on a merged set.

## Allowed paths
`src/app/api/routes/po_sets.py` (route), `templates/po_set_detail.html`, `scripts/ui_test_env.py`, `tests/**`,
`docs/UI_TEST_PLAN.md`. Forbidden: merge.py, reconciliation.py, models, migrations. No schema change is needed.
Also (small, same file): fix the "Customs Clearance: Attached" line to require BOTH CUSTOMS and SHIPPING, and add one sentence to
the upload help text: "Upload both, check the file names, then press Merge Now."
