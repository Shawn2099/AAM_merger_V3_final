import json
import re
import sqlite3
from collections import Counter, defaultdict

DB = r"C:\Users\malliath\Desktop\aam_merger\pattern_lab\db\aam_merger_lab.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

print("=== po_sets.reconcile_reason distribution")
for r in con.execute(
    "select status, reconcile_reason, count(*) n from po_sets group by 1,2 order by n desc"
):
    print(f"  {r['status']:<12} {str(r['reconcile_reason'])[:70]:<72} {r['n']}")

print("\n=== per-set composition (status x doc-type multiset)")
comp = Counter()
for s in con.execute("select id, status from po_sets"):
    types = [
        r["doc_type"]
        for r in con.execute(
            "select doc_type from documents where po_set_id=? order by doc_type", (s["id"],)
        )
    ]
    comp[(s["status"], "+".join(types) or "(empty)")] += 1
for (st, c), n in sorted(comp.items(), key=lambda kv: (-kv[1], kv[0])):
    print(f"  {st:<12} {c:<28} {n}")

print("\n=== documents per set (count histogram)")
for r in con.execute(
    """select cnt, count(*) sets from (
         select po_set_id, count(*) cnt from documents
         where po_set_id is not null group by 1) group by 1 order by 1"""
):
    print(f"  {r['cnt']} docs/set -> {r['sets']} sets")

print("\n=== documents with NO po_set_id, by type/ambiguity")
for r in con.execute(
    """select doc_type, po_reference_ambiguous, (po_no_normalized is null) po_null,
              count(*) n
       from documents where po_set_id is null group by 1,2,3 order by n desc"""
):
    print(f"  {r['doc_type']} amb={r['po_reference_ambiguous']} po_null={r['po_null']} -> {r['n']}")

print("\n=== sets with more than one PO document")
for r in con.execute(
    """select d.po_set_id, count(*) n from documents d where d.doc_type='PO'
       group by 1 having n>1"""
):
    print(f"  set {r['po_set_id']}: {r['n']} PO docs")

print("\n=== sets by doc count of each type (multi-DN / multi-SI)")
for r in con.execute(
    """select po_set_id, sum(doc_type='DN') dns, sum(doc_type='SI') sis, sum(doc_type='PO') pos
       from documents where po_set_id is not null group by 1
       having dns>1 or sis>1 or pos>1 order by pos desc, dns desc"""
):
    print(f"  set {r['po_set_id']}: PO={r['pos']} DN={r['dns']} SI={r['sis']}")
