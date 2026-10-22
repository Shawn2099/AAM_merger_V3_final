"""scripts/backup_db.py - Safe online SQLite WAL backup for AAM Merger V3 (SPEC NFR-6).

Usage:
    python scripts/backup_db.py [--retention-days 30] [--backup-dir ./data/backup]

The Windows Scheduled Task that runs this has no working directory, so every
relative path in `config.yaml` is resolved against the project root — never
against the process cwd. A missing `config.yaml` is an error: backing up a
guessed path would report success while saving the wrong data.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def database_path_from_config(cfg_path: Path, root: Path) -> Path:
    """Read `paths.database_path`, resolving a relative path against `root`."""
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"Config not found: {cfg_path} — cannot locate the database to back up"
        )
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    raw = (data.get("paths") or {}).get("database_path")
    if not raw:
        raise KeyError(f"{cfg_path} has no paths.database_path")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def run_backup(
    retention_days: int = 30,
    backup_dir: Path | None = None,
    project_root: Path | None = None,
) -> Path:
    root = Path(project_root).resolve() if project_root is not None else ROOT
    db_path = database_path_from_config(root / "config.yaml", root)

    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found at {db_path}")

    backup_dir = root / "data" / "backup" if backup_dir is None else Path(backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    target_path = backup_dir / f"aam_merger_{timestamp}.db"

    print(f"Checkpointing WAL on {db_path}...")
    src_conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        # Checkpoint WAL pages into main DB file
        src_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

        # Perform atomic online backup using SQLite backup API
        dst_conn = sqlite3.connect(str(target_path))
        try:
            print("Streaming atomic online backup...")
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    size_kb = target_path.stat().st_size / 1024
    print(f"[OK] Backup created: {target_path.name} ({size_kb:.1f} KB)")

    # Retention cleanup
    if retention_days > 0:
        now_ts = datetime.now(UTC).timestamp()
        cutoff_seconds = retention_days * 86400
        for item in backup_dir.glob("aam_merger_*.db"):
            if item.is_file():
                age_seconds = now_ts - item.stat().st_mtime
                if age_seconds > cutoff_seconds:
                    print(f"Pruning backup older than {retention_days} days: {item.name}")
                    try:
                        item.unlink()
                    except OSError as err:
                        print(f"Warning: could not delete {item.name}: {err}", file=sys.stderr)

    return target_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup SQLite WAL database.")
    parser.add_argument(
        "--retention-days", type=int, default=30, help="Days of backups to keep (default: 30)"
    )
    parser.add_argument("--backup-dir", type=str, default=None, help="Directory to store backups")
    args = parser.parse_args()

    backup_dir = Path(args.backup_dir).resolve() if args.backup_dir else None
    try:
        run_backup(retention_days=args.retention_days, backup_dir=backup_dir)
    except Exception as exc:
        print(f"[ERROR] Backup failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
