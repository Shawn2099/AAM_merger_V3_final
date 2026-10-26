"""Run a transport/parameter variant over the gold set (additive to run_extraction.py).

  uv run python prompt_improvement/eval/run_variants.py --label V1 --variant native --repeats 3
Results land in results/<label>/ in the format run_extraction.py writes, so score.py works.
Each result carries "_usage" (tokens/cost/latency); usage_report.py summarises it.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_extraction as R  # noqa: E402
import variants  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--variant", required=True)
    ap.add_argument("--docs")
    ap.add_argument("--prompts", help="candidate prompts .py (_SYSTEM_PROMPT/_PAGE_PROMPT)")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--retries", type=int, default=1)
    a = ap.parse_args()
    from app.core.config import load_env
    from app.services import extraction

    load_env()
    ids = (
        R.parse_doc_spec(a.docs)
        if a.docs
        else sorted(int(k) for k in json.loads(R.GOLD_PATH.read_text(encoding="utf-8")))
    )
    if a.prompts:
        system, page, _src = R.load_prompts(a.prompts, extraction)
        extraction._SYSTEM_PROMPT, extraction._PAGE_PROMPT = system, page
    extraction._call_vlm = variants.make_call_vlm(a.variant)
    cfg = R.load_vlm_cfg(None)
    docs = R.fetch_docs(ids)
    out_dir = R.RESULTS_DIR / a.label
    out_dir.mkdir(parents=True, exist_ok=True)
    runs = {i: [None] * a.repeats for i in docs}
    jobs = [(i, r) for i in docs for r in range(a.repeats)]

    def work(j):
        i, r = j
        o = R.one_call(extraction, docs[i], cfg, a.retries)
        o["repeat"] = r
        return i, r, o

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for f in as_completed([pool.submit(work, j) for j in jobs]):
            i, r, o = f.result()
            runs[i][r] = o
            if not o["ok"]:
                print(f"  doc {i} rep {r}: ERR {o['error'][:300]}")
    for i, rs in runs.items():
        payload = {
            "doc_id": i,
            "label": a.label,
            "model": cfg.vlm.model,
            "variant": a.variant,
            "prompt_source": a.prompts or "live",
            "runs": rs,
        }
        (out_dir / f"{i}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    nerr = sum(1 for rs in runs.values() for r in rs if not r["ok"])
    print(f"{a.label} [{a.variant}] done: {len(jobs)} calls, {nerr} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
