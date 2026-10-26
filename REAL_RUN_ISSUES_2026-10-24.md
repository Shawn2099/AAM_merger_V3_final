# Real end-to-end run, 2026-10-24: issues and fixes

Run: fresh DB (`scripts/migrate_db.py`), 200 corpus files, `vlm.raster_dpi: 150`, `reasoning_effort: high`,
request timeout 180 s. Sync finished with 0 errors. Details: `REAL_RUN_MERGED_VERIFY_2026-10-24.md`,
`REAL_RUN_DIAGNOSIS_2026-10-24.md`.

Result: 12 merged, 3 mismatched, 18 quarantined, the rest pending. 11 of 12 merges verified correct against
the PDF pages; 1 packet incomplete (I-1). No wrong merge.

| # | Issue | Severity | Status |
|---|---|---|---|
| I-1 | A one-digit PO misread on a PO continuation page (`4500051195` read as `4500057195`) created a phantom set and left the merged packet of set 8 without that PO page. Consistent misread; only the deferred second read would catch it. | Medium | Open, by decision (no new API calls/features). Retry the check after the second-read decision. |
| I-2 | My new `duplicate_document_number` guard quarantined pages 1-2 of ONE invoice (same number, different lines). | Medium (recall) | Fixed: held only when the documents sharing a number also share a line number (a numberless line counts as shared). Tests: `tests/test_duplicate_document_number.py`. Sets 7 and 15 keep the old message until re-run. |
| I-3 | An empty invoice totals page with the same number as a page that has lines tripped `vendor_document_has_no_line_items`. | Low | Fixed in the same change. |
| I-4 | "PO has no line items" said "contact the VLM operator"; the real cause is that the input holds only page 1 of N. | Low | Fixed (wording). |
| I-5 | `PRODUCT` §3.1 lacked `duplicate_document_number`; `test_fr_10_1_pinned` used one invoice number twice. | Low | Fixed. |
| I-6 | Page-split inputs: a PO split over several files is held by `single_po_document`; a PO printed "1 of 6" with only page 1 supplied. | Limitation | Kept as hold (precision). Business question: are the remaining PO pages expected in the packet? |
| I-7 | Vendor numbering (10/20/30 vs 1/2/3, `1-1` release numbers, renumbered lines) quarantines 13 sets as unmatched lines. | Limitation | Correct holds; no misreads found. |
| I-8 | Four overflow-text rows on PO documents were stored as lines with no number and quantity 0. None in a merged set. | Low | Open; observe. |
| I-9 | Leaked Prefect temporary servers (dozens of orphaned `uvicorn --factory` processes) after the test suite. | Low | Open (test hygiene). |
| I-10 | Earlier robustness audit (R-01..R-18): R-01 and R-14 fixed; R-02, R-04, R-05, R-10 still open. | Mixed | See `robustness_audit_2026-10-24.md`. |
