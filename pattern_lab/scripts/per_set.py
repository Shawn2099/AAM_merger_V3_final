from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"C:\Users\malliath\Desktop\aam_merger")
sys.path.insert(0, str(ROOT / "src"))

from app.services.reconciliation import compare_po_set_lines  # noqa: E402

con = sqlite3.connect(ROOT / "pattern_lab" / "db" / "aam_merger_lab.db")
con.row_factory = sqlite3.Row


def lines_of(doc_id):
    return [
        {
            "line_item_no": r["line_item_no"],
            "description": r["description"],
            "quantity": r["quantity"],
        }
        for r in con.execute(
            "select line_item_no, description, quantity from line_items where document_id=?",
            (doc_id,),
        )
    ]


def verdict(res):
    if res["po_fail"]:
        return "IDENTITY_FAIL"
    if [f for f in res["flags"] if f["type"] == "identification"]:
        return "UNMATCHED"
    qty = [f for f in res["flags"] if f["type"] == "quantity"]
    if not qty:
        return "FULL_MATCH"
    if [f for f in qty if (f["vendor_quantity"] or 0) > (f["po_quantity"] or 0)]:
        return "OVER"
    return "PARTIAL"


print(f"{'set':>3} {'status':<12} {'PO':>2} {'DN':>2} {'SI':>2} {'distinctPO':>10} "
      f"{'verdict_if_guard_ignored':<12} stored reason")

would_unblock = defaultdict(int)
for s in con.execute("select id,status,reconcile_reason from po_sets order by id"):
    docs = con.execute("select * from documents where po_set_id=?", (s["id"],)).fetchall()
    by = defaultdict(list)
    for d in docs:
        by[d["doc_type"]].append(d)
    pools = {k: sum((lines_of(d["id"]) for d in by[k]), []) for k in ("PO", "DN", "SI")}
    v = verdict(compare_po_set_lines(pools["PO"], pools["DN"], pools["SI"], 85))
    dpo = len({d["po_no_normalized"] for d in by["PO"]})
    would_unblock[(s["status"], dpo > 1, v)] += 1
    print(f"{s['id']:>3} {s['status']:<12} {len(by['PO']):>2} {len(by['DN']):>2} {len(by['SI']):>2} "
          f"{dpo:>10} {v:<12} {str(s['reconcile_reason'])[:60]}")

print("\n=== grouping")
for (st, multi, v), n in sorted(would_unblock.items(), key=lambda kv: -kv[1]):
    print(f"  status={st:<12} multi_PO_docs={multi!s:<5} verdict={v:<12} {n}")
