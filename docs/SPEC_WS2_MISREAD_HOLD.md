# SPEC WS2 (T13): possible PO misread hold and the operator's three actions

Status: **design for review, not built.** Author: Claude (architect). Builder: worker. Reviewer: **Opus** (reconciliation change plus
a migration). Decision source: D1 (user, 2026-10-24): do not merge a suspected misread; hold it and let the operator choose.
Evidence: HANDOFF section 6c "D1 design notes" (real run: exactly one true case, set 9 vs set 8; a plain "one digit apart" rule
would have flagged two legitimate pairs).

## Rule (precision over recall: hold when in doubt)
Set A is **suspect** when ALL are true:
1. A is active (not archived, not merged) and every document in A is a `PO` document (no DN, SI, customs or shipping).
2. Another active set B has a `po_no_normalized` that differs from A's in exactly one character position (same length, compared
   as strings after the usual normalisation) AND B holds at least one DN or SI.
3. A's `po_misread_cleared` is false.

Effect: A and B both get reason `possible_po_misread` (status `pending`) and **neither merges**. Text on both pages:
"Possible misread: PO {A} looks like PO {B} (one digit differs). Check the page and choose: move it to the right PO, say it is not a
misread, or Force Merge." B is held only while an uncleared suspect A exists. Force Merge on either set still works (audited).

Evaluation order inside `_reconcile_po_set_inner`: after the settle check passes (so a late DN/SI for A gets its chance) and before
the quantity comparison can produce `merged`. The lookup runs in both directions: A asks "am I suspect", B asks "is there an
uncleared suspect one digit from me". Reconciling A re-evaluates B and vice versa.

Known false positive (accepted, to be documented in PRODUCT section 8 and pinned as `test_known_limitation_...`): a legitimate PO
that arrives before its DN/SI while a one-digit neighbour is ready to merge. Mitigations: the settle time and the "Not a misread"
action.

## Operator actions (set page of A or B; each audited as AuditLog `manual_status_change`; each takes the per-PO lock and answers 409 when busy)
1. **Move to another PO** (A's documents only): `POST /po_sets/{id}/documents/{doc_id}/move` with form field `po_no`.
   Normalises X; refuses an empty or same key; refuses a letters conflict (`PoLettersConflict`); refuses a document that is not in
   this set. The target is found with `get_or_create_po_set(create=False)` and must already exist (a move never mints a set; if it
   does not exist the page says so). Sets the document's `po_no_raw`, `po_no_normalized`, `po_no_source="manual"` and `po_set_id`;
   if A is left empty it is archived through the standard archive path (audited, never deleted); reconciles both sets.
2. **Not a misread**: `POST /po_sets/{id}/clear_misread` sets `po_misread_cleared=True` on that set and reconciles it and its neighbour.
3. **Force Merge**: unchanged.

## Schema
One migration: `po_sets.po_misread_cleared BOOLEAN NOT NULL DEFAULT 0` (server_default `0`), Alembic revision chained after the
current head. `scripts/migrate_db.py` must still upgrade both fresh and create_all-era databases. Add the column to `models.py`.
(A migration-free alternative that derives "cleared" from an audit row was rejected: it adds an audit query to every reconcile.)

## Tests the builder must write (test first; new file `tests/test_misread_hold.py`)
- True positive: A = PO-only `4500057195`, B = `4500051195` with PO, DN and SI all matching. B does NOT merge; both are `pending`
  with `possible_po_misread`; A's page shows both actions.
- Must still merge: two one-digit-apart POs that each have PO, DN and SI complete (real sets 2/3 and 41/43); A with a DN attached
  is not suspect; different lengths are not suspect; a merged or archived A is ignored.
- Clear: after `clear_misread`, B merges on the next reconcile; the flag survives a re-match.
- Move: the document moves to B, A is archived (not deleted), B reconciles and merges, audit rows are written, the stored file is untouched.
- Move refusals: unknown target, same key, letters conflict, document from another set, busy lock (409, nothing written).
- Force Merge on A or B works and is audited.
- Settling: during the settle window the hold is not evaluated early (the set shows `settling`).
- Migration: a fresh DB and a legacy create_all DB both end at head with the column.
- UI cases in `tests/ui/test_ui_set_detail.py` plus a seeded scenario `misread_pair` in `scripts/ui_test_env.py`.
- `make business-logic` passes. PRODUCT section 8 gets the rule and the false positive. `REASON_TEXT` and the quarantine
  `WHAT_TO_DO` table get the new reason (tests that enumerate reasons must still pass).

## Rules
Allowed: `src/app/services/reconciliation.py`, `src/app/api/routes/po_sets.py`, `src/app/models/models.py`, `alembic/versions/`,
`templates/po_set_detail.html`, `scripts/ui_test_env.py`, new tests, the docs named above. Forbidden: matching, merge and grouping
logic, anything not listed. Do not weaken existing tests. Claude (Opus) reviews before commit; run GitNexus impact on
`_reconcile_po_set_inner` first.

## Open questions
None blocking. Default button labels: "Move to another PO", "Not a misread", "Force Merge".
