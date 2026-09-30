"""Proof: attach an orphan DN using the PO's per-line delivery-note numbers.

CORRECTED MODEL (2026-09-29, from the user's observation of real vendor docs)
-----------------------------------------------------------------------------
The first proposal in this file assumed the per-line `dn_no` lived on the DN.
That is backwards. What vendors actually print is:

  * The PO carries a per-ROW delivery-note reference. It tells you, per PO
    line, WHICH delivery note will cover that line.
  * The DN carries its own number as the DOCUMENT number.

So the orphan case is: a DN arrives with no printed PO number. It has a
perfectly good `documents.dn_no` (its own number). What it lacks is a route
to a PO Set, because `po_no_normalized` is empty and grouping keys on PO
number.

The PO's per-line `dn_no` is exactly the missing link. If the PO belonging to
set A prints `GDN-100` against some of its lines, and a DN turns up whose
document number is `GDN-100` with no PO number, then that DN belongs to set A.

WHY THIS IS DIFFERENT FROM THE PREVIOUS PROPOSAL
------------------------------------------------
The lookup is INVERTED, and that matters:

  * Previous: take the orphan's per-line values, match them against other
    documents' `dn_no`. The anchor had to be another DN, and
    `extract_document` only ever sets `doc.dn_no` for DN documents
    (extraction.py:376) - so the anchor was always another DN.

  * This: take the ORPHAN'S OWN document number (`documents.dn_no`, which is
    present by definition in this scenario) and find PO line items carrying
    that same value. The anchor is now a PO - the one document type that is
    guaranteed to be attached to a set, because only PO mints sets
    (sync.py `_ANCHOR_TYPES = ("PO",)`).

That is a materially stronger anchor, and it is the version that is worth
building.

These tests document current behaviour and the intended rule. The fallback is
NOT implemented yet.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import (
    DocType,
    Document,
    ExtractionStatus,
    LineItem,
    POSet,
    POSetStatus,
)
from app.models.base import Base
from app.services.grouping import resolve_unattached_documents


def _cfg(tmp_path, name="assoc2.db"):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / name)
    cfg.paths.output_folder = str(tmp_path / "output")
    cfg.paths.quarantine_folder = str(tmp_path / "quarantine")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    for p in (
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
    ):
        Path(p).mkdir(parents=True, exist_ok=True)
    return cfg


def _pdf(path: Path) -> None:
    w = PdfWriter()
    w.add_blank_page(width=100, height=100)
    w.write(str(path))


def _add_doc(cfg, name, doc_type, po_set_id=None, po_no=None, dn_no=None, lines=()):
    """lines = [(line_no, qty, per_line_dn_no), ...]"""
    eng = get_engine(cfg)
    with Session(eng) as s:
        path = Path(cfg.paths.stored_documents_folder) / f"{name}.pdf"
        _pdf(path)
        d = Document(
            sha256_hash=f"h-{name}",
            original_filename=f"{name}.pdf",
            stored_path=str(path),
            doc_type=DocType[doc_type],
            extraction_status=ExtractionStatus.valid,
            po_set_id=po_set_id,
            po_no_normalized=po_no,
            dn_no=dn_no,
        )
        s.add(d)
        s.commit()
        for ln, qty, line_dn in lines:
            s.add(
                LineItem(
                    document_id=d.id,
                    line_item_no=ln,
                    description=f"item {ln}",
                    quantity=qty * 1000,
                    unit_price=100 * 1000,
                    dn_no=line_dn,
                )
            )
        s.commit()
        return d.id


def _make_set(cfg, po_no):
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        return ps.id


def _po_docs_referencing(session, dn_no: str) -> list[int]:
    """PO document ids whose per-line delivery-note reference is `dn_no`.

    This is the lookup the fallback would perform: a line-item query filtered
    on the printed delivery-note number, narrowed to PO documents, because a
    PO is the only document type guaranteed to be attached to a set.
    """
    rows = session.query(LineItem.document_id).filter(LineItem.dn_no == dn_no).all()
    po_ids = []
    for (doc_id,) in rows:
        doc = session.get(Document, doc_id)
        if doc is not None and doc.doc_type == DocType.PO:
            po_ids.append(doc_id)
    return po_ids


# ---------------------------------------------------------------------------
# 1. The corrected target scenario
# ---------------------------------------------------------------------------


def test_orphan_dn_attaches_via_the_po_line_reference(tmp_path):
    """THE TARGET SCENARIO, now working.

    Set A's PO prints `GDN-100` against lines 1 and 2 - the vendor telling us
    which delivery note will cover them. A DN arrives whose document number
    IS `GDN-100`, but with no printed PO number.

    Before this fallback existed the DN stayed unattached forever: it had its
    own number, but `po_no_normalized` was empty so grouping had no key, and
    the old anchor search only matched *other documents that also carried a
    `documents.dn_no`* - which no PO ever does, since `extract_document`
    sets that field only for DN documents.

    Now the PO's per-row reference is the route back to the set.
    """
    cfg = _cfg(tmp_path)
    set_a = _make_set(cfg, "PO-A")

    # The PO: prints the delivery-note reference per row.
    _add_doc(
        cfg,
        "po_a",
        "PO",
        po_set_id=set_a,
        po_no="PO-A",
        lines=[("1", 10, "GDN-100"), ("2", 20, "GDN-100"), ("3", 5, "GDN-200")],
    )
    # A DN that knows its own number but not its PO.
    orphan_id = _add_doc(
        cfg,
        "dn100",
        "DN",
        po_no=None,
        dn_no="GDN-100",
        lines=[("1", 10, None), ("2", 20, None)],
    )

    touched = resolve_unattached_documents(cfg)

    with Session(get_engine(cfg)) as s:
        orphan = s.get(Document, orphan_id)
        assert orphan.dn_no == "GDN-100", "precondition: the DN knows its own number"
        assert orphan.po_set_id == set_a, (
            "the orphan DN should now attach to the set whose PO references it"
        )
        assert orphan.po_no_normalized == "PO-A", (
            "it must inherit the PO number, otherwise reconciliation has no key"
        )
    assert set_a in touched


# ---------------------------------------------------------------------------
# 2. The corrected anchor is always available
# ---------------------------------------------------------------------------


def test_po_anchor_is_guaranteed_to_be_attached(tmp_path):
    """Only PO mints a set, so a PO anchor is always resolved.

    This is what makes the inverted lookup strictly better than the previous
    proposal, where the anchor had to be another DN and DNs are frequently
    themselves unattached. Here, a PO that is in the system is in a set by
    construction.
    """
    cfg = _cfg(tmp_path)
    set_a = _make_set(cfg, "PO-A")
    po_id = _add_doc(cfg, "po_a", "PO", po_set_id=set_a, po_no="PO-A", lines=[("1", 10, "GDN-100")])
    with Session(get_engine(cfg)) as s:
        po = s.get(Document, po_id)
        assert po.po_set_id == set_a, "a PO in the system is always attached to a set"
        values = {li.dn_no for li in po.line_items if li.dn_no}
        assert values == {"GDN-100"}, values


# ---------------------------------------------------------------------------
# 3. The rule, asserted against real behaviour
# ---------------------------------------------------------------------------


def test_unambiguous_reference_attaches(tmp_path):
    """The one configuration the fallback acts on.

    The orphan's document number appears on the per-row references of POs that
    all belong to exactly ONE set. Unambiguous, so it attaches and inherits
    the PO number it needs for reconciliation to have a key.
    """
    cfg = _cfg(tmp_path)
    set_a = _make_set(cfg, "PO-A")
    _add_doc(cfg, "po_a", "PO", po_set_id=set_a, po_no="PO-A", lines=[("1", 10, "GDN-100")])
    orphan_id = _add_doc(cfg, "dn100", "DN", po_no=None, dn_no="GDN-100", lines=[("1", 10, None)])

    resolve_unattached_documents(cfg)

    with Session(get_engine(cfg)) as s:
        orphan = s.get(Document, orphan_id)
        assert orphan.po_set_id == set_a
        assert orphan.po_no_normalized == "PO-A"


def test_refuses_when_po_anchors_span_two_sets(tmp_path):
    """The same DN number printed on POs belonging to different sets.

    Genuinely ambiguous - the delivery note covers lines on two POs. Must be
    left unattached rather than first-wins: a wrong attach would file a
    delivery note under a purchase order it may not belong to.
    """
    cfg = _cfg(tmp_path)
    set_a = _make_set(cfg, "PO-A")
    set_b = _make_set(cfg, "PO-B")
    _add_doc(cfg, "po_a", "PO", po_set_id=set_a, po_no="PO-A", lines=[("1", 10, "GDN-100")])
    _add_doc(cfg, "po_b", "PO", po_set_id=set_b, po_no="PO-B", lines=[("7", 10, "GDN-100")])
    orphan_id = _add_doc(cfg, "dn100", "DN", po_no=None, dn_no="GDN-100", lines=[("1", 10, None)])

    resolve_unattached_documents(cfg)

    with Session(get_engine(cfg)) as s:
        orphan = s.get(Document, orphan_id)
        assert orphan.po_set_id is None, (
            f"ambiguous across two sets and must not attach, got {orphan.po_set_id}"
        )
        assert orphan.po_no_normalized is None


def test_refuses_when_no_po_mentions_the_number(tmp_path):
    """A DN number no PO references is not evidence of anything."""
    cfg = _cfg(tmp_path)
    set_a = _make_set(cfg, "PO-A")
    _add_doc(cfg, "po_a", "PO", po_set_id=set_a, po_no="PO-A", lines=[("1", 10, "GDN-OTHER")])
    orphan_id = _add_doc(cfg, "dn999", "DN", po_no=None, dn_no="GDN-999", lines=[("1", 10, None)])

    resolve_unattached_documents(cfg)

    with Session(get_engine(cfg)) as s:
        orphan = s.get(Document, orphan_id)
        assert orphan.po_set_id is None, (
            "an unresolvable delivery-note number must not attach anything"
        )


def test_a_dn_without_any_number_is_untouched(tmp_path):
    """No document number and no PO reference: there is nothing to match on."""
    cfg = _cfg(tmp_path)
    set_a = _make_set(cfg, "PO-A")
    _add_doc(cfg, "po_a", "PO", po_set_id=set_a, po_no="PO-A", lines=[("1", 10, "GDN-100")])
    orphan_id = _add_doc(cfg, "dn_blank", "DN", po_no=None, dn_no=None, lines=[("1", 10, None)])

    resolve_unattached_documents(cfg)

    with Session(get_engine(cfg)) as s:
        orphan = s.get(Document, orphan_id)
        assert orphan.po_set_id is None, "a DN with no number must not be guessed onto a set"


# 4. Blast radius
# ---------------------------------------------------------------------------


def test_a_wrong_attach_cannot_produce_a_silent_merge(tmp_path):
    """Bounding the risk: a misattachment still has to reconcile.

    Even if the fallback attached a DN to the wrong set, PO vs DN vs SI
    quantities must still agree. Inconsistent, so it quarantines. This is why
    using a VLM-read string here is materially safer than adding it to the
    reconciliation matcher.
    """
    from app.services.reconciliation import reconcile_po_set

    cfg = _cfg(tmp_path)
    set_a = _make_set(cfg, "PO-A")
    _add_doc(cfg, "po_a", "PO", po_set_id=set_a, po_no="PO-A", lines=[("1", 100, None)])
    _add_doc(cfg, "si_a", "SI", po_set_id=set_a, po_no="PO-A", lines=[("1", 100, None)])
    # A DN that plainly does not belong here.
    _add_doc(cfg, "dn_wrong", "DN", po_set_id=set_a, po_no="PO-A", lines=[("1", 40, None)])

    res = reconcile_po_set(set_a, cfg)
    assert res["status"] != "merged", f"an inconsistent set must not merge: {res}"
