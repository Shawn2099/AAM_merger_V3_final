import re
import sqlite3
from collections import Counter

DB = r"C:\Users\malliath\Desktop\aam_merger\pattern_lab\db\aam_merger_lab.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

print("=== distinct PO numbers per set where >1 PO doc exists")
for r in con.execute(
    """select po_set_id,
              count(*) n_po,
              count(distinct po_no_normalized) n_distinct_po,
              group_concat(distinct po_no_normalized) keys
         from documents where doc_type='PO' and po_set_id is not null
         group by po_set_id having n_po > 1 order by n_po desc"""
):
    print(f"  set {r['po_set_id']:<3} POdocs={r['n_po']:<2} distinct_po_keys={r['n_distinct_po']} -> {r['keys']}")

print("\n=== line_item_no shapes (all rows)")
SHAPES = [
    (r"^\d+$", "plain digits"),
    (r"^\d+\.\d+$", "decimal 1.1"),
    (r"^\d+-\d+$", "dash pair 1-1"),
    (r"^\d+/\d+$", "slash pair 1/1"),
    (r"^\d+\s*of\s*\d+$", "N of M"),
    (r"^[A-Za-z]+\d+$", "letter prefix"),
    (r"^\d+[A-Za-z]$", "letter suffix"),
    (r"^0\d{3,}$", "zero padded"),
    (r"^[A-Za-z]", "starts with letters"),
    (r"^\D", "starts non-digit"),
]
buckets = Counter()
samples = {}
for r in con.execute(
    """select li.line_item_no, d.doc_type from line_items li
       join documents d on d.id = li.document_id"""
):
    v = r["line_item_no"]
    if v is None:
        buckets[("NULL", r["doc_type"])] += 1
        continue
    matched = False
    for pat, label in SHAPES:
        if re.match(pat, v):
            buckets[(label, r["doc_type"])] += 1
            samples.setdefault((label, r["doc_type"]), v)
            matched = True
            break
    if not matched:
        buckets[("OTHER: " + repr(v)[:18], r["doc_type"])] += 1

for (label, dt), n in sorted(buckets.items(), key=lambda kv: (kv[0][1], -kv[1])):
    s = samples.get((label, dt), "")
    print(f"  {dt:<3} {label:<22} {n:<4} e.g. {s}")

print("\n=== zero-padded / leading-zero line numbers (would collapse if int-cast)")
for r in con.execute(
    """select distinct line_item_no from line_items
       where line_item_no glob '0*' order by 1 limit 40"""
):
    print("  ", r[0])

print("\n=== non-numeric line numbers, distinct")
for r in con.execute(
    """select line_item_no, count(*) n from line_items
       where line_item_no is not null and line_item_no not glob '[0-9]*'
       group by 1 order by n desc"""
):
    print(f"  {r[0]!r} x{r['n']}")

print("\n=== line numbers containing letters or punctuation")
for r in con.execute(
    """select line_item_no, count(*) n from line_items
       where line_item_no is not null and line_item_no glob '*[^0-9]*'
       group by 1 order by n desc"""
):
    print(f"  {r[0]!r} x{r['n']}")

print("\n=== unit_price sanity (schema still has the column)")
for r in con.execute(
    "select count(*) n, sum(unit_price=0) zero, sum(unit_price<0) neg, min(unit_price) mn, max(unit_price) mx from line_items"
):
    print(f"  rows={r['n']} zero={r['zero']} neg={r['neg']} min={r['mn']} max={r['mx']}")

print("\n=== quantity multiples (x1000 scaling check)")
for r in con.execute(
    "select sum(quantity%1000=0) mult1000, sum(quantity%1000!=0) non_mult, count(*) n from line_items"
):
    print(f"  multiple_of_1000={r['mult1000']} not={r['non_mult']} total={r['n']}")
for r in con.execute(
    "select distinct quantity from line_items where quantity%1000!=0 limit 20"
):
    print("   odd qty:", r[0])
