# AAM Merger: Operator Runbook (supervised pilot)

For the person who runs the dashboard every day during the pilot.
Status: draft, 2026-10-25. Items marked **[TO CONFIRM]** are not in the
project documents yet. Fill them in before the pilot starts.

Sources: `docs/CUSTOMS_OPERATIONS.md`, `docs/DELIVERY.md`, `docs/CORE_LOGIC.md`,
`docs/DEPLOYMENT_PLAN.md`, `AAM_merger_V3_PRODUCT.md` (sections 3 and 8),
`templates/dashboard.html` and `templates/po_set_detail.html`.

---

## 1. What the system does

1. You drop PDF files (purchase orders, delivery notes, sales invoices) into the input folder.
2. The app reads each file with an AI model, and groups files that carry the same PO number into one **PO Set**.
3. It compares the quantities. The PO must equal the delivery notes **and** the invoices, line by line.
4. If they agree, it writes **one merged PDF per PO** into the output folder. The merged PDF is the packet.
5. If anything does not agree, or cannot be read, the set is held with a reason. Nothing is merged on a doubt.

---

## 2. Daily check

### Morning (before you start work)

1. Open the dashboard: `http://[TO CONFIRM: server IP]:8000/dashboard`.
2. Look at the four cards at the top: **Merged & Ready**, **Needs Review**, **Awaiting Customs**, **Quarantined**.
3. Open the **Pending** tab. Check that yesterday's new files show **Read** on their set page, not **Extracting** or **Failed** for a long time.
4. Check the **Unclassified** page (left menu). The number badge shows documents the app could not place.
5. If new files arrived overnight but nothing moved, check the three services before anything else.
   Run `.\scripts\manage_services.ps1 -Action status` on the server. All three must say `Running`.
   Also check that the nightly run happened (see the log, section 8).

**Good looks like:** no set older than a day in Pending with no reason, Quarantined and Needs Review
counts that you recognise, and yesterday's new files either merged or held with a reason.

### End of day

1. Open the **Quarantined** and **Needs Review** tabs. Each row should have a reason you understand.
2. Open each **Awaiting Customs** set. Is anyone still sending the customs or shipping file?
3. Check the **Merged** tab: every row you merged today should have a packet you have checked (section 6).
4. Check that today's backup file exists: `data\development\snapshots\db` (backup runs at 01:00 every night).
5. Write the day's notes (section 6, "What to record").

**Bad looks like:** a set is "Pending" for days with no reason, a merged set has no packet
("Merged - packet missing"), or the services are not Running.

---

## 3. Status glossary

The row status is the big badge. The **reason** is the sentence on the same row. Where the reason
code is given in brackets, it is the code the app uses; the row shows the sentence.

### Main statuses

| Badge or tab on the dashboard | What it means | What you do |
|---|---|---|
| **Pending / Waiting** (tab "Pending") | Not ready. Something is missing or still being read. This is not a pass. | Read the reason. Wait if it is "waiting" or "not yet read". Otherwise follow the reason in the table below. |
| **Needs Review (Discrepancy)** (tab "Needs Review", status `mismatched`) | A line has different quantities on the PO and on a delivery note or invoice. | Open the set. Compare the printed documents with the matrix. Decide which document is wrong. Do not Force Merge to clear it (section 5). |
| **🚨 Quarantined** (tab "Quarantined") | Something is broken or cannot be confirmed. The whole set is held, not one line. | Open it with **Inspect & Resolve**. Read the reason. Fix the cause if you can (section 4 and 5). Archive only if the set is not needed. |
| **🔒 Awaiting Customs** (tab "Awaiting Customs", status `blocked_customs`) | The PO, delivery note and invoice agree, but the customs switch is ON and the CUSTOMS and SHIPPING files are not both attached. | Follow section 4. |
| **Merged & Ready** (tab "Merged", status `merged`) | The packet is written in the output folder. | Check the packet (section 6). Download it with **Download Merged PDF** if needed. |
| **Merged • review** (amber) | Merged, but at least one line was matched by its description, or by a numbering rule, not by a printed line number. | Open the set. Check the marked item descriptions in the matrix against the printed documents. |
| **Force-merged - review** (red) | An operator used Force Merge. The checks were bypassed on purpose. | Check the audit log entry and the reason written. Check the packet page count (section 6). |
| **Merged - packet missing** | The database says merged, but the packet file is not in the output folder. | [TO CONFIRM: what the operator does; call the on-site contact. Do not press Force Merge.] |
| **Merged • customs not yet in packet** / **🛃 Customs append pending** | The set is merged, the customs switch is ON, and the customs and shipping pages are not in the packet yet. The retry runs at the next sync (usually midnight). | Upload both files if missing (section 4, scenario 2). Otherwise close any PDF viewer that has the packet open, then wait for the next sync. |

