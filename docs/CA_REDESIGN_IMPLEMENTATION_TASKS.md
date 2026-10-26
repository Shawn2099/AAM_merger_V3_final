# CA UI/UX Redesign — Granular Implementation Task Breakdown

**Document:** `docs/CA_REDESIGN_IMPLEMENTATION_TASKS.md`  
**Repository:** `Shawn2099/AAM_merger_V3_final`  
**Branch:** `feat/ui-ca-redesign`  
**Status:** Approved Plan — Ready for Execution  
**Date:** 2026-10-26 (IST)  

---

## High-Level Execution Overview

| Phase | Description | Backend Impact | Risk Level |
|---|---|---|---|
| **Phase 1** | 1-to-1 Unified Naming Alignment (Flagged, Merged & Ready, Quantity Mismatch) | None (Templates only) | Zero |
| **Phase 2** | Quarantine Folder Action Buttons (`Remove Set to Quarantine Folder`, `Move to Quarantine Folder`) | None (Templates only) | Zero |
| **Phase 3** | PDF Preview Everywhere (Add `Preview` to Flagged Desk) | None (Templates only) | Zero |
| **Phase 4** | Unmatched Files 3 Quick Sub-Views (Alpine.js Tabs) | None (Templates only) | Zero |
| **Phase 5** | PO Set Detail Polish & Zero-State Guidance | None (Templates only) | Zero |
| **Phase 6** | Automated Verification & Playwright Browser Test Suite | Test assertions update | Zero |

---

## Detailed Task Breakdown

### Phase 1: 1-to-1 Unified Naming Alignment
*Enforce constant, identical terminology across Navigation, KPI Cards, Filter Tabs, and Row Badges.*

- [x] **Task 1.1: KPI Cards (`templates/_dashboard_kpi_grid.html`)**
  - KPI Card 2: Updated title to **`✅ Merged & Ready`**
  - KPI Card 3: Updated title to **`⚠️ Quantity Mismatch`**
  - KPI Card 4: Retained title **`🔒 Awaiting Customs/Shipping Docs`**
  - KPI Card 5: Updated title to **`🚨 Flagged`**

- [x] **Task 1.2: Dashboard Tabs (`templates/_dashboard_tabs.html`)**
  - Tab 2: Retained text **`Awaiting Documents`**
  - Tab 3: Updated text to **`Quantity Mismatch`**
  - Tab 4: Retained text **`Awaiting Customs/Shipping Docs`**
  - Tab 5: Updated text to **`Merged & Ready`**
  - Tab 6: Updated text to **`Flagged`**

- [x] **Task 1.3: Table Status Badges (`templates/_dashboard_table.html` & `templates/po_set_detail.html`)**
  - `pending` → **`Awaiting Documents`**
  - `mismatched` (under) → **`Waiting (part delivered)`**
  - `mismatched` (other) → **`Quantity Mismatch`**
  - `blocked_customs` → **`🔒 Awaiting Customs/Shipping Docs`**
  - `merged` → **`Merged & Ready`**
  - `quarantined` → **`🚨 Flagged`**

---

### Phase 2: Quarantine Folder Action Buttons
*Ensure every button that moves files out explicitly names the Quarantine Folder.*

- [x] **Task 2.1: PO Set Detail (`templates/po_set_detail.html`)**
  - Red removal button: **`Remove Set to Quarantine Folder`** (retains `data-testid="btn-confirm-remove-set"`).
  - Modal text confirms files are copied to `data/quarantine/_removed_sets/{PO_NUMBER}/`.

- [x] **Task 2.2: Flagged Desk (`templates/_quarantine_table.html`)**
  - Red dismissal button on each row: **`Move to Quarantine Folder`** (retains `data-testid="btn-archive-row"`).
  - Confirmation modal confirms files are held in `data/quarantine/`.
  - Confirm button: **`Move to Quarantine Folder`** (retains `data-testid="btn-confirm-archive-row"`).

