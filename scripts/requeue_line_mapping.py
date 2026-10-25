"""Re-reconcile quarantined/pending sets whose verdict changes under the line-number fallback.

Why this exists
---------------
Sets quarantined before the fallback shipped (PO 10,20,30 vs DN/SI 1,2,3, or PO
`1-1` vs DN/SI `1`) are never re-reconciled by the engine: a quarantined set keeps
its stored reason until an operator presses Redo matching. This one-off script
finds the sets that the fallback would change, and can re-run them in one go.

Default is a DRY RUN: the database is opened read-only and nothing is written.
Pass `--apply` to re-run reconcile on exactly the listed sets, the same way the
Redo matching button does (reset quarantined -> pending under the per-set lock,
then `reconcile_po_set`, which writes the normal reason, audit and merge output).

Usage:
    python scripts/requeue_line_mapping.py [--config PATH] [--db PATH] [--apply]

`--config` defaults to $AAM_CONFIG_PATH or config.yaml; `--db` overrides
`paths.database_path`. Refuses `--apply` while a sync is running.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import AppConfig, load_config  # noqa: E402
from app.core.database import get_engine  # noqa: E402
from app.models import DocType, POSet, POSetStatus  # noqa: E402
from app.services.reconciliation import compare_po_set_lines  # noqa: E402

ACTION = "requeue_line_mapping"
_ADDONS = (DocType.CUSTOMS.value, DocType.SHIPPING.value)


@dataclass(frozen=True)
class Candidate:
    po_set_id: int
    po_no: str
    status: str
    old_verdict: str
    new_verdict: str
    label: str


def verdict(comparison: dict) -> str:
    """The engine's verdict rules, applied to a `compare_po_set_lines` result."""
    if comparison["po_fail"]:
        return "quarantined"
    failing = [f for f in comparison["flags"] if f["type"] in ("identification", "quantity")]
    if not failing:
        return "merged"
    if any(f["type"] == "identification" for f in failing):
        return "quarantined"
    if any((f.get("vendor_quantity") or 0) > 0 for f in failing if f["type"] == "quantity"):
        return "mismatched"
    return "pending"


def _value(x) -> str:
    return x.value if hasattr(x, "value") else str(x)


def _predict(ps: POSet, cfg: AppConfig) -> Candidate | None:
    """The change the fallback would make to this set, or None when there is none.

    Mirrors the engine's early guards, so a set the engine would hold for another
    reason (a failed document, two PO documents, a non-positive quantity) is not
    listed: re-running it could not change its outcome.
    """
    docs = list(ps.documents or [])
    by_type: dict[str, list] = {"PO": [], "DN": [], "SI": []}
    for d in docs:
        t = _value(d.doc_type)
        if t in by_type:
            by_type[t].append(d)
        elif t not in _ADDONS:
            return None  # unclassified document attached
        if t not in _ADDONS and _value(d.extraction_status) != "valid":
            return None
    if not all(by_type.values()):
        return None
    if cfg.reconciliation.single_po_document and len(by_type["PO"]) > 1:
        return None

    def lines(group: list) -> list[dict]:
        return [
            {
                "line_item_no": li.line_item_no,
                "description": li.description,
                "quantity": li.quantity,
                "document_id": d.id,
            }
            for d in group
            for li in d.line_items
        ]

    po, dn, si = lines(by_type["PO"]), lines(by_type["DN"]), lines(by_type["SI"])
    if not po or any(int(r.get("quantity") or 0) <= 0 for r in (*po, *dn, *si)):
        return None
    thr = cfg.matching.fuzzy_description_threshold
    old = compare_po_set_lines(po, dn, si, thr)
    new = compare_po_set_lines(po, dn, si, thr, line_fallback=cfg.matching.line_number_fallback)
    label = new["line_mapping"]["label"]
    if not label or verdict(old) == verdict(new):
        return None
    if _value(ps.status) == verdict(new) and ps.line_mapping_rule == label:
        return None  # already re-run under the fallback; nothing left to do
    return Candidate(
        po_set_id=ps.id,
        po_no=ps.po_no_normalized or "",
        status=_value(ps.status),
        old_verdict=verdict(old),
        new_verdict=verdict(new),
        label=label,
    )


