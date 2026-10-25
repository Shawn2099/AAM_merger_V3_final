import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(r"C:\Users\malliath\Desktop\aam_merger")
sys.path.insert(0, str(ROOT / "src"))
from rapidfuzz import fuzz  # noqa: E402

from app.services.matching import normalize_line_no  # noqa: E402

con = sqlite3.connect(ROOT / "pattern_lab" / "db" / "aam_merger_lab.db")
con.row_factory = sqlite3.Row

print("=== RAK packets: customer token in filename vs PO line-number style")
for d in con.execute(
    "select id, original_filename, po_set_id from documents where doc_type='PO' "
    "and original_filename like 'SIV-RAK%' order by id"
):
    keys = [
        r["line_item_no"]
        for r in con.execute(
            "select line_item_no from line_items where document_id=? order by id", (d["id"],)
        )
    ]
    m = re.search(r"-(Ades|Northern|Noble|Ensco)", d["original_filename"], re.I)
    token = m.group(1) if m else "?"
    style = "none"
    if keys and all(k and k.isdigit() and int(k) % 10 == 0 for k in keys):
        style = "steps of 10"
    elif any(k and re.fullmatch(r"\d+-1", k) for k in keys):
        style = "N-1"
    elif keys and all(k and k.isdigit() for k in keys):
        style = "1..n"
    elif keys:
        style = "mixed"
    print(f"  set {d['po_set_id']:<3} {token:<9} {style:<12} {keys[:8]} {d['original_filename']}")

print("\n=== po_no_raw -> digits-key collisions (distinct raw, same digits)")
by_digits = defaultdict(set)
for d in con.execute("select distinct po_no_raw, po_no_normalized from documents"):
    if d["po_no_normalized"]:
        by_digits[d["po_no_normalized"]].add(d["po_no_raw"])
for k, v in by_digits.items():
    if len(v) > 1:
        print(f"  key {k}: {sorted(v)}")

print("\n=== description comparability (PO line vs its number-matched DN/SI line)")
scores = []
suffix = Counter()
for s in con.execute("select id from po_sets order by id"):
    docs = con.execute("select id, doc_type from documents where po_set_id=?", (s["id"],)).fetchall()
    po = {}
    for d in docs:
        if d["doc_type"] != "PO":
            continue
        for r in con.execute(
            "select line_item_no, description from line_items where document_id=?", (d["id"],)
        ):
            k = normalize_line_no(r["line_item_no"])
            if k:
                po.setdefault(k, r["description"])
    for d in docs:
        if d["doc_type"] == "PO":
            continue
        for r in con.execute(
            "select line_item_no, description from line_items where document_id=?", (d["id"],)
        ):
            k = normalize_line_no(r["line_item_no"])
            if not k or k not in po:
                continue
            p, v = po[k], r["description"] or ""
            scores.append(fuzz.token_sort_ratio(p.lower(), v.lower()))
            if re.search(r"line\s*item\s*-\s*\d+", v, re.I):
                suffix["has 'Line Item - N'"] += 1
            else:
                suffix["no suffix"] += 1

if scores:
    scores.sort()
    n = len(scores)
    buckets = Counter()
    for sc in scores:
        if sc >= 95:
            buckets[">=95"] += 1
        elif sc >= 85:
            buckets["85-95"] += 1
        elif sc >= 60:
            buckets["60-85"] += 1
        else:
            buckets["<60"] += 1
    print(f"  pairs={n} min={scores[0]:.0f} p10={scores[n//10]:.0f} median={scores[n//2]:.0f} max={scores[-1]:.0f}")
    print("  ", dict(buckets))
    print("  suffix:", dict(suffix))

print("\n=== numberless line items (the description fallback's only trigger)")
for r in con.execute(
    """select li.id, li.document_id, d.doc_type, d.po_set_id, li.quantity,
              substr(li.description,1,60) descr
       from line_items li join documents d on d.id=li.document_id
       where li.line_item_no is null or trim(li.line_item_no)=''"""
):
    print(f"  doc{r['document_id']:<4} {r['doc_type']} set={r['po_set_id']} qty={r['quantity']} {r['descr']}")

print("\n=== line_items.dn_no populated")
for r in con.execute(
    """select li.document_id, d.doc_type, d.po_set_id, li.line_item_no, li.dn_no
       from line_items li join documents d on d.id=li.document_id
       where li.dn_no is not null and li.dn_no<>'' order by li.document_id limit 20"""
):
    print(f"  doc{r['document_id']:<4} {r['doc_type']} set={r['po_set_id']} line={r['line_item_no']} dn_no={r['dn_no']}")

print("\n=== sets 9, 28, 56, 57 (PO with no line items)")
for sid in (9, 28, 56, 57):
    print(f"\n  --- set {sid}")
    for d in con.execute(
        "select id, doc_type, original_filename, po_no_raw, dn_no, si_no, raw_extraction_json "
        "from documents where po_set_id=? order by id", (sid,)
    ):
        j = json.loads(d["raw_extraction_json"])
        n = con.execute("select count(*) from line_items where document_id=?", (d["id"],)).fetchone()[0]
        print(f"    doc{d['id']:<4} {d['doc_type']} lines={n:<2} page_count={j.get('page_count')} "
              f"po_raw={d['po_no_raw']!r} dn={d['dn_no']} si={d['si_no']} {d['original_filename']}")
