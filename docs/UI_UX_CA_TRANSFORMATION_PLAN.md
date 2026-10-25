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

## 2. Operational Reality & Role Dynamics (Small-to-Mid Scale CA Firm)

### 2.1 The Two-Tier User Hierarchy
* **Article Trainees / Audit Assistants (Day-to-Day Operators):**
  * Primary users sitting in front of the screen. They monitor incoming files, attach Customs Bills of Entry & Air Waybills, route unclassified documents, and verify initial 3-way line matches.
  * *UX Requirement:* **Foolproof, guard-railed, zero cognitive ambiguity.** Clear next steps, obvious dropzones, and zero risk of accidental data destruction.
* **Senior CAs / Audit Partners (Exception Approvers):**
  * Only inspect sets flagged with discrepancies or requiring a Management Override (Force Merge).
  * *UX Requirement:* **A 15-second decision view.** An executive finding box stating the exact variance delta, an audit justification box, and 1-click authorization.

### 2.2 The Pipeline & Tool Reality (Verification Cockpit)
* **Input:** Unsorted PDF streams dumped directly into `data/input`.
* **Engine:** Autonomous grouping by PO digits, VLM extraction, and 3-way quantity matching.
* **UI Role:** A **Verification, Customs Gating, and Approval Cockpit**. Its primary jobs:
  1. Verify automated 3-way match.
  2. Upload missing Customs Clearance and Shipping documents.
  3. Resolve holding/unclassified files.
  4. Authorize final packet compilation.
* **Output:** Merged, statutory-compliant PDF packets delivered to `data/output`.

---

## 3. Heading & Section Naming Master Taxonomy

| Current System Name | Proposed CA-Friendly Name | Commercial / Accounting Rationale |
| :--- | :--- | :--- |
| **AAM Merger v3** | **AAM Document Reconciler** <small>v3</small> | Focuses on statutory reconciliation rather than mechanical merging. |
| **📊 Dashboard** | **📊 Reconciliation Worklists** | Standard audit firm structure where daily tasks are worklists to be cleared. |
| **❓ Unclassified** | **📥 Unassigned Documents** | Plain language for unplaced files needing a PO reference. |
| **🚨 Quarantine** | **🚨 Audit Exceptions Queue** | Professional accounting term for sets failing automated reconciliation rules. |
| **📜 Audit Log** | **📜 Compliance Audit Trail** | Standard statutory accounting title for read-only regulatory event logs. |
| **🧩 Merge PDFs** | **🧩 Manual Document Utility** | Clarifies that this is a standalone tool separate from the pipeline. |
| **PO Sets (X records)** | **Purchase Order Packets** `(X Active)` | Eliminates database jargon ("Sets", "Records"). |
| **✅ Merged & Ready** | **✅ Reconciled & Ready** | Confirms 100% mathematical and statutory match. |
| **⚠️ Needs Review** | **⚠️ Quantity Discrepancies** | Specific to line-item delivery/invoicing deltas. |
| **🔒 Awaiting Customs** | **🛃 Missing Customs / Shipping Docs** | Action-oriented: tells trainees exactly what is missing. |
| **Why (Column)** | **Audit Finding / Engine Verdict** | Professional audit report terminology. |
| **3-Way Reconciliation Matrix** | **3-Way Reconciliation Schedule** | Accounting convention for 3-way comparison schedules. |
| **Attach Supporting Document** | **📎 Attach Customs Clearance & Air Waybill** | Directly names the two required statutory documents. |
| **Confirm Force Merge** | **⚠️ Authorize Management Override** | Reflects supervisory authorization with an immutable audit record. |
| **Move to quarantine and remove set** | **🗑️ Move Files to Exception Folder & Remove Packet** | Accurately describes the two-step safety guarantee. |

---

## 4. The 7 CA Approval & Review Touchpoints (How to Tackle Each)