### Pending reasons (shown on a Pending row)

| Reason (code) | What it means | What you do |
|---|---|---|
| "Reconciled; waiting for more files to arrive" (`settling`) | Everything agrees. The set was started by the input watcher and its newest file is less than the settle time old (default 5 minutes). | Nothing. It merges by itself on a later pass, unless another file arrives first. |
| No delivery note / no invoice yet (`missing_dn_document`, `missing_si_document`) | The PO is there but one side is missing. | Wait for the file. If it never comes, tell the on-site contact. |
| Document not yet read (`document_not_yet_read`) | A file is still being read by the AI. | Wait a few minutes. If it stays "Not read" for a long time, see section 8. |
| Partial delivery (`partial_fulfillment`) | A PO line has no delivery or invoice line yet. | Wait for the rest of the documents. This is a wait, not a pass. |
| Two PO documents in the set (`multiple_po_documents`) | The set holds more than one PO file. The app will not add them up. | Open the set. Find the extra PO file. Decide with the on-site contact (see section 5 on the Manual Merger). |
| Confirm it belongs here (`placed_by_number_only`) | A delivery note was placed in this set only because its DN number matches. It has no PO number of its own. | Open the set. Check the document. If it really belongs, press **✔ Confirm it belongs here** on that document row. If not, do not merge it. |
| Packet write failed (`packet_write_failed`) | The output folder could not be written (disk full, folder locked, permissions). | [TO CONFIRM: operator action; the set retries on the next sync.] Tell the on-site contact. |

### Quarantine reasons (shown on a Quarantined row)

| Reason (code) | What it means | What you do |
|---|---|---|
| Document extraction failed (`document_extraction_failed`) | A file in the set could not be read. | Press **⚡ Re-Extract with AI** on the set. If it fails again, check the log (section 8). |
| Unmatched vendor line (`unmatched_vendor_line`) | A delivery note or invoice line matches no PO line. | Compare the line with the PO. If the vendor sent something not on the PO, do not merge. |
| No line items (`po_document_has_no_line_items`, `vendor_document_has_no_line_items`, `no_line_item_evidence`) | A PO, delivery note or invoice was read with no lines. | Press **Re-Extract with AI**. If it is still empty, check the file itself. |
| Line with no number and no description (`po_line_missing_line_item_no`) | A PO line cannot be identified. | Check the PO file. Do not merge it by hand. |
| PO number mismatch (`po_reference_mismatch`) | A document prints a different PO number from the set. | Find the wrong file. Do not Force Merge it. |
| Duplicate document number (`duplicate_document_number`) | Two delivery notes (or two invoices) carry the same number. This is often a re-scan. | Check whether one is a copy. [TO CONFIRM: how to remove a copy from a set.] |
| Non-positive quantity (`non_positive_quantity`) | A quantity is zero, negative or unreadable. | Check the printed quantity. Re-Extract if the read was wrong. |
| Packet could not be named (`packet_naming_failed`) or source unreadable (`packet_source_unreadable`) | The packet name clashes with another set's packet, or a PDF is missing, damaged or locked. | Check the file. Ask for an unlocked copy. Tell the on-site contact. |
| Unclassified document in set (`unclassified_document_in_set`) | A file with no type is attached to the set. | Open the file, set its type, or move it out. [TO CONFIRM: how.] |

### Held documents (Unclassified page, not a set yet)

| Reason | What it means | What you do |
|---|---|---|
| No PO number (`missing_po_number`) | The app cannot find a PO number on the file. | Open the file. Type the PO number in the box and press **Save & Route**, if you can read it. |
| Ambiguous PO number (`ambiguous_po_number`) | The file prints more than one PO number. | Decide which one is right. Do not guess. |
| PO letters conflict (`po_letters_conflict`) | The digits match an open set, but the letters differ, so they are probably different POs. | Do not place it on the set by hand unless you are sure. Ask the on-site contact. |

---

## 4. Customs and shipping documents

The customs switch is a **per-set** switch. It is called **Customs Check** on the set page.
It is OFF by default. It needs **both** a CUSTOMS file and a SHIPPING file. Extra files are allowed.

**Rules:**

