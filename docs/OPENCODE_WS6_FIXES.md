# WS6 fix list (Opus review: approve with fixes). Edit only tests/_ws6_helpers.py, tests/test_ws6_*.py, scripts/soak_d21_timing.py. No src/, no git.
1. tests/test_ws6_concurrency.py 6.2 (~:195,:199,:219): set poll_enabled=True ONLY around the manual SyncPoller ticks (as 6.1 ~:141) and False before each TestClient is entered, so the lifespan never starts a real background SyncPoller.
2. 6.2 (~:214): reuse the SAME poller from ~:208 (it has the _queued flag, poller.py:179) to prove "queued, then runs on a later tick"; a fresh poller proves nothing.
3. 7.3 (restart_recovery ~:246-253): keep the reason missing_dn_document (a DN that crashed mid-read has no PO number so it never attaches) but add a docstring stating this deviates from the plan and why, and add a strict-xfail companion test named test_ws6_03_stuck_document_reason_names_the_unread_document asserting document_not_yet_read, reason="SUSPECTED PRODUCT BUG WS6-03: stuck DN reported as 'No delivery note in this set yet'".
4. 6.1 steps 4-5 and 6.2: assert the unrelated DN document is `valid` after the tick, the set's reconcile timestamp is unchanged, and read the Scan result and any sync errors (a "database is locked" must fail the test, never be swallowed).
5. _call (~:72): catch Exception too and record ("exc", repr(e)); assert no "exc" results.
6. Gate wait 30 s -> 120 s in tests/_ws6_helpers.py (~:129) and the 60 s wait in test_ws6_concurrency.py -> 120.
7. 6.3: report in the test (assert or record) whether any round produced a 409; if none can, say so in the docstring.
8. scripts/soak_d21_timing.py: os.chdir(root); resolve config.example.yaml against the repo root (REPO), not the cwd.
9. 8.2: add the SHA/stored-bytes check shared with 8.1. 9.1: assert counts["resolve"] and counts["attach"] or drop them. Fix audit_count typing.
Acceptance: tests/test_ws6_*.py green 3 runs in a row, ruff check + format clean. Finish with the plan's section 11 report and list every deviation.
