# Batch 1 and review fixes (2026-10-25): what changed and why

Branch `fix/mvp-high-priority`. Source decisions: `deployment/DECISIONS_FINAL_2026-10-25.md`.
Plan: `deployment/BUILD_PLAN_2026-10-25.md`, then `deployment/FIX_PLAN_2026-10-25.md`.
Product rules are in `AAM_merger_V3_PRODUCT.md` (sections 3.1.1, 8 entries L, N, O, and
"Known exceptions"). Operator wording is in `docs/TESTER_GUIDE.md`.

## 1. Operator-facing changes

| Area | Change | Decision | Commit |
|---|---|---|---|
| Times | Every screen shows IST with the zone. The database stays UTC. | D7 | bef32ba |
| Wording | Archive dialogs say "the archive folder"; audit subtitle lists the real actions; sync-paused notice explains the pause | I-45, I-47, I-26 | fc7c5d5 |
| Unknown page | A browser gets the styled "Page not found"; API clients keep JSON | I-44 | f62c688 |
| Re-Match / Discard | Re-Match says what matching decided; Discard asks first when there are edits | I-43, I-49 | f63ab33 |
| Layout | Labels on holding-page controls; darker nav badge and tab counts; narrow windows stack | I-50, I-51 | b05a461 |
| Reclassify | Every manual type or PO change writes an audit row | I-38 | 16e4f60 |
| Merged sets | "Packet contains customs pages: Yes/No"; a note when customs is off but the packet still has them | D18 | a6d5aba |
| Worklists | "Needs a decision" for several PO files or a DN-number placement; "Waiting (part delivered)" for short deliveries; "Matched by description - needs a second check"; "Force-merged - needs a second check" | D15, D16, Q6 | c7f7475 |
| Holding page | "Move to quarantine" on each row copies the file to the quarantine folder and takes the row off the list | D9, D10 | 776cacf |
| Rejected files | No type, PO or Save & Route controls; the reason is shown | I-40 | 776cacf |
| Quarantine page | "Files set aside": failed reads and unsplit bundles, with why and when | D9 | bc7c03a |
| Combined bundles | An unsplit multi-document PDF leaves the holding list and cannot be reclassified (409) | D17 | f0db4f6 |
| Customs files | Per-file Delete before merge only; the packet is never changed by the Delete button after merge | D1 | 7dad937 |
| Customs toggle | Turning it OFF on an unmerged set is refused while CUSTOMS or SHIPPING files are attached | decision 2026-10-25 | 7dad937 |
| How to merge | The set page says: press Merge Now; Force Merge needs a reason | D-B2a | 7dad937 |
| Remove a set | "Move to quarantine and remove set" at the bottom of an open set | decision 2026-10-25 | 552fef7 |
| Audit page | Removal rows read "Set Removed" and name the folder | step 13 | 552fef7 |

## 2. Removal of a set (the two-step delete, approved)

1. Copy and verify every stored PDF, the input-folder copies, the JSON export and the
   manifest into `quarantine/_removed_sets/<PO>_<stamp>_<id>/`. Write
   `REMOVAL_IN_PROGRESS.txt` first.
2. Only after step 1 succeeds, delete the set's rows in one transaction, with an audit row
   that keeps the old set id and PO number. Then write `REMOVED.txt`.

Failure at any step: nothing is deleted, moved input files are put back, the folder is kept
with `REMOVAL_FAILED.txt`, and the operator gets a 500 that names the folder.
Refused: merged sets (D1), archived sets, and any time a sync is reading documents.
Stored PDFs in `data/stored` are never deleted. Missing stored PDFs are listed and do not
stop the removal (decision 10). Unreadable input-folder files stop it.

## 3. Review fixes (second and third Opus audits)

- Set-aside takes the sync lock inside the service (not only the route). An input copy is
  removed only after a quarantine PDF exists (7457254).
- The quarantine marker is bound to the folder actually chosen (62aa37f).
- Rollback keeps the folder whenever an input file cannot be put back; restores never
  overwrite a file the operator put back; restores write a temporary name first (076db2b,
  a92eb49, 4c27e00).
- Shared-hash rule: an input copy stays in place when another active document holds the
  same bytes (e46f03f).
- The "under delivery" check runs once per dashboard refresh, not once per row (fe928aa).
- Type-check clean again (a92eb49).
- A removed CUSTOMS file keeps the archived-set message; only documents moved from the
  holding page get the quarantine message (4806612).

## 4. Known limits (also in PRODUCT section 8)

- **L** A PO document cannot be removed from a set. Force Merge is the way out.
- **N** Removing a set deletes its rows. The old audit rows keep their history, but the link
  to the set is cleared (`ON DELETE SET NULL`); the link survives in `audit_log.json`.
- **O** A removed set's id can be reused (no AUTOINCREMENT). The old folder is marked
  `removed:<id>` so the new set does not adopt it.
- The "Waiting (part delivered)" label reads quantity flags only.
- The COMBINED type is named only in `models.py` and `extraction.py`; Layer 2 uses the helper
  `unsplit_bundle_clause()` / `is_unsplit_bundle()`.
- A removal or a holding-page move holds the sync sidecar for a few seconds; a scheduled sync
  that starts then is skipped until its next run.

## 5. Not done (deliberately)

- Simplifications suggested by the audit: write straight into the final folder; copy the input
  files instead of moving them. They change the design, so they wait for a change that needs
  them.
- Per-file Delete on merged sets, and a customs packet rebuild after merge (D1, D2): left as
  decided.
- Narrowing the sync lock (I-26): needs its own design.
- Switching the id sequence to AUTOINCREMENT: needs a migration.
- Verification on a real Windows host (file locks, antivirus, long paths) and a real
  concurrent removal against a live sync: not run here.

## 6. How this was verified

- Targeted tests per change; the browser tests for the set page, the holding page and the
  dashboard; `ty check src` clean; full suite run after the batches (exit 0 on the last run).
- Opus audits: two reviews of the removal work (findings fixed or listed above) and one
  review of the plan; the audit of the two-step strategy approved it.
