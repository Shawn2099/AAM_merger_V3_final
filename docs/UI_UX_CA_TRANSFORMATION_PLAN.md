# UI/UX CA Transformation Blueprint & Session Context Plan
**Repository:** `Shawn2099/AAM_merger_V3_final`  
**Active Development Branch:** `fix/mvp-high-priority`  
**Production Head:** `main` (commit `bd33eb2`, PR #5 merged)  
**Target User Persona:** Chartered Accountants (CAs), Commercial Finance Controllers, Tax & Statutory Auditors, Accounts Operations  
**Last Updated:** 2026-10-26 (IST)

---

## 1. Project Background & System Context (For Future Sessions)

### Non-Negotiable Core Tenets (Ground Truth & Scope Boundaries)
* **1. The Database is the Single Source of Truth:**
  * Every number, status, badge, and reason shown on the UI must be 100% verified against SQLite database rows.
  * No hallucinated or fabricated columns (e.g. `unit_price`, `uom`, or separate `vendor_printed_desc` do NOT exist in `line_items`; do not display or promise them).
* **2. Zero Changes to Core Logic:**
  * No modifications to reconciliation algorithms, VLM prompts, grouping schemas, poller flows, or locking mechanisms.
  * Only presentation-layer UI/UX refinements (HTML templates, CSS styles, ergonomic layouts, and micro-interactions) are in scope, and only with explicit permission.
* **3. Zero Feature / Signal Discard:**
  * Every single signal and feature in the current UI exists for a verified operational reason and MUST BE PRESERVED:
    * The exact engine `reconcile_reason` must always be visible.
    * Advisory badges: `description_match_count` and per-line `desc_matched` must remain prominent.
    * Line mapping rules: `line_mapping_rule` and per-line `mapped_from` must remain prominent.
    * Document placement sources: `po_no_source == 'dn_number'` and "Confirm it belongs here" buttons.
    * Customs facts: `has_customs_toggle`, `packet_has_customs`, `customs_append_pending`, and supporting doc delete buttons (`remove_supporting`).
    * All operational actions: `Merge Now`, `Re-Match`, `Re-Extract`, `Toggle Customs`, `Force Merge`, `Move to quarantine and remove set`, `Archive`.
  * The goal is **not to subtract or invent features**, but to make every existing database truth **100% clear, mathematically readable, and self-explanatory for CA clients**.

### Architectural Truths & Non-Negotiable Engine Invariants
* **The Core Formula:** Quantities are the **sole signal** for matching and reconciliation.
  * Every quantity in SQLite is stored as an integer scaled $\times 1000$ (e.g., $10.5 \to 10500$). Comparisons are strictly integer math; no floats.
  * Part numbers, line types, SKUs, and UOM were **retired on 2026-09-29** (migration `c1a2b3d4e5f6`) because vendor formatting variations caused high false-quarantine rates.
  * Unit prices were removed from VLM schemas and database storage in migration `d5e6f7a8b9c0` (R-02).
* **Database & Concurrency:**
  * Synchronous SQLAlchemy 2.x with SQLite WAL mode. Single-writer, multi-reader.
  * Optimistic locking protocol: Concurrent updates/merges to the same PO Set raise HTTP 409 Conflict via `po_sets.locked_by_action` and `locked_at`.
* **Branch Strategy:**
  * `main`: Protected deployment-only tree. Contains only production runtime, migrations, static assets, templates, and operator documentation.
  * `fix/mvp-high-priority`: Comprehensive development branch carrying all test suites, evaluation scripts, benchmarks, and architectural plans.

### Documented Limitations to Respect (Product Doc §8)
* **Limitation N:** "Move to quarantine and remove set" deletes database rows (atomic single-transaction delete) while archiving source PDFs, input copies, and JSON manifest exports to `quarantine/_removed_sets/<PO>_<stamp>_<id>/`.
* **Limitation O:** SQLite `po_sets.id` does not use `AUTOINCREMENT`. If the latest set is deleted, the next created set may reuse its primary key. Mitigated by stamping quarantine folders with `removed:<id>:<stamp>`.
* **Limitation L:** A PO document cannot be removed individually from a set via the UI. If a set contains conflicting PO files, operators must either Force Merge (if both apply) or remove the set.

---

## 2. The CA Persona: Friction Points & Mental Model

Chartered Accountants and commercial audit managers evaluate the software against **statutory audit standards**:
1. **Mathematical Proof & Deltas:** A CA requires explicit variance calculations ($PO - DN$ and $DN - SI$). Mental subtraction on raw quantities is unacceptable in high-volume reconciliation.
2. **Substance vs Form (Fidelity):** CAs fear vendor material substitution (e.g. delivering Grade B goods with the same quantity as Grade A). The UI must show both the PO description and the vendor’s printed description side-by-side.
3. **Statutory Gating:** Customs Bill of Entry and Transshipment Air Waybills (AWB) are legal prerequisites for payment release. They must be presented as a clear compliance checklist.
4. **Defensible Accountability:** Management overrides ("Force Merge") must not look like reckless developer bypasses; they must serve as **Authorized Exception Certificates** complete with variance logs, business justification, and authorizer attribution.

### 2.1 Operational Reality & Role Dynamics (Small-to-Mid Scale CA Firm)
* **The Trainee vs Partner Split:**
  * **Article Trainees / Audit Assistants:** Primary day-to-day operators. They monitor the input folder, route unclassified documents, attach Customs Bills of Entry & Air Waybills, and review initial 3-way line matches. *UI Requirement:* Foolproof, guard-railed, zero cognitive ambiguity, clear next steps.
  * **Senior CAs / Audit Partners:** Inspect exceptions, check flagged discrepancies, and authorize overrides (Force Merges). *UI Requirement:* A Partner needs to review an exception packet, read the discrepancy delta, approve or reject, and exit in under 30 seconds.
* **The Pipeline Reality (Verification & Gatekeeper Terminal):**
  * **Input:** Unsorted PDF streams dumped directly into `data/input`.
  * **Engine:** Autonomous grouping by PO digits, VLM extraction, and 3-way quantity matching.
  * **UI Terminal Role:** The UI is *not* a heavy ERP; it is a **Verification, Customs Gating, and Approval Cockpit**. Its primary jobs:
    1. Verify automated 3-way match.
    2. Upload missing Customs Clearance and Shipping documents.
    3. Resolve holding/unclassified files.
    4. Authorize final packet compilation.
  * **Output:** Merged, statutory-compliant PDF packets delivered to `data/output`.
* **Discrepancy Treatment (Under-Delivery vs Over-Billing):**
  * **Partial Shipments ($DN < PO$):** Routine commercial hold. The firm expects to wait for subsequent shipments. *UI must display this calmly as "Awaiting balance (20 units)", not as a scary red error.*
  * **Over-Billing ($SI > DN$ or un-ordered lines):** High-risk audit exception. Must be visually isolated for client notification or credit note request.
* **Firm Scale (Multi-Client Operation):**
  * The firm services **many clients**. While the engine groups by PO digits, the UI should minimize cross-client confusion by prominently displaying vendor/client names and providing fast search/filtering.

---

## 3. Screen-by-Screen Audit & Redesign Blueprint

### Screen 1: Global Shell & Header (`templates/base.html`)
* **Eliminate:**
  * Separate "🚨 Quarantine" link in top navbar (fold into the Dashboard's "Exceptions" view).
  * Duplicate "Merge PDFs" button from dashboard header.
* **Add:**
  * Live **Sync State Pill** in header: `🟢 Ready (Last scan: 12m ago)` or `🔄 Scanning input (Doc 2 of 5)...`.
  * Contextual metadata and zoom/rotate controls inside the slide-over PDF drawer header.
* **Relocate:**
  * Group navbar into: **Worklists (Dashboard)** | **Unclassified Ingest (Badge)** | **Audit Trail** | **Tools ▾ (Manual Merger, Settings)**.

---

### Screen 2: Main Dashboard (`templates/dashboard.html`, `_dashboard_table.html`, `_dashboard_kpi_grid.html`)
* **Eliminate:**
  * Redundant `<select id="status-filter">` dropdown (keep clean horizontal tab pills).
  * Long wrapped sentences in the "Why" column.
* **Add:**
  * Multi-select row checkboxes + Batch Action Bar: `[📥 Download Selected Packets (ZIP)]` and `[🖨️ Print Reconciliation Summaries]`.
  * Commercial context in KPI cards:
    * `24 Sets Ready to Authorize (100% Match)`
    * `12 Sets Short Deliveries (Awaiting Supplier Balance)`
    * `5 Sets Audit Exceptions (Discrepancies / Action Needed)`
    * `8 Sets Customs Hold (Missing AWB / Bill of Entry)`
* **Relocate:**
  * Replace the raw "Why" column with structured, high-contrast status tags:
    * `🟡 -20 Qty (Line 1)` (Short delivery)
    * `🔴 Overbilled +10 (Line 4)` (Invoice exceeds GRN)
    * `🛃 Missing Bill of Entry` (Customs required)
    * `⚠️ Multiple POs Attached` (Contractual conflict)

---

### Screen 3: PO Set Detail & 3-Way Reconciliation (`templates/po_set_detail.html`)
* **Eliminate:**
  * Vertical clutter: Remove separate bottom cards for "Archive Quarantined PO Set" and "Move to quarantine and remove set".
  * Developer-centric justification note: *"The matrix below compares line quantities only... check above stopped it first."*
* **Add (The 3-Way Reconciliation Schedule):**
  * **Delivery Variance Column:** $DN - PO$ (Highlight negative in Amber: *Short Delivery*).
  * **Invoicing Variance Column:** $SI - DN$ (Highlight positive in Red: *Overbilled / Credit Note Required*).
  * **Vendor Item Description Column:** Displays what was actually printed on the vendor's delivery note alongside the PO baseline description.
  * **Formal Totals Row:** Sum of PO Qty, Sum of DN Qty, Sum of SI Qty, Net Variance.
  * **Statutory Compliance Checklist:** Clean side-by-side card showing:
    * `[☑] Purchase Order (Verified)`
    * `[☑] Delivery Challan / GRN (Verified)`
    * `[☑] Tax Invoice (Verified)`
    * `[☒] Bill of Entry (Customs) ── [📎 Attach PDF]`
    * `[☒] Air Waybill (Shipping) ── [📎 Attach PDF]`
  * **Export Workpaper Button:** `[🖨️ Export 3-Way Reconciliation Certificate (PDF / CSV)]`.
* **Relocate:**
  * Danger actions: Consolidate "Force Merge" and "Remove Set" under a collapsed **Supervisor Controls / Management Exceptions** section.

---

### Screen 4: Unclassified Ingest Area (`templates/unclassified.html`)
* **Eliminate:**
  * Technical SHA-256 strings in table rows (`SHA: e7f8a9b0c1...`).
  * Repetitive text: *"Server key: 210851"*.
* **Add:**
  * Accordion categorization by problem type:
    1. 📂 **Missing PO Number** (Clean files needing order assignment)
    2. 🔀 **Conflicting References** (Files with multiple PO references)
    3. ⚠️ **Corrupt / Unreadable Files** (Password-protected, damaged)
  * **Batch Assign Bar:** Select 5 files → `[ Assign Type: DN ▾ ]` `[ PO #: 210851 ]` `[ 🚀 Route All ]`.
  * **Vendor Rejection Helper:** 1-click button to copy email notice explaining why an invoice was rejected.

---

### Screen 5: System Audit Log (`templates/audit.html`)
* **Eliminate:**
  * Raw JSON disclosure blocks (`<details><summary>Raw JSON</summary><pre>...</pre></details>`).
* **Add:**
  * Clean, formatted **Audit Attribute Grid** showing authorizer, reason, affected documents, and waived line items.
  * Filter pills: `[ All Events ]` `[ ⚡ Management Overrides ]` `[ 🚨 Set Removals ]` `[ ⚙️ Settings Changes ]`.
  * Export options: `[ 📊 Export to Excel (.xlsx) ]` and `[ 📄 Export Statutory Audit Report (.PDF) ]`.

---

## 4. Design Frameworks & Industry UI/UX Inspirations

### A. The Core Design Frameworks

#### 1. Management by Exception (MBE) Architecture
* **The Rule:** *Never make an accountant review data that the machine has already mathematically verified.*
* In commercial accounts payable, 80–90% of line items match cleanly.
* **Application to AAM:**
  * Clean matches ($PO = DN = SI$) are visually muted (soft green/slate) or grouped into batch-approvable states.
  * Anomalies (shortages, over-billing, missing documents) are aggressively spotlighted using semantic colors.

#### 2. Nielsen Norman Group (NN/g) Enterprise Heuristics
* **Match Between System & Real World (Heuristic #2):** Replace internal developer terms (`VLM`, `quarantined`, `hashes`, `tokens`, `mapped`) with standard accounting language (`PO Baseline`, `Goods Receipt Note`, `Tax Invoice`, `Bill of Entry`, `Delivery Variance`).
* **Recognition Rather than Recall (Heuristic #6):** Do not force operators to calculate mental math deltas. Display calculated variance columns ($DN - PO$ and $SI - DN$) and compare descriptions side-by-side.
* **Error Prevention & Graceful Recovery (Heuristics #5 & #9):** Provide plain-language recommendations (e.g. *"Vendor overbilled on Line 2. Request credit note before authorizing packet"* instead of raw failure codes like `po_reference_mismatch`).

#### 3. Ant Design Pro & Salesforce Lightning (SLDS) Data Grid Patterns
* **Grouped Header Hierarchy:** Multi-level table headers that segment comparative columns:
  * `[ Contract Baseline (PO) ]` | `[ Physical Fulfillment (DN) ]` | `[ Financial Claim (SI) ]` | `[ Reconciliation Audit ]`
* **Strict Alignment Standards:**
  * Text and descriptions: **Left-aligned**.
  * Numerical quantities and variances: **Right-aligned** with monospace font.
  * Status badges, dates, and icons: **Centered**.
* **Zero Cell Overload:** Keep table cells clean and scannable. Never overload table rows with nested forms; offload complex edits into split drawers or inspection modals.

---

### B. Industry UI/UX Benchmarks & Direct Inspirations

| System | Core UI/UX Pattern | What AAM Merger Should Adopt |
| :--- | :--- | :--- |
| **Stampli** | **Central Split-Pane Verification**<br>Digitized source document pinned side-by-side with extracted line items. | Clicking any discrepancy in the matrix opens the PDF preview drawer focused directly on that line's page. Eliminates tab switching. |
| **Vic.ai** | **Autonomous Matching & Confidence Scoring**<br>Confidence badges guide eyes straight to low-confidence anomalies. | Tag vendor lines with confidence indicators: `Exact Match (100%)` vs `Matched by Description (Needs Check)`. |
| **Coupa & Tipalti** | **3-Way Match Delta Grids**<br>Explicit *Ordered, Received, and Invoiced* columns with delta chips (`-20 short`). | The 3-Way Reconciliation matrix must feature dedicated variance columns and a formal totals summary row. |
| **Ramp / Bill.com** | **Consumer-Grade Fintech Simplicity**<br>Sleek Inter typography, high-contrast badges, progressive disclosure. | Remove harsh developer boxes and raw JSON; adopt clean cards, rounded badges, and clear action hierarchies. |
| **Xero / QuickBooks** | **Two-Column Clearing Workflow**<br>Clear visual balance of matched vs unmatched items. | The Unclassified holding area adopts a clean "Match / Assign / Split" clearing view rather than an unorganized table. |

---

## 5. Implementation Phasing & Next Steps

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 1: Jargon Removal & Visual Polish (Zero DB / Backend Changes)         │
│ • Update terminology: "Quarantine" -> "Audit Exception",                    │
│   "Force Merge" -> "Management Override", "Re-Extract with AI" -> "Re-Parse"│
│ • Add Variance columns (Delivery Δ and Invoicing Δ) to the matrix           │
│ • Add formal Totals Row to the reconciliation table                         │
│ • Add Print Stylesheet (@media print) for physical 3-Way Match Certificates │
├─────────────────────────────────────────────────────────────────────────────┤
│ PHASE 2: Layout & Workflow Restructuring                                    │
│ • Consolidate red buttons on PO Set Detail under "Supervisor Controls"      │
│ • Refactor Dashboard tabs into commercial worklists                         │
│ • Implement batch routing in Unclassified holding area                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ PHASE 3: Audit & Reporting Exports                                          │
│ • Add Excel/CSV export for Reconciliation Statements & System Audit Log     │
│ • Deep-link line item clicks directly to target page in the PDF drawer      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Ongoing Session Log & Insights Tracker

| Date | Author / Agent | Milestone / Insight Recorded | Status |
| :--- | :--- | :--- | :--- |
| **2026-10-25** | Claude & Shawn | Completed Batch 1 features (safe set removal, customs toggle refusal, holding set-aside, IST timestamps, responsive styling). Created PR #5. | Merged to `main` (`bd33eb2`) |
| **2026-10-26** | Antigravity | Full code audit of PR #5 (27 files, 134 tests verified green). Boot clean-room verified. | Production Ready |
| **2026-10-26** | Antigravity | Conducted Senior UI/UX Audit for CA clients; created comprehensive screen-by-screen blueprint (`docs/UI_UX_CA_TRANSFORMATION_PLAN.md`). | Blueprint Established |
| **2026-10-26** | Antigravity | Researched design frameworks (MBE, NN/g, SLDS, Ant Design Pro) and industry inspirations (Stampli, Vic.ai, Coupa, Ramp); added Section 4 to blueprint. | Framework Integrated |

