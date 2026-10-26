"""Take a consistent snapshot of the live DB into the pattern lab.

Source is opened READ-ONLY (mode=ro). Nothing is written to the source.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

SRC = Path(r"C:\Users\malliath\Desktop\aam_merger\data\aam_merger.db")
LAB = Path(r"C:\Users\malliath\Desktop\aam_merger\pattern_lab")
DST = LAB / "db" / "aam_merger_lab.db"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    DST.parent.mkdir(parents=True, exist_ok=True)
    if DST.exists():
        DST.unlink()

    src = sqlite3.connect(f"file:{SRC.as_posix()}?mode=ro", uri=True)
    try:
        src.execute("PRAGMA query_only = ON")
        dst = sqlite3.connect(DST)
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode = DELETE")
        finally:
            dst.close()
    finally:
        src.close()

    print(f"src : {SRC}  ({SRC.stat().st_size:,} bytes)  sha256={sha256(SRC)}")
    print(f"copy: {DST}  ({DST.stat().st_size:,} bytes)  sha256={sha256(DST)}")

    con = sqlite3.connect(DST)
    try:
        print("integrity:", con.execute("PRAGMA integrity_check").fetchone()[0])
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        for t in tables:
            n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            print(f"  {t:<28} {n:>6} rows")
    finally:
        con.close()


if __name__ == "__main__":
    main()
