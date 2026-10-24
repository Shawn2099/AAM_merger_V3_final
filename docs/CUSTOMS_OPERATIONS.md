# Customs and shipping documents: how it works and what the operator does

Status: 2026-10-25, reviewed by Opus against the code (see DEPLOYMENT_PLAN D19, D20). Source of truth for the runbook (WS7).
Code: `services/customs.py`, `services/merge.py`, `services/reconciliation.py`, `api/routes/po_sets.py`, `api/routes/dashboard.py`.

## Facts
- The requirement is a **per-set** switch (`Customs Check` button on the set page), default OFF. There is no global setting.
- Needs BOTH a `CUSTOMS` and a `SHIPPING` document. Extra documents are allowed.
- The files are attached as-is: no AI reading, no content check, no check that they belong to that PO. The app only checks that
  the upload is a readable PDF within the size cap, and that the same bytes are not already attached to another set.
- Uploaded files are stored in `stored_documents_folder/<sha256>.pdf` and are never deleted. They are not written to the input
  folder. Only the merged packet goes to `output_folder`.
- Packet name: first SI invoice number, else the PO number. Page order: `merge.legal_order` from the config, default
  SI, DN, PO, SHIPPING, CUSTOMS; documents of the same type go by id. Pages are copied whole.
- The packet is always rebuilt IN PLACE (temporary file, then `os.replace`). **No copy of a pre-customs packet is kept.**
- Customs documents never take part in the quantity check.

## Scenario 1: customs is turned on before the set merges
1. Open the set, press **Customs Check** (now ON). Status does not change.
2. When PO, DN and SI match, the set shows **Awaiting Customs** (`blocked_customs`) instead of merging. A mismatch or quarantine keeps
   its own status; the customs gate never hides it.
3. Upload the CUSTOMS file and the SHIPPING file (Attach Supporting Document). Check the file names first.
   A wrong file can be taken out with **Remove** (set not merged yet), then upload the right one.
4. Press **Merge Now**. All checks still apply. The packet is written, the set becomes `merged`, the input copies are removed.
   Do not use Force Merge: it skips the quantity and customs checks and shows the set as "Force-merged".
5. If Merge Now is forgotten, the next sync (manual Scan, nightly run, or a sync started by new input files) merges it.
   Uploading does NOT start a sync, and the poller does not wake for a set that is only waiting, so expect to press Merge Now.
   A sync started by the poller also waits out the settle time after the newest upload.

## Scenario 2: the set has already merged, then customs is turned on
1. A packet without customs pages exists in the output folder.
2. Press **Customs Check** (ON). The status stays `merged`. The page shows "Merged • customs not yet in packet".
3. Upload CUSTOMS and SHIPPING. After the first one nothing happens. When the second is attached the packet is rebuilt
   automatically (or, if both were uploaded earlier, pressing the toggle rebuilds it). Audit row `customs_append`; `merged_at` unchanged.
4. If the rebuild fails (for example the packet is open in a viewer that locks the file) the set keeps "customs append pending".
   Close the viewer and press nothing: the next sync retries. The poller does not wake for it, so in practice that is the nightly run
   unless someone presses Scan.
5. Once merged, the customs files cannot be removed or replaced in the app (409). Rebuilding overwrites a packet that may already
   have been sent, so only do it if nobody has used the first version.

## Rules for the operator
- Decide before the merge whether the PO needs customs, and turn the switch on first (Scenario 1). It writes the packet once.
- Check the file before uploading; there is no content check.
- Keep Customs Check ON while replacing a file; with it OFF a later upload does not rebuild a packet.
- Turning the switch OFF after an append leaves the customs pages in the packet. Nothing rebuilds it without them.
- Turning the switch on or off is not audited (known limitation, `test_known_limitation_customs_toggle_writes_no_audit_row`).

## Known limits (accepted)
- No copy of the pre-customs packet.
- A delivered packet's customs file cannot be replaced (`test_known_limitation_delivered_packet_customs_cannot_be_replaced`).
- No content check on the uploaded file.
- The retry of a failed rebuild runs at the next sync, usually midnight.
