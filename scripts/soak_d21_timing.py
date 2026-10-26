"""D21 timing table: what one sync pass costs with 0, 50 and 200 pending input files.

D21 (2026-10-25) decided against a re-hash cache: the idle poller already compares path,
mtime and size per tick, so an unchanged folder starts nothing. What a pass costs is the
stability poll on every waiting file, re-grouping and re-reconciling every open set. This
script measures those parts, with the **production** stability settings (2 s x 2, 8
parallel workers), so the plan's estimate can be checked against reality.

Not a test — run it by hand:

    uv run python scripts/soak_d21_timing.py --root %TEMP%\\aam_d21_soak --counts 0 50 200

It refuses to wipe a folder whose name does not contain "soak", keeps the provider key
empty, uses a canned VLM (no network) and writes only inside ``--root``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from _ws6_helpers import CannedVLM, plain_extract_task_for  # noqa: E402
from pypdf import PdfWriter  # noqa: E402

from app.core.config import load_config  # noqa: E402
from app.services import grouping, ingestion, reconciliation  # noqa: E402

TIMINGS: Counter[str] = Counter()
FILES_GIVEN = [0]


class _Patch:
    """The one method `CannedVLM` needs from pytest's monkeypatch."""

    @staticmethod
    def setattr(target, name, value) -> None:
        setattr(target, name, value)


def _write_pdf(path: Path, width: float) -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=200)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer.write(str(path))
    return path


def _payload(kind: str, number: str, po: str | None) -> dict:
    lines = [{"line_item_no": "1", "description": "BOLT", "quantity": "10", "dn_no": None}]
    return {
        "document_type": kind,
        "document_number": number,
        "po_no_raw": number if kind == "PO" else po,
        "po_reference": None if kind == "PO" else po,
        "po_reference_ambiguous": False,
        "vendor_name": "ACME",
        "components": [],
        "line_items": lines,
    }


