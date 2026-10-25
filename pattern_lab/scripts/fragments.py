import sqlite3
from collections import defaultdict

DB = r"C:\Users\malliath\Desktop\aam_merger\pattern_lab\db\aam_merger_lab.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

print("=== fragment pages of the same doc number: disjoint lines or repeats?")
sets = [
    r["id"] for r in con.execute(
        """select s.id from po_sets s join documents d on d.po_set_id=s.id
           group by s.id
           having sum(d.doc_type='PO')>1 or sum(d.doc_type='DN')>1 or sum(d.doc_type='SI')>1"""
    )
]
for sid in sets:
    docs = con.execute(
        """select id, doc_type, dn_no, si_no, original_filename from documents
           where po_set_id=? order by doc_type, id""", (sid,)
    ).fetchall()
    print(f"\n--- set {sid}")
    groups = defaultdict(list)
    for d in docs:
        key = (d["doc_type"], d["dn_no"] or d["si_no"] or "")
        groups[key].append(d)
    for (dt, num), members in groups.items():
        if len(members) < 2 and dt != "PO":
            continue
        print(f"  {dt} {num or '(no number)'} -> {len(members)} files")
        seen = {}
        for m in members:
            lines = [
                (r["line_item_no"], r["quantity"])
                for r in con.execute(
                    "select line_item_no, quantity from line_items where document_id=? order by id",
                    (m["id"],),
                )
            ]
            keys = [ln for ln, _ in lines]
            dup_here = [k for k in set(keys) if keys.count(k) > 1]
            overlap = [k for k in set(keys) if k in seen]
            print(
                f"    doc{m['id']:<4} {m['original_filename']:<32} "
                f"n={len(lines):<3} keys={keys} "
                f"{'DUP_IN_DOC=' + str(dup_here) if dup_here else ''}"
                f"{' OVERLAP_PREV=' + str(overlap) if overlap else ''}"
            )
            for k in keys:
                seen[k] = m["id"]
