"""Summarise _usage (tokens/cost/latency) per result label, and score summary per label.

  uv run python prompt_improvement/eval/usage_report.py V0 V1 V2 ...
"""

from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score as S  # noqa: E402

D = Path(__file__).resolve().parent / "results"
gold = json.loads((S.EVAL_DIR / "gold.json").read_text(encoding="utf-8"))

print(
    f"{'label':14}{'calls':>6}{'err':>4}{'pass%':>7}{'prec%':>7}{'wrongF':>7}{'wrongL':>7}{'extra':>6}"
    f"{'nullF':>6}{'nullL':>6}{'stable':>9}{'ptok':>8}{'ctok':>7}{'rtok':>7}{'$/doc':>9}{'lat_s':>7}"
)
for lab in sys.argv[1:]:
    calls = errs = 0
    pt, ct, rt, cost, lat = [], [], [], [], []
    for f in (D / lab).glob("*.json"):
        for r in json.loads(f.read_text(encoding="utf-8"))["runs"]:
            calls += 1
            if not r["ok"]:
                errs += 1
                continue
            u = (r["result"] or {}).get("_usage") or {}
            pt.append(u.get("prompt_tokens") or 0)
            ct.append(u.get("completion_tokens") or 0)
            rt.append(u.get("reasoning_tokens") or 0)
            cost.append(u.get("cost") or 0)
            lat.append(u.get("latency_s") or r["elapsed_s"])
    s = S.score_label(gold, S.load_results(lab, D))
    fc, lc = s["field_counts"], s["line_counts"]
    cc = sum(fc[f][S.CORRECT] for f in S.FIELDS) + lc[S.CORRECT]
    ww = sum(fc[f][S.WRONG] for f in S.FIELDS) + lc[S.WRONG] + lc["extra"]
    wf = sum(fc[f][S.WRONG] for f in S.FIELDS)
    nf = sum(fc[f][S.NULL] for f in S.FIELDS)
    m = lambda x: st.mean(x) if x else 0  # noqa: E731
    print(
        f"{lab:14}{calls:>6}{errs:>4}{100 * s['passes'] / max(1, s['total_runs']):>7.1f}"
        f"{100 * cc / max(1, cc + ww):>7.1f}{wf:>7}{lc[S.WRONG]:>7}{lc['extra']:>6}{nf:>6}{lc[S.NULL]:>6}"
        f"{s['stable_docs']:>5}/{s['n_docs']:<3}{m(pt):>8.0f}{m(ct):>7.0f}{m(rt):>7.0f}"
        f"{m(cost):>9.5f}{m(lat):>7.1f}"
    )
