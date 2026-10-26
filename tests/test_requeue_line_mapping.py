"""scripts/requeue_line_mapping.py: dry run is read-only, --apply re-runs exactly the listed sets."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml
from pypdf import PdfWriter
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import requeue_line_mapping as rq  # noqa: E402

from app.core.config import load_config  # noqa: E402
from app.core.database import get_engine  # noqa: E402
from app.models import (  # noqa: E402
    AuditAction,
    AuditLog,
    DocType,
    Document,
    ExtractionStatus,
    LineItem,
    POSet,
    POSetStatus,
)
from app.models.base import Base  # noqa: E402

K = 1000


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    paths = {
        "input_folder": tmp_path / "input",
        "output_folder": tmp_path / "output",
        "quarantine_folder": tmp_path / "quarantine",
        "stored_documents_folder": tmp_path / "stored",
        "combined_folder": tmp_path / "combined",
        "log_folder": tmp_path / "logs",
        "database_path": tmp_path / "aam.db",
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"paths": {k: str(v) for k, v in paths.items()}}), encoding="utf-8"
    )
    monkeypatch.setenv("AAM_CONFIG_PATH", str(cfg_path))
    cfg = load_config(cfg_path)
    for p in paths.values():
        if p.suffix != ".db":
            p.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(cfg))
    return cfg_path, cfg


def _add_set(cfg, po_no, po, dn, si, status):
    """A set with one PO, one DN and one SI document; each arg is [(line_no, qty)]."""
    with Session(get_engine(cfg)) as s:
        ps = POSet(
            po_no_normalized=po_no,
            status=status,
            reconcile_reason="A delivery or invoice line could not be matched to any PO line"
            if status == POSetStatus.quarantined
            else None,
        )
        s.add(ps)
        s.flush()
        for dtype, lines in ((DocType.PO, po), (DocType.DN, dn), (DocType.SI, si)):
            sha = hashlib.sha256(f"{po_no}-{dtype.value}".encode()).hexdigest()
            path = Path(cfg.paths.stored_documents_folder) / f"{sha}.pdf"
            w = PdfWriter()
            w.add_blank_page(width=100, height=100)
            w.write(str(path))
            doc = Document(
                sha256_hash=sha,
                original_filename=f"{po_no}-{dtype.value}.pdf",
                stored_path=str(path),
                doc_type=dtype,
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps.id,
                po_no_normalized=po_no,
                si_no=f"INV-{po_no}" if dtype == DocType.SI else None,
                invoice_no=f"INV-{po_no}" if dtype == DocType.SI else None,
            )
            s.add(doc)
            s.flush()
            for no, qty in lines:
                s.add(
                    LineItem(
                        document_id=doc.id,
                        line_item_no=no,
                        description=f"Item {no}",
                        quantity=qty * K,
                    )
                )
        s.commit()
        return ps.id


def _seed(cfg):
    x10 = [("1", 5), ("2", 3)]
    ids = {
        # quarantined today, merges under x10
        "fixable": _add_set(
            cfg, "PO-FIX", [("10", 5), ("20", 3)], x10, x10, POSetStatus.quarantined
        ),
        # quarantined, partial delivery under x10 -> pending
        "partial": _add_set(
            cfg, "PO-PART", [("10", 5), ("20", 3), ("30", 1)], x10, x10, POSetStatus.quarantined
        ),
        # genuinely unmatched: no rule fits, stays quarantined
        "stuck": _add_set(cfg, "PO-STUCK", [("1", 5), ("3", 3)], x10, x10, POSetStatus.quarantined),
        # exact numbering: nothing to change
        "exact": _add_set(cfg, "PO-EXACT", [("1", 5), ("2", 3)], x10, x10, POSetStatus.pending),
    }
    return ids


def _snapshot(cfg):
    with Session(get_engine(cfg)) as s:
        sets = {
            p.po_no_normalized: (p.status, p.reconcile_reason, p.line_mapping_rule)
            for p in s.query(POSet).all()
        }
        audits = s.query(AuditLog).count()
    return sets, audits


def test_dry_run_lists_only_changed_sets_and_writes_nothing(rig, capsys):
    cfg_path, cfg = rig
    ids = _seed(cfg)
    db = Path(cfg.paths.database_path)
    before_bytes = db.read_bytes()
    before = _snapshot(cfg)

    assert rq.main(["--config", str(cfg_path)]) == 0
    out = capsys.readouterr().out

    assert "DRY RUN" in out
    assert "PO-FIX" in out
    assert "PO-PART" in out
    assert "PO-STUCK" not in out
    assert "PO-EXACT" not in out
    assert "2 set(s) would change." in out
    fix_row = next(line for line in out.splitlines() if "PO-FIX" in line)
    assert "quarantined" in fix_row
    assert "merged" in fix_row
    assert "x10" in fix_row
    part_row = next(line for line in out.splitlines() if "PO-PART" in line)
    assert "pending" in part_row
    assert ids  # all four sets exist
    assert db.read_bytes() == before_bytes
    assert _snapshot(cfg) == before


def test_apply_reruns_exactly_the_listed_sets(rig, capsys):
    cfg_path, cfg = rig
    ids = _seed(cfg)
    stuck_before = _snapshot(cfg)[0]["PO-STUCK"]
    exact_before = _snapshot(cfg)[0]["PO-EXACT"]

    assert rq.main(["--config", str(cfg_path), "--apply"]) == 0
    out = capsys.readouterr().out
    assert f"set {ids['fixable']} (PO-FIX): merged" in out
    assert f"set {ids['partial']} (PO-PART): pending" in out

    sets, _ = _snapshot(cfg)
    assert sets["PO-FIX"][0] == POSetStatus.merged
    assert sets["PO-FIX"][2] == "x10"
    assert "Line numbers mapped by rule x10." in sets["PO-FIX"][1]
    assert sets["PO-PART"][0] == POSetStatus.pending
    assert "Line numbers mapped by rule x10." in sets["PO-PART"][1]
    # untouched sets keep their status, reason and label
    assert sets["PO-STUCK"] == stuck_before
    assert sets["PO-EXACT"] == exact_before

    with Session(get_engine(cfg)) as s:
        audits = s.query(AuditLog).filter(AuditLog.action == AuditAction.line_mapping_applied).all()
        assert [a.po_set_id for a in audits] == [ids["fixable"]]
        assert json.loads(audits[0].detail)["SI"]["rule"] == "x10"
        assert all(p.locked_by_action is None for p in s.query(POSet).all())

    # a second run finds nothing left to change
    assert rq.main(["--config", str(cfg_path)]) == 0
    again = capsys.readouterr().out
    assert "No quarantined or pending set changes" in again


def test_apply_is_refused_while_a_sync_is_running(rig, tmp_path, capsys):
    """The sync lock beside the EFFECTIVE database (--db included) is what is checked."""
    import sqlite3

    from filelock import FileLock

    cfg_path, cfg = rig
    _seed(cfg)
    other = tmp_path / "other"
    other.mkdir()
    other_db = other / "aam.db"
    src = sqlite3.connect(str(cfg.paths.database_path))
    dst = sqlite3.connect(str(other_db))
    src.backup(dst)  # WAL-safe copy
    src.close()
    dst.close()
    before = other_db.read_bytes()

    with FileLock(str(other / ".sync.lock"), timeout=0):
        assert rq.main(["--config", str(cfg_path), "--db", str(other_db), "--apply"]) == 3
    assert other_db.read_bytes() == before

    # the lock is released after a successful apply, so a second apply is not refused
    assert rq.main(["--config", str(cfg_path), "--apply"]) == 0
    assert rq.main(["--config", str(cfg_path), "--apply"]) == 0


def test_db_override_and_missing_database(rig, tmp_path, capsys):
    cfg_path, _ = rig
    assert rq.main(["--config", str(cfg_path), "--db", str(tmp_path / "nope.db")]) == 2
    assert "database not found" in capsys.readouterr().err


def test_unmigrated_database_is_refused_with_a_clear_message(rig, capsys):
    import sqlite3

    cfg_path, cfg = rig
    conn = sqlite3.connect(str(cfg.paths.database_path))
    conn.execute("ALTER TABLE po_sets DROP COLUMN line_mapping_rule")
    conn.commit()
    conn.close()
    assert rq.main(["--config", str(cfg_path)]) == 4
    assert "not migrated" in capsys.readouterr().err