```
┌────────────────────────────────────────────────────────────────────────┐
│                   THE 7 CA APPROVAL & REVIEW TOUCHPOINTS               │
├───────────────────────────────────┬────────────────────────────────────┤
│ JUNIOR TRAINEE VERIFICATIONS      │ SENIOR CA / PARTNER SIGN-OFFS      │
│ 1. Confirm DN-Only Placement      │ 5. Management Override (Force Merge)│
│ 2. Description Match Review       │ 6. Multi-PO Document Decision      │
│ 3. Line Numbering Rule Review     │ 7. Permanent Packet Removal        │
│ 4. Routine Partial Delivery Hold  │                                    │
└───────────────────────────────────┴────────────────────────────────────┘
```

### 1. Document Matched by DN Number Only (`po_no_source == 'dn_number'`)
* *What the DB has:* Document attached because its DN number matched another document in the set.
* *How the UI tackles it:* Trainee sees an advisory context pill: *"Document INV-44 does not cite a PO, but matches Delivery Note #102 in this set."* Button remains: **[ ✔ Confirm Document Belongs to PO ]**.

### 2. Description Match Fallback Review (`description_match_count > 0`, `r.desc_matched`)
* *What the DB has:* Lines matched by text similarity because the vendor printed no line numbers.
* *How the UI tackles it:* Affected table rows feature a crisp audit disclosure chip: `[ 🔍 Matched by description fallback — verify item text ]`. Trainee inspects the description and confirms.

### 3. Line Numbering Mapping Rule Review (`line_mapping_rule`, `r.mapped_from`)
* *What the DB has:* Vendor lines numbered 10, 20, 30 mapped by rule to PO lines 1, 2, 3.
* *How the UI tackles it:* Table header displays an explanatory banner: *"Line Numbering Standardized: Vendor lines (10x) aligned to PO lines (1, 2, 3)."* Column displays `#1 [Vendor Line 10]`.

### 4. Routine Partial Deliveries (`shortfall == 'under'`)
* *What the DB has:* Every disagreeing quantity is strictly below the PO ($DN < PO$ and $SI \le DN$).
* *How the UI tackles it:* Displayed in calm **Warm Amber**: *"🟡 Awaiting Remaining Balance (Partial Delivery: 20 units pending)"*. Trainees understand this is normal commercial waiting, avoiding partner panic.

### 5. Management Override / Force Merge (`action="force_merge"`)
* *What the DB has:* Override bypassing reconciliation and customs gates with $\ge 20$-char justification.
* *How the UI tackles it:* Structured **Partner Sign-Off Checklist**:
  * Automatically summarizes discrepancies being waived (*"Line 1: 20 units delivery shortage; Bill of Entry waived"*).
  * Mandatory Partner Justification box.
  * Button: **"Authorize Management Override & Merge Packet"**.

### 6. Multi-PO Document Conflict (`multi_po_decision == True`)
* *What the DB has:* Two PO files arrived in the same set; auto-merge refuses.
* *How the UI tackles it:* Partner Decision Box explaining:
  * *If both files form the full order:* Use **Management Override**.
  * *If one file is an incorrect duplicate:* Use **Move Files to Exception Folder & Remove Packet**, remove the duplicate from the input folder, and re-scan.

### 7. Packet Removal to Quarantine (`remove_to_quarantine`)
* *What the DB has:* Copies files to `quarantine/_removed_sets/`, writes JSON exports and `REMOVED.txt`, deletes database rows.
* *How the UI tackles it:* Clear modal reassuring the user: *"All files and audit history are safely archived in the quarantine folder before database rows are cleared. Nothing is destroyed."*

---

## 5. UI Theme & Visual Design Specifications for CA Firms

### 5.1 Institutional Audit Palette (High Contrast, WCAG AA)
```css
:root {
  /* Brand & Surfaces */
  --nav-bg: #0f172a;        /* Deep Trust Navy */
  --primary: #1d4ed8;       /* Corporate Cobalt */
  --bg: #f8fafc;            /* Crisp Warm Slate */
  --card-bg: #ffffff;
  --border: #cbd5e1;        /* High-contrast crisp gridlines */

  /* Audit Statuses */
  --clr-matched-text: #14532d;   --clr-matched-bg: #dcfce7;
  --clr-pending-text: #854d0e;   --clr-pending-bg: #fef9c3;
  --clr-risk-text: #991b1b;      --clr-risk-bg: #fee2e2;
  --clr-customs-text: #1e40af;   --clr-customs-bg: #dbeafe;
}
```