- Decide before the merge whether the PO needs customs. Turn the switch ON first.
- Check that the file is the right document before you upload. The app does **not** read or check the content.
- Press **Merge Now** to finish a customs set. Do **not** press **Force Merge** for customs.
- The **Remove** button (on the document row) takes out a wrong CUSTOMS or SHIPPING file, **before** the merge only. After the merge it is not shown. Upload the right file afterwards.

### Scenario 1: customs is needed before the set merges

1. Open the set. Press **🛡️ Customs Check** so it shows **ON**. The status does not change.
2. When PO, delivery note and invoice match, the set shows **🔒 Awaiting Customs**. It does not merge yet.
   If the set has a mismatch or a quarantine, that status stays. The customs gate never hides it.
3. Upload the CUSTOMS file and the SHIPPING file with **Attach Supporting Document (Customs / Shipping)**.
   Choose the type in the drop-down, then press **Upload & Attach**. Check the file names first.
4. If you attached the wrong file, press **Remove** on that document row (set not merged yet). Then upload the right one.
5. Press **Merge Now**. All checks still apply. The packet is written and the set becomes **Merged & Ready**.
6. If you forget **Merge Now**, the next sync merges it (a manual scan, the nightly run, or a sync started by new files).
   Uploading does **not** start a sync, and the watcher does not wake for a set that is only waiting.
   So you must press **Merge Now** yourself.

### Scenario 2: the set has already merged, then customs is needed

1. The set is already merged. Its packet has no customs pages. The page shows **Merged • customs not yet in packet**.
2. Press **Customs Check** so it shows **ON**. The status stays **Merged**.
3. Upload CUSTOMS and SHIPPING. After the first one nothing happens. When the second one is attached,
   the packet is rebuilt in place. The audit log gets a `customs_append` row. The merge date does not change.
   If both files were uploaded earlier, pressing the switch rebuilds the packet.
4. If the rebuild fails (for example, a PDF viewer has the packet open and locks it), the set keeps
   **Customs append pending**. Close the viewer and do nothing else. The retry runs at the next sync.
   The watcher does not wake for this, so in practice that is the nightly run, unless someone presses **Scan Input Folder**.
5. Once a set is merged, its customs files **cannot be removed or replaced** in the app (you will get a refusal).
   Only rebuild if nobody has used the first version of the packet. If in doubt, ask the on-site contact first.

Other customs notes:

- If you turn the switch OFF after a customs append, the customs pages stay in the packet. Every switch change is audited.
- There is no pre-customs copy of a packet [TO CONFIRM: approved way to keep one].

---

## 5. Buttons and tools

Set page buttons (open a set from its PO number):

| Button | What it does | When to use it |
|---|---|---|
| **Merge Now** | Re-checks the set. Merges it **only if every check passes**. Nothing is written if it is not ready. | After you finish a manual step (for example, customs uploads). It is safe to press at any time. |
| **🔄 Re-Match (Instant)** | Re-runs the matching rules on the data already read. No AI, no cost. | After a fix that does not need a re-read. |
| **⚡ Re-Extract with AI** | Sends every file in the set to the AI again. Costs money. The set goes back to **Pending** first. | When a file was read wrongly, or extraction failed. Not for routine checks. |
| **⚠️ Force Merge** | Merges the set **without** the matching and customs checks. It packs whatever files exist now. It writes an audit row. If you write a reason, it must be at least 20 characters. | Only when the on-site contact has agreed, and you can write a true reason. Never for customs. Never to clear a mismatch you have not understood. |
| **📦 Archive Quarantined PO Set** | Moves a quarantined set out of the active lists. Nothing is deleted: the files and rows are kept and an export goes to the archived sets folder. Only shown on a quarantined set. | Only when the set is not needed. Archive is not Delete. |
| **Preview** (document row) / **Download Merged PDF** | Preview opens the PDF in a drawer. Download gets the packet. | Count pages with Preview. Download the packet to check it. |
| **✔ Confirm it belongs here** (document row) | Confirms a delivery note that was placed only by its number. Audited. | Only after you have checked it really belongs to this PO. |

Dashboard: **⚡ Scan Input Folder** starts a sync now (needs the Prefect server). **🧩 Merge PDFs** opens the Manual Merger.

**Force Merge rules:** it bypasses matching and the customs check, so a wrong quantity can merge this way.
Write the real reason, tell the on-site contact the same day [TO CONFIRM: who approves], and check the packet afterwards.

**Re-Extract:** the AI may read a document type differently and replace the type you set by hand. A failed re-read leaves the set **Pending**, not unchanged.

