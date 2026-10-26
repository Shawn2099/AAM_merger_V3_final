"""Known limitations: a leftover packet at the output name is never adopted (merge.py).

A crash between `os.replace` of the packet and `s.commit()` leaves a complete
PDF at the output name with no row pointing at it. The engine does NOT adopt
that file, even when it looks like ours: a page-count match is not proof of
ownership, and CORE_LOGIC section 6 says an existing packet is never overwritten.
Any file already at the name keeps raising MergeNamingError and the set is
quarantined. Recovery is manual: the operator inspects and removes the file,
then runs Re-Match.
"""

import uuid
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
from app.models.base import Base
from app.services.merge import MergeNamingError, merge_po_set


def _cfg_with_tmp(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test.db"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    (tmp_path / "stored").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    return cfg


def _pdf(p: Path, pages: int, width: int = 200) -> Path:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=200)
    w.write(str(p))
    return p


def _set_with_docs(tmp_path, cfg, si_no: str, docs_info: list[dict]) -> int:
    """One POSet with one evidence line per document. Each doc is a 1-page PDF
    whose mediabox width encodes its identity (so page order is checkable)."""
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = POSet(po_no_normalized=f"PO-{si_no}", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        for n, info in enumerate(docs_info):
            dt = info["doc_type"]
            pdf = _pdf(tmp_path / "stored" / f"{si_no}_{dt}_{n}.pdf", 1, info["width"])
            doc = Document(
                sha256_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                original_filename=f"{dt}.pdf",
                stored_path=str(pdf),
                doc_type=DocType(dt),
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps.id,
                si_no=info.get("si_no"),
            )
            s.add(doc)
            s.flush()
            s.add(
                LineItem(
                    document_id=doc.id,
                    line_item_no="1",
                    description=f"{dt} item",
                    quantity=1000,
                )
            )
        s.commit()
        return ps.id


_DOCS = [
    {"doc_type": "SI", "si_no": "INV-ORPH", "width": 400},
    {"doc_type": "DN", "width": 300},
    {"doc_type": "PO", "width": 200},
]


def _owned_set(tmp_path, cfg, si_no: str, docs_info: list[dict]) -> int:
    """Create and merge a set normally, so it owns its packet file."""
    set_id = _set_with_docs(tmp_path, cfg, si_no, docs_info)
    assert merge_po_set(set_id, cfg) is not None
    return set_id


def _emulate_lost_commit(cfg, set_id: int) -> None:
    """Clear the row as if the commit after os.replace never landed."""
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, set_id)
        ps.status = POSetStatus.pending
        ps.merged_output_path = None
        ps.merged_at = None
        s.commit()


def test_known_limitation_leftover_packet_after_a_crash_quarantines_the_set(tmp_path):
    """Known limitation: a lost commit leaves a packet this set wrote, and the
    retry refuses it. The operator must remove the file, then Re-Match."""
    cfg = _cfg_with_tmp(tmp_path)
    set_id = _owned_set(tmp_path, cfg, "INV-ORPH", _DOCS)
    packet = Path(cfg.paths.output_folder) / "INV-ORPH.pdf"
    assert packet.is_file()
    leftover_bytes = packet.read_bytes()

    _emulate_lost_commit(cfg, set_id)  # file stays, row no longer owns it

    with pytest.raises(MergeNamingError, match="refusing to overwrite"):
        merge_po_set(set_id, cfg)

    assert packet.read_bytes() == leftover_bytes, "the leftover packet must not be touched"
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, set_id)
        assert ps.merged_output_path is None


def test_known_limitation_unowned_file_with_matching_page_count_is_never_overwritten(tmp_path):
    """Known limitation: a foreign file with the same page count is still refused.
    Page count alone is not proof of ownership."""
    cfg = _cfg_with_tmp(tmp_path)
    set_id = _set_with_docs(tmp_path, cfg, "INV-ORPH", _DOCS)
    foreign = _pdf(tmp_path / "output" / "INV-ORPH.pdf", pages=3, width=999)
    foreign_bytes = foreign.read_bytes()

    with pytest.raises(MergeNamingError, match="refusing to overwrite"):
        merge_po_set(set_id, cfg)

    assert foreign.read_bytes() == foreign_bytes, "an unowned file must never be overwritten"
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, set_id)
        assert ps.status == POSetStatus.pending
        assert ps.merged_output_path is None


def test_file_owned_by_another_set_is_refused(tmp_path):
    """A packet delivered by another set is never overwritten, even when this
    set would produce the same page count."""
    cfg = _cfg_with_tmp(tmp_path)
    _owned_set(tmp_path, cfg, "INV-ORPH", _DOCS)  # owns output/INV-ORPH.pdf
    other_id = _set_with_docs(tmp_path, cfg, "INV-ORPH", _DOCS)
    owned = Path(cfg.paths.output_folder) / "INV-ORPH.pdf"
    owned_bytes = owned.read_bytes()

    with pytest.raises(MergeNamingError, match="refusing to overwrite"):
        merge_po_set(other_id, cfg)

    assert owned.read_bytes() == owned_bytes, "another set's delivered packet was touched"
    eng = get_engine(cfg)
    with Session(eng) as s:
        assert s.get(POSet, other_id).merged_output_path is None
