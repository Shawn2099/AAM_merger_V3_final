# SPEC WS2 (T13): possible PO misread hold and the operator's actions

> **SCOPE FREEZE (owner, 2026-10-25, D17): NOT TO BE BUILT for now.** The manual merger covers a wrongly read PO, so the "Move to another PO" action is dropped for good. Whether the hold itself is ever built is open question Q9 (recommended: skip until the first real week gives data). Keep this spec as the design on file only.

Status: **rev 2 (after Opus review 2026-10-25, which rejected rev 1). Not built. Q8 answered by the owner (D16): no guard exemption.**
Author: Claude (architect). Builders: **Claude writes the reconciliation hunk** (CLAUDE.md: core logic inline); the worker (DeepSeek)
writes the routes, template, seed scenario and tests. Reviewer: Opus before commit. Decision source: D1 (owner, 2026-10-24):
do not merge a suspected misread; hold it and let the operator choose. Evidence: HANDOFF section 6c "D1 design notes" (real run:
exactly one true case, set 9 vs set 8; a plain "one digit apart" rule would have flagged two legitimate pairs).

## Rule (precision over recall: hold when in doubt)
Set A is **suspect** when ALL are true:
1. A is open (not archived, not merged, not quarantined), has >= 1 non-archived document, and EVERY non-archived document is `PO`
   (no DN, SI, customs or shipping). An empty set is never suspect.
2. Another open set B (same openness test) has a `po_no_normalized` of the same length that differs from A's in exactly one
   character position, AND B holds at least one non-archived DN or SI.
3. A's `po_misread_cleared` is false.

Several neighbours: if more than one set is one digit from A, the hold text lists all of them.

## Where it runs (verified against `reconciliation.py` at 21b5f0d)
Earlier holds win (`placed_by_number_only`, `multiple_po_documents`, mismatch, customs). Two insertion points:
- **A-side:** in `_reconcile_po_set_inner` right after the unread/foreign-document guards (after ~line 539) and BEFORE the
  `if not dn_docs` early return (~553-561; A is PO-only, so it would otherwise exit as `missing_dn_document` first). A suspect A
  returns `pending` / `possible_po_misread`.
- **B-side:** right after the settle block (~line 962) and before the auto-merge import (~line 964). B returns `pending` /
  `possible_po_misread` while any uncleared suspect A is one digit away.
B in its own settle window shows `settling`; after it, `possible_po_misread`. B's hold never waits for A's arrival time.

