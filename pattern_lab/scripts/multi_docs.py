import sqlite3

DB = r"C:\Users\malliath\Desktop\aam_merger\pattern_lab\db\aam_merger_lab.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

q = """
select s.id set_id, s.status, s.po_no_normalized,
       d.id doc_id, d.doc_type, d.po_no_raw, d.po_no_normalized, d.dn_no, d.si_no,
       d.original_filename, d.extraction_attempt_count,
       (select count(*) from line_items li where li.document_id=d.id) lines
from po_sets s join documents d on d.po_set_id=s.id
order by s.id, d.doc_type, d.id
"""
rows = con.execute(q).fetchall()
by = {}
for r in rows:
    by.setdefault(r["set_id"], []).append(r)

for sid, docs in by.items():
    status = docs[0]["status"]
    pos = [d for d in docs if d["doc_type"] == "PO"]
    dns = [d for d in docs if d["doc_type"] == "DN"]
    sis = [d for d in docs if d["doc_type"] == "SI"]
    if len(pos) <= 1 and len(dns) <= 1 and len(sis) <= 1:
        continue
    print(f"\n--- set {sid} [{status}] po_key={docs[0]['po_no_normalized']}")
    for group, label in ((pos, "PO"), (dns, "DN"), (sis, "SI")):
        for d in group:
            print(
                f"   {label} doc{d['doc_id']:<4} lines={d['lines']:<3} "
                f"po_raw={str(d['po_no_raw']):<18} dn={str(d['dn_no']):<20} si={str(d['si_no']):<22} "
                f"file={d['original_filename']}"
            )
