# User guide — testing the AAM Merger

This tool collects purchase orders (PO), delivery notes (DN) and sales
invoices (SI) and builds **one merged PDF per PO**. It checks that the
quantities on the DN and the SI add up to what the PO ordered, and shows you
every step on a dashboard.

It is an MVP under test. Please use it with real documents, and report anything
that looks wrong — especially a set that merges when the numbers do not match.

---

## 1. Open it

Open this address in Chrome or Edge:

```
http://<server-ip>:8000/dashboard
```

There is no login. Everyone in the office sees the same dashboard and the same
documents. The tool runs on one PC; your browser only displays it.

---

## 2. Put documents in

Copy your PDFs into the shared intake folder (for example
`\\<server-name>\AAM-Input`), or ask whoever runs the server to put them in
`data\input`.

Rules that matter:

- **One document per file.** Do not split one PO into one file per page — the
  tool will treat the pages as separate POs and quarantine the set.
- **PDFs only.** The filename is not used as evidence; only what is printed on
  the page counts.
- A document that has already been read is never read again, so re-copying a
  file is harmless.

---

## 3. Scan

Press **⚡ Scan Input Folder** on the dashboard.

- The first scan reads new documents with an AI vision model; that takes a few
  seconds per document.
- Later scans are fast and free — already-read files are skipped.
- If someone else is scanning, you get "Sync already running"; just wait.
- The table refreshes itself; you do not need to reload the page.

---

## 4. The five statuses

| Status | What it means | What to do |
|---|---|---|
| **Pending** | Waiting for the other documents, or not evaluated yet | Wait, or scan again after the missing document arrives |
| **Merged & Ready** | PO = DN total = SI total; the packet is written | Download it from the set's page |
| **Needs Review** | The quantities do not add up | Open the set, read the matrix, fix the paperwork or Force Merge with a note |
| **Awaiting Customs** | The customs/shipping requirement is switched on but documents are missing | Upload the customs and shipping PDFs on the set page |
| **Quarantined** | A critical problem (e.g. zero quantity, two POs in one set) | Read the reason on the Quarantine page |

---

## 5. Reviewing a PO set

Click a row to open the set. You will see the **3-way reconciliation matrix**
(PO / DN / SI by line number) and the list of attached documents.

The buttons on a set:

- **📦 Merge Now** — re-checks everything and merges if the set is now ready.
  Safe to press at any time: if something is still missing it simply tells you
  what.
- **🔄 Re-Match (Instant)** — re-runs the comparison on data already read. Free
  and instant. Use after correcting a document.
- **⚡ Re-Extract with AI** — sends the documents to the AI again. Costs one
  model call per document. Use only when the extracted lines look wrong.
- **🛡️ Customs Check** — turns the customs + shipping requirement on or off for
  this set. It can also be switched on **after** the set is merged: upload both
  documents and the delivered packet is rebuilt so they are included. The set
  stays "Merged & Ready" throughout.
- **⚠️ Force Merge** — overrides the quantity and customs checks and merges
  anyway. Add the reason in the confirmation box (at least 20 characters); it
  is written to the audit log. **Use it only when you are sure the paperwork is
  right but the tool cannot see it.** A forced packet looks exactly like an
  automatic one afterwards.

Two people can work at once: while someone is acting on a set, the set is
locked and other buttons are disabled. A "locked" message is not an error.

---

## 6. Documents the tool could not place

The **❓ Unclassified** page is the holding area. It lists documents that were
read but could not be attached to a PO, or could not be identified at all.

- Preview the PDF.
- Choose the document type.
- Type the PO number it belongs to (the normalized form is shown to you as you
  type) and press **Save & Route**.

A document waiting there is not lost and is not an error. It simply needs a
human to say which PO it belongs to.

---

## 7. Quarantine

The **🚨 Quarantine** page lists sets that failed a critical check and documents
that could not be read at all. Each row states the reason in plain language.

Deleting a quarantined set removes its database records **but keeps the files**
and writes an audit entry. If you need the documents again, they are still on
disk — ask the server operator.

---

## 8. What testers should know

These are deliberate limits of the current version, not bugs:

- **Quantities are the only thing compared.** Prices, item descriptions, UOM and
  SKU are not compared. Two different items on the same line number with the
  same quantity would be treated as equal — so a human review of every merge is
  required, not optional.
- **One PO per set.** A second PO document under the same number quarantines the
  set rather than doubling the quantities.
- **Content only.** A promising filename is ignored on purpose.
- **A document with no readable PO number waits** in the holding area for a
  human; it is not quarantined.
- **Successful extraction is cached.** Re-copying an already-read file does not
  call the AI again.

---

## 9. Reporting a problem

Please include:

1. the PO number (or the set's row) and the document filenames;
2. what you expected and what the tool did;
3. a screenshot of the set page or the dashboard row;
4. roughly when it happened (so the log can be checked).

Send it to the person running the server — they have the logs and the database
backups.

Do not treat the merged packets as the system of record during the test. Keep
your own copies.

---

## 10. Quick answers

**Why is my new document not showing?** Press Scan; the table also refreshes
every few seconds, but a scan is what reads new files.

**Why did nothing happen when I clicked?** If the page was open a long time,
hard-refresh with Ctrl+F5.

**Why does a set say "Needs Review" when I know the quantities match?** Open it
and read the matrix. The usual causes are a missed line, a page split into
several files, or a quantity the AI misread — use Re-Extract if the line looks
wrong.

**Can two people merge different sets at the same time?** Yes. The lock is per
PO set, not global.
