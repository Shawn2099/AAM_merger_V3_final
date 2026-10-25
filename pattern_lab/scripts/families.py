import json
import re
import sqlite3
from collections import Counter, defaultdict

DB = r"C:\Users\malliath\Desktop\aam_merger\pattern_lab\db\aam_merger_lab.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

docs = con.execute(
    "select id, doc_type, original_filename, po_set_id, po_no_normalized, dn_no, si_no, raw_extraction_json "
    "from documents order by id"
).fetchall()

print("=== filename prefix families (operator naming) vs extracted vendor_name")
fam = Counter()
fam_vendor = defaultdict(Counter)
for d in docs:
    m = re.match(r"([A-Za-z]+)-([A-Za-z]+)-", d["original_filename"])
    key = "-".join(m.groups()) if m else re.match(r"([A-Za-z]+)", d["original_filename"]).group(1)
    vn = (json.loads(d["raw_extraction_json"]).get("vendor_name") or "").strip()
    fam[(key, d["doc_type"])] += 1
    fam_vendor[key][vn] += 1
for (k, dt), n in sorted(fam.items()):
    print(f"  {k:<10} {dt:<3} {n}")
print("\n  prefix -> vendor_name values seen:")
for k, c in sorted(fam_vendor.items()):
    print(f"   {k}:")
    for v, n in c.most_common(4):
        print(f"      {n:>3}  {v}")

print("\n=== line-number style per filename prefix (PO docs only)")
style = defaultdict(Counter)
for d in docs:
    if d["doc_type"] != "PO":
        continue
    m = re.match(r"([A-Za-z]+)-([A-Za-z]+)-", d["original_filename"])
    key = "-".join(m.groups()) if m else "?"
    keys = [
        r["line_item_no"]
        for r in con.execute(
            "select line_item_no from line_items where document_id=? order by id", (d["id"],)
        )
    ]
    if not keys:
        style[key]["(no lines)"] += 1
        continue
    if all(k and k.isdigit() and int(k) % 10 == 0 for k in keys):
        style[key]["steps of 10"] += 1
    elif any(k and re.fullmatch(r"\d+-1", k) for k in keys):
        style[key]["N-1 release"] += 1
    elif all(k and k.isdigit() for k in keys):
        style[key]["consecutive digits"] += 1
    else:
        style[key]["mixed/odd"] += 1
for k, c in sorted(style.items()):
    print(f"  {k:<10} {dict(c)}")

print("\n=== page_count evidence vs line-item presence")
print("  docs with page_count > 1 (model claims multi-page source):")
for d in docs:
    j = json.loads(d["raw_extraction_json"])
    pc = j.get("page_count")
    if pc and pc > 1:
        n = con.execute("select count(*) from line_items where document_id=?", (d["id"],)).fetchone()[0]
        print(f"   doc{d['id']:<4} {d['doc_type']} page_count={pc:<3} components={len(j.get('components') or [])} "
              f"lines={n:<3} set={d['po_set_id']} {d['original_filename']}")

print("\n=== documents with components == [] (no component reported)")
for d in docs:
    j = json.loads(d["raw_extraction_json"])
    if not j.get("components"):
        n = con.execute("select count(*) from line_items where document_id=?", (d["id"],)).fetchone()[0]
        print(f"   doc{d['id']:<4} {d['doc_type']} page_count={j.get('page_count')} lines={n} "
              f"set={d['po_set_id']} {d['original_filename']}")

print("\n=== PO docs with zero line items")
for d in docs:
    if d["doc_type"] != "PO":
        continue
    n = con.execute("select count(*) from line_items where document_id=?", (d["id"],)).fetchone()[0]
    if n == 0:
        j = json.loads(d["raw_extraction_json"])
        print(f"   doc{d['id']:<4} set={d['po_set_id']:<3} page_count={j.get('page_count')} {d['original_filename']}")

print("\n=== line_items.dn_no (per-line DN reference) usage")
for r in con.execute(
    "select count(*) n, sum(dn_no is not null and dn_no<>'') used from line_items"
):
    print(f"  rows={r['n']} with_dn_no={r['used']}")

print("\n=== description_match_count on po_sets")
for r in con.execute(
    "select description_match_count d, count(*) n from po_sets group by 1"
):
    print(f"  count={r['d']} -> {r['n']} sets")