### 5.2 Native Tabular Typography (LAN-Safe, No Web Fonts)
```css
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
.qty-val, .variance-val, .table-amount {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-variant-numeric: tabular-nums; /* Equal digit widths for perfect column vertical alignment */
  text-align: right;
  font-weight: 600;
}
```

### 5.3 Information Density: Compact Accounting Grid
* Row height: Reduced from `48px` to `34px–36px`.
* Alternating row shading (Zebra striping): `tr:nth-child(even) { background-color: #f8fafc; }`.
* Result: **15 to 20 line items visible simultaneously** on standard 14" office laptops.
* Badges: Crisp `4px` rectangular audit chips instead of rounded bubble pills.

### 5.4 Statutory Print Stylesheet (`@media print`)
When a CA presses `Ctrl + P`:
1. Navbars, action buttons, and upload forms are hidden (`display: none`).
2. A formal letterhead appears: `AAM COMMERCIAL RECONCILER — STATUTORY 3-WAY MATCHING WORKPAPER`.
3. The 3-way matrix prints with a double-underlined Totals row.
4. Formal signature blocks print:
   * *Prepared by (Trainee): _______________*
   * *Reviewed & Approved by (Partner): _______________*

---

## 6. Implementation Phasing & Next Steps

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 1: Jargon Removal, Mathematical Deltas & Theme Polish (Zero DB Impact)│
│ • Update headings and badge labels across templates to CA terminology.      │
│ • Calculate Delivery Variance (DN - PO) & Invoicing Variance (SI - DN).     │
│ • Add Totals Summary Row to the 3-Way Reconciliation Schedule.              │
│ • Apply High-Contrast Audit Palette, Tabular Numbers & Compact Row Padding. │
│ • Add @media print stylesheet for physical statutory workpaper signing.     │
├─────────────────────────────────────────────────────────────────────────────┤
│ PHASE 2: Layout Ergonomics & Workflow Consolidation                         │
│ • Move Customs Upload form adjacent to Customs Checklist card.              │
│ • Format Executive Finding Box for Partner 15-second review.                │
│ • Structure Force Merge modal into an Override Authorization Checklist.     │
├─────────────────────────────────────────────────────────────────────────────┤
│ PHASE 3: Audit & Reporting Exports                                          │
│ • Add Excel/CSV export for Reconciliation Statements & System Audit Log.    │
│ • Implement page-accurate PDF preview deep-linking.                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Ongoing Session Log & Insights Tracker

| Date | Author / Agent | Milestone / Insight Recorded | Status |
| :--- | :--- | :--- | :--- |
| **2026-10-25** | Claude & Shawn | Completed Batch 1 features (safe set removal, customs toggle refusal, holding set-aside, IST timestamps, responsive styling). Created PR #5. | Merged to `main` (`bd33eb2`) |
| **2026-10-26** | Antigravity | Full code audit of PR #5 (27 files, 134 tests verified green). Boot clean-room verified. | Production Ready |
| **2026-10-26** | Antigravity | Senior UI/UX Audit for CA clients (`docs/UI_UX_CA_TRANSFORMATION_PLAN.md`). | Blueprint Established |
| **2026-10-26** | Antigravity | Research on Design Frameworks (MBE, NN/g, SLDS) & Industry Inspirations (Stampli, Vic.ai, Coupa). | Framework Integrated |
| **2026-10-26** | Antigravity | Enshrined Core Tenets (DB truth, zero logic change, zero feature discard), Trainee vs Partner dynamics, 7 Review Touchpoints, and Theme/Print specs. | Master Blueprint Complete |
