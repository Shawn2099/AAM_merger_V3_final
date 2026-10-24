"""Letters must agree across the WHOLE set, not only with the minting PO.

The PO key is the digits; letters are the safety check. Every attach path
compares a document with `po_sets.po_no_letters`, which comes from the minting
PO alone. Two ways around that check reach reconcile:

- a set minted by a letterless PO (`25-26-163`) accepts a `TOGA-PO-25-26-163`
  DN and an `ARM-PO-25-26-163` SI, each compatible with "no letters";
- a document already in a TOGA set is re-read as ARM and keeps its set,
  because the drift guard compares digits only.

Both are two different POs in one packet. Reconcile must quarantine them with
`po_reference_mismatch`, never merge. The positive control shows the same data
with agreeing letters still merges, so the guard is not vacuous.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
from app.models.base import Base
from app.services.grouping import normalize_po_no, po_letters
from app.services.reconciliation import reconcile_po_set


@pytest.fixture()
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c.paths.database_path = str(tmp_path / "letters.db")
    for name in ("output", "stored", "quarantine", "archive", "combined", "input", "logs"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    c.paths.output_folder = str(tmp_path / "output")
    c.paths.stored_documents_folder = str(tmp_path / "stored")
    c.paths.quarantine_folder = str(tmp_path / "quarantine")
    c.paths.archive_folder = str(tmp_path / "archive")
    c.paths.combined_folder = str(tmp_path / "combined")
    c.paths.input_folder = str(tmp_path / "input")
    c.paths.log_folder = str(tmp_path / "logs")
    Base.metadata.create_all(get_engine(c))
    return c


def _pdf(path: Path) -> str:
    w = PdfWriter()
    w.add_blank_page(width=100, height=100)
    w.write(str(path))
    return str(path)


def _set(cfg, set_raw: str, docs: list[tuple[DocType, str]]) -> int:
    """A set keyed on `set_raw` with one 10-unit line per document (all agree)."""
    stored = Path(cfg.paths.stored_documents_folder)
    with Session(get_engine(cfg)) as s:
        ps = POSet(
            po_no_normalized=normalize_po_no(set_raw),
            po_no_letters=po_letters(set_raw) or None,
            status=POSetStatus.pending,
        )
        s.add(ps)
        s.commit()
        for i, (dtype, raw) in enumerate(docs):
            d = Document(
                sha256_hash=f"{i:064d}",
                original_filename=f"{dtype.value.lower()}{i}.pdf",
                stored_path=_pdf(stored / f"{dtype.value.lower()}{i}.pdf"),
                doc_type=dtype,
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps.id,
                po_no_raw=raw,
                po_no_normalized=normalize_po_no(raw),
                si_no=f"INV-{i}" if dtype == DocType.SI else None,
                invoice_no=f"INV-{i}" if dtype == DocType.SI else None,
            )
            s.add(d)
            s.flush()
            s.add(
                LineItem(
                    document_id=d.id,
                    line_item_no="1",
                    description="Item",
                    quantity=10_000,
                )
            )
        s.commit()
        return ps.id


def test_agreeing_letters_still_merge(cfg):
    ps_id = _set(
        cfg,
        "25-26-163",
        [
            (DocType.PO, "25-26-163"),
            (DocType.DN, "TOGA-PO-25-26-163"),
            (DocType.SI, "TOGA-PO-25-26-163"),
        ],
    )
    assert reconcile_po_set(ps_id, cfg)["status"] == "merged"


def test_letterless_anchor_with_two_different_lettered_documents_is_quarantined(cfg):
    ps_id = _set(
        cfg,
        "25-26-163",
        [
            (DocType.PO, "25-26-163"),
            (DocType.DN, "TOGA-PO-25-26-163"),
            (DocType.SI, "ARM-PO-25-26-163"),
        ],
    )
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "quarantined"
    assert res["reason"] == "po_reference_mismatch"
    assert "TOGAPO" in res["detail"] and "ARMPO" in res["detail"]
    with Session(get_engine(cfg)) as s:
        ps = s.get(POSet, ps_id)
        assert ps.status == POSetStatus.quarantined
        assert ps.merged_output_path is None


def test_a_re_read_that_changes_the_letters_is_quarantined(cfg):
    ps_id = _set(
        cfg,
        "TOGA-PO-25-26-163",
        [
            (DocType.PO, "TOGA-PO-25-26-163"),
            (DocType.DN, "25-26-163"),
            (DocType.SI, "TOGA-PO-25-26-163"),
        ],
    )
    # The SI is re-read and now prints ARM; it keeps its set (same digits).
    with Session(get_engine(cfg)) as s:
        si = s.query(Document).filter(Document.doc_type == DocType.SI).one()
        si.po_no_raw = "ARM-PO-25-26-163"
        si.po_no_normalized = normalize_po_no(si.po_no_raw)
        s.commit()
    res = reconcile_po_set(ps_id, cfg)
    assert res["status"] == "quarantined"
    assert res["reason"] == "po_reference_mismatch"


def test_a_re_read_with_a_digitless_po_number_detaches_the_document(cfg):
    """`OPG` keys as "" and the digit drift guard skips an empty key, so a
    re-read that only finds letters must leave the set, like a re-read that
    finds no number at all."""
    from app.services.extraction import populate_from_raw_dict

    ps_id = _set(cfg, "60213576 OPG", [(DocType.DN, "60213576")])
    with Session(get_engine(cfg)) as s:
        doc = s.query(Document).one()
        assert doc.po_set_id == ps_id
        populate_from_raw_dict(
            doc,
            {"document_type": "DN", "po_no_raw": "OPG", "line_items": []},
            s,
            cfg,
        )
        assert doc.po_set_id is None
        assert not doc.po_no_normalized


def test_a_re_read_that_finds_two_po_numbers_detaches_the_document(cfg):
    """An attached document re-read as ambiguous must leave its set.

    The first number still matches the set's digits, so neither the drift
    guard nor the letters guard sees anything wrong; without the detach the
    set merges a document that names two POs."""
    from app.services.extraction import populate_from_raw_dict

    ps_id = _set(
        cfg,
        "161538",
        [(DocType.PO, "161538"), (DocType.DN, "161538"), (DocType.SI, "161538")],
    )
    with Session(get_engine(cfg)) as s:
        dn = s.query(Document).filter(Document.doc_type == DocType.DN).one()
        populate_from_raw_dict(
            dn,
            {
                "document_type": "DN",
                "po_no_raw": "161538",
                "po_reference_ambiguous": True,
                "line_items": [{"line_item_no": "1", "description": "Item", "quantity": "10"}],
            },
            s,
            cfg,
        )
        s.commit()
        assert dn.po_reference_ambiguous is True
        assert dn.po_set_id is None
    assert reconcile_po_set(ps_id, cfg)["status"] != "merged"


def test_a_manual_po_survives_an_ambiguous_re_read(cfg):
    """The operator's typed PO resolves the ambiguity; a re-read keeps it placed."""
    from app.services.extraction import populate_from_raw_dict

    ps_id = _set(cfg, "161538", [(DocType.DN, "161538")])
    with Session(get_engine(cfg)) as s:
        dn = s.query(Document).one()
        dn.po_no_source = "manual"
        s.commit()
        populate_from_raw_dict(
            dn,
            {"document_type": "DN", "po_no_raw": "161538", "po_reference_ambiguous": True},
            s,
            cfg,
        )
        assert dn.po_set_id == ps_id
        assert dn.po_reference_ambiguous is False
