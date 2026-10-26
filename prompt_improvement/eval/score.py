"""Score extraction results against gold.json, with a precision view.

Usage (from repo root):
  uv run python prompt_improvement/eval/score.py baseline            # one run
  uv run python prompt_improvement/eval/score.py baseline v2         # side by side
  options: --gold PATH  --results-dir PATH  --verbose (per-doc detail)  --json

gold.json: {doc_id: {document_type, document_number, po_reference,
                     lines:[{line_item_no, quantity}]}}   (absent field = not scored)

Per scored field the outcome is one of:
  correct  - matches gold
  wrong    - a non-null prediction that differs (or a value where gold says null)
  null     - prediction missing/null/unreadable where gold has a value (safe failure)
Rules:
  document_type          exact string.
  document_number        gold PO docs: app normalize_po_no on both; otherwise stripped exact.
  po_reference           app normalize_po_no on both (gold null = must be null).
  lines                  grouped by the app's normalize_line_no and quantities summed as the
                         app's x1000 integers (the matching rule). Each GOLD line key is
                         scored; pred keys absent from gold are "extra" (a spurious line,
                         counted as a wrong, and the doc fails).
  quantity               gold may be a string/number as printed (parsed with the app's
                         parser) or {"quantity_scaled": int} on the line.
Doc passes iff every scored field is correct and there are no extra lines.
A failed call (no result) = all null, doc fails.
With --repeats results, each run is scored; the summary averages over runs and a
"stability" figure reports how many docs gave identical outcomes+values in every repeat.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO = EVAL_DIR.parents[1]
sys.path.insert(0, str(REPO / "src"))

from app.services.extraction import _parse_scaled_int  # noqa: E402
from app.services.grouping import normalize_po_no  # noqa: E402
from app.services.matching import normalize_line_no  # noqa: E402

CORRECT, WRONG, NULL = "correct", "wrong", "null"
FIELDS = ("document_type", "document_number", "po_reference", "lines")


def _s(v) -> str | None:
    if v is None:
        return None
    t = str(v).strip()
    return t or None


def _po_norm(v) -> str | None:
    t = _s(v)
    if t is None:
        return None
    return normalize_po_no(t) or None


def _outcome_str(gold, pred) -> str:
    """gold/pred already normalised (None = null)."""
    if gold is None:
        return CORRECT if pred is None else WRONG
    if pred is None:
        return NULL
    return CORRECT if gold == pred else WRONG


def _qty_scaled(line: dict) -> int:
    if line.get("quantity_scaled") is not None:
        return int(line["quantity_scaled"])
    return _parse_scaled_int(line.get("quantity"))


def _agg(lines) -> tuple[dict[str, int], set[str]]:
    """key -> summed scaled qty; plus keys having any unreadable (0) row."""
    sums: dict[str, int] = defaultdict(int)
    unreadable: set[str] = set()
    for ln in lines or []:
        key = normalize_line_no(ln.get("line_item_no"))
        q = _qty_scaled(ln)
        if q <= 0:
            unreadable.add(key)
        sums[key] += max(q, 0)
    return dict(sums), unreadable


def score_run(gold: dict, pred: dict | None) -> dict:
    """Score one prediction dict against one gold entry."""
    pred = pred or {}
    out: dict = {"fields": {}, "values": {}}

    if "document_type" in gold:
        g, p = _s(gold["document_type"]), _s(pred.get("document_type"))
        out["fields"]["document_type"] = _outcome_str(g, p)
        out["values"]["document_type"] = p
    if "document_number" in gold:
        is_po = _s(gold.get("document_type") or pred.get("document_type")) == "PO"
        norm = _po_norm if is_po else _s
        g, p = norm(gold["document_number"]), norm(pred.get("document_number"))
        out["fields"]["document_number"] = _outcome_str(g, p)
        out["values"]["document_number"] = p
    pred_amb = bool(pred.get("po_reference_ambiguous"))
    out["pred_ambiguous"] = pred_amb
    out["ambiguity"] = None  # held / wrong / missed, only for gold ambiguous:true docs
    if "po_reference" in gold:
        g, p = _po_norm(gold["po_reference"]), _po_norm(pred.get("po_reference"))
        if gold.get("ambiguous"):
            # Gold says two PO numbers print. Flagging ambiguous is the correct (held) result and
            # po_reference may be anything; a non-ambiguous non-null PO is a guess (WRONG);
            # non-ambiguous null is a miss (NULL: no PO but the doc is not held for review).
            if pred_amb:
                out["ambiguity"], oc = "held", CORRECT
            elif p is not None:
                out["ambiguity"], oc = "wrong", WRONG
            else:
                out["ambiguity"], oc = "missed", NULL
            out["fields"]["po_reference"] = oc
        else:
            out["fields"]["po_reference"] = _outcome_str(g, p)
            if pred_amb:
                # Over-flagging: the app holds this doc unattached, so it is a failure even when
                # po_reference itself is right. Counted as its own line and fails the doc.
                out["ambiguity"] = "false_flag"
        out["values"]["po_reference"] = p
        if pred_amb:
            out["values"]["ambiguous"] = True

    out["extra_lines"] = []
    if "lines" in gold:
        gsum, _ = _agg(gold["lines"])
        psum, punread = _agg(pred.get("line_items"))
        per: dict[str, str] = {}
        for key, gq in gsum.items():
            if key not in psum or key in punread:
                per[key] = NULL
            else:
                per[key] = CORRECT if psum[key] == gq else WRONG
        extras = sorted(k for k in psum if k not in gsum)
        out["lines"] = per
        out["extra_lines"] = extras
        out["values"]["lines"] = {k: v for k, v in sorted(psum.items())}
        if any(v == WRONG for v in per.values()) or extras:
            out["fields"]["lines"] = WRONG
        elif any(v == NULL for v in per.values()):
            out["fields"]["lines"] = NULL
        else:
            out["fields"]["lines"] = CORRECT
    out["pass"] = (
        bool(out["fields"])
        and out["ambiguity"] in (None, "held")
        and all(v == CORRECT for v in out["fields"].values())
        and not out["extra_lines"]
    )
    return out


def load_results(label: str, results_dir: Path) -> dict[int, list[dict | None]]:
    """doc_id -> list of per-repeat result dicts (None = failed call)."""
    res: dict[int, list] = {}
    for f in sorted((results_dir / label).glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        res[int(d["doc_id"])] = [r.get("result") if r.get("ok") else None for r in d["runs"]]
    return res


def score_label(gold: dict, results: dict[int, list]) -> dict:
    """Aggregate. Docs in gold with no results file count as missing (excluded from rates)."""
    docs: dict[int, dict] = {}
    for sid, g in gold.items():
        did = int(sid)
        if did not in results:
            continue
        runs = [score_run(g, p) for p in results[did]]
        sigs = {json.dumps([r["fields"], r["values"], r["extra_lines"]], sort_keys=True) for r in runs}
        docs[did] = {"runs": runs, "stable": len(sigs) == 1, "n_runs": len(runs)}

    field_counts = {f: {CORRECT: 0, WRONG: 0, NULL: 0} for f in FIELDS}
    line_counts = {CORRECT: 0, WRONG: 0, NULL: 0, "extra": 0}
    total_runs = passes = 0
    amb = {"held": 0, "wrong": 0, "missed": 0, "false_flag": 0}
    for sid, d in docs.items():
        for r in d["runs"]:
            total_runs += 1
            if r.get("ambiguity"):
                amb[r["ambiguity"]] += 1
            passes += r["pass"]
            for f, o in r["fields"].items():
                field_counts[f][o] += 1
            for o in r.get("lines", {}).values():
                line_counts[o] += 1
            line_counts["extra"] += len(r["extra_lines"])
    return {
        "docs": docs,
        "field_counts": field_counts,
        "line_counts": line_counts,
        "total_runs": total_runs,
        "passes": passes,
        "ambiguity": amb,
        "n_docs": len(docs),
        "n_missing": sum(1 for s in gold if int(s) not in results),
        "stable_docs": sum(d["stable"] for d in docs.values()),
        "multi_run": any(d["n_runs"] > 1 for d in docs.values()),
    }


def _rate(n, d) -> str:
    return f"{n}/{d} ({100.0 * n / d:.1f}%)" if d else "n/a"


def _summary_rows(s: dict) -> list[tuple[str, str]]:
    rows = [("docs scored", str(s["n_docs"]) + (f" (+{s['n_missing']} no result)" if s["n_missing"] else ""))]
    rows.append(("doc pass (per run)", _rate(s["passes"], s["total_runs"])))
    for f in FIELDS:
        c = s["field_counts"][f]
        n = sum(c.values())
        if not n:
            continue
        rows.append((f"{f} correct", _rate(c[CORRECT], n)))
        rows.append((f"{f} WRONG", _rate(c[WRONG], n)))
        rows.append((f"{f} null", _rate(c[NULL], n)))
    lc = s["line_counts"]
    n = lc[CORRECT] + lc[WRONG] + lc[NULL]
    if n:
        rows.append(("line qty correct", _rate(lc[CORRECT], n)))
        rows.append(("line qty WRONG", _rate(lc[WRONG], n)))
        rows.append(("line qty null", _rate(lc[NULL], n)))
        rows.append(("extra (spurious) lines", str(lc["extra"])))
    am = s.get("ambiguity") or {}
    if sum(am.get(k, 0) for k in ("held", "wrong", "missed")):
        n = am["held"] + am["wrong"] + am["missed"]
        rows.append(("ambiguity HELD (gold-ambiguous docs)", _rate(am["held"], n)))
        rows.append(("ambiguity WRONG (guessed a PO)", _rate(am["wrong"], n)))
        rows.append(("ambiguity missed (null PO, not flagged)", _rate(am["missed"], n)))
    if "false_flag" in am:
        rows.append(("false ambiguity flags (non-ambiguous docs)", str(am["false_flag"])))
    # precision = correct / (correct + wrong): how often a non-null answer is right
    cc = sum(s["field_counts"][f][CORRECT] for f in FIELDS) + lc[CORRECT]
    ww = sum(s["field_counts"][f][WRONG] for f in FIELDS) + lc[WRONG] + lc["extra"]
    rows.append(("PRECISION (correct/(correct+wrong))", _rate(cc, cc + ww)))
    if s["multi_run"]:
        rows.append(("stable across repeats", _rate(s["stable_docs"], s["n_docs"])))
    return rows


def render(labels: list[str], scored: list[dict], verbose: bool) -> str:
    rows = [_summary_rows(s) for s in scored]
    keys: list[str] = []
    for r in rows:
        for k, _ in r:
            if k not in keys:
                keys.append(k)
    lookup = [dict(r) for r in rows]
    w0 = max(len(k) for k in keys) + 2
    wc = [max(len(lab), *(len(d.get(k, "-")) for k in keys)) + 2 for lab, d in zip(labels, lookup)]
    lines = ["".ljust(w0) + "".join(lab.ljust(w) for lab, w in zip(labels, wc))]
    lines.append("-" * (w0 + sum(wc)))
    for k in keys:
        lines.append(k.ljust(w0) + "".join(d.get(k, "-").ljust(w) for d, w in zip(lookup, wc)))

    ids = sorted(set().union(*(s["docs"].keys() for s in scored)))
    lines.append("")
    lines.append("per-doc (P=pass F=fail; per-run for repeats; fields: c=correct w=WRONG n=null)")
    for did in ids:
        cells = []
        for s in scored:
            d = s["docs"].get(did)
            if not d:
                cells.append("-")
                continue
            parts = []
            for r in d["runs"]:
                fl = " ".join(
                    f"{f[0] if f != 'document_type' else 'T'}{ {CORRECT: 'c', WRONG: 'w', NULL: 'n'}[o] }"
                    for f, o in r["fields"].items()
                )
                parts.append(("P " if r["pass"] else "F ") + fl)
            cells.append(" | ".join(parts) + ("" if d["stable"] else " UNSTABLE"))
        marker = ""
        if len(scored) == 2:
            a, b = (s["docs"].get(did) for s in scored)
            if a and b:
                pa, pb = all(r["pass"] for r in a["runs"]), all(r["pass"] for r in b["runs"])
                marker = "  <- B better" if pb and not pa else "  <- B REGRESSED" if pa and not pb else ""
        lines.append(f"  doc {did}: " + "  ||  ".join(cells) + marker)
        if verbose:
            for lab, s in zip(labels, scored):
                d = s["docs"].get(did)
                if not d:
                    continue
                for i, r in enumerate(d["runs"]):
                    bad = {k: v for k, v in r["fields"].items() if v != CORRECT}
                    badl = {k: v for k, v in r.get("lines", {}).items() if v != CORRECT}
                    if bad or badl or r["extra_lines"]:
                        lines.append(
                            f"      [{lab} r{i}] {bad} lines={badl} extra={r['extra_lines']} "
                            f"pred={json.dumps(r['values'], default=str)[:300]}"
                        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("labels", nargs="+", help="one or two result labels")
    ap.add_argument("--gold", default=str(EVAL_DIR / "gold.json"))
    ap.add_argument("--results-dir", default=str(EVAL_DIR / "results"))
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit summary counts as JSON")
    args = ap.parse_args()
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    scored = [score_label(gold, load_results(lab, Path(args.results_dir))) for lab in args.labels]
    if args.json:
        print(json.dumps([{k: v for k, v in s.items() if k != "docs"} for s in scored], indent=2))
    else:
        print(render(args.labels, scored, args.verbose))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