**Settings (⚙️ Settings):** shows how the service is set up and lets you change the safe settings (folder watching, wait time before merging,
the AI model and its limits, matching rules, lock time, log file). Each row says when a change takes effect: **Live**, **Next sync**
or **Needs restart**. Every value is checked before it is saved; a wrong value is refused with a message beside it and nothing changes.
A copy of the previous file is kept in the `config_backups` folder next to `config.yaml` (newest 20), and each save is in the Audit Log.
Folders, the database, allowed host names and the midnight schedule are shown under **Fixed by IT** and are not changed here.
Details: `docs/SETTINGS_SPEC.md`.

**Manual Merger (Merge PDFs):** builds a packet by hand, with no AI and nothing saved to the database or output folder.
It gives a **browser download** only [TO CONFIRM: where the file is saved and how it is recorded]. It refuses locked or password-protected PDFs.

---

## 6. First-week supervision (pilot rules)

The first week is supervised. **Nobody runs the system unattended.** A one-digit PO misread on a continuation page is not detected by the app, so your page check is the protection.

**After each merged packet:**

1. Open the packet (**Download Merged PDF**).
2. Count the pages of the packet.
3. Count the pages of each source file. Use **Preview** on each document row, or open the source PDF.
4. The packet page count must equal the sum of the source pages: every PO, delivery note and invoice in the set, plus customs and shipping when they are on. Pages are copied whole.
5. Check that the PO number on the packet is the number you expect.
6. If something is missing or the PO number is wrong, **stop**. Do not send the packet. Go to the next step.

**If a PO was read wrongly:**

1. Do not Force Merge.
2. Build the correct packet with the **Manual Merger** (section 5).
3. Tell the on-site contact the same day and record it (below).

**What to record** (a suggested notebook, not produced by the app; kept at [TO CONFIRM: location]):

- Each merged packet: date, PO number, packet pages, source pages, OK or not.
- Each Force Merge: PO number, the reason written, who agreed.
- Each customs set: PO number, switch ON or OFF, both files attached yes or no.
- Each unresolved set: PO number, reason shown, what you tried, who you asked.

---

## 7. Things never to do

- Do **not** delete files by hand from the data folders (`data\stored`, `data\output`, `data\combined`, `data\quarantine`). The database rows point at those files.
- Do **not** run `scripts\reset_data.ps1` on the pilot PC. It destroys the database and all ingested and generated files.
- Do **not** put the database on a network drive, or share the whole `data` folder. Share only `data\input`, if at all.
- Do **not** use Force Merge for customs, and do not use it to clear a mismatch you have not understood.
- Do **not** change `config.yaml` or `.env` without the on-site contact. After a change to the poll or settle settings, restart the web service. After a change to `vlm:` settings, re-run `scripts\deploy_prefect.py`.
- Do **not** expose port 8000 to the internet. There is no login. Anyone on the office network who can reach the app can press every button.
- Do **not** use a personal OpenRouter key. The pilot uses the tester key in `.env`.
- Do **not** rebuild a packet that may already have been sent. A customs append overwrites it in place.

---