**The engine never reconciles another set.** The neighbour lookup is a read-only ORM query (no raw SQL).
`_reconcile_po_set_inner` must not call `reconcile_po_set` for the neighbour (infinite recursion, and B would be reconciled
without B's lock). The scheduled sweep (`flows/sync.py` Phase 2) already visits every open set. Only the two operator routes
below reconcile the neighbour, under the neighbour's lock.

Hold text (both pages): "Possible misread: PO {A} looks like PO {B} (one digit differs). Check the page and choose: move it to
the right PO, say it is not a misread, or Force Merge." Add `possible_po_misread` to `REASON_TEXT` AND to `_DETAILED_REASONS`
(otherwise the detail naming both POs is dropped). The quarantine `WHAT_TO_DO` table is NOT touched (this is a pending state, and
`quarantine.py` is out of scope).

Known false positive (documented in PRODUCT section 8, pinned as `test_known_limitation_...`): consecutive PO numbers usually differ
in their last digit, so a legitimate PO that arrives days before its DN/SI holds its ready neighbour until those arrive or the
operator presses "Not a misread". It resolves itself but can be frequent. The settle time does NOT mitigate this (it is non-zero
only in poller runs). The owner should confirm this trade-off after the first real run.

## Operator actions (each audited as AuditLog `manual_status_change`; one audit row per set changed)
All of them: acquire every lock they need through `_acquire_lock`, in ascending set-id order; answer **409 before any write** if
any is busy; release all in `finally`; refuse with a message when the page's set is archived/merged/quarantined.

1. **Not a misread** `POST /po_sets/{id}/clear_misread`. The flag lives on A. On A's page it clears A. On B's page it clears every
   uncleared suspect A that is holding B. Then it reconciles every set it changed plus the page's set, each under its own lock.
2. **Move to another PO** `POST /po_sets/{id}/documents/{doc_id}/move`, form field `po_no`. After the move the target holds two PO documents (`multiple_po_documents`); the page for the target must say so and point to Force Merge, which the operator uses (audited). Q8 is answered: nothing is exempted.
   Normalise X; refuse an empty or same key, a letters conflict (`PoLettersConflict`), a document not in this set, and a quarantined
   target. The target must be an OPEN set found with `get_or_create_po_set(create=False)` (`_find_open_set` excludes merged and
   archived sets): the message is "no open PO Set for {X}", never "does not exist". A move never mints a set. Sets the document's
   `po_no_raw`, `po_no_normalized`, `po_no_source="manual"`, `po_set_id`. If A is left with no non-archived document, archive it
   with ONE route-local step: set `archived_at`/`archived_by` and write an audit row `{"archived_empty_after_move": true}`; no
   file export (`archive_quarantined` raises on any status except quarantined, `quarantine.py:838-839`, and must not be edited).
   Nothing is ever deleted. Reconcile both sets.
3. **Force Merge**: unchanged.

## Q8 (ANSWERED by the owner 2026-10-25, D16): option (a), accept; no exemption is built
The phantom page is a PO document with zero line items (HANDOFF 6c). Moving it into B gives B two PO documents, and
`single_po_document` defaults to true (`config.py:271`), so B holds as `multiple_po_documents` and never merges.
Options: (a) accept it; the operator Force Merges after a Move (honest but clumsy); (b) **recommended:** a PO document that was
placed by hand (`po_no_source == "manual"`) AND has zero line items is a continuation page and is not counted by the
single-PO-document guard. (b) is a small change in the reconcile hunk (Claude writes it) and needs its own test and a PRODUCT
section 8 line. The owner chose (a): do NOT build option (b). The Move test therefore asserts that the target set holds as `multiple_po_documents` after a move and merges only after a Force Merge (with the audit row). The worker builds all three actions.

## Schema and migration
`po_sets.po_misread_cleared BOOLEAN NOT NULL DEFAULT 0` (server_default `0`) in `models.py`.
Alembic revision with `down_revision = "e8f9a0b1c2d3"` (current head). **Also** add a `_MARKERS` entry
`("po_sets.po_misread_cleared", "<new revision>")` and a docstring line in `scripts/migrate_db.py` (the file says to keep it in step
when adding a column); otherwise a create_all-era DB that already has the column is stamped one revision short and the upgrade
fails with "duplicate column". Test both: fresh DB, and a create_all-era DB that already has the column.

## Tests the builder must write (test first)
New `tests/test_misread_hold.py`:
- True positive: A = PO-only `4500057195` with ZERO line items (matches the real case), B = `4500051195` with PO, DN, SI all
  matching, **A seeded before B**. B does NOT merge; both `pending` with `possible_po_misread`; the dashboard and both set pages
  show it (WS2 acceptance requires the dashboard assertion).
- Must still merge: two one-digit-apart POs that each have PO+DN+SI complete (real sets 2/3 and 41/43); A with a DN attached is not
  suspect; different lengths; merged, archived or quarantined A ignored; an empty set is not suspect; several neighbours list all.
- Clear: on A's page and on B's page; after it B merges on the next reconcile; the flag survives a re-match; one audit row per set.
- Locks: a busy A or B gives 409 with nothing written; both locks released after success and after failure.
- Force Merge on A or B works and is audited.
- Settling: B inside its own settle window shows `settling`, after it `possible_po_misread`.
- Migration: fresh DB and a legacy create_all DB (with the column already present) both reach head.
- Move tests (after Q8): moved to an open target; unknown/merged/quarantined target refused; same key; letters conflict; document from
  another set; A archived by the route-local step; stored file untouched; B merges under the owner's answer.
- Run the WHOLE suite and report every existing fixture that has a PO-only set one digit from a complete set. Do not edit those
  expectations; report them.
- UI: scenario `misread_pair` in `scripts/ui_test_env.py` (A seeded first) and cases in `tests/ui/test_ui_set_detail.py`.
- `make business-logic` passes. `REASON_TEXT` enumeration tests (`tests/test_synthetic_spec.py:557`) stay green.

## Allowed paths
Claude: `src/app/services/reconciliation.py`. Worker: `src/app/api/routes/po_sets.py`, `src/app/models/models.py`,
`alembic/versions/`, `scripts/migrate_db.py`, `templates/po_set_detail.html`, `scripts/ui_test_env.py`, new tests,
`tests/ui/test_ui_set_detail.py`. Forbidden: matching, merge, grouping, `quarantine.py`, anything not listed. Do not weaken existing
tests. Run GitNexus impact on `_reconcile_po_set_inner` first; Opus reviews before commit.
