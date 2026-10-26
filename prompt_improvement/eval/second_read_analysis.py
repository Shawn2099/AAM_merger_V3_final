"""Second-read (agreement) analysis over EXISTING saved runs. No API calls.

Question: if the app made two reads of every document and held the document whenever they
disagree, how many WRONG values would that catch, and how many correct documents would it hold?

Unit = ordered pair of reads of the same document (A = the read the app keeps, B = the checker).
With 3 repeats a document gives 6 ordered pairs. Pairs that include a failed call are dropped
(an infrastructure failure means retry, not hold) and counted separately.

Comparison uses what the APP keys on (not what score.py scores):
  type        exact document_type
  docno       document_number stripped, whitespace collapsed, upper-cased
  po          (normalize_po_no, po_letters) of the app's PO key: document_number for a PO
              (falls back to po_reference), po_reference otherwise
  amb         po_reference_ambiguous
  lines       {normalize_line_no: summed x1000 qty} over ALL predicted rows
Rules: ID = type|docno|po|amb must agree; FULL = ID and lines must agree.

Truth (score.py against gold): a read is
  PASS    every scored field correct (score.py pass)
  UNSAFE  at least one WRONG value (wrong field, wrong line qty, extra line, guessed PO)
  SAFE-FAIL  no wrong value but not a pass (null field / false ambiguity flag): the app holds
          it anyway, so a hold is neither a catch nor a cost.

Usage (repo root):
  uv run --no-sync python prompt_improvement/eval/second_read_analysis.py            # report
  uv run --no-sync python prompt_improvement/eval/second_read_analysis.py --detail   # + wrong list
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO = EVAL_DIR.parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(EVAL_DIR))

from score import CORRECT, NULL, WRONG, score_run  # noqa: E402

from app.services.extraction import _parse_scaled_int  # noqa: E402
from app.services.grouping import letters_compatible, normalize_po_no, po_letters  # noqa: E402
from app.services.matching import normalize_line_no  # noqa: E402

RESULTS = EVAL_DIR / "results"
GOLD_T = json.loads((EVAL_DIR / "gold.json").read_text(encoding="utf-8"))
GOLD_H = json.loads((EVAL_DIR / "gold_heldout.json").read_text(encoding="utf-8"))

# label -> (gold, transport/prompt note). P6 tuning (37 failed calls) and v2 (40 failed) are
# kept but flagged; failed calls are dropped from pairs anyway.
LABELS = {
    "P5": (GOLD_T, "img150+eff=high, P5 (ADOPTED)"),
    "H_P5": (GOLD_H, "img150+eff=high, P5 (ADOPTED), held-out"),
    "P0": (GOLD_T, "img150+eff=high, live prompt"),
    "P1": (GOLD_T, "img150+eff=high, P1"),
    "P2": (GOLD_T, "img150+eff=high, P2"),
    "P3": (GOLD_T, "img150+eff=high, P3"),
    "P4": (GOLD_T, "img150+eff=high, P4"),
    "P6": (GOLD_T, "img150+eff=high, P6 (37/111 calls failed)"),
    "R_high": (GOLD_T, "img150+eff=high, live"),
    "R_med": (GOLD_T, "img150+eff=medium, live"),
    "R_low": (GOLD_T, "img150+eff=low, live"),
    "R_seed": (GOLD_T, "img150+seed=42, live"),
    "V3": (GOLD_T, "img150, live"),
    "V4": (GOLD_T, "img200, live"),
    "V0": (GOLD_T, "native PDF default, live"),
    "V1": (GOLD_T, "native PDF plugin, live"),
    "baseline": (GOLD_T, "native PDF (app at the time), live"),
    "V2ok": (GOLD_T, "mistral-ocr plugin, live (20 docs)"),
    "v2": (GOLD_T, "mistral-ocr plugin, live (40 failed)"),
    "H_P0": (GOLD_H, "img150+eff=high, live, held-out"),
    "H_P4": (GOLD_H, "img150+eff=high, P4, held-out"),
    "H_P6": (GOLD_H, "img150+eff=high, P6, held-out"),
    "H_V0": (GOLD_H, "native PDF, live, held-out"),
}
IMG150_T = ["P0", "P1", "P2", "P3", "P4", "P5", "P6", "R_high", "R_med", "R_low", "R_seed", "V3"]
IMG150_H = ["H_P0", "H_P4", "H_P5", "H_P6"]


def load(label: str) -> dict[int, list[dict | None]]:
    out: dict[int, list] = {}
    for f in sorted((RESULTS / label).glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        out[int(d["doc_id"])] = [r.get("result") if r and r.get("ok") else None for r in d["runs"]]
    return out


def _s(v) -> str | None:
    if v is None:
        return None
    t = " ".join(str(v).split()).upper()
    return t or None


def po_key(pred: dict) -> tuple[str, str] | None:
    raw = pred.get("po_reference")
    if pred.get("document_type") == "PO":
        raw = pred.get("document_number") or raw
    if not raw or not str(raw).strip():
        return None
    return normalize_po_no(str(raw)), po_letters(str(raw))


def lines_key(pred: dict) -> dict[str, int]:
    sums: dict[str, int] = defaultdict(int)
    for ln in pred.get("line_items") or []:
        sums[normalize_line_no(ln.get("line_item_no"))] += _parse_scaled_int(ln.get("quantity"))
    return dict(sums)


#: "strict": doc number compared as printed (whitespace-collapsed, upper), PO as
#:           (digits, letters) exactly.
#: "app":    compare only what the app stores/keys on. A PO's own number is already the PO key
#:           (so its doc number is not compared again); a DN/SI number is compared alnum-only
#:           (formatting such as "10006-0000079633" vs "100060000079633" is not a disagreement);
#:           PO keys agree when digits are equal and letters are compatible (grouping rule).
MODE = "app"


def _alnum(v) -> str | None:
    t = "".join(c for c in str(v or "") if c.isalnum()).upper()
    return t or None


def signature(pred: dict) -> dict:
    if MODE == "strict":
        docno = _s(pred.get("document_number"))
    else:
        docno = None if pred.get("document_type") == "PO" else _alnum(pred.get("document_number"))
    return {
        "type": pred.get("document_type"),
        "docno": docno,
        "po": po_key(pred),
        "amb": bool(pred.get("po_reference_ambiguous")),
        "lines": lines_key(pred),
    }


ID_FIELDS = ("type", "docno", "po", "amb")


def _po_eq(a, b) -> bool:
    if MODE == "strict" or a is None or b is None:
        return a == b
    return a[0] == b[0] and letters_compatible(a[1], b[1])


def disagree(a: dict, b: dict, rule: str) -> list[str]:
    if rule == "PO":
        fields: tuple[str, ...] = ("po",)
    else:
        fields = ID_FIELDS + (("lines",) if rule == "FULL" else ())
    return [f for f in fields if not (_po_eq(a[f], b[f]) if f == "po" else a[f] == b[f])]


def wrong_items(sc: dict) -> list[str]:
    """Every WRONG value in one scored read, one entry per value."""
    items = [f for f, o in sc["fields"].items() if o == WRONG and f != "lines"]
    items += [f"line:{k}" for k, o in sc.get("lines", {}).items() if o == WRONG]
    items += [f"extra:{k}" for k in sc.get("extra_lines", [])]
    return items


def truth_class(sc: dict, pred: dict | None = None) -> str:
    """UNSAFE / PASS (correct and auto-placed) / PASS-HELD (correct, but the app holds it
    anyway: ambiguity flag or no PO key) / SAFE-FAIL. Only PASS can be a false hold."""
    if wrong_items(sc):
        return "UNSAFE"
    if not sc["pass"]:
        return "SAFE-FAIL"
    if pred is not None and (pred.get("po_reference_ambiguous") or po_key(pred) is None):
        return "PASS-HELD"
    return "PASS"


def analyse(label: str, gold: dict, rules=("ID", "FULL")) -> dict:
    res = load(label)
    st = {
        r: Counter()
        for r in rules
    }
    failed_pairs = 0
    per_doc = {}
    for sid, g in gold.items():
        did = int(sid)
        if did not in res:
            continue
        runs = res[did]
        scored = [score_run(g, p) if p is not None else None for p in runs]
        sigs = [signature(p) if p is not None else None for p in runs]
        per_doc[did] = {"scored": scored, "sigs": sigs, "runs": runs}
        for i, j in itertools.permutations(range(len(runs)), 2):
            if sigs[i] is None or sigs[j] is None:
                failed_pairs += 1
                continue
            sc = scored[i]
            tc = truth_class(sc, runs[i])
            wi = wrong_items(sc)
            unscored_doc = not g.get("lines") or "uncertain_fields" in g
            for r in rules:
                d = disagree(sigs[i], sigs[j], r)
                c = st[r]
                c["pairs"] += 1
                c[f"{tc}"] += 1
                if d:
                    c["held"] += 1
                    c[f"held_{tc}"] += 1
                    if unscored_doc:
                        c["held_partly_unscored_doc"] += 1
                c["wrong_items"] += len(wi)
                if d:
                    c["wrong_caught"] += len(wi)
                for f in d:
                    c[f"dis_{f}"] += 1
                    if tc == "PASS":
                        c[f"falsehold_{f}"] += 1
    # 3-read rule: hold unless all 3 reads agree; A = repeat 0.
    tri = {r: Counter() for r in rules}
    for did, d in per_doc.items():
        if any(s is None for s in d["sigs"]):
            continue
        sc = d["scored"][0]
        tc = truth_class(sc, d["runs"][0])
        for r in rules:
            held = any(disagree(d["sigs"][0], s, r) for s in d["sigs"][1:])
            tri[r]["docs"] += 1
            tri[r][tc] += 1
            tri[r]["wrong_items"] += len(wrong_items(sc))
            if held:
                tri[r]["held"] += 1
                tri[r][f"held_{tc}"] += 1
                tri[r]["wrong_caught"] += len(wrong_items(sc))
    return {"pairs": st, "tri": tri, "failed_pairs": failed_pairs, "per_doc": per_doc}


def wrong_catalogue(label: str, gold: dict, per_doc: dict) -> list[dict]:
    """Per (doc, wrong item): values across repeats, consistent vs noisy."""
    out = []
    for did, d in per_doc.items():
        items = set()
        for sc in d["scored"]:
            if sc:
                items.update(wrong_items(sc))
        for it in sorted(items):
            vals = []
            nwrong = 0
            for sc, p in zip(d["scored"], d["runs"]):
                if sc is None:
                    vals.append("FAILED")
                    continue
                if it in wrong_items(sc):
                    nwrong += 1
                if it.startswith("line:") or it.startswith("extra:"):
                    vals.append(sc["values"].get("lines", {}).get(it.split(":", 1)[1]))
                else:
                    vals.append(sc["values"].get(it))
            n_ok = sum(1 for sc in d["scored"] if sc)
            kind = (
                "CONSISTENT" if nwrong == n_ok and len({json.dumps(v) for v in vals}) == 1 else "noisy"
            )
            g = gold[str(did)]
            gv = g.get(it) if not it.startswith(("line:", "extra:")) else None
            if it.startswith("line:"):
                key = it.split(":", 1)[1]
                gv = sum(
                    _parse_scaled_int(x.get("quantity"))
                    for x in g.get("lines", [])
                    if normalize_line_no(x.get("line_item_no")) == key
                )
            out.append(
                {
                    "label": label,
                    "doc": did,
                    "gold_type": g.get("document_type"),
                    "item": it,
                    "gold": gv,
                    "values": vals,
                    "n_wrong": nwrong,
                    "kind": kind,
                }
            )
    return out


def cross(label_a: str, label_b: str, gold: dict, rules=("PO", "ID", "FULL")) -> dict:
    """A from label_a, B from label_b, all 3x3 repeat combinations per doc."""
    ra, rb = load(label_a), load(label_b)
    st = {r: Counter() for r in rules}
    caught_items = []
    for sid, g in gold.items():
        did = int(sid)
        if did not in ra or did not in rb:
            continue
        for pa in ra[did]:
            if pa is None:
                continue
            sa = signature(pa)
            sc = score_run(g, pa)
            tc = truth_class(sc, pa)
            wi = wrong_items(sc)
            for pb in rb[did]:
                if pb is None:
                    continue
                sb = signature(pb)
                for r in rules:
                    d = disagree(sa, sb, r)
                    c = st[r]
                    c["pairs"] += 1
                    c[tc] += 1
                    c["wrong_items"] += len(wi)
                    if d:
                        c["held"] += 1
                        c[f"held_{tc}"] += 1
                        c["wrong_caught"] += len(wi)
                        if r == "ID":
                            caught_items.extend((did, w) for w in wi)
                        for f in d:
                            if tc == "PASS":
                                c[f"falsehold_{f}"] += 1
    return {"st": st, "caught": Counter(caught_items)}


def pct(n, d) -> str:
    return f"{n}/{d} ({100.0 * n / d:.1f}%)" if d else "0/0 (n/a)"


def row(label: str, c: Counter) -> str:
    return (
        f"| {label} | {c['pairs']} | {c['UNSAFE']} | {pct(c['held_UNSAFE'], c['UNSAFE'])} | "
        f"{pct(c['wrong_caught'], c['wrong_items'])} | {pct(c['held_PASS'], c['PASS'])} | "
        f"{pct(c['held'], c['pairs'])} |"
    )


HDR = (
    "| label | pairs | A unsafe | unsafe A caught | wrong values caught | PASS A falsely held | "
    "all pairs held |\n|---|---|---|---|---|---|---|"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--json", help="also dump the wrong catalogue to this path")
    ap.add_argument("--mode", choices=("app", "strict"), default="app")
    args = ap.parse_args()
    global MODE
    MODE = args.mode
    print(f"comparison mode: {MODE}")

    results = {}
    catalogue = []
    for lab, (gold, _note) in LABELS.items():
        if not (RESULTS / lab).exists():
            continue
        a = analyse(lab, gold)
        results[lab] = a
        catalogue += wrong_catalogue(lab, gold, a["per_doc"])

    for rule in ("ID", "FULL"):
        print(f"\n### Same-call repeat, 2 reads (ordered pairs), rule {rule}\n")
        print(HDR)
        pool_t, pool_h = Counter(), Counter()
        for lab, a in results.items():
            c = a["pairs"][rule]
            print(row(f"{lab} ({LABELS[lab][1]})", c) + (f" failed pairs {a['failed_pairs']}" if a["failed_pairs"] else ""))
            if lab in IMG150_T:
                pool_t += c
            if lab in IMG150_H:
                pool_h += c
        print(row("POOLED img150 tuning", pool_t))
        print(row("POOLED img150 held-out", pool_h))

    for rule in ("ID", "FULL"):
        print(f"\n### 3 reads, hold unless all agree (A = repeat 0), rule {rule}\n")
        print("| label | docs | unsafe | unsafe caught | wrong values caught | PASS held | held |")
        print("|---|---|---|---|---|---|---|")
        for lab in ("P5", "H_P5", "P0", "R_high", "H_P0", "H_P4"):
            c = results[lab]["tri"][rule]
            print(
                f"| {lab} | {c['docs']} | {c['UNSAFE']} | {pct(c['held_UNSAFE'], c['UNSAFE'])} | "
                f"{pct(c['wrong_caught'], c['wrong_items'])} | {pct(c['held_PASS'], c['PASS'])} | "
                f"{pct(c['held'], c['docs'])} |"
            )

    print("\n### False holds on the adopted config, by disagreeing field (ordered pairs, PASS A)\n")
    for lab in ("P5", "H_P5"):
        c = results[lab]["pairs"]["FULL"]
        fh = {k[10:]: v for k, v in c.items() if k.startswith("falsehold_")}
        print(f"- {lab}: {fh}  (held pairs on partly-unscored docs: {c['held_partly_unscored_doc']})")

    print("\n### Wrong-value catalogue: consistent vs noisy\n")
    kinds = Counter()
    kinds_by = defaultdict(Counter)
    for w in catalogue:
        grp = "img150 tuning" if w["label"] in IMG150_T else "img150 held-out" if w["label"] in IMG150_H else "other transports"
        kinds_by[grp][w["kind"]] += 1
        kinds[w["kind"]] += 1
    for grp, k in kinds_by.items():
        print(f"- {grp}: {dict(k)}")
    print(f"- all: {dict(kinds)}")
    for lab in ("P5", "H_P5"):
        print(f"\n{lab} wrong values:")
        for w in catalogue:
            if w["label"] == lab:
                print(f"  doc {w['doc']} {w['gold_type']} {w['item']}: gold={w['gold']} reads={w['values']} -> {w['kind']}")
    if args.detail:
        print("\nAll wrong values (img150 labels):")
        for w in catalogue:
            if w["label"] in IMG150_T + IMG150_H:
                print(f"  {w['label']} doc {w['doc']} {w['gold_type']} {w['item']}: gold={w['gold']} reads={w['values']} -> {w['kind']}")

    print("\n### Cross-variant second read (A = adopted P5 read, B = differently built read)\n")
    print(HDR.replace("pairs |", "pairs (3x3) |", 1))
    for a_lab, b_lab, gold in (
        ("P5", "P0", GOLD_T),
        ("P5", "R_high", GOLD_T),
        ("P5", "P2", GOLD_T),
        ("P5", "V1", GOLD_T),
        ("P5", "V0", GOLD_T),
        ("H_P5", "H_P0", GOLD_H),
        ("H_P5", "H_V0", GOLD_H),
        ("P0", "P5", GOLD_T),
        ("H_P0", "H_P5", GOLD_H),
    ):
        x = cross(a_lab, b_lab, gold)
        for rule in ("PO", "ID", "FULL"):
            c = x["st"][rule]
            fh = {k[10:]: v for k, v in c.items() if k.startswith("falsehold_")}
            print(row(f"{a_lab} vs {b_lab} [{rule}]", c) + f" false-hold fields {fh}")
    if args.json:
        Path(args.json).write_text(json.dumps(catalogue, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