## 8. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Other PCs cannot open the dashboard | The firewall rule for port 8000 is missing, or the office network has changed. | On the server, re-run the installer with `-OpenFirewall`. Check the office network. |
| The dashboard opens but buttons do nothing | The browser is showing old pages. | Press **Ctrl+F5** to hard-refresh. |
| "Sync already running" (409) | Someone else pressed Scan. | Wait for the spinner to finish. Clicking twice does not duplicate work. |
| Documents stay "Not read" or "Failed" | The server has no internet, or the OpenRouter key is out of quota. | Check `data\logs\aam_merger.log`. Tell the on-site contact. |
| Log says `OPENROUTER_API_KEY not set` | `.env` is missing or not in the project folder. | Fix `.env`. Restart `AAMMerger-Web`. |
| Nightly sync does not run, or a manual Scan fails | The Prefect server is not running, or the work pool is missing. | Check `AAMMerger-PrefectServer` is Running. Re-run the installer. |
| Watcher stalls; the log warns about a lock every two minutes | A hung sync still holds the lock. | Restart `AAMMerger-Web`. [TO CONFIRM: who restarts it.] |
| Set is "Customs append pending" | The rebuild failed, often because a viewer has the packet open. | Close the viewer. Wait for the next sync. |
| Packet is missing a page | A continuation page was read with a one-digit error in the PO number (not detected by the app). The complete set can merge without that page. | Check every packet's page count (section 6). Repair with the Manual Merger. |
| A set reads "Merged" but the PO is right and lines look wrong | Two rows with the same line number and quantity can describe different items. The app cannot see that. | Compare the descriptions by eye against the printed PO. |
| A document was placed under the wrong PO | The Unclassified placement cannot be undone in the app. | If the set is quarantined, archive it and add the files again. If it only sits Pending, tell the on-site contact. |
| HTTP 400 from the AI provider, and the log says the batch halted | The provider refused the requests, often because of a settings or model problem, not the files. Nightly processing pauses for an hour. | Tell the on-site contact. Do not re-send the files again and again. |
| Settings page shows a red "settings file on disk is not valid" banner | `config.yaml` was edited by hand (or restored) and no longer passes the checks. The service keeps running on the last good settings. | Tell IT. They fix the line named in the banner, or copy the newest file from `config_backups` over `config.yaml`. Do not restart the service until it is fixed: a restart on a broken file will not start. |
| Service restarted but the log says "STARTED on the newest valid backup" (and Settings shows the red banner) | `config.yaml` was broken when the service started, so it is running on the newest good copy in `config_backups`. Recent settings changes made after that backup are not active. | Tell IT. They repair `config.yaml` (or copy the newest `config_backups` file over it) and restart. If no backup is valid the service will not start at all. |
| Settings page says "changed by someone else" | Two people had the page open. | Nothing was lost: open Settings again and redo your change. |
| Dashboard reachable under an unexpected host name | The app accepts any host name unless `server.allowed_hosts` is set. | Tell the on-site contact to set it (DELIVERY section 4). |

---

## 9. Before a large run (hundreds of files)

- **Set the PC to never sleep.** The installer does this on AC power. A sleeping PC stops the poller, the midnight run and LAN access.
- **Smoke-test with one file first.** Use the paid key, send one PDF, and watch the credit balance on the OpenRouter account. A missing key or used-up credit makes every document wait through retries, and that can waste hours.
- **Copy first, then scan.** Copy all the PDFs into the input folder, then press **Scan Input Folder**. Do not let files trickle in during the pass.
- **Expect "a sync is running" (409).** While the pass runs, operator actions such as Force Merge, Remove or Archive may answer "a sync is running". That is expected. Wait until the pass ends, then review.
- **Switch off the folder watcher while reviewing.** If the watcher is on, turn it off on the Settings page while you review.
- **Files are read oldest first.** The app reads each PDF in order of its own last-modified time, oldest first. A copy that keeps the source's time is read in the source's place in that order.

### How files should arrive (email downloads)

- Save the PDFs from the email, or extract the ZIP first. A `.zip` file placed in the input folder is **not** read: only `.pdf` files are. Extract it and copy the PDFs.
- Then copy or move the PDFs into the input folder. Moving within the same disk is instant and safe.
- A copy or an extraction is picked up only after the file has been left alone for at least 30 seconds. The program looks at both the modified time and the creation time, so an extracted old file still waits.
- Copy the whole batch in one go, then wait about a minute before pressing **Scan Input Folder**.
- Do not press Scan or expect a result while files are still being copied.
- A file skipped as "still copying" is picked up on the next scan. Nothing is lost.

---

## 10. Known open items

These are known and not yet fixed.

- **Force Merge on a set that is already merged** returns OK and writes a second audit row.
- **An orphan sync marker** can block operator actions for up to 20 minutes unless **Scan Input Folder** is pressed.
- **A document left mid-read** waits up to 15 minutes before it is read again.
- **A stray `.tmp` file** can remain in the output folder after a killed write. It is harmless; delete it.
- **A ZIP file in the input folder is ignored; extract it first.**

---

## 11. Who to call

| Problem | Who | Contact |
|---|---|---|
| Anything on the screen or a packet you do not understand | On-site contact: [TO CONFIRM: on-site contact name] | [TO CONFIRM: phone or chat] |
| Services not running, server down, network, firewall | IT / server admin: [TO CONFIRM: IT contact name] | [TO CONFIRM: phone or email] |
| OpenRouter key, quota, billing | [TO CONFIRM: key owner] | [TO CONFIRM] |
| Software bug, wrong merge, anything you cannot solve | [TO CONFIRM: developer / product contact] | [TO CONFIRM] |
| Approval for Force Merge | [TO CONFIRM: approver] | [TO CONFIRM] |
| Pilot schedule and who checks the first week | [TO CONFIRM: pilot lead] | [TO CONFIRM] |

When you call, give the **PO number**, the **status and reason** shown on the row, and the time.
