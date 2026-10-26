"""Catch rate of a same-call second read on IDENTITY-wrong reads only (no API calls).

A read counts if it has a WRONG document_type, document_number or po_reference (incl. a guessed
PO on a two-PO doc). Rule ID, app-key comparison, ordered pairs. Also: distinct printed line
values behind the "0 quantity errors" figure.

  uv run --no-sync python prompt_improvement/eval/second_read_identity_only.py
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import second_read_analysis as S  # noqa: E402

ID_WRONG = {"document_type", "document_number", "po_reference"}


def run(labels, gold):
    tot = caught = 0
    for lab in labels:
        a = S.analyse(lab, gold)
        for did, d in a["per_doc"].items():
            for i, j in itertools.permutations(range(len(d["runs"])), 2):
                if d["sigs"][i] is None or d["sigs"][j] is None:
                    continue
                if not ID_WRONG & set(S.wrong_items(d["scored"][i])):
                    continue
                tot += 1
                caught += bool(S.disagree(d["sigs"][i], d["sigs"][j], "ID"))
    return caught, tot


for name, labels, gold in (
    ("P5", ["P5"], S.GOLD_T),
    ("H_P5", ["H_P5"], S.GOLD_H),
    ("pooled img150 tuning", S.IMG150_T, S.GOLD_T),
    ("pooled img150 held-out", S.IMG150_H, S.GOLD_H),
    ("pooled img150 all", None, None),
):
    if labels is None:
        c1, t1 = run(S.IMG150_T, S.GOLD_T)
        c2, t2 = run(S.IMG150_H, S.GOLD_H)
        c, t = c1 + c2, t1 + t2
    else:
        c, t = run(labels, gold)
    print(f"{name}: identity-wrong A reads caught {c}/{t}" + (f" ({100 * c / t:.1f}%)" if t else ""))

n_t = sum(len(g.get("lines", [])) for g in S.GOLD_T.values())
n_h = sum(len(g.get("lines", [])) for g in S.GOLD_H.values())
print(f"distinct gold line values: tuning {n_t}, held-out {n_h}, total {n_t + n_h}; "
      f"rule-of-three bound 3/{n_t + n_h} = {3 / (n_t + n_h):.4f}")
