"""Read-only browser sweep of a running AAM Merger instance (developer helper).

Not a pytest test and not collected by pytest. It exercises the pages an operator
uses and reports console errors, page errors, failed/4xx requests, cross-host
requests, titles, responsive overflow and the CSRF/Origin guard. It changes no
data, so it is safe to point at a running UI harness.

    uv run python scripts/ui_explore_global.py                       # http://127.0.0.1:8765
    uv run python scripts/ui_explore_global.py --base http://127.0.0.1:8000
    uv run python scripts/ui_explore_global.py --json report.json

The harness to point it at:
    uv run python scripts/ui_test_env.py --root data/ui_test --serve

Set cases are found from manifest.json when `--root` is given (default
data/ui_test), so the sweep visits real seeded set ids instead of guessed ones.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = [
    ("dashboard", "/dashboard"),
    ("unclassified", "/unclassified"),
    ("quarantine", "/quarantine"),
    ("audit", "/audit"),
    ("manual_merger", "/manual/merger"),
]


def _set_pages(root: Path) -> list[tuple[str, str]]:
    """Up to three seeded set pages, from the harness manifest when available."""
    manifest = root / "manifest.json"
    if not manifest.is_file():
        return []
    data = json.loads(manifest.read_text(encoding="utf-8"))
    scenarios = data.get("scenarios") or {}
    wanted = ("merged_basic", "needs_review_quantity", "quarantined_unmatched_vendor_line")
    names = [n for n in wanted if n in scenarios] or list(scenarios)[:3]
    return [(n, f"/po_sets/{scenarios[n]['id']}/view") for n in names]


def sweep(base: str, pages: list[tuple[str, str]]) -> dict:
    report: dict = {"base": base, "pages": {}, "titles": {}, "widths": {}, "origin": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        for name, path in pages:
            seen = {
                "console": [],
                "page_errors": [],
                "failed": [],
                "http_errors": [],
                "foreign": [],
            }

            def on_console(msg, seen=seen):
                if msg.type == "error":
                    seen["console"].append(msg.text)

            def on_pageerror(exc, seen=seen):
                seen["page_errors"].append(str(exc))

            def on_requestfailed(req, seen=seen):
                seen["failed"].append(f"{req.method} {req.url} :: {req.failure}")

            def on_response(resp, seen=seen):
                if resp.status >= 400:
                    seen["http_errors"].append(f"{resp.status} {resp.request.method} {resp.url}")

            def on_request(req, seen=seen, base=base):
                if not req.url.startswith(base):
                    seen["foreign"].append(req.url)

            handlers = [
                ("console", on_console),
                ("pageerror", on_pageerror),
                ("requestfailed", on_requestfailed),
                ("response", on_response),
                ("request", on_request),
            ]
            for event, handler in handlers:
                page.on(event, handler)

            resp = page.goto(base + path, wait_until="load")
            page.wait_for_timeout(400)
            report["pages"][name] = {
                "status": resp.status if resp else None,
                "title": page.title(),
                **seen,
            }
            report["titles"][name] = page.title()

            for event, handler in handlers:
                page.remove_listener(event, handler)

        for width in (1024, 1366, 768):
            page.set_viewport_size({"width": width, "height": 900})
            for name, path in (
                ("dashboard", "/dashboard"),
                ("set", next((p for n, p in pages if n.startswith("set")), "/dashboard")),
            ):
                page.goto(base + path, wait_until="load")
                page.wait_for_timeout(300)
                metrics = page.evaluate(
                    "() => ({scrollWidth: document.documentElement.scrollWidth,"
                    " clientWidth: document.documentElement.clientWidth})"
                )
                report["widths"].setdefault(str(width), {})[name] = {
                    **metrics,
                    "horizontal_overflow": metrics["scrollWidth"] > metrics["clientWidth"],
                }

        foreign = {"Origin": "http://evil.example"}
        report["origin"]["foreign_post_merge"] = _short(
            ctx.request.post(base + "/po_sets/9/merge", headers=foreign)
        )
        report["origin"]["foreign_delete_quarantine"] = _short(
            ctx.request.delete(base + "/po_sets/10/quarantine", headers=foreign)
        )
        report["origin"]["foreign_get_dashboard"] = _short(
            ctx.request.get(base + "/dashboard", headers=foreign)
        )

        browser.close()
    return report


def _short(response) -> dict:
    return {"status": response.status, "body": response.text()[:200]}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--root", default="data/ui_test", help="harness root (for manifest.json)")
    ap.add_argument("--json", default="", help="write the report here instead of stdout")
    args = ap.parse_args()

    pages = PAGES + _set_pages(Path(args.root))
    report = sweep(args.base.rstrip("/"), pages)
    text = json.dumps(report, indent=2)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
        print(f"wrote {args.json}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
