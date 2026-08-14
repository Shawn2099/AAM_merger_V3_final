# AAM_merger_V3 — Business Idea & Logic Document

**Status:** Final, pre-spec. This document is intended to be detailed enough that SPEC.md can be written directly from it without further clarification.

**Build scope for this phase:** Input folder → identification → extraction → grouping → matching → reconciliation → automatic merge → output folder (file named by Invoice/SI number).

**Explicitly out of scope for this phase (Phase 2, parked):** Post-merge distribution (company lookup, email drafting) and the email plugin. These are acknowledged as future work and are not designed against in this document or in the resulting spec.

---

## 1. Purpose

This is an AI-assisted document reconciliation system for a CA firm. It automates the repetitive, error-prone manual work of matching Purchase Orders (PO), Delivery Notes (DN), and Sales Invoices (SI), and produces a single merged PDF per PO once quantities reconcile exactly. The CA remains the final reviewer of every merged output — the system never represents itself as the final authority on correctness, only as a preparer of reconciled document packets.

**Deployment context:** Windows Server 2016, LAN-accessed by multiple employees, no authentication/roles in v1 (explicit accepted risk — see FAQ), modest file volume (not high-throughput).

---

## 2. Roles

- **Employee:** copies vendor PDFs (received via email, outside this system) into the shared input folder. Can trigger Sync, view the dashboard, use manual actions (redo, quarantine, force merge).
- **CA (reviewer):** reviews merged output PDFs before they're carried forward for accounting purposes. The system does not gate this review step — it's a human process outside the tool.
- **System:** has no concept of user identity in v1. Any action taken by anyone on the LAN is attributed to "the system" in the audit log, not to a named person.

---

## 3. Glossary

| Term | Meaning |
|---|---|
| PO | Purchase Order — source-of-truth commercial intent |
| DN | Delivery Note — evidence of physical delivery |
| SI | Sales Invoice — evidence of billing |
| PO Set | All documents sharing the same normalized PO number |
| po_no | The normalized Purchase Order number used as the grouping key |
| line_item_no | The line-item identifier used as the primary matching key within a PO Set |
| Aggregate DN Qty | Sum of a given line item's quantity across all DNs in a PO Set |
| Aggregate SI Qty | Sum of a given line item's quantity across all SIs in a PO Set |
| Combined document | A single PDF containing PO + DN + SI content together |
| Manual-upload document | A document type never sent through the VLM and never used in reconciliation — `CUSTOMS`, `SHIPPING`, `COMMERCIAL_INVOICE`. Attached to the merged packet as-is when relevant. |
| Reconciliation | The act of checking PO qty against DN and SI aggregates |
| Merge | Concatenating a PO Set's documents into one output PDF |
| Quarantine | Whole-PO-Set human-review state for cases the system cannot confidently resolve |

---

## 4. Input & Ingestion

### 4.1 How files arrive
Employees manually copy vendor PDFs into a shared input folder. There is no automated email pull in this phase.

### 4.2 How files are picked up
- **Not continuous/event-driven.** A folder-watcher was considered and rejected — filesystem events are unreliable over Windows/SMB network shares.
- **Decision:** a user-triggered **Sync** button, plus a **scheduled midnight run**. No continuous background watcher.
- **Overlap handling:** only one ingestion run may execute at a time. If Sync is triggered while a run (manual or scheduled) is already in progress, the new trigger is ignored and the UI shows "Sync already running" rather than queuing a second concurrent pass.
- **Missed midnight run** (e.g. server was down): no catch-up run is scheduled. The next manual Sync or the following midnight run picks up whatever is still in the input folder — this is safe because the input folder is never cleared until a file has been fully processed (see 4.4).

### 4.3 Mid-copy safety
A lightweight stability check (compare file size across a short poll interval, e.g. 2 seconds, before treating a file as ready) is included as a cheap safeguard against reading a file mid-copy. This is in scope for this build.

### 4.4 Deduplication
Every file is hashed (SHA-256) on ingestion. Same hash = same logical document regardless of filename → deduplicated. Only one document record is created per unique hash, no matter how many times or under how many names it appears in the input folder.

