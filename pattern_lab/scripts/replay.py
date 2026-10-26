"""Read-only replay of the product's comparison over the lab DB copy.

Nothing is written anywhere: pure functions only, data read from the copy.

Two views per PO Set:
  as-is   -> every document treated as its own page (what the engine saw)
  stitched-> page fragments of the same document number concatenated
             (simulates what a splitter/stitcher would feed the reconciler)
"""

from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"C:\Users\malliath\Desktop\aam_merger")
sys.path.insert(0, str(ROOT / "src"))

from app.services.reconciliation import compare_po_set_lines  # noqa: E402

DB = ROOT / "pattern_lab" / "db" / "aam_merger_lab.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row


def doc_number(doc) -> str:
    return (
        doc["dn_no"]
        or doc["si_no"]
        or doc["po_no_normalized"]
        or doc["po_no_raw"]
        or f"doc{doc['id']}"
    ).strip()


def lines_of(doc_id: int) -> list[dict]:
    return [
        {
            "line_item_no": r["line_item_no"],
            "description": r["description"],
            "quantity": r["quantity"],
        }
        for r in con.execute(
            "select line_item_no, description, quantity from line_items "
            "where document_id=? order by id",
            (doc_id,),
        )
    ]


def verdict(res: dict) -> str:
    if res["po_fail"]:
        return "IDENTITY_FAIL(" + res["po_fail"] + ")"
    ident = [f for f in res["flags"] if f["type"] == "identification"]
    if ident:
        return "UNMATCHED_VENDOR_LINE"
    qty = [f for f in res["flags"] if f["type"] == "quantity"]
    if not qty:
        return "FULL_MATCH"
    under = [f for f in qty if (f["vendor_quantity"] or 0) < (f["po_quantity"] or 0)]
    over = [f for f in qty if (f["vendor_quantity"] or 0) > (f["po_quantity"] or 0)]
    if over:
        return f"OVER({len(over)} lines)"
    return f"PARTIAL({len(under)} short)"


def run_set(sid: int, group: str) -> tuple[str, dict]:
    docs = con.execute(
        "select * from documents where po_set_id=? order by doc_type, id", (sid,)
    ).fetchall()
    pools: dict[str, list[dict]] = {"PO": [], "DN": [], "SI": []}
    if group == "as-is":
        for d in docs:
            pools[d["doc_type"]] += lines_of(d["id"])
    else:
        buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
        for d in docs:
            buckets[(d["doc_type"], doc_number(d))].append(d["id"])
        for (dt, _num), ids in buckets.items():
            for i in ids:
                pools[dt] += lines_of(i)
    res = compare_po_set_lines(pools["PO"], pools["DN"], pools["SI"], 85)
    return verdict(res), res


sets = con.execute("select id, status, reconcile_reason from po_sets order by id").fetchall()

print(f"{'set':>4} {'stored':<12} {'as-is':<26} {'stitched':<26} docs")
summary = defaultdict(int)
for s in sets:
    a, _ = run_set(s["id"], "as-is")
    b, _ = run_set(s["id"], "stitched")
    n = con.execute(
        "select count(*) from documents where po_set_id=?", (s["id"],)
    ).fetchone()[0]
    summary[(s["status"], a, b)] += 1
    if a != b:
        print(f"{s['id']:>4} {s['status']:<12} {a:<26} {b:<26} {n}")

print("\n=== stored status vs as-is vs stitched")
for (st, a, b), n in sorted(summary.items(), key=lambda kv: -kv[1]):
    print(f"  {st:<12} as-is={a:<26} stitched={b:<26} {n}")

print("\n=== transitions caused by stitching")
for s in sets:
    a, _ = run_set(s["id"], "as-is")
    b, res = run_set(s["id"], "stitched")
    if a == b:
        continue
    print(f"\n  set {s['id']} ({s['status']}): {a} -> {b}")
    print(f"    stored reason: {s['reconcile_reason']}")
    for f in res["flags"][:8]:
        print(
            f"      [{f['priority']}] {f['type']:<19} {f['pool']} line="
            f"{f['line_item_no']!r} po={f['po_quantity']} vendor={f['vendor_quantity']}"
        )
