import json
import sqlite3
from collections import Counter

DB = r"C:\Users\malliath\Desktop\aam_merger\pattern_lab\db\aam_merger_lab.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

rows = con.execute(
    "select id, doc_type, original_filename, raw_extraction_json from documents"
).fetchall()

top_keys = Counter()
line_keys = Counter()
doc_keys = Counter()
none_json = 0
parse_fail = 0
samples = {}

for r in rows:
    raw = r["raw_extraction_json"]
    if not raw:
        none_json += 1
        continue
    try:
        j = json.loads(raw)
    except Exception:
        parse_fail += 1
        continue
    if not isinstance(j, dict):
        top_keys[f"<{type(j).__name__}>"] += 1
        continue
    doc_keys[tuple(sorted(j))] += 1
    for k in j:
        top_keys[k] += 1
    for li in j.get("line_items") or []:
        if isinstance(li, dict):
            line_keys[tuple(sorted(li))] += 1
    samples.setdefault(r["doc_type"], j)

print(f"rows={len(rows)} no_json={none_json} parse_fail={parse_fail}")
print("\n=== top-level key sets")
for keys, n in doc_keys.most_common():
    print(f"  {n:>4}  {keys}")
print("\n=== top-level key frequency")
for k, n in top_keys.most_common():
    print(f"  {k:<24} {n}")
print("\n=== line-item key sets")
for keys, n in line_keys.most_common():
    print(f"  {n:>4}  {keys}")

for dt, j in samples.items():
    print(f"\n=== sample {dt}")
    print(json.dumps(j, indent=2)[:1800])
