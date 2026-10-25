"""scripts/backup_db.py - Safe online SQLite WAL backup for AAM Merger V3 (SPEC NFR-6).

Usage:
    python scripts/backup_db.py [--retention-days 30] [--backup-dir ./data/backup]

The Windows Scheduled Task that runs this has no working directory, so every
relative path in `config.yaml` is resolved against the project root — never
against the process cwd. A missing `config.yaml` is an error: backing up a
guessed path would report success while saving the wrong data.

Backup safety (audit P2-7):

* The file name carries microseconds, and a counter is appended if even that
  name already exists — two runs in the same second cannot overwrite each
  other.
* The SQLite backup streams into a hidden `.partial` file in the backup
  folder and is moved into place with `os.replace` only after
  `PRAGMA integrity_check` says `ok`. A failed backup leaves no half-written
  `aam_merger_*.db` for an operator to trust.
* Retention only deletes files that still pass `PRAGMA integrity_check`; a
  corrupt old backup is kept and reported, because deleting the only copy of
  something a human may still want to inspect is not a cleanup.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

#: Name prefix of every accepted backup (`aam_merger_<UTC stamp>.db`).
BACKUP_PREFIX = "aam_merger_"

#: Suffix of the in-progress file; hidden so `aam_merger_*.db` never matches it.
PARTIAL_SUFFIX = ".partial"


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


def _next_backup_path(backup_dir: Path, moment: datetime | None = None) -> Path:
    """A name no other backup (or in-flight partial) is using.

    Microseconds make a same-second collision very unlikely; the counter makes
    it impossible (a restored/clock-skewed host can repeat a timestamp).
    """
    moment = moment or datetime.now(UTC)
    stem = f"{BACKUP_PREFIX}{moment.strftime('%Y%m%d_%H%M%S_%f')}"
    candidate = backup_dir / f"{stem}.db"
    counter = 1
    while candidate.exists() or (backup_dir / f".{candidate.name}{PARTIAL_SUFFIX}").exists():
        candidate = backup_dir / f"{stem}_{counter}.db"
        counter += 1
    return candidate


def _integrity_check(db_path: Path) -> tuple[bool, str]:
    """`PRAGMA integrity_check` on an existing file -> (ok, message)."""
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        try:
            row = conn.execute("PRAGMA integrity_check;").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as err:
        return False, str(err)
    result = (row[0] if row else "") or ""
    return result.strip().lower() == "ok", result


def _config_file(root: Path) -> Path:
    """The app's own config: `AAM_CONFIG_PATH` wins over `<root>/config.yaml`.

    The app and `reset_data.ps1` honour the variable; a backup that ignored it
    would snapshot a different database from the one the reset clears.
    """
    env = os.getenv("AAM_CONFIG_PATH")
    return Path(env) if env else root / "config.yaml"


def run_backup(
    retention_days: int = 30,
    backup_dir: Path | None = None,
    project_root: Path | None = None,
) -> Path:
    root = Path(project_root).resolve() if project_root is not None else ROOT
    db_path = database_path_from_config(_config_file(root), root)

    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found at {db_path}")

    backup_dir = root / "data" / "backup" if backup_dir is None else Path(backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)

    target_path = _next_backup_path(backup_dir)
    # Hidden and not `*.db`: a crash leaves a `.partial` file that no reader or
    # retention pass mistakes for a backup.
    tmp_path = backup_dir / f".{target_path.name}{PARTIAL_SUFFIX}"

    print(f"Checkpointing WAL on {db_path}...")
    src_conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        # Checkpoint WAL pages into main DB file
        src_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

        # Perform atomic online backup using SQLite backup API, then verify
        # before the file is given a name an operator will trust.
        print("Streaming atomic online backup...")
        dst_conn = sqlite3.connect(str(tmp_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()

        ok, detail = _integrity_check(tmp_path)
        if not ok:
            raise RuntimeError(
                f"integrity_check failed for {target_path.name}: {detail!r} — "
                "backup refused, the database may be damaged"
            )
        os.replace(tmp_path, target_path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise
    finally:
        src_conn.close()

    size_kb = target_path.stat().st_size / 1024
    print(f"[OK] Backup created: {target_path.name} ({size_kb:.1f} KB)")

    # Retention cleanup — verified files only.
    if retention_days > 0:
        now_ts = datetime.now(UTC).timestamp()
        cutoff_seconds = retention_days * 86400
        for item in sorted(backup_dir.glob(f"{BACKUP_PREFIX}*.db")):
            if not item.is_file() or item == target_path:
                continue
            age_seconds = now_ts - item.stat().st_mtime
            if age_seconds <= cutoff_seconds:
                continue
            ok, detail = _integrity_check(item)
            if not ok:
                print(
                    f"Keeping {item.name}: integrity_check said {detail!r}; "
                    "inspect or delete it by hand",
                    file=sys.stderr,
                )
                continue
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
