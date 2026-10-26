# ruff: noqa: E402, E501, I001
"""Human-readable census of every UI route, state and tab.

This is the "walk the app like an operator" pass. It builds the same throwaway
harness as the pytest suite, opens every route in a real Chromium, and captures:

* a full-page screenshot at 1440 px and 800 px;
* console errors, uncaught JS errors, failed requests and 4xx/5xx responses;
* axe violations (critical/serious are highlighted);
* row counts and database counts for the dashboard;
* a one-file HTML report for review.

    uv run python scripts/ui_census.py
    uv run python scripts/ui_census.py --headed --no-fail

Nothing is sent off the machine. The environment is synthetic and is deleted
and rebuilt on every run.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import ui_test_env

from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import sync_playwright

REPORT_DIR = REPO / "reports" / "ui-census"
SHOT_DIR = REPORT_DIR / "screens"
FAVICON = "favicon.ico"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _db_counts(db_path: Path) -> dict:
    if not db_path.exists():
        return {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        counts = {
            row[0]: row[1]
            for row in con.execute(
                "select status, count(*) from po_sets where archived_at is null group by status"
            )
        }
        counts["total"] = sum(counts.values())
        counts["held"] = con.execute(
            "select count(*) from documents where po_set_id is null and archived_at is null"
        ).fetchone()[0]
        return counts
    finally:
        con.close()


def _routes(manifest: dict) -> list[dict]:
    routes: list[dict] = [
        {"id": "dashboard", "path": "/dashboard", "expected": 200},
        {"id": "dashboard-pending", "path": "/dashboard?status=pending", "expected": 200},
        {"id": "dashboard-mismatched", "path": "/dashboard?status=mismatched", "expected": 200},
        {
            "id": "dashboard-blocked-customs",
            "path": "/dashboard?status=blocked_customs",
            "expected": 200,
        },
        {"id": "dashboard-merged", "path": "/dashboard?status=merged", "expected": 200},
        {"id": "dashboard-quarantined", "path": "/dashboard?status=quarantined", "expected": 200},
    ]
    for name in (
        "merged_basic",
        "needs_review_quantity",
        "awaiting_customs",
        "quarantined_unmatched_vendor_line",
        "archived_readonly",
        "merged_with_customs",
        "description_match_target",
    ):
        scenario = manifest["scenarios"].get(name)
        if scenario:
            routes.append(
                {
                    "id": f"set-{name}",
                    "path": f"/po_sets/{scenario['id']}/view",
                    "expected": 200,
                }
            )
    routes += [
        {"id": "unclassified", "path": "/unclassified", "expected": 200},
        {"id": "quarantine", "path": "/quarantine", "expected": 200},
        {"id": "audit", "path": "/audit", "expected": 200},
        {"id": "settings", "path": "/settings", "expected": 200},
        {"id": "merger", "path": "/manual/merger", "expected": 200},
        {"id": "not-found", "path": "/po_sets/999999/view", "expected": 404},
        {"id": "bad-filter", "path": "/dashboard?status=nonsense", "expected": 200},
    ]
    return routes


def _capture(page, axe, record: dict, base: str, out: Path) -> None:
    console: list[dict] = []
    errors: list[str] = []
    failed: list[dict] = []
    bad: list[dict] = []

    def on_console(message):
        if message.type == "error":
            console.append({"text": message.text, "location": message.location})

    page.on("console", on_console)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on(
        "requestfailed",
        lambda request: failed.append(
            {"url": request.url, "failure": str(request.failure)}
        ),
    )
    page.on(
        "response",
        lambda response: response.status >= 400
        and bad.append({"status": response.status, "url": response.url}),
    )

    started = time.perf_counter()
    response = page.goto(base + record["path"], wait_until="networkidle")
    elapsed = round((time.perf_counter() - started) * 1000)
    page.wait_for_timeout(250)  # let Alpine settle x-show/x-cloak

    record["url"] = page.url
    record["status"] = response.status if response else None
    record["elapsed_ms"] = elapsed
    record["title"] = page.title()
    record["h1"] = [text.strip() for text in page.locator("h1").all_inner_texts()]
    record["h2"] = [text.strip() for text in page.locator("h2, h3").all_inner_texts()][:12]
    record["rows"] = {
        "po": page.get_by_test_id("po-row").count(),
        "quarantine": page.get_by_test_id("quarantine-row").count(),
        "unclassified": page.get_by_test_id("unclassified-row").count(),
        "audit": page.get_by_test_id("audit-row").count(),
        "documents": page.get_by_test_id("doc-row").count(),
    }

    results = axe.run(page)
    record["axe"] = {
        "violations": [
            {
                "id": violation["id"],
                "impact": violation.get("impact"),
                "help": violation.get("help"),
                "nodes": len(violation.get("nodes", [])),
            }
            for violation in results.response["violations"]
        ]
    }

    screens = {}
    for width, height, label in ((1440, 900, "1440"), (800, 900, "800")):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(150)
        shot = out / f"{_slug(record['id'])}-{label}.png"
        page.screenshot(path=shot, full_page=True, animations="disabled")
        screens[label] = shot.relative_to(REPORT_DIR).as_posix()
    page.set_viewport_size({"width": 1440, "height": 900})
    record["screens"] = screens

    record["console"] = console
    record["pageerrors"] = errors
    record["failed"] = failed
    record["bad_responses"] = bad

    page.remove_listener("console", on_console)


def _problems(record: dict) -> list[str]:
    problems = []
    if record.get("status") != record.get("expected"):
        problems.append(f"status {record.get('status')} (expected {record.get('expected')})")
    for message in record["pageerrors"]:
        problems.append(f"JS error: {message}")
    for message in record["console"]:
        if FAVICON not in (message.get("location") or {}).get("url", ""):
            problems.append(f"console: {message['text']}")
    for request in record["failed"]:
        problems.append(f"request failed: {request['url']}")
    for response in record["bad_responses"]:
        if FAVICON not in response["url"] and not (
            record.get("expected") == 404 and response["status"] == 404
        ):
            problems.append(f"HTTP {response['status']}: {response['url']}")
    for violation in record["axe"]["violations"]:
        if violation["impact"] in ("critical", "serious"):
            problems.append(
                f"axe {violation['impact']}: {violation['id']} ({violation['nodes']} nodes)"
            )
    return problems


def _write_report(records: list[dict], counts: dict, base: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "census.json").write_text(
        json.dumps({"base": base, "counts": counts, "records": records}, indent=2),
        encoding="utf-8",
    )

    total_problems = sum(len(_problems(record)) for record in records)
    rows = []
    cards = []
    for record in records:
        problems = _problems(record)
        status_class = "bad" if problems else "ok"
        a11y = sum(
            1 for v in record["axe"]["violations"] if v["impact"] in ("critical", "serious")
        )
        rows.append(
            "<tr class='{cls}'>"
            "<td><a href='#{anchor}'>{title}</a><div class='path'>{path}</div></td>"
            "<td>{status}</td><td>{time} ms</td><td>{po}/{quar}/{uncl}/{docs}</td>"
            "<td>{a11y}</td><td>{problems}</td>"
            "<td><a href='{shot1440}'><img loading='lazy' src='{shot1440}' alt='1440'></a></td>"
            "</tr>".format(
                cls=status_class,
                anchor=record["id"],
                title=html.escape(record["id"]),
                path=html.escape(record["path"]),
                status=record.get("status"),
                time=record.get("elapsed_ms", 0),
                po=record["rows"]["po"],
                quar=record["rows"]["quarantine"],
                uncl=record["rows"]["unclassified"],
                docs=record["rows"]["documents"],
                a11y=a11y,
                problems=html.escape("; ".join(problems)) or "—",
                shot1440=record["screens"]["1440"],
            )
        )
        cards.append(
            """
            <details id="{anchor}" class="{cls}">
              <summary>{title} <span class="path">{path}</span>
                <span class="badge">{status}</span>
                <span class="badge">{problems}</span>
              </summary>
              <p class="meta">Title: {page_title} · {time} ms · rows PO/quarantine/unclassified/docs:
                 {po}/{quar}/{uncl}/{docs}</p>
              <p class="meta">Headings: {headings}</p>
              <div class="shots">
                <figure><figcaption>1440 px</figcaption>
                  <a href="{shot1440}"><img loading="lazy" src="{shot1440}" alt="1440"></a></figure>
                <figure><figcaption>800 px</figcaption>
                  <a href="{shot800}"><img loading="lazy" src="{shot800}" alt="800"></a></figure>
              </div>
              <pre>{details}</pre>
            </details>
            """.format(
                anchor=record["id"],
                cls="bad" if problems else "ok",
                title=html.escape(record["id"]),
                path=html.escape(record["path"]),
                status=record.get("status"),
                problems=html.escape("; ".join(problems)) or "clean",
                page_title=html.escape(record.get("title", "")),
                time=record.get("elapsed_ms", 0),
                po=record["rows"]["po"],
                quar=record["rows"]["quarantine"],
                uncl=record["rows"]["unclassified"],
                docs=record["rows"]["documents"],
                headings=html.escape(" | ".join(record["h1"] + record["h2"])),
                shot1440=record["screens"]["1440"],
                shot800=record["screens"]["800"],
                details=html.escape(
                    json.dumps(
                        {
                            "console": record["console"],
                            "pageerrors": record["pageerrors"],
                            "failed": record["failed"],
                            "bad_responses": record["bad_responses"],
                            "axe": record["axe"]["violations"],
                        },
                        indent=2,
                    )
                ),
            )
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>UI census — CA redesign</title>
<style>
  body {{ font: 14px/1.5 Segoe UI, system-ui, sans-serif; margin: 24px; color:#0f172a; }}
  h1 {{ margin-bottom: 4px; }}
  .sub {{ color:#64748b; margin-bottom: 24px; }}
  .summary {{ display:flex; gap:16px; margin:16px 0 28px; }}
  .stat {{ background:#f1f5f9; border:1px solid #e2e8f0; border-radius:8px; padding:12px 18px; }}
  .stat b {{ display:block; font-size:24px; }}
  table {{ border-collapse: collapse; width:100%; }}
  th, td {{ border-bottom:1px solid #e2e8f0; padding:8px; text-align:left; vertical-align:top; }}
  th {{ background:#f8fafc; position:sticky; top:0; }}
  tr.bad {{ background:#fff1f2; }}
  tr.ok {{ background:#f0fdf4; }}
  img {{ max-width:220px; max-height:140px; border:1px solid #cbd5e1; border-radius:4px; }}
  .path {{ color:#64748b; font-family:ui-monospace, monospace; font-size:12px; }}
  .badge {{ background:#e2e8f0; border-radius:999px; padding:1px 8px; font-size:12px; }}
  .shots {{ display:flex; gap:16px; flex-wrap:wrap; }}
  .shots img {{ max-width:100%; max-height:70vh; }}
  details {{ border:1px solid #e2e8f0; border-radius:8px; padding:8px 12px; margin:10px 0; }}
  details.bad {{ border-color:#fecdd3; }}
  summary {{ cursor:pointer; }}
  pre {{ background:#f8fafc; padding:10px; overflow:auto; font-size:12px; }}
  .meta {{ color:#475569; font-size:12px; }}
</style>
</head>
<body>
<h1>UI census — CA redesign</h1>
<div class="sub">Every route and tab, captured in a real Chromium against the synthetic harness.
Generated {time.strftime("%Y-%m-%d %H:%M:%S")}. Base: {html.escape(base)}</div>
<div class="summary">
  <div class="stat"><b>{len(records)}</b>routes captured</div>
  <div class="stat"><b>{total_problems}</b>problems</div>
  <div class="stat"><b>{sum(1 for r in records if any(v["impact"] in ("critical","serious") for v in r["axe"]["violations"]))}</b>pages with serious axe</div>
  <div class="stat"><b>{counts.get("total", 0)}</b>active sets · {counts.get("held", 0)} held docs</div>
</div>
<table>
<thead><tr><th>Route</th><th>HTTP</th><th>Load</th><th>Rows</th><th>Serious axe</th><th>Problems</th><th>Preview</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
<h2>Route details</h2>
{''.join(cards)}
</body></html>"""
    index = REPORT_DIR / "index.html"
    index.write_text(document, encoding="utf-8")
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO / "data" / "ui_test_census"))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--no-fail", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if "ui_test" not in root.name:
        raise SystemExit("the census root name must contain 'ui_test'")

    print(f"building synthetic environment at {root} ...")
    manifest = ui_test_env.build(root)
    proc, base = ui_test_env.serve(root, args.port or None)
    print(f"serving {base}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(SHOT_DIR, ignore_errors=True)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    counts = _db_counts(root / "aam_merger.db")
    records: list[dict] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not args.headed)
            context = browser.new_context(
                viewport={"width": 1440, "height": 900}, accept_downloads=True
            )
            page = context.new_page()
            axe = Axe()
            for route in _routes(manifest):
                record = {
                    **route,
                    "console": [],
                    "pageerrors": [],
                    "failed": [],
                    "bad_responses": [],
                    "screens": {},
                }
                try:
                    _capture(page, axe, record, base, SHOT_DIR)
                except Exception as exc:  # keep the census going
                    record["pageerrors"].append(f"census capture failed: {exc}")
                    record.setdefault("rows", {"po": 0, "quarantine": 0, "unclassified": 0, "audit": 0, "documents": 0})
                    record.setdefault("title", "")
                    record.setdefault("h1", [])
                    record.setdefault("h2", [])
                    record.setdefault("elapsed_ms", 0)
                    record.setdefault("axe", {"violations": []})
                    record.setdefault("screens", {"1440": "", "800": ""})
                records.append(record)
                problems = _problems(record)
                print(f"  {'FAIL' if problems else 'ok  '} {route['path']}")
                for problem in problems:
                    print(f"         - {problem}")
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # pragma: no cover
            proc.kill()

    index = _write_report(records, counts, base)
    problems = sum(len(_problems(record)) for record in records)
    print(f"\n{len(records)} routes, {problems} problems")
    print(f"report: {index}")
    return 0 if args.no_fail or problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
