import json
import re
import sqlite3
from collections import Counter, defaultdict

DB = r"C:\Users\malliath\Desktop\aam_merger\pattern_lab\db\aam_merger_lab.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

docs = con.execute(
    "select id, doc_type, original_filename, po_no_raw, po_no_normalized, dn_no, si_no, "
    "po_set_id, raw_extraction_json from documents order by id"
).fetchall()

print("=== page_count from raw JSON")
pc = Counter(json.loads(d["raw_extraction_json"]).get("page_count") for d in docs)
print("  ", dict(pc))

print("\n=== components per document")
cc = Counter(len(json.loads(d["raw_extraction_json"]).get("components") or []) for d in docs)
print("  ", dict(cc))

print("\n=== section flags by doc_type (has_po/has_dn/has_si)")
fl = Counter()
for d in docs:
    j = json.loads(d["raw_extraction_json"])
    fl[(d["doc_type"], j["has_po_section"], j["has_dn_section"], j["has_si_section"])] += 1
for k, n in sorted(fl.items()):
    print(f"  {k} -> {n}")

print("\n=== po_no_raw shapes")
shapes = Counter()
for d in docs:
    v = d["po_no_raw"]
    if v is None:
        shapes["None"] += 1
    elif re.fullmatch(r"\d+", v):
        shapes["all digits"] += 1
    elif re.fullmatch(r"[A-Za-z]+\d+/\d+", v):
        shapes["LPO26/2360 style"] += 1
    else:
        shapes[f"OTHER {v!r}"] += 1
for k, n in shapes.most_common():
    print(f"  {k:<28} {n}")

print("\n=== PO numbers by length (normalized)")
ln = Counter(len(d["po_no_normalized"]) for d in docs if d["po_no_normalized"])
print("  ", dict(sorted(ln.items())))

print("\n=== po_no_raw that differ from po_no_normalized (digits stripped)")
for d in docs:
    raw, norm = d["po_no_raw"], d["po_no_normalized"]
    if raw and norm and re.sub(r"\D", "", raw) != norm:
        print(f"  doc{d['id']:<4} {d['doc_type']} raw={raw!r} norm={norm!r} file={d['original_filename']}")

print("\n=== duplicate PO numbers across DIFFERENT sets")
by_po = defaultdict(set)
for d in docs:
    if d["po_no_normalized"]:
        by_po[d["po_no_normalized"]].add(d["po_set_id"])
for po, sets_ in by_po.items():
    if len(sets_) > 1:
        print(f"  po {po} -> sets {sorted(sets_, key=str)}")

print("\n=== SI / DN number vs filename stem")
def stem(fn: str) -> str:
    return re.sub(r"\.pdf$", "", fn, flags=re.I)
same_si = same_dn = 0
si_diff = Counter()
for d in docs:
    st = stem(d["original_filename"])
    # strip the page suffix: -pages-N or -N
    base = re.sub(r"(-pages)?-\d+$", "", st)
    num = d["si_no"] if d["doc_type"] == "SI" else d["dn_no"] if d["doc_type"] == "DN" else None
    if num:
        if num == st or num == base:
            if d["doc_type"] == "SI":
                same_si += 1
            else:
                same_dn += 1
        else:
            si_diff[(d["doc_type"], num, base)] += 1
print(f"  SI number == packet filename base: {same_si}/{sum(1 for d in docs if d['doc_type']=='SI')}")
print(f"  DN number == packet filename base: {same_dn}/{sum(1 for d in docs if d['doc_type']=='DN')}")
print("  mismatches (first 12):")
for k, n in list(si_diff.items())[:12]:
    print(f"    {k} x{n}")

print("\n=== vendor_name per set (does it agree across the set?)")
agree = disagree = mixed = 0
for s in con.execute("select id from po_sets order by id"):
    names = {
        (json.loads(d["raw_extraction_json"]).get("vendor_name") or "").strip().lower()
        for d in con.execute(
            "select raw_extraction_json from documents where po_set_id=?", (s["id"],)
        )
    }
    names.discard("")
    if len(names) == 0:
        mixed += 1
    elif len(names) == 1:
        agree += 1
    else:
        disagree += 1
        print(f"  set {s['id']}: {sorted(names)}")
print(f"  agree={agree} disagree={disagree} empty={mixed}")

print("\n=== distinct vendor_name spellings, folded")
vn = Counter()
for d in docs:
    vn[(json.loads(d["raw_extraction_json"]).get("vendor_name") or "").strip()] += 1
for k, n in vn.most_common():
    print(f"  {n:>3}  {k}")
