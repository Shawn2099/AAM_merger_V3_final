"""Run the app's own VLM extraction over stored PDFs with a pluggable prompt pair.

Read-only w.r.t. the app: the SQLite DB is opened with mode=ro, nothing is written
to the DB or data/ folders. Results go to prompt_improvement/eval/results/<label>/.

Usage (from repo root):
  uv run python prompt_improvement/eval/run_extraction.py --label baseline --docs 12,15-18
  uv run python prompt_improvement/eval/run_extraction.py --label v2 --prompts cand.py --repeats 3
  (omit --docs to use every doc id in gold.json)

--prompts: a .py file path or dotted module exposing _SYSTEM_PROMPT and _PAGE_PROMPT
(SYSTEM_PROMPT / PAGE_PROMPT also accepted). Default: the live prompts in extraction.py.
The prompts are swapped by setting attributes on app.services.extraction in-process,
so the real _call_vlm (instructor + OpenRouter + schema) is what runs.
--model overrides cfg.vlm.model for this run only.

Output per doc: results/<label>/<doc_id>.json =
  {"doc_id", "label", "model", "prompt_hash", "runs": [{"repeat","ok","error","elapsed_s","result"}]}
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

EVAL_DIR = Path(__file__).resolve().parent
REPO = EVAL_DIR.parents[1]
sys.path.insert(0, str(REPO / "src"))

DB_PATH = REPO / "data" / "aam_merger.db"
RESULTS_DIR = EVAL_DIR / "results"
GOLD_PATH = EVAL_DIR / "gold.json"


def parse_doc_spec(spec: str) -> list[int]:
    ids: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            ids.extend(range(int(a), int(b) + 1))
        else:
            ids.append(int(part))
    return list(dict.fromkeys(ids))


def load_prompts(spec: str | None, extraction) -> tuple[str, str, str]:
    """Return (system, page, source_label)."""
    if not spec:
        return extraction._SYSTEM_PROMPT, extraction._PAGE_PROMPT, "live"
    p = Path(spec)
    if p.suffix == ".py" and p.exists():
        s = importlib.util.spec_from_file_location("candidate_prompts", p)
        mod = importlib.util.module_from_spec(s)
        s.loader.exec_module(mod)  # type: ignore[union-attr]
    else:
        mod = importlib.import_module(spec)
    system = getattr(mod, "_SYSTEM_PROMPT", None) or getattr(mod, "SYSTEM_PROMPT", None)
    page = getattr(mod, "_PAGE_PROMPT", None) or getattr(mod, "PAGE_PROMPT", None)
    if not system or not page:
        raise SystemExit(f"{spec}: needs _SYSTEM_PROMPT and _PAGE_PROMPT")
    return system, page, spec


def load_vlm_cfg(model_override: str | None) -> SimpleNamespace:
    """Only the vlm section is needed by _call_vlm; read YAML directly (no mkdirs)."""
    import yaml

    data = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8")) or {}
    v = data.get("vlm", {})
    vlm = SimpleNamespace(
        model=model_override or v.get("model"),
        request_timeout_seconds=v.get("request_timeout_seconds", 60),
        api_key_env_var=v.get("api_key_env_var", "OPENROUTER_API_KEY"),
    )
    return SimpleNamespace(vlm=vlm)


def fetch_docs(ids: list[int]) -> dict[int, dict]:
    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        out = {}
        for i in ids:
            row = con.execute(
                "select id, doc_type, stored_path, original_filename from documents where id=?",
                (i,),
            ).fetchone()
            if row:
                path = Path(row[2].replace("\\", "/"))
                if not path.is_absolute():
                    path = REPO / path
                out[i] = {"id": row[0], "db_type": row[1], "path": path, "name": row[3]}
        return out
    finally:
        con.close()


def one_call(extraction, doc: dict, cfg, retries: int) -> dict:
    t0 = time.time()
    last_err = None
    for attempt in range(retries + 1):
        try:
            res = extraction._call_vlm(str(doc["path"]), doc["db_type"], cfg)
            return {
                "ok": True,
                "error": None,
                "elapsed_s": round(time.time() - t0, 2),
                "result": res,
            }
        except Exception as e:  # noqa: BLE001 - record and continue the batch
            last_err = f"{type(e).__name__}: {str(e)[:500]}"
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    return {"ok": False, "error": last_err, "elapsed_s": round(time.time() - t0, 2), "result": None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--label", required=True, help="results sub-folder name")
    ap.add_argument("--docs", help="ids: '1,5,10-14'; default = all ids in gold.json")
    ap.add_argument("--prompts", help="candidate prompts: .py path or dotted module")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--retries", type=int, default=1, help="extra tries on exception per call")
    ap.add_argument("--model", help="override cfg.vlm.model")
    args = ap.parse_args()

    if args.docs:
        ids = parse_doc_spec(args.docs)
    elif GOLD_PATH.exists():
        ids = sorted(int(k) for k in json.loads(GOLD_PATH.read_text(encoding="utf-8")))
    else:
        raise SystemExit("no --docs and no gold.json")

    from app.services import extraction

    system, page, src = load_prompts(args.prompts, extraction)
    extraction._SYSTEM_PROMPT = system  # monkeypatch: _call_vlm reads these at call time
    extraction._PAGE_PROMPT = page
    phash = hashlib.sha256((system + "\n\x00\n" + page).encode()).hexdigest()[:12]
    cfg = load_vlm_cfg(args.model)

    docs = fetch_docs(ids)
    missing = [i for i in ids if i not in docs]
    if missing:
        print(f"WARNING: doc ids not in DB: {missing}", file=sys.stderr)
    out_dir = RESULTS_DIR / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"label={args.label} prompts={src} hash={phash} model={cfg.vlm.model} "
        f"docs={len(docs)} repeats={args.repeats}"
    )

    runs: dict[int, list] = {i: [None] * args.repeats for i in docs}
    jobs = [(i, r) for i in docs for r in range(args.repeats)]

    def work(job):
        i, r = job
        out = one_call(extraction, docs[i], cfg, args.retries)
        out["repeat"] = r
        return i, r, out

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [ex.submit(work, j) for j in jobs]
        for f in as_completed(futs):
            i, r, out = f.result()
            runs[i][r] = out
            status = "ok" if out["ok"] else "ERR " + str(out["error"])[:80]
            print(f"  doc {i} rep {r}: {status} ({out['elapsed_s']}s)")

    for i, rs in runs.items():
        payload = {
            "doc_id": i,
            "label": args.label,
            "model": cfg.vlm.model,
            "prompt_source": src,
            "prompt_hash": phash,
            "runs": rs,
        }
        (out_dir / f"{i}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    nerr = sum(1 for rs in runs.values() for r in rs if not r["ok"])
    print(f"done: {len(jobs)} calls, {nerr} errors -> {out_dir}")
    return 1 if jobs and nerr == len(jobs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