def _cfg_for(root: Path):
    """A config pointed entirely at `root`, with the production stability settings kept."""
    # Resolved against the repository, not the working directory: `main` chdirs into
    # `--root` so that anything the app writes by relative path lands there too.
    cfg = load_config(REPO / "config.example.yaml")
    base = root.as_posix()
    cfg.paths.input_folder = f"{base}/input"
    cfg.paths.output_folder = f"{base}/output"
    cfg.paths.quarantine_folder = f"{base}/quarantine"
    cfg.paths.stored_documents_folder = f"{base}/stored"
    cfg.paths.combined_folder = f"{base}/combined"
    cfg.paths.archive_folder = f"{base}/archive"
    cfg.paths.log_folder = f"{base}/logs"
    cfg.paths.database_path = f"{base}/aam.db"
    cfg.manual_merge.temp_folder = f"{base}/manual_merge_tmp"
    cfg.schedule.poll_enabled = False
    for sub in ("input", "output", "quarantine", "stored", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return cfg


def _bind(cfg) -> None:
    """Point every module that resolves the config at this run's config."""
    import app.api.routes.dashboard as dash
    import app.api.routes.po_sets as po_routes
    import app.api.routes.sync as sync_route
    import app.flows.sync as flow_sync
    import app.services.sync_lock as sync_lock

    for mod in (dash, po_routes, sync_route, flow_sync, sync_lock):
        mod.load_config = lambda *a, **k: cfg
    flow_sync._extract_task_for = plain_extract_task_for


def _install_timers() -> None:
    """Time the parts of a pass that scale with the number of waiting files."""
    import app.flows.sync as flow_sync

    real_stability = flow_sync._poll_stability

    def timed_stability(files, cfg):
        count = len(files)
        started = time.perf_counter()
        try:
            return real_stability(files, cfg)
        finally:
            TIMINGS["stability_s"] += time.perf_counter() - started
            FILES_GIVEN[0] += count

    flow_sync._poll_stability = timed_stability

    real_ingest = ingestion.ingest_file

    def timed_ingest(src, cfg):
        started = time.perf_counter()
        try:
            return real_ingest(src, cfg)
        finally:
            TIMINGS["ingest_s"] += time.perf_counter() - started

    ingestion.ingest_file = timed_ingest

    real_resolve = grouping.resolve_unattached_documents

    def timed_resolve(cfg):
        started = time.perf_counter()
        try:
            return real_resolve(cfg)
        finally:
            TIMINGS["grouping_s"] += time.perf_counter() - started

    grouping.resolve_unattached_documents = timed_resolve

    real_attach = grouping.attach_unattached_to_open_sets

    def timed_attach(cfg):
        started = time.perf_counter()
        try:
            return real_attach(cfg)
        finally:
            TIMINGS["grouping_s"] += time.perf_counter() - started

    grouping.attach_unattached_to_open_sets = timed_attach

    real_reconcile = reconciliation.reconcile_po_set

    def timed_reconcile(po_set_id, cfg):
        started = time.perf_counter()
        try:
            return real_reconcile(po_set_id, cfg)
        finally:
            TIMINGS["reconcile_s"] += time.perf_counter() - started

    reconciliation.reconcile_po_set = timed_reconcile


def _seed_pending(cfg, canned: CannedVLM, count: int) -> None:
    """`count // 2` sets of PO + SI with no DN: their files stay in input."""
    inp = Path(cfg.paths.input_folder)
    for i in range(count // 2):
        po = f"4500070{i:04d}"
        canned.add(
            _write_pdf(inp / f"po_{i:04d}.pdf", 300.0 + 4 * i),
            _payload("PO", po, None),
        )
        canned.add(
            _write_pdf(inp / f"si_{i:04d}.pdf", 300.0 + 4 * i + 1),
            _payload("SI", f"SI-{po}", po),
        )


def _run_count(root: Path, count: int, canned: CannedVLM) -> tuple:
    import app.flows.sync as flow_sync

    sub = root / f"count_{count}"
    cfg = _cfg_for(sub)
    _bind(cfg)
    _seed_pending(cfg, canned, count)

    cold = flow_sync.sync_flow.fn()
    if cold.get("errors"):
        raise SystemExit(f"[ERROR] cold pass with {count} pending files: {cold}")

    inp = Path(cfg.paths.input_folder)
    new_po = "4500079999"
    canned.add(_write_pdf(inp / "new_po.pdf", 900.0), _payload("PO", new_po, None))

    TIMINGS.clear()
    FILES_GIVEN[0] = 0
    calls_before = sum(canned.calls.values())
    started = time.perf_counter()
    warm = flow_sync.sync_flow.fn()
    total = time.perf_counter() - started
    if warm.get("errors"):
        raise SystemExit(f"[ERROR] warm pass with {count} pending files: {warm}")
    return (
        count,
        TIMINGS["stability_s"],
        TIMINGS["ingest_s"],
        TIMINGS["grouping_s"],
        TIMINGS["reconcile_s"],
        total,
        sum(canned.calls.values()) - calls_before,
        FILES_GIVEN[0],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--root", required=True, help='scratch folder; its name must contain "soak"'
    )
    parser.add_argument("--counts", type=int, nargs="+", default=[0, 50, 200])
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if "soak" not in root.name.lower():
        raise SystemExit(f"refusing to wipe {root}: its name must contain 'soak'")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    # Extraction dumps a debug copy next to the log folder and falls back to
    # `data/raw_extractions` relative to the CWD; running from `--root` keeps every
    # byte this script writes inside the scratch folder.
    os.chdir(root)

    # Fail closed on the provider: no key, no network, canned answers only.
    os.environ["OPENROUTER_API_KEY"] = ""
    canned = CannedVLM(_Patch)
    _install_timers()

    rows = [_run_count(root, count, canned) for count in args.counts]

    print(
        f"{'pending':>7} | {'stability_s':>11} | {'ingest_s':>8} | "
        f"{'grouping_s':>10} | {'reconcile_s':>11} | {'total_s':>7} | "
        f"{'vlm_calls':>9} | files_given"
    )
    for count, stability, ingest, group, reconcile, total, calls, given in rows:
        print(
            f"{count:>7} | {stability:>11.2f} | {ingest:>8.2f} | {group:>10.2f} | "
            f"{reconcile:>11.2f} | {total:>7.2f} | {calls:>9} | {given}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
