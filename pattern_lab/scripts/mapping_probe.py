"""Probe candidate line-number transforms against the lab copy (read-only)."""

from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"C:\Users\malliath\Desktop\aam_merger")
sys.path.insert(0, str(ROOT / "src"))
from app.services.matching import normalize_line_no  # noqa: E402

con = sqlite3.connect(ROOT / "pattern_lab" / "db" / "aam_merger_lab.db")
con.row_factory = sqlite3.Row

FNS = {
    "x10": lambda k: str(int(k) * 10) if k.isdigit() else None,
    "div10": lambda k: str(int(k) // 10) if k.isdigit() and int(k) % 10 == 0 else None,
    "add_rel": lambda k: (k + "-1") if k.isdigit() else None,
    "strip_rel": lambda k: k[:-2] if k.endswith("-1") and k[:-2].isdigit() else None,
}


def keys(doc_ids):
    out = []
    for i in doc_ids:
        out += [
            (normalize_line_no(r["line_item_no"]), r["quantity"])
            for r in con.execute(
                "select line_item_no, quantity from line_items where document_id=?", (i,)
            )
        ]
    return out


def agg(rows):
    d = defaultdict(int)
    for k, q in rows:
        d[k] += q
    return d


def try_pool(po_keys, vendor_rows):
    """Return list of (rule, matched_all) candidates."""
    cands = []
    for name, fn in FNS.items():
        mapped = {}
        ok = True
        for k, q in vendor_rows:
            if not k:
                ok = False
                break
            mk = fn(k)
            if mk is None:
                ok = False
                break
            if mk not in po_keys:
                ok = False
                break
            if mk in mapped:
                ok = False  # not one-to-one
                break
            mapped[mk] = q
        if ok and mapped:
            cands.append(name)
    return cands


print("=== sets whose unmatched vendor lines are explained by a single transform")
hits = defaultdict(list)
for s in con.execute("select id, status from po_sets order by id"):
    docs = con.execute("select id, doc_type from documents where po_set_id=?", (s["id"],)).fetchall()
    by = defaultdict(list)
    for d in docs:
        by[d["doc_type"]].append(d["id"])
    po = agg(keys(by["PO"]))
    po_keys = set(po)
    found = {}
    for pool in ("DN", "SI"):
        rows = keys(by[pool])
        if not rows:
            continue
        unmatched = [k for k, _ in rows if k not in po_keys]
        if not unmatched:
            continue
        cands = try_pool(po_keys, rows)
        if cands:
            found[pool] = (cands, unmatched)
    if found:
        hits[s["id"]] = found
        print(f"  set {s['id']:<3} [{s['status']:<11}] " + "; ".join(
            f"{p}: {c} (unmatched={u})" for p, (c, u) in found.items()))

print(f"\n  total: {len(hits)} sets")

print("\n=== sets with unmatched lines and NO transform explanation")
for s in con.execute("select id, status from po_sets order by id"):
    docs = con.execute("select id, doc_type from documents where po_set_id=?", (s["id"],)).fetchall()
    by = defaultdict(list)
    for d in docs:
        by[d["doc_type"]].append(d["id"])
    po_keys = set(agg(keys(by["PO"])))
    unres = {}
    for pool in ("DN", "SI"):
        rows = keys(by[pool])
        unmatched = [k for k, _ in rows if k not in po_keys]
        if unmatched and not try_pool(po_keys, rows):
            unres[pool] = unmatched
    if unres:
        print(f"  set {s['id']:<3} [{s['status']:<11}] {unres}")
