# Claude's Plan — AAM Merger V3, ship the MVP

**Status:** rescoped 2026-10-23. The MVP ships in **2 days**. The current version works,
so this is **corrections only, no rewrite and no restructuring before ship.**
Decisions: Prefect stays; description fallback, DN per-line PO-reference anchoring, Force
Merge and customs stay as they are.

## 1. The core, in the owner's words

A CA firm drops many PDFs into a folder. Each unique file goes through a VLM that
extracts data or rejects it. The DB is the single source of truth. Three document
types (PO, SI, DN), each carrying a PO number. One set is one PO document plus n SI
plus n DN with the same PO number. Merge if and only if every line's quantity
matches exactly across PO, SI and DN. The line number is the key; description is
the fallback; no UOM, no price. Layer 1 handles PDFs and their edge cases. Layer 2
trusts the DB and matches. Ambiguity is never guessed: a person decides.

Standing rules: a filename never feeds matching; the packet is named by invoice
number, else PO number; a combined PDF is quarantined whole (`split_combined: false`).
Full contract: `docs/CORE_LOGIC.md`.

## 2. Before ship (in this order)

| # | Correction | Why it matters | Done when |
|---|---|---|---|
| 1 | Commit the pilot tree and the combined-quarantine change | HEAD must be what is tested | done (`a227f8a`, `178b466`) |
| 2 | `style:` commit, `ruff format --check .` clean | `make lint` failed at HEAD on 9 files | done, 837 tests pass |
| 3 | **Live end-to-end run** with the real OpenRouter key and real vendor PO/DN/SI PDFs on a clean install: sync, merged packet, dashboard | The live VLM path has never run in this tree; it is the biggest unknown | an observed merged packet; any extraction defect fixed |
| 4 | Combined-file guard: a `COMBINED` row must never be attachable to a set by reclassify; the reason file text must not tell the operator to "enter a PO number" for it | Small, but wrong guidance on a shipped screen | tests + wording fixed |
| 5 | Installer dry run on the target Windows machine (services, Prefect pool and schedule, backup task, LAN URL) following `docs/DELIVERY.md` | Admin-only steps were never exercised | both services up, `aam-midnight-sync` registered |
| 6 | Replay check (light): run the stored extractions of the existing DB copy through group -> compare with no VLM, and record the verdict counts | A cheap regression guard for corrections 3-5 | counts recorded, unchanged by the fixes |
| 7 | Handover: refresh `docs/DELIVERY.md` / `TESTER_GUIDE.md` for the combined rule | Testers must know a combined PDF lands in quarantine | docs updated |

Gate for each correction: `impact` before editing, then full suite, `ruff check`,
`ruff format --check`, `ty check src`, `detect_changes` before the commit, and commit by
naming files.

## 3. After the MVP (not started, not needed to ship)

Kept here so the thinking is not lost; none of it happens before ship.

- **Layer separation.** Target shape: Intake (file -> dedup -> VLM -> validate -> trusted DB
  row) / Decide (pure function over one PO's snapshot -> verdict) / Act (persist status,
  write packet, quarantine, audit). The code already behaves this way; the one fused spot is
  `_reconcile_po_set_inner` (about 400 lines), which decides and acts together.
- **Folder layout** `layer1_intake / layer2_decide / layer3_act` plus a boundary test, done
  as a mechanical move after the decide/act split so each function lands once.
- Split `quarantine.py` (quarantine / archive / manual merge); fold `retry.py` and the
  Prefect-level retry into one mechanism; remove the dead `max_concurrent_extraction_tasks`.
- Make `CORE_LOGIC.md` the only spec and move the older ones to `docs/history/`.
- Revisit Prefect vs Task Scheduler once the client has run the MVP.

## 4. Risks to ship

- Live VLM extraction quality is unproven here (`EXTRACTION_PROMPT_AUDIT.md` lists known
  fail-safe quarantines: false "ambiguous" notes, non-item PO pages, step-10 numbering).
- A real NSSM service install needs admin and has not been done.
- No authentication (accepted LAN risk); the audit log is accountability, not access control.