def find_candidates(session: Session, cfg: AppConfig) -> list[Candidate]:
    """Quarantined or pending, non-archived sets whose verdict would change. Read-only."""
    sets = (
        session.execute(
            select(POSet)
            .where(
                POSet.archived_at.is_(None),
                POSet.status.in_((POSetStatus.quarantined, POSetStatus.pending)),
            )
            .order_by(POSet.id)
        )
        .scalars()
        .all()
    )
    return [c for ps in sets if (c := _predict(ps, cfg)) is not None]


def _readonly_engine(db_path: Path):
    return create_engine(f"sqlite:///file:{db_path.as_posix()}?mode=ro&uri=true")


def _requeue(cand: Candidate, cfg: AppConfig) -> str:
    """Re-run one set the way Redo matching does. Returns the resulting status."""
    from app.services.locking import acquire_lock, release_lock
    from app.services.reconciliation import reconcile_po_set

    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, cand.po_set_id)
        if ps is None:
            return "missing"
        if not acquire_lock(ps, ACTION, s, cfg):
            return f"skipped (locked by {ps.locked_by_action})"
    try:
        with Session(eng) as s:
            ps = s.get(POSet, cand.po_set_id)
            # Redo matching: the explicit gate that lets a quarantined set re-enter.
            if ps is not None and ps.status == POSetStatus.quarantined:
                ps.status = POSetStatus.pending
                s.commit()
        result = reconcile_po_set(cand.po_set_id, cfg)
        return str(result.get("status"))
    finally:
        with Session(eng) as s:
            ps = s.get(POSet, cand.po_set_id)
            if ps is not None:
                release_lock(ps, s, ACTION)


def _acquire_sync_lock(db_path: Path):
    """Try-acquire the sync lock that sits next to `db_path`; None when held or on any error."""
    import contextlib
    import time

    from filelock import FileLock, Timeout

    from app.services import sync_lock

    try:
        lock_file = db_path.parent.resolve() / sync_lock._LOCK_NAME
        lock = FileLock(str(lock_file), timeout=0, thread_local=False)
        lock.acquire(timeout=0)
    except Timeout:
        return None
    except Exception:
        return None
    with contextlib.suppress(OSError):
        sync_lock._sidecar_for(lock_file).write_text(str(time.time()))
    return lock


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=None, help="config.yaml path")
    ap.add_argument("--db", default=None, help="database path (overrides paths.database_path)")
    ap.add_argument("--apply", action="store_true", help="re-run reconcile (default: dry run)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.db:
        cfg.paths.database_path = Path(args.db)
    db_path = Path(cfg.paths.database_path)
    if not db_path.exists():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 2

    eng = _readonly_engine(db_path)
    try:
        with eng.connect() as conn:
            cols = {row[1] for row in conn.exec_driver_sql("pragma table_info(po_sets)")}
        if "line_mapping_rule" not in cols:
            print(
                f"{db_path} is not migrated (po_sets.line_mapping_rule is missing); "
                "run scripts/migrate_db.py first.",
                file=sys.stderr,
            )
            return 4
        with Session(eng) as s:
            candidates = find_candidates(s, cfg)
    finally:
        eng.dispose()

    mode = "APPLY" if args.apply else "DRY RUN (nothing is written; pass --apply to re-run)"
    print(f"Line-number fallback requeue - {mode}")
    print(f"Database: {db_path}")
    if not candidates:
        print("No quarantined or pending set changes under the fallback.")
        return 0
    print(f"{'Set':>6}  {'PO':<24}{'Now':<13}{'Was':<13}{'Would be':<13}Rule")
    for c in candidates:
        print(
            f"{c.po_set_id:>6}  {c.po_no:<24}{c.status:<13}"
            f"{c.old_verdict:<13}{c.new_verdict:<13}{c.label}"
        )
    print(f"{len(candidates)} set(s) would change.")
    if not args.apply:
        return 0

    # Hold the sync lock of the EFFECTIVE database for the whole loop (honours --db, fails
    # closed): a sync that starts mid-run would otherwise race the per-set re-runs.
    lock = _acquire_sync_lock(db_path)
    if lock is None:
        print(
            "A sync is running or the sync lock is unavailable; try again later.", file=sys.stderr
        )
        return 3
    try:
        for c in candidates:
            print(f"set {c.po_set_id} ({c.po_no}): {_requeue(c, cfg)}")
    finally:
        from app.services.sync_lock import release_sync_lock

        release_sync_lock(lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
