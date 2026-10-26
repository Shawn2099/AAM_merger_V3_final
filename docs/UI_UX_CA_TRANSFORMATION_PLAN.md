# AAM Merger V3 — Final UI/UX & System Architecture Specification

**Repository:** `Shawn2099/AAM_merger_V3_final`  
**Target User Persona:** Chartered Accountants (CAs), Tax & Statutory Auditors, Finance Controllers, Accounts Operations  
**Status:** Authoritative Specification & Design Alignment Record  
**Date:** 2026-10-26 (IST)  

---

## 1. Core Philosophy & Architectural Ground Truths

1. **The 4 Real Business Folders Only:**
   The application operates strictly with four folders on disk:
   * 📁 `data/input` — Incoming vendor PDFs placed by users/scanners.
   * 📁 `data/stored` — Deduplicated raw storage for active PDFs in the pipeline.
   * 📁 `data/output` — Reconciled, compiled PDF packets.
   * 📁 `data/quarantine` — The single destination for all rejected, flagged, set-aside, or removed files.
   > **Architectural Law:** There is **no separate user-facing "archive" concept**. The internal developer snapshot path `data/development/archived_sets` is an internal developer testing artifact and is never presented to the user. All problematic files, set-aside files, and removed sets live in `data/quarantine`.

2. **The Database is the Single Source of Truth:**
   * Every quantity, status, badge, and reason is read directly from SQLite rows.
   * No fabricated columns (no fake prices, no UOM, no SKUs).
   * Quantities are scaled integers ($\times 1000$). Matching is exact integer arithmetic.

3. **Clean, Uncluttered CA Experience (Zero Bloat):**
   * Keep the 6-column reconciliation matrix clean, compact, and focused.
   * No unnecessary variance math columns (`Delivery Var`, `Invoice Var`).
   * No unneeded total quantity rows at the bottom of the table.
   * No letterhead certificates or signoff blocks.

---

## 2. 1-to-1 Unified Naming Standard (Identical & Constant Everywhere)

To eliminate all cognitive friction and mental translation for CAs and article clerks, the terminology is **100% constant and identical across all views**:

| SQLite Status | Navigation | Dashboard KPI Card | Dashboard Filter Tab | Row Status Badge | Meaning |
|---|---|---|---|---|---|
| `pending` | — | Total Pool | **`Awaiting Documents`** | **`Awaiting Documents`** | Waiting on vendor DN or SI to arrive |
| `mismatched` | — | **`⚠️ Quantity Mismatch`** | **`Quantity Mismatch`** | **`Quantity Mismatch`**<br>*(or `Partial Delivery`)* | Quantities disagree (under/over delivery) |
| `blocked_customs`| — | **`🔒 Awaiting Customs/Shipping Docs`** | **`Awaiting Customs/Shipping Docs`** | **`🔒 Awaiting Customs/Shipping Docs (2 docs needed)`** | Quantities match, waiting for BoE / AWB |
| `merged` | — | **`✅ Merged & Ready`** | **`Merged & Ready`** | **`Merged & Ready`** | 100% 3-way matched and packet ready |
| `quarantined` | **`🚨 Flagged`** | **`🚨 Flagged`** | **`Flagged`** | **`🚨 Flagged`** | Stopped by hard validation failure |
| `DocType.UNKNOWN`| **`❓ Unmatched Files`**| — | — | — | Unplaced documents holding inbox |

---

## 3. The Three Unified Action Buttons (Explicitly Naming Quarantine Folder)

Every button that moves files out of active workflows explicitly states where the files go:

1. **In PO Set Detail (`po_set_detail.html`):**  
   🔴 **`Remove Set to Quarantine Folder`**  
   *What it does:* Copies the set's attached PDFs into `data/quarantine/_removed_sets/{PO_NUMBER}_{TIMESTAMP}/`, deletes the database rows, and logs a permanent audit entry.
2. **On the Flagged Desk (`_quarantine_table.html`):**  
   🔴 **`Move to Quarantine Folder`**  
   *What it does:* Dismisses the flagged set from active review, safely stores its record in quarantine, frees the SHA-256 hash (allowing corrected re-uploads), and logs an audit entry.
3. **In Unmatched Files & Files Set Aside (`unclassified.html` & `_files_set_aside.html`):**  
   ⚪ **`Move to Quarantine Folder`**  
   *What it does:* Moves a bad or unreadable PDF out of the inbox into `data/quarantine/_documents/{filename}/`.

---

## 4. PDF Preview Everywhere (`👁️ Preview`)