### 4.5 Storage / retention
- The system makes its own internal copy of every file (`stored_path`), independent of the original in the input folder.
- The input folder copy is only cleared once **both**: (a) a merged output has been produced for that PO, and (b) all extracted data for the associated documents is persisted in the database.
- Internal stored copies are kept indefinitely — never auto-deleted. If a merge ever needs redoing (bug, correction, dispute), the original source PDFs must still exist. The database's extracted fields are the AI's *interpretation* of a document, not the document itself — the actual PDF is the real audit trail.

### 4.6 Corrupted/unreadable files
A file that cannot be opened or parsed at all is treated the same as an extraction failure — it goes through the same retry-then-`failed` path (see 6.4). No separate failure state is introduced for this case.

---

## 5. Document Identification

Every ingested PDF is classified by the AI/VLM into one of:

- `PO`
- `DN`
- `SI`
- `COMBINED` (a single PDF containing PO + DN + SI content together)
- `CUSTOMS` / `SHIPPING` / `COMMERCIAL_INVOICE` — manual-upload support documents, never sent through the VLM, never used in reconciliation. `COMMERCIAL_INVOICE` sits alongside `CUSTOMS`/`SHIPPING` in this same family (confirmed against a real sample — a Commercial Invoice is a distinct document from the SI/Tax Invoice, carries HS Code/origin/packing data matching the Customs Declaration's values, and is not a reconciliation input). All three in this family are manually attached to a PO Set at the human's discretion, not extracted or matched against.
- `UNKNOWN`

Classification happens before any reconciliation logic runs.

**`UNKNOWN` handling:** a document that cannot be classified has no reliable `po_no` and therefore cannot be grouped into a PO Set. It is placed in a separate unclassified holding area (not silently dropped, not auto-quarantined against a PO Set it may not belong to) pending manual classification.

---

## 6. Extraction

### 6.1 Method
Each unique PDF (post-dedup) is sent to a VLM with native PDF input (no rasterization step) using a structured output schema, enforced programmatically (structured-output library + schema validation) rather than trusting free-form model output.

### 6.2 Extracted fields
- **Document-level:** doc type, `po_no`, `dn_no`, `si_no`, invoice number
- **Line-item level:** `line_item_no`, description, quantity, unit price

`part_no` was considered as a matching field and dropped — it is inconsistently populated across real vendor documents and cannot be relied on.

### 6.3 Known extraction gotchas (must be handled in prompt design)
- **PO decoy numbers:** a PO document can carry a secondary "PO Code" alongside the real PO number. Extraction must validate against the number that also appears on the corresponding SI/DN's own PO field, not just grab the first PO-looking string on the page.
- **Split line items:** a single line item's description can be visually split across a header line and a detail block. This must not be extracted as two separate items.
- **Handwritten/manually-corrected fields (rare):** a scanned DN's quantity or other field can occasionally carry a handwritten overwrite or correction mark. No special detection or flagging is built for this — it's treated like any other extraction, and the existing exact-match reconciliation gate is the safety net: if a misread value doesn't reconcile, the PO Set naturally surfaces as `mismatched` for human review anyway. Building dedicated handwriting/annotation detection isn't justified for a rare case when the downstream gate already catches the failure mode.
- **Physical stamps, seals, and hole-punch marks near the line-item table:** several real DN samples carry customer approval stamps (e.g. a "Senior Rig Manager" stamp) placed near or over the table area. This is expected and not treated as a problem as long as the underlying data remains visually legible — native full-page PDF input reads around it. No mitigation needed unless a future real sample shows the stamp actually obscuring data.
- **Unit of measure (UOM) is intentionally not extracted or stored.** Units are inconsistent across documents for the same line (e.g. a PO says "BX", the DN/SI for the same line say "box" or "EACH") but reconciliation only ever compares the numeric quantity, never the unit label — so capturing UOM would add extraction surface area for no matching benefit.

### 6.4 Failure handling
- Retry up to 3× with backoff. This applies both to hard failures (API/network errors) and to soft failures (the model's output fails schema validation).
- After 3 failures, that specific document is marked `failed`. This does not block matching evaluation for the rest of that PO Set — a stuck document degrades only itself, not the whole set.
- No confidence scoring is used. Extraction is treated as binary: schema-valid or not. Ambiguity about *correctness* (as opposed to structural validity) is resolved downstream by matching logic and quarantine, never by an extraction confidence threshold.

### 6.5 Combined documents
A `COMBINED` PDF is sent to the VLM once. The model is prompted to return a single structured response containing all logical sub-documents it can identify within that PDF (a PO section, a DN section, an SI section), each with its own header and line-item data — not three separate extraction calls.

If the model cannot confidently identify all expected sections within a combined document, that is treated as an extraction failure for that document (retry, then `failed`) rather than a partial/automatic pass.

### 6.6 Redo vs. Re-match (two distinct user actions)
- **Redo/Re-extract:** re-sends the document to the VLM. Costs an API call, slower.
- **Redo matching:** re-runs matching logic against already-stored extracted data. Fast, free, no API call.
- These are kept separate so a user doesn't reach for the expensive option out of habit when they just want to re-check matching (e.g. after manually correcting a database value).

---

## 7. PO-wise Grouping

The PO number is the common identifier across all related documents. The PO document contains its own PO number; each DN and SI contains a "P.O." field referencing the same number.

**Rule:** all documents whose PO number is the same (after normalization — non-alphanumeric characters stripped, uppercased, so `PO-1234`, `po 1234`, `PO/1234` all resolve to the same key) belong to the same **PO Set**.

A PO Set normally contains one PO, one or more DNs, and one or more SIs. Multiple DNs and multiple SIs are expected, not exceptions.

A new `po_no` seen for the first time starts a new PO Set. This is the entire grouping model — nothing more elaborate than "same PO number, same set."

---

## 8. Line-Item Matching

Within a PO Set, each PO line item must be matched to its corresponding DN and SI lines.

1. **`line_item_no` — the only trusted primary key.** If present and matching, that's a confident basis for comparison.
2. **Description (fuzzy match) — fallback only**, used only when `line_item_no` is absent. It is never used to override or double-check a `line_item_no` match. Default fuzzy-match threshold: **token-sort similarity ≥ 85%** (e.g. via `rapidfuzz`) — configurable constant, not hardcoded logic, so it can be tuned later without a redesign.
3. `slno` and positional order were considered and **not adopted** as trusted matching signals — cases where neither `line_item_no` nor description cleanly resolves a line go to quarantine rather than being guessed at.
4. `part_no` was dropped entirely (see 6.2).

**Duplicate/conflicting line references:** if two documents of the same type (e.g. two DNs) both reference the same `line_item_no`, their quantities are summed as normal per the aggregation rule (Section 9) — duplication itself isn't a problem. It only becomes a quarantine case if their **descriptions conflict** in a way that suggests they aren't actually the same line item (i.e. the system can't confidently say they refer to the same PO line).

**Unmatched extra lines:** if a DN or SI contains a line item that cannot be matched to any PO line (no `line_item_no` match and fuzzy description match also fails), the entire PO Set is not a full match and goes to quarantine — not silently ignored, not silently accepted.

---

## 9. Quantity Aggregation

For every PO line item, two independent totals are calculated:

```
Aggregate DN Quantity = DN1 qty + DN2 qty + DN3 qty + ...
Aggregate SI Quantity = SI1 qty + SI2 qty + SI3 qty + ...
```

These are calculated **separately** — DN and SI totals are never added together.

---

## 10. Core Reconciliation Rule

For every PO line item:

```
PO Quantity == Aggregate DN Quantity   (independent check)
        AND
PO Quantity == Aggregate SI Quantity   (independent check)
```

Both checks must independently hold. This is **not** `PO Quantity == Aggregate DN Quantity + Aggregate SI Quantity`.

**Example:**
```
PO qty = 100
DN1 = 40, DN2 = 60 → Aggregate DN = 100 ✓ (matches PO independently)
SI1 = 70, SI2 = 30 → Aggregate SI = 100 ✓ (matches PO independently)
→ RECONCILED
```

**No tolerance/rounding band.** This is an exact integer-scale comparison (quantities and money represented as integers, decimals scaled ×1000, to avoid float drift). Quantity is the dominant business concern (over-delivery/over-invoicing); a VAT-rounding-driven price mismatch is a theoretically possible but accepted, low-priority risk on the price check, not something engineered around preemptively.

**Reconciliation is line-by-line, not PO-total-based.** Every relevant PO line must independently pass. One failing line fails the whole PO Set — there is no partial-pass state.

**Negative/zero/credit-note quantities:** out of scope for this phase. Any such value encountered routes that PO Set to quarantine rather than being processed as a normal reconciliation case.

---

## 11. Price Check

Quantity is the primary reconciliation condition. Price is a secondary check, also exact (no tolerance band), but subordinate to quantity in determining status — a price-only mismatch does not by itself block what quantity has already determined, but is still flagged for the reviewer.

**Comparison priority when multiple flags exist on one item** (most fundamental first, this is also the order the UI surfaces flags in):
1. Line-item identification (can the system even confidently say which item this is)
2. Quantity
3. Price

---

## 12. Customs Flow (pre-merge gate)

Some PO Sets require customs support documents before they're eligible for merge:

- A PO Set can be manually toggled "Has Customs" at any time, regardless of its current reconciliation status.
- Once toggled, the PO Set is forced into a `blocked_customs` state and waits for exactly 2 manually-uploaded documents — `CUSTOMS` and `SHIPPING` — before merge becomes available. These two documents are never sent through the VLM; they are attached as-is.
- A `COMBINED` document that would otherwise be self-reconciled still waits on the 2 customs uploads if the toggle is on.
- **`COMMERCIAL_INVOICE`** is a third manual-upload document type in the same family as `CUSTOMS`/`SHIPPING` — also never sent through the VLM, never a reconciliation input. Unlike `CUSTOMS`/`SHIPPING`, it is not a required gate for merge (the 2-document wait is still only `CUSTOMS` + `SHIPPING`); it can be manually attached to a PO Set as an optional supporting document when relevant.

This is the only status that structurally "waits" for more documents after a PO Set's core reconciliation set has otherwise arrived. Everything else either completes (merged), needs a human (quarantined), or is a numeric problem (mismatched).

---

## 13. PO Set Statuses (Five, Final)

| Status | Meaning |
|---|---|
| `pending` | Not yet enough documents/data to complete reconciliation. Not an error — just "not fully delivered/invoiced yet." |
| `mismatched` | Required documents are present, but quantities (or, secondarily, price) don't reconcile per Section 10/11. |
| `quarantined` | The system cannot confidently resolve the set on its own — unresolvable join keys, conflicting/duplicate line references, extraction confusion, unmatched extra lines, or invalid quantities (negative/zero). Also reachable manually at any time, even on an already-`mismatched` PO Set, as a way to physically stage files for review without implying additional system uncertainty. **Quarantine is whole-PO-Set only — never per line item.** One bad/ambiguous line pulls the entire set into quarantine, even if the rest is clean. This trades off precision for simplicity: a human reviewing one PO Set's full document set at once is simpler than a UI expressing partial-PO states. |
| `blocked_customs` | Waiting on the 2 customs/shipping uploads (see Section 12). |
| `merged` | Fully reconciled and the combined output PDF has been produced in the output folder. |

**Quarantine's physical counterpart:** when a PO Set is quarantined, copies (never the original untouched files) of every document tied to that `po_no` move to a physical quarantine folder, so a human reviewer has everything in one place.

**Can `mismatched` self-recover?** Yes — if the PO Set is still open (not yet merged) and new or corrected documents/data arrive for the same `po_no`, the next reconciliation pass automatically re-evaluates it, and it can move to `pending`, or directly to `merged` if now fully reconciled, without a manual trigger. A manual "redo matching" action exists separately for forcing a re-check without new data arriving (e.g. after a manual database correction).

---

## 14. Merge Behavior

- **Auto-merge fires only when every relevant line item across every associated document reconciles cleanly** — the PO Set reaches a fully clean state, not `mismatched` or `quarantined`.
- **Combined documents** are treated as self-reconciled and auto-ready on their own — **unless** customs is toggled on, in which case they still wait on the 2 customs/shipping uploads. A combined document only counts as self-reconciled if the AI could confidently identify and extract a PO section, a DN section, and an SI section within it (see 6.5) — not merely because the file is tagged `COMBINED`.
- **Merge order in the output PDF:** SI → DN → PO → (AWB → Customs Declaration, if customs applies). This places billing evidence first, then delivery evidence, then ordering evidence, then logistics/customs attachments.
- **Merge is whole-document concatenation only — never row-level editing or reordering.** Each source PDF is combined in full, exactly as received, in the document order above. The system does not re-sort line items within a document, does not re-order rows to follow PO line sequence, and does not edit page content in any way. This holds even when a document's internal line order doesn't match the PO's order (a confirmed real case — see Section 19).
- **Output filename:** the Invoice/SI number. This field is guaranteed present on at least one document in any PO Set (SI is a required document for merge to fire at all), so no fallback naming logic is required.
- **Once a PO Set reaches `merged`, it is closed permanently.** It is never re-opened, re-evaluated, or overwritten by later activity.
- **Late-arriving documents sharing a `po_no` with an already-`merged` PO Set are treated as a brand-new set**, exactly like any first-time `po_no` — not appended to or reconciled against the closed one. This falls out naturally from the grouping model: grouping happens by `po_no` at evaluation time, and a closed set simply isn't part of that evaluation anymore.
- **Combined-vs-separate collision** (both a combined doc and separate PO/DN/SI docs exist for the same `po_no`): first-completed wins and stays authoritative; the other stays visible but non-authoritative, not routed to quarantine. Accepted as a rare internal-tool edge case.

### 14.1 Force Merge
- Available on any PO Set regardless of current status.
- Requires a confirmation modal (destructive/irreversible action).
- **Fully unconditional** — bypasses all matching logic and the customs gate. Merges directly with whatever customs/shipping files exist at that moment (0, 1, or 2 of them).
- Writes a permanent audit log entry every time it's used.
- Prioritizes simplicity: this is an internal tool, force-merging before customs docs exist is a rare edge case, and the audit log is the safety net for accountability.

### 14.2 Manual PDF Merger (separate tool)
A fully separate, non-AI utility: a user uploads their own files, reorders via drag-and-drop, merges, downloads. No AI, no database row, no `po_no` association. Deliberately isolated from the automated pipeline, including visually/navigationally in the UI, so nobody confuses "the AI pipeline" with "just merge my own files."

---

## 15. Human Review & the AI/Logic Boundary

The system is an automation assistant, not an autonomous auditor. The AI's job is strictly:

```
PDF → document understanding → structured extraction
```

Everything after that — grouping, matching, aggregation, comparison, status — is deterministic business logic, not AI judgment:

```
Structured data → PO grouping → line matching → quantity aggregation → deterministic comparison → result
```

**Why this separation matters:** a model should never "guess" that two quantities are close enough. Whenever extraction or matching is uncertain, the system routes to quarantine for human review, rather than letting the AI make a judgment call that results in an automatic merge.

The CA remains responsible for the final review and decision to carry a merged output forward — this system automates the repetitive matching work, it does not replace that final judgment.

---

## 16. End-to-End Flow

```
PDF INPUT FOLDER
      │
      ▼
SYNC (manual button or midnight schedule)
      │
      ▼
DEDUP (SHA-256) → STORE INTERNAL COPY
      │
      ▼
DOCUMENT CLASSIFICATION (VLM)
      │
      ▼
STRUCTURED EXTRACTION (VLM, schema-validated, retry x3)
      │
      ▼
PO-WISE GROUPING (normalized po_no)
      │
      ▼
LINE-ITEM MATCHING (line_item_no primary, description fallback)
      │
      ▼
AGGREGATE DN QTY  +  AGGREGATE SI QTY   (calculated independently)
      │
      ▼
RECONCILIATION: PO qty == Agg DN qty  AND  PO qty == Agg SI qty  (per line)
      │
   ┌──┴───────────────┬─────────────────┐
   ▼                  ▼                 ▼
PENDING          MISMATCHED        QUARANTINED
(wait for more)  (numeric issue)   (human review, whole PO Set)
   │
   ▼ (once fully reconciled, and customs gate clear if applicable)
MERGE → OUTPUT PDF (named by Invoice/SI number) in output folder
   │
   ▼
CA REVIEW (outside the system)
```

---

## 17. Real-World Document Findings (Confirmed via Sample Review)

Three real PO Sets from actual vendor documents were reviewed against this logic before finalizing it. Findings that changed or reinforced the design:

### 17.1 No fixed template can be assumed
Three PO Sets, three structurally different PO layouts (different field sets, table structures, header/footer conventions — one from STS, one from IRE, one from Ensign), all correctly reconcilable under the same `line_item_no`-based logic. **Extraction must work from semantic document understanding, not positional/template matching** — this is a confirmed design constraint, not a hypothetical one.

### 17.2 Vendor-specific recurring patterns
- The vendor across all three sample sets (RAAS) consistently embeds a "Line Item - N" sub-line inside the description field on both DN and SI documents, distinct from the actual `line_item_no` column. This is a repeating template quirk, not a one-off — extraction prompts must not treat it as a second line item.
- `part_no` was blank on every DN sample reviewed across all three sets, confirming the earlier decision to drop it as a matching signal (Section 8) with real, repeated evidence rather than a single case.

### 17.3 DN is the highest extraction-risk document type
Scanned DN forms (colored physical paper, camera/scan-captured) consistently show OCR/text-layer corruption in this vendor's samples, while POs and SIs (digitally generated) have clean text layers. DNs should be treated as the document type most likely to need extraction retries or produce lower-quality reads in production. This doesn't change any business logic, but is worth flagging for extraction prompt design and expectations-setting.

### 17.4 SI → DN cross-reference field exists but is not used as a matching key
Every SI sample includes a "Delivery Note No" column referencing the specific DN each line came from. This is real, consistently present data — not currently used for matching or reconciliation (`line_item_no` already does that job reliably), but worth capturing at extraction time as a useful audit/display field, low-cost to add.

### 17.5 Company field = the buyer, not the vendor
Across all three PO Sets, the vendor (RAAS) stayed constant while the buyer varied (STS, IRE, Ensign). This confirms that "Company," wherever it's used in a future phase (e.g. distribution/email lookup), should resolve to the buyer — the field that actually varies and would determine routing.

### 17.6 Line ordering is not guaranteed to follow PO order
An SI's line items can be ordered by which DN they came from rather than by `line_item_no` sequence (confirmed in a real sample: a later-arriving DN's line appeared first on the SI). This further confirms `line_item_no` as the only safe join key — and confirms merge output must never attempt to re-sort rows into PO order (see Section 14).

### 17.7 Handwritten corrections and physical stamps are expected, not exceptional
Real DN samples include an occasional handwritten quantity correction and, separately, customer approval stamps placed near the line-item table. Neither is treated as a special case requiring new detection logic — see the extraction gotchas in Section 6.3 for the reasoning (existing reconciliation gate is the safety net; stamps are a non-issue as long as underlying data stays legible).

### 17.8 Split-delivery-across-multiple-DNs for a single PO line — not yet confirmed by a real sample
Every reviewed PO Set had each PO line fulfilled by exactly one DN. The aggregation logic (Section 9) is designed to handle a line being split across multiple DNs, but this hasn't been observed in a real document yet. Flagged as a case to validate with a synthetic test if a real example doesn't surface before dev.

---

## 18. Explicit Assumptions Made to Close Open Questions

These are concrete defaults chosen so the spec has no unresolved ambiguity. Each is a reasonable default consistent with the system's existing philosophy (deterministic, human-in-the-loop on uncertainty, simple over clever) — flag any of these if you want a different behavior:

1. Concurrent Sync/midnight overlap → second trigger is ignored while a run is in progress ("Sync already running").
2. Missed midnight run → no catch-up logic; next Sync/midnight naturally picks up unprocessed files.
3. Corrupted/unreadable PDF → same retry-then-`failed` path as any other extraction failure.
4. Mid-copy stability check → included in this build (simple size-stability poll).
5. Retry triggers → both hard failures (API/network) and soft failures (schema validation) count toward the 3-retry limit.
6. No AI confidence scoring anywhere — extraction is binary valid/invalid; ambiguity is handled by matching/quarantine logic, not confidence thresholds.
7. Combined documents → one extraction call returning all identifiable sub-document sections.
8. Multi-page/long line-item tables → relies on native full-PDF input with no page-cropping step; treated as an unengineered risk area to monitor, not a blocker.
9. `UNKNOWN` doc type → held in a separate unclassified area, not force-grouped or silently dropped.
10. Fuzzy description-match threshold → 85% token-sort similarity, as a tunable constant.
11. Negative/zero/credit-note quantities → out of scope; routed to quarantine if encountered.
12. Conflicting duplicate line references (same `line_item_no`, mismatched description across docs) → quarantine; same `line_item_no` with consistent description → normal summation.
13. `mismatched` PO Sets can self-recover automatically on new data, without requiring a manual redo trigger, as long as the set is still open.
14. Background worker orchestration tooling (e.g. Prefect) is a default choice, not a final commitment — swappable later without a redesign.
15. No auth/roles in v1 is an explicit, accepted risk: anyone with LAN access to the tool has full access, including Force Merge. Stated openly rather than silently assumed.
16. The prior repository's YOLO-based extraction path and vision assets are fully discarded — this is a clean rebuild using the prior repo only as a reference for lessons learned (known bugs, dead code paths), not as a code base to branch from.

---

## 19. Frequently Asked Questions

**Q: Why not just compare PO total quantity against DN+SI combined?**
Because DN and SI represent two different things — what was physically delivered, and what was billed — and they can legitimately diverge from each other even when both correctly reconcile against the PO independently. Checking them separately against the PO is the actual business requirement; summing them together would hide real discrepancies (e.g. an over-invoice masked by an under-delivery).

**Q: What happens if only a PO and a DN exist, with no SI yet?**
The PO Set stays `pending` — it's not an error, just incomplete. It's only evaluated for `mismatched`/reconciled once the required document types are present.

**Q: What if the same line item number appears with different descriptions on two different DNs?**
That's not treated as a simple sum-and-continue case — the system can't confidently say they refer to the same PO line, so the whole PO Set goes to `quarantine`.

**Q: Does one bad line item block the entire PO from merging?**
Yes. Quarantine and mismatched status are both whole-PO-Set, not per-line. This is a deliberate simplification — it avoids building partial-merge logic, at the cost of one bad line pulling the whole set into review.

**Q: Can a PO ever be merged twice, or re-opened after merge?**
No. Once `merged`, a PO Set is permanently closed. Any later document sharing that PO number starts an entirely new PO Set from scratch.

**Q: What stops someone from bypassing all the matching logic?**
Force Merge exists for exactly that, deliberately unconditional (including bypassing the customs gate), but every use is confirmed via a modal and permanently logged. It's an accepted, audited edge case rather than a hidden backdoor.

**Q: Does Force Merge check customs documents first?**
No — it merges with whatever customs/shipping files exist at that moment (0, 1, or 2), regardless of the customs toggle.

**Q: What happens to combined PDFs — are they treated as automatically complete?**
Only if the AI can confidently identify a PO, DN, and SI section within the file. If it can't, that's treated as an extraction failure for that document, not an automatic pass.

**Q: What if two documents both claim to be "the" combined document for the same PO, or a combined doc and separate docs both exist for the same PO?**
Whichever completes reconciliation first becomes the authoritative record for that PO Set; the other stays visible but non-authoritative. This is accepted as a rare edge case rather than engineered around with tie-breaking rules.

**Q: Why is price a secondary check instead of equal priority to quantity?**
Because over-delivery/over-invoicing (quantity risk) is the dominant business concern, and building an exact-match system is simpler than engineering a rounding-tolerant price check. A VAT-rounding-driven false price mismatch is an accepted, low-priority risk to fix if it actually occurs.

**Q: Does the AI ever decide whether something reconciles?**
No. The AI only converts unstructured PDFs into structured data. Every reconciliation decision — matching, aggregation, comparison, status — is deterministic code, not a model's judgment call.

**Q: What happens to the original files in the input folder after processing?**
They're only cleared once a PO Set has both produced a merged output AND had all its extracted data persisted to the database. The system's own internal copies of every file are kept indefinitely, regardless of what happens to the input folder copy.

**Q: Is there a login system?**
No, not in this phase. Anyone with access to the tool on the LAN has full access to every action, including destructive ones like Force Merge. This is an explicitly accepted risk for an internal tool with modest usage, not an oversight.

**Q: What happens after a PDF is merged — does anything automatic happen next, like emailing it out?**
Not in this phase. The system's scope ends at producing the merged, correctly-named output PDF in the output folder. Distribution (identifying the company, drafting an email) is a deliberately separate future phase, not designed against here.

**Q: Why "redo matching" and "redo/re-extract" as two separate actions instead of one "retry" button?**
Because they have very different costs — re-extraction calls the paid AI API and is slow; redo-matching just re-runs free, instant logic against already-stored data. Keeping them separate stops a user from reaching for the expensive option out of habit when they only need to re-check the logic (e.g. after manually correcting a database value).

**Q: What if a vendor PDF is corrupted or won't open?**
It's treated exactly like any other extraction failure — retried up to 3 times, then marked `failed`, without blocking the rest of that PO Set's documents.

**Q: Why keep quarantined files as copies instead of moving the originals?**
So the original processing trail stays intact even while a human reviewer has a convenient, self-contained folder of everything relevant to that PO Set to work from.
