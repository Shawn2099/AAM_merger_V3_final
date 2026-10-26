# WS1 fix list (Opus review verdict: REJECT until done). Templates diff T1-T21 is APPROVED: do not touch it.
Same hard rules as docs/OPENCODE_WS1_PLAN.md. Where the plan and the current code disagree, the PLAN wins. Do not weaken assertions.

1. scripts/ui_test_env.py: add the scenarios the plan section 4 names but were never seeded: settling_merge_now_target, confirm_placement_target,
   rematch_target, merge_now_mismatch_target, merge_now_reason_target (plus plan-named read-only settling and placed_by_number_only scenarios;
   placed_by_number_only_target must not be shared with the confirm test). Implement section 4.5 manifest fields (settle_minutes, per-document
   po_no_source). Settle window per plan (240) for the settling scenarios so the state is stable on a slow machine.
2. Missing cases: D-13, D-14, S-14b (xfail strict, SUSPECTED PRODUCT BUG per plan), S-23 as the plan defines (Merge Now on a settling set),
   S-24 (RO case + API negative: confirm_placement on the PO document returns 409 "needs no confirmation"), G-01b (xfail strict, no favicon).
   Rename the worker's current "S-23" (test_ui_set_detail.py ~:493) to S-25.
3. S-13 must use rematch_target, S-14 must use merge_now_mismatch_target and assert the full toast "Not merged - still mismatched".
4. S-09: integers x1000 (no float tolerance), expected totals from the DB not from the page's own mapping, parse orphan flag quantities, iterate every po_sets row.
5. S-12: add the .pdf-named non-PDF-bytes case ("not a PDF file"), plus doc-row count and DB customs_doc_count == 2.
6. G-03: use clientWidth, parametrize (6 params per plan), merged_basic. G-01: networkidle, no blanket ERR_ABORTED filter, four /static/* 200 checks.
   G-05: merged_basic plus the control same-origin POST expecting 200. G-02: open a preview drawer, count ws/wss URLs. G-06: add /po_sets/999999/view and /dashboard?status=nonsense.
7. Restore dropped assertions: D-06 (filter mismatched + All statuses reset), D-11 DB reason[:40], S-11 reason[:40] + "Customs Clearance: Missing",
   S-20 DB status/archived_at, S-22 btn-merge-now enabled + SETTLING_PREFIX, S-25 packet starts with %PDF + audit count == 1,
   Q-02 use quarantined_po_no_lines and set-po == 7000010, Q-03 quarantine_delete audit count == 1, X-02 in set_detail with reason-card checks per plan.
8. U-01: revert your edit to the original (or keep it ONLY if you list it under HARNESS H-03 in the report with the justification).
Acceptance: whole tests/ui green 3 runs in a row and in reverse file order; ruff check + format clean; tests/test_business_scenarios_ui.py and
tests/test_ui_test_env_key.py green. Report in the plan's format and list every deviation. Do not stop before printing the report.