The slide-over PDF viewer drawer is available in **all 3 document views**:
1. **PO Set Detail:** Available on every attached PO, Delivery Note, Invoice, and Customs document.
2. **Unmatched Files:** Available on every unplaced file in the holding area.
3. **Flagged Desk (Files Set Aside):** Available on every quarantined document and unsplit Combined PDF so the CA can inspect pages directly in the browser.

---

## 5. Unmatched Files Sub-Views (`/unclassified`)

The Unmatched Files page serves as an **Actionable Inbox** for files requiring manual routing. It is organized into 3 quick sub-views using lightweight client-side Alpine.js filtering:

1. **`Needs PO Number` (Actionable):**
   * Files read successfully by AI/OCR, but missing a PO number on the printed page, or carrying ambiguous multiple PO numbers.
   * **Operator Action:** Click `👁️ Preview`, type in PO Number, and click `Save & Route` to attach directly to the PO Set.
2. **`Unreadable / Failed` (Non-Actionable):**
   * Files that failed AI extraction after 3 attempts, corrupted PDFs, or unreadable scans.
   * **Operator Action:** Click `Move to Quarantine Folder` to set it aside.
3. **`All Unmatched Files`:**
   * Master view of all unplaced documents.

*Unsplit Combined PDFs are filtered out of the actionable inbox to prevent clutter.*

---

## 6. Flagged Desk Review (`/quarantine`)

The review desk for all exceptions and set-aside files:
* **Quarantined PO Sets Table:**
  * Lists orders halted by hard rules (zero quantity, orphan lines, PO reference mismatch).
  * Buttons: `Inspect & Resolve` and `Move to Quarantine Folder`.
  * Confirmation modal clearly displays:
    ```
    Move to Quarantine Folder?
    This removes PO Set {po_no} from the active Flagged desk.
    Destination: data/quarantine/
    Source PDFs: Preserved safely in data/stored/
    Audit Log: Permanent record created
    ```
* **Files Set Aside Table:**
  * Lists unattached files set aside into quarantine (including unsplit Combined PDFs).
  * Subfolder path displayed: `_documents/{filename_stem}_{short_hash}/`.
  * Equipped with the **`👁️ Preview`** button on each row.

---

## 7. PO Set Detail View (`/po_sets/{id}/view`)

1. **Clean 6-Column 3-Way Match Matrix:**
   * `Line Item No`
   * `Description`
   * `PO Qty`
   * `Delivered Qty (DN)`
   * `Invoiced Qty (SI)`
   * `Status` (`Match`, `Shortage`, `Over`, `Missing`)
   *(Zero total rows, zero variance math columns, zero signature blocks).*
2. **Interactive Customs Requirement Toggle Switch:**
   * High-contrast switch button with sliding thumb and `ON` / `OFF` indicator.
   * When `ON`: Mandatory requirement for `CUSTOMS` (BoE) and `SHIPPING` (AWB) documents before merging.
3. **Action Buttons:**
   * `Merge Now` (re-evaluates gates and merges if compliant).
   * `Re-Match (Instant)` (re-calculates matches on existing extracted data).
   * `Re-Extract with AI` (re-runs AI parser on attached documents).
   * `Remove Set to Quarantine Folder` (removes set from active dashboard).
   * `Force Merge` (management override requiring >= 20 char justification).

---

## 8. Handling of Combined PDFs (Multi-Document Scans)

When a multi-document PDF (containing PO + DN + SI together) arrives in `data/input`:
1. The AI classifies it as `COMBINED`.
2. To prevent token waste and avoid corrupting 3-way line counts, the engine immediately moves it out of `data/input`.
3. It is copied to `data/quarantine/_documents/{filename_stem}_{short_hash}/` with a `QUARANTINE.txt` explanation.
4. **Strict Separation (Option 1):**
   * The file lives **strictly on the `🚨 Flagged` desk** under *"Files set aside"*.
   * It is equipped with the **`👁️ Preview`** button so the CA can inspect the bundled pages.
   * It does **NOT** appear in `❓ Unmatched Files` to keep the Unmatched Files inbox 100% clean and actionable.

---

## 9. Visual Cues & Chrome Browser Compatibility

* Emojis are preserved alongside text (`📊`, `❓`, `🚨`, `🧩`, `📜`, `🔒`, `⚠️`, `✅`) for strong instant visual cues.
* Standard UTF-8 encoding ensures 100% flawless color rendering in Google Chrome and Microsoft Edge on Windows 10/11 and macOS.
