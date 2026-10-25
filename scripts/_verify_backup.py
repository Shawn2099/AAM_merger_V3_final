"""scripts/_verify_backup.py - compare a live AAM Merger database with a backup.

Called by scripts/verify_backup.ps1. Both SQLite files are opened READ-ONLY.

Checks:
  * row count of documents, line_items, po_sets, audit_log (source vs backup);
  * SHA-256 of up to 3 stored PDFs named in the backup's documents table,
    against the hash recorded in that row.

Exit code 0 = every check PASS, 1 = at least one FAIL, 2 = could not run.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

TABLES = ("documents", "line_items", "po_sets", "audit_log")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _open_ro(path: Path, immutable: bool = False) -> sqlite3.Connection:
    """Read-only. `immutable` (backup files only) stops SQLite from leaving -wal/-shm beside it."""
    if not path.is_file():
        raise FileNotFoundError(f"database not found: {path}")
    flags = "mode=ro&immutable=1" if immutable else "mode=ro"
    return sqlite3.connect(f"{path.resolve().as_uri()}?{flags}", uri=True)


def database_path(source: Path) -> Path:
    """`source` is a database file or a config.yaml holding paths.database_path."""
    if source.suffix.lower() not in (".yaml", ".yml"):
        return source
    import yaml

    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    raw = Path(str((data.get("paths") or {}).get("database_path", "")))
    if not str(raw) or str(raw) == ".":
        raise ValueError(f"paths.database_path missing in {source}")
    return raw if raw.is_absolute() else (source.resolve().parent / raw)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(
    source_db: Path,
    backup_db: Path,
    root: Path | None = None,
    samples: int = 3,
    allow_source_newer: bool = False,
) -> list[Check]:
    checks: list[Check] = []
    src = _open_ro(source_db)
    bak = _open_ro(backup_db, immutable=True)
    try:
        for table in TABLES:
            try:
                n_src = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                n_bak = bak.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error as exc:
                checks.append(Check(f"rows:{table}", False, f"unreadable: {exc}"))
                continue
            ok = n_bak == n_src or (allow_source_newer and n_bak <= n_src)
            checks.append(Check(f"rows:{table}", ok, f"source={n_src} backup={n_bak}"))

        rows = []
        try:
            base = (
                "SELECT stored_path, sha256_hash FROM documents "
                "WHERE stored_path IS NOT NULL AND stored_path <> '' "
            )
            total = bak.execute(
                "SELECT COUNT(*) FROM documents WHERE stored_path IS NOT NULL AND stored_path <> ''"
            ).fetchone()[0]
            # Oldest, newest, then the middle row: spread the sample over the history.
            for order, offset in (("ASC", 0), ("DESC", 0), ("ASC", total // 2)):
                row = bak.execute(
                    f"{base}ORDER BY id {order} LIMIT 1 OFFSET ?", (offset,)
                ).fetchone()
                if row is not None and row not in rows:
                    rows.append(row)
            rows = rows[:samples]
        except sqlite3.Error as exc:
            checks.append(Check("files:query", False, f"unreadable: {exc}"))
        for stored, recorded in rows:
            p = Path(stored)
            if not p.is_absolute() and root is not None:
                p = root / p
            if not p.is_file():
                checks.append(Check(f"file:{p.name}", False, f"missing: {p}"))
                continue
            actual = _sha256(p)
            checks.append(
                Check(
                    f"file:{p.name}",
                    actual == recorded,
                    "sha256 matches"
                    if actual == recorded
                    else f"sha256 {actual[:12]} != {recorded[:12]}",
                )
            )
    finally:
        src.close()
        bak.close()
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="live database file or config.yaml")
    ap.add_argument("--backup", required=True, help="backup database file")
    ap.add_argument("--root", default=None, help="base for relative stored_path values")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--allow-source-newer", action="store_true")
    a = ap.parse_args(argv)
    try:
        src = database_path(Path(a.source))
        checks = verify(
            src,
            Path(a.backup),
            Path(a.root) if a.root else None,
            max(0, min(a.samples, 3)),
            a.allow_source_newer,
        )
    except (OSError, ValueError, ImportError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}")
        return 2
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name:<{width}}  {c.detail}")
    failed = [c for c in checks if not c.ok]
    print(f"\nRESULT: {'FAIL' if failed else 'PASS'} ({len(checks) - len(failed)}/{len(checks)})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
