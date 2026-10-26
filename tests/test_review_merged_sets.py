# ruff: noqa: F811
"""The post-upgrade review query lists merged sets whose packet needs a human look."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy.orm import Session
from test_business_scenarios_ui import _doc, _set, cfg  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import review_merged_sets  # noqa: E402

from app.core.database import get_engine  # noqa: E402
from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus  # noqa: E402

LINE = [("1", "BOLT", 100)]


def _merged(cfg, po: str, *, dn_lines=LINE, si_status=ExtractionStatus.valid) -> int:
    pid = _set(cfg, po)
    _doc(cfg, pid, DocType.PO, f"{po}-po.pdf", LINE, po_no=po)
    _doc(cfg, pid, DocType.DN, f"{po}-dn.pdf", dn_lines, po_no=po)
    _doc(cfg, pid, DocType.SI, f"{po}-si.pdf", LINE, po_no=po, status=si_status)
    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, pid)
        ps.status = POSetStatus.merged
        ps.merged_output_path = f"out/{po}.pdf"
        s.commit()
    return pid


def test_lists_only_sets_that_need_review(cfg):
    clean = _merged(cfg, "PO-CLEAN")
    empty_dn = _merged(cfg, "PO-EMPTYDN", dn_lines=[])
    bad_si = _merged(cfg, "PO-BADSI", si_status=ExtractionStatus.failed)
    with_unknown = _merged(cfg, "PO-UNK")
    did = _doc(cfg, with_unknown, DocType.UNKNOWN, "PO-UNK-x.pdf", LINE, po_no="PO-UNK")
    with Session(get_engine(cfg)) as s:
        s.get(Document, did).po_set_id = with_unknown
        s.commit()
    pending = _set(cfg, "PO-PENDING")
    _doc(cfg, pending, DocType.DN, "p-dn.pdf", [], po_no="PO-PENDING")

    with Session(get_engine(cfg)) as s:
        found = {f.po_set_id: f for f in review_merged_sets.find_review_candidates(s)}

    assert set(found) == {empty_dn, bad_si, with_unknown}
    assert clean not in found
    assert "zero line items" in found[empty_dn].reasons[0]
    assert "extraction_status" in found[bad_si].reasons[0]
    assert "UNKNOWN" in found[with_unknown].reasons[0]
    assert found[empty_dn].output_path == "out/PO-EMPTYDN.pdf"


def test_main_prints_and_exits_zero(cfg, tmp_path, capsys, monkeypatch):
    _merged(cfg, "PO-EMPTYDN", dn_lines=[])
    monkeypatch.setattr(review_merged_sets, "load_config", lambda path=None: cfg)
    assert review_merged_sets.main([]) == 0
    out = capsys.readouterr().out
    assert "PO-EMPTYDN" in out and "zero line items" in out
