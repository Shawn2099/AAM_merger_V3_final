"""Would "the value must appear in the PDF text layer" catch wrong reads? No API calls.

The PDF text layer is NOT truth (see EXTRACTION_EVAL_FINDINGS section 1); it is used here only as
a verifier signal. Documents without a usable text layer (scans) are "not applicable": the check
cannot run on them and must not hold them.

For every scored value of every saved read (document_number, po_reference, line quantities):
  outcome per gold (correct / wrong) x present-in-text (yes / no / n.a.).
Catch rate   = wrong values absent from text / wrong values on text-layer docs.
False holds  = correct values absent from text / correct values on text-layer docs.

Matching modes for identifiers:
  alnum  : value upper-cased, non-alphanumerics removed, substring of the same transform of the
           text (lenient; "22613 , 0" and "226130" both match a page printing "22613 , 0").
  token  : value with whitespace removed must be a substring of the text with whitespace
           removed, punctuation kept (strict; catches punctuation rewrites).
Quantities: every number-like token on the page is parsed with the app's parser (x1000) and the
read's per-row quantity must be in that set.

Usage (repo root):
  uv run --no-sync python prompt_improvement/eval/text_layer_check.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

EVAL_DIR = Path(__file__).resolve().parent
REPO = EVAL_DIR.parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(EVAL_DIR))

from score import CORRECT, WRONG, score_run  # noqa: E402
from second_read_analysis import GOLD_H, GOLD_T, IMG150_H, IMG150_T, load  # noqa: E402

from app.services.extraction import _parse_scaled_int  # noqa: E402
from app.services.matching import normalize_line_no  # noqa: E402

DB = REPO / "data" / "aam_merger.db"
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def doc_paths(ids: list[int]) -> dict[int, Path]:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        out = {}
        for i in ids:
            row = con.execute("select stored_path from documents where id=?", (i,)).fetchone()
            if row:
                p = Path(row[0].replace("\\", "/"))
                out[i] = p if p.is_absolute() else REPO / p
        return out
    finally:
        con.close()


def page_text(path: Path) -> str:
    try:
        with fitz.open(path) as d:
            return "\n".join(pg.get_text() for pg in d)
    except Exception as e:  # noqa: BLE001 - a missing/corrupt file is reported, not fatal
        return f"<<ERROR {type(e).__name__}>>"


def alnum(s: str) -> str:
    return "".join(c for c in s if c.isalnum()).upper()


def nows(s: str) -> str:
    return "".join(s.split()).upper()


class Page:
    def __init__(self, text: str):
        self.raw = text
        self.has_text = len(alnum(text)) >= 40 and not text.startswith("<<ERROR")
        self.a = alnum(text)
        self.t = nows(text)
        self.nums = set()
        for m in NUM_RE.findall(text):
            v = _parse_scaled_int(m)
            if v > 0:
                self.nums.add(v)
            v2 = _parse_scaled_int(m.replace(",", ""))
            if v2 > 0:
                self.nums.add(v2)

    def has_id(self, v: str, mode: str) -> bool:
        return (alnum(v) in self.a) if mode == "alnum" else (nows(v) in self.t)


def run(labels: list[str], gold: dict, pages: dict[int, Page]) -> dict:
    c = Counter()
    misses = []
    for lab in labels:
        res = load(lab)
        for sid, g in gold.items():
            did = int(sid)
            pg = pages.get(did)
            for p in res.get(did, []):
                if p is None:
                    continue
                sc = score_run(g, p)
                if pg is None or not pg.has_text:
                    c["values_no_text_layer"] += 1
                    continue
                for f in ("document_number", "po_reference"):
                    o = sc["fields"].get(f)
                    v = p.get(f)
                    if o not in (CORRECT, WRONG) or not v:
                        continue
                    for mode in ("alnum", "token"):
                        present = pg.has_id(str(v), mode)
                        c[f"id_{mode}_{o}"] += 1
                        if not present:
                            c[f"id_{mode}_{o}_absent"] += 1
                            if mode == "token" and o == CORRECT:
                                misses.append((lab, did, f, v))
                    if o == WRONG:
                        misses.append((lab, did, f + " WRONG", v, pg.has_id(str(v), "alnum"), pg.has_id(str(v), "token")))
                per = sc.get("lines", {})
                for ln in p.get("line_items") or []:
                    key = normalize_line_no(ln.get("line_item_no"))
                    o = per.get(key)
                    if key in sc.get("extra_lines", []):
                        o = WRONG
                    if o not in (CORRECT, WRONG):
                        continue
                    q = _parse_scaled_int(ln.get("quantity"))
                    if q <= 0:
                        continue
                    c[f"qty_{o}"] += 1
                    if q not in pg.nums:
                        c[f"qty_{o}_absent"] += 1
                        if o == CORRECT:
                            misses.append((lab, did, "qty", ln.get("quantity")))
    return {"c": c, "misses": misses}


def main() -> int:
    ids = sorted({int(k) for k in GOLD_T} | {int(k) for k in GOLD_H})
    paths = doc_paths(ids)
    pages = {i: Page(page_text(p)) for i, p in paths.items()}
    with_text = sorted(i for i, pg in pages.items() if pg.has_text)
    print(f"docs: {len(ids)}, found in DB: {len(paths)}, with a text layer: {len(with_text)}")
    print(f"  tuning with text: {sum(1 for i in with_text if str(i) in GOLD_T)}/{len(GOLD_T)}")
    print(f"  held-out with text: {sum(1 for i in with_text if str(i) in GOLD_H)}/{len(GOLD_H)}")
    print(f"  no text layer: {sorted(set(ids) - set(with_text))}")

    def pct(n, d):
        return f"{n}/{d} ({100.0 * n / d:.1f}%)" if d else "0/0"

    for name, labels, gold in (
        ("P5 (tuning)", ["P5"], GOLD_T),
        ("H_P5 (held-out)", ["H_P5"], GOLD_H),
        ("pooled img150 tuning", IMG150_T, GOLD_T),
        ("pooled img150 held-out", IMG150_H, GOLD_H),
        ("pooled native/ocr tuning", ["V0", "V1", "baseline", "V2ok"], GOLD_T),
    ):
        r = run(labels, gold, pages)
        c = r["c"]
        print(f"\n## {name}  (values on docs without text layer, skipped: {c['values_no_text_layer']} reads)")
        for mode in ("alnum", "token"):
            print(
                f"  ids [{mode}]: WRONG caught (absent) {pct(c[f'id_{mode}_wrong_absent'], c[f'id_{mode}_wrong'])}; "
                f"CORRECT falsely absent {pct(c[f'id_{mode}_correct_absent'], c[f'id_{mode}_correct'])}"
            )
        print(
            f"  qty: WRONG caught (absent) {pct(c['qty_wrong_absent'], c['qty_wrong'])}; "
            f"CORRECT falsely absent {pct(c['qty_correct_absent'], c['qty_correct'])}"
        )
        seen = set()
        for m in r["misses"]:
            k = (m[1], m[2], str(m[3]))
            if k in seen:
                continue
            seen.add(k)
            print("   ", m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
