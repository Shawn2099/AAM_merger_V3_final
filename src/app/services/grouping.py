import re


def normalize_po_no(raw: str) -> str:
    """Normalize PO number per SPEC §6.1: strip non-alphanumeric characters, uppercase."""
    return re.sub(r"[^A-Za-z0-9]", "", raw).upper()


def get_or_create_po_set(po_no: str, cfg, create: bool = True):
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet, POSetStatus
    from app.models.base import Base

    norm = normalize_po_no(po_no)
    eng = get_engine(cfg)
    # ensure tables exist (handles isolated tmp_path DB in tests)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = (
            s.query(POSet)
            .filter(POSet.po_no_normalized == norm, POSet.status != POSetStatus.merged)
            .order_by(POSet.id.asc())
            .first()
        )
        if ps is not None:
            return ps
        if not create:
            # Attach-only (BLOCKER-5): DN/SI/UNKNOWN docs must never mint
            # orphan sets from decoy codes — they wait visibly unattached
            # (unclassified view) until a PO/COMBINED anchors the key.
            return None
        ps = POSet(po_no_normalized=norm, status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        return ps


def attach_unattached_to_open_sets(cfg) -> set[int]:
    """Attach unattached valid docs to open same-key PO Sets. Never mints:
    keys without an open set wait indefinitely for more files (human
    decision 2026-09-05). Returns touched PO Set ids."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import Document, ExtractionStatus, POSet, POSetStatus

    eng = get_engine(cfg)
    touched: set[int] = set()
    with Session(eng) as s:
        unattached = (
            s.query(Document)
            .filter(
                Document.po_set_id.is_(None),
                Document.extraction_status == ExtractionStatus.valid,
                Document.po_no_normalized.isnot(None),
            )
            .all()
        )
        for doc in unattached:
            ps = (
                s.query(POSet)
                .filter(
                    POSet.po_no_normalized == doc.po_no_normalized,
                    POSet.status != POSetStatus.merged,
                )
                .order_by(POSet.id.asc())
                .first()
            )
            if ps is None:
                continue  # no anchor yet — keep waiting, stay visible
            doc.po_set_id = ps.id
            touched.add(ps.id)
        s.commit()
    return touched


def resolve_unattached_documents(cfg) -> set[int]:
    """Group unattached documents (e.g. Delivery Notes without printed PO) into PO Sets.

    Strategies:
    1. Cross-reference: If doc has dn_no and an SI document has the same dn_no
       and an open po_set_id.
    2. Sibling filename prefix: If doc shares a batch filename prefix (e.g. SIV-DTS-25-477)
       with an already-grouped SI or PO document in an open PO set.
    """
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import Document, ExtractionStatus, POSet, POSetStatus

    eng = get_engine(cfg)
    touched: set[int] = set()

    with Session(eng) as s:
        unattached = (
            s.query(Document)
            .filter(
                Document.po_set_id.is_(None),
                Document.extraction_status == ExtractionStatus.valid,
            )
            .all()
        )
        for doc in unattached:
            matched_ps_id: int | None = None
            matched_po_norm: str | None = None

            # 1. Match by Delivery Note number. Anchor types stay open
            # (human decision 2026-09-05: any logical common anchor may
            # attach — multi-DN POs and -1/-2 variants are normal). But
            # anchors split across DISTINCT sets mean genuine ambiguity:
            # leave unattached instead of first-wins guessing.
            if doc.dn_no:
                anchors = (
                    s.query(Document)
                    .filter(
                        Document.dn_no == doc.dn_no,
                        Document.id != doc.id,
                        Document.po_set_id.isnot(None),
                    )
                    .all()
                )
                anchor_sets = {a.po_set_id for a in anchors if a.po_set_id}
                if len(anchor_sets) == 1:
                    anchor = next(a for a in anchors if a.po_set_id in anchor_sets)
                    matched_ps_id = anchor.po_set_id
                    matched_po_norm = anchor.po_no_normalized

            # 2. Match by sibling filename prefix
            if not matched_ps_id and doc.original_filename:
                fn = doc.original_filename
                parts = fn.replace("_", "-").split("-pages-")[0].split("-")
                if len(parts) >= 4:
                    prefix = "-".join(parts[:4])
                    siblings = (
                        s.query(Document)
                        .filter(
                            Document.original_filename.like(f"{prefix}%"),
                            Document.id != doc.id,
                            Document.po_set_id.isnot(None),
                        )
                        .all()
                    )
                    sibling_ps_ids = {sib.po_set_id for sib in siblings if sib.po_set_id}
                    if len(sibling_ps_ids) == 1:
                        cand_id = sibling_ps_ids.pop()
                        target_sib = next(sib for sib in siblings if sib.po_set_id == cand_id)
                        matched_ps_id = cand_id
                        matched_po_norm = target_sib.po_no_normalized

            if matched_ps_id:
                ps = s.get(POSet, matched_ps_id)
                if ps and ps.status != POSetStatus.merged:
                    doc.po_set_id = matched_ps_id
                    if not doc.po_no_normalized and matched_po_norm:
                        doc.po_no_normalized = matched_po_norm
                    touched.add(matched_ps_id)

        s.commit()
    return touched