- [x] **Task 2.3: Unmatched Files & Set-Aside Rows (`templates/unclassified.html` & `templates/_files_set_aside.html`)**
  - Button text: **`Move to Quarantine Folder`** (retains `data-testid="btn-set-aside"` and `data-testid="btn-set-aside-quarantine"`).

---

### Phase 3: PDF Preview Everywhere (`👁️ Preview`)
*Ensure the slide-over PDF preview drawer is available in all document sections.*

- [x] **Task 3.1: Flagged Desk (`templates/_files_set_aside.html`)**
  - Added `👁️ Preview` button on each row in the "Files set aside" table.
- [x] **Task 3.2: Verify Unmatched Files & PO Set Detail Preview Buttons**
  - Verified `👁️ Preview` button is fully responsive and opens the slide-over drawer smoothly on all documents.

---

### Phase 4: Unmatched Files Sub-Views (Alpine.js Tabs)
*Organize Unmatched Files into 3 quick sub-views with live count badges.*

- [x] **Task 4.1: Sub-View Tabs in `templates/unclassified.html`**
  - Wrapped table in Alpine.js: `x-data="{ activeTab: 'all' }"`
  - Added 3 tab buttons with live counts:
    1. `All Unmatched Files` (Master list)
    2. `Needs PO Number` (Actionable: extraction valid, no PO attached or ambiguous PO)
    3. `Unreadable / Failed` (Non-Actionable: extraction failed)
- [x] **Task 4.2: Client-Side Row Visibility**
  - Bound `x-show` on each table row so only documents for the active tab are displayed.
- [x] **Task 4.3: Exclude Combined PDFs from `Needs PO Number`**
  - Backend query already filters unsplit Combined bundles (`~unsplit_bundle_clause()`); they live strictly on the Flagged desk.

---

### Phase 5: PO Set Detail Polish & Reassuring Zero-States

- [x] **Task 5.1: PO Set Detail Invariants**
  - Clean 6-column matrix: `Line Item No`, `Description`, `PO Qty`, `Delivered Qty (DN)`, `Invoiced Qty (SI)`, `Status`.
  - Zero total rows, zero variance columns, zero signoff blocks.
  - Interactive Customs Requirement toggle switch button (`ON` / `OFF`).
- [x] **Task 5.2: Reassuring Zero-State Messages (`templates/_dashboard_table.html`)**
  - When 0 records exist under an active tab, display helpful, professional messages:
    - Quantity Mismatch: `All Clear: No quantity discrepancies found across active purchase orders.`
    - Customs Blocked: `Clearance Complete: No purchase orders currently blocked by missing customs clearance or shipping documents.`
    - Awaiting Documents: `All Caught Up: No purchase orders currently waiting for vendor delivery notes or invoices.`
    - Flagged / Quarantined: `Clean Pipeline: No purchase orders are currently flagged for critical exceptions or conflicts.`
    - Merged & Ready: `No completed orders yet.`

---

### Phase 6: Automated Verification & Testing

- [x] **Task 6.1: Update UI Test Assertions for Unified Naming**
  - Updated `STATUS_TEXT` in `tests/ui/test_ui_dashboard.py` and `tests/ui/test_ui_unclassified.py`.
- [x] **Task 6.2: Run Core Test Suite**
  - Run:
    ```powershell
    uv run pytest tests/test_manual_merger_ui.py tests/test_dashboard.py tests/test_set_remove_to_quarantine.py tests/test_styled_404.py tests/test_batch1_audit_findings.py
    ```
  - Result: 104 passed.
- [x] **Task 6.3: Run Playwright Browser UI Suite**
  - Run:
    ```powershell
    $env:RUN_E2E="1"; uv run pytest tests/ui/test_ui_global.py tests/ui/test_ui_dashboard.py tests/ui/test_ui_batch1_audit_findings.py tests/ui/test_ui_unclassified.py tests/ui/test_ui_set_detail.py
    ```
  - Result: 100% passing across all UI suites.
- [x] **Task 6.4: Git Commit & Remote Push**
  - Staged all UI changes, test suites (including `tests/ui/release/`), and documentation.
  - Committed and pushed to `origin feat/ui-ca-redesign` (commit `9a5f28c`).
