# Core Fix Plan — make what exists correct (no new features)

**Date:** 2026-10-23. Source: five-way code review (reconciliation, ingestion/extraction,
concurrency, web, tests/ops) plus a spot-check run of the normalisers.

**Rule for this plan:** fix existing behaviour so it obeys the owner's core; add nothing new.
Where the code is unsure it must hold the item and ask a person, never guess. Anything that
would be a feature is recorded as a limitation (PRODUCT §8, `test_limitation_*`) instead.

## The core, as the owner states it

1. A CA firm drops many PDFs in a folder. Each unique file goes through a VLM that extracts
   data or discards it. The DB is the single source of truth.
2. Three types: PO, SI, DN, each carrying a PO number. One set = one PO doc + n SI + n DN
   with the same PO number.
3. Merge iff, for every item, PO qty = ΣDN qty = ΣSI qty exactly. Line item no is the key,
   description is the fallback. No UOM, no price.
4. Layer 1 = ingestion and extraction (all PDF edge cases: combined, unknown, corrupt).
   Layer 2 = matching; it assumes the DB is correct.
5. Any ambiguity: never guess, ask the user.

## Core check: does the code honour each clause?

| Clause | Verdict | Evidence |
|---|---|---|
| Unique files only (SHA-256) | Holds for identical bytes | review: dedup sound; re-scan with different bytes is a limitation |
| VLM extracts or discards | Mostly. **Breaks on provider outage**: good docs are marked failed/quarantined | `extraction.py:786`, retry total ~22 s vs cap 3 |
| DB is the truth | Holds | engine reads only the DB |
| Set = same PO number | **Breaks**: normaliser merges different POs and truncates numbers | verified: `12-345`=`123-45`; `12,345`→`12` |
| 1 PO + n DN + n SI | Holds | second PO doc holds the set pending |
| Exact per-item quantity equality | Holds (integers x1000) | engine reviewed sound |
| Line no primary | **Weak spot**: `1.05`→`1.5`, `1.01`→`1.1` collide | verified |
| Description only as fallback | Holds, but the 85 % match can pick a near-identical wrong item | limitation, see below |
| Layer 2 trusts the DB, Layer 1 guards the door | Gap: a PO can take its key from `po_reference` | `extraction.py:285` |
| Ambiguity → ask the user | Mostly (ambiguous/missing PO is held). The normaliser is the exception: it guesses | see A, B |

## Work items (in order; one commit each, named files only)

Gate for every item: GitNexus `impact` before the edit, then `pytest -q`, `ruff check`,
`ruff format --check`, `ty check src`, `make business-logic`, `detect_changes`, then commit.

**1. PO-number normalisation obeys "don't guess" (`grouping.normalize_po_no`)**
- Keep the separators that distinguish numbers; do not flatten `12-345` and `123-45`.
- Do not strip a "revision comma" unless it is the documented `, Rev # N` form; `12,345`
  must not become `12`.
- A PO number the normaliser cannot parse safely is held for a person, not truncated.
- Do not strip `PO` from the start of a real token (`POST123`).
- Tests: the collision cases above plus the existing `test_po_number_normalisation.py` set.

**2. Line-number normalisation (`matching.normalize_line_no`)**
- Only whole-number leading zeros are stripped (`01`=`1`). `1.05`, `1.01` and `1.1` stay distinct.
- Tests: sub-line collisions; existing line-key tests stay green.

**3. Extraction prompt and key choice (`extraction.py`)**
- A PO takes its set number from its own document number first, `po_reference` second.
- A DN's quantity is the delivered/shipped column, never ordered or balance. If the column is
  not identifiable the document is flagged for a person (existing ambiguity path).
- Freight, discount and handling rows are excluded like tax rows.
- Wording and one key-selection line only. Success is judged by the live run (item 5).

**4. A provider outage must not condemn a document**
- 429, 5xx, timeout, network drop and missing key leave the document retryable
  (`pending`), do not consume content attempts, and do not quarantine its PO set.
- Only a genuine content failure (unreadable, schema-invalid after the cap) quarantines.
- Test: stub the client to raise 429 three times; the document stays retryable and the set
  is not quarantined.

**5. Live run on real PDFs (the only test of the VLM)**
- 20-30 real sets, including partial deliveries, multi-DN/SI, multi-page, scanned,
  no-line-number rows, combined PDFs.
- A person compares every extracted line against the printed PDF. Record: merged-but-wrong
  (must be 0), false quarantines, extraction errors. Fix extraction defects only.
- Replay the stored extractions of the existing DB copy through group → compare before and
  after items 1-4 and record verdict counts. They should not change except where a set was
  previously mis-keyed.

**6. Delivery config and handover (no code)**
- Set `server.allowed_hosts` to the server name and IP; set `prefect.schedule_timezone`.
- Client gets their own API key. Ship NSSM in `tools/`. Installer dry run on the target PC.
- Tester guide states plainly: the packet is not machine-proven against the printed
  documents; a person reviews it before it goes onward. Add "confirm yesterday's sync ran"
  to the daily checklist.

## Recorded as limitations, not built (PRODUCT §8 + `test_limitation_*`)

- Re-scanned copy of the same DN (different bytes) is summed twice.
- Description fallback may pair two near-identical descriptions (size or number differs).
- UOM invisible; step-10 numbering quarantines; European decimals.
- Operator actions not audited: customs toggle, reclassify, customs upload, re-extract/match.
- Reclassify is not confined to the holding area.
- Sync-lock staleness windows (1 h either way); orphan packet after a crash mid-merge.
- No pagination, no log rotation, backups on the same disk, no clickjacking header.
- Flaky `test_po_lock_timeout_releases`: passes 10/10 alone; suspected cross-test global
  logging/handler state. Watch on the client machine.

## Definition of ready to ship

- Items 1-4 merged with tests; full suite, ruff, ty, business-logic green.
- Live run: zero merged-but-wrong; every quarantine shows a readable reason.
- Installer verified on the target machine; limitations written in the tester guide.
