import re

# Words that label a PO reference rather than being part of the number itself.
# Only ever dropped as whole tokens, so a segment like "PO186000" inside
# D7264-PO186000-013-01 is never damaged.
_LABEL_WORDS = {
    "NO",
    "NO.",
    "NUM",
    "NUMBER",
    "NBR",
    "REV",
    "REVISION",
    "REF",
    "REFERENCE",
    "PURCHASE",
    "ORDER",
    "ORD",
    "BUYER",
    "BUYERS",
    "ORDERS",
    "DOC",
    "DOCUMENT",
    "REFNO",
    "PURCHASEORDER",
    "PURCHASEORDERS",
    "AND",
    "THE",
    "OF",
}

# "PO" is a label ONLY in leading position. Mid-string it is part of a
# structured code: the PO prints D7264-PO-186000-013-01 while the DN and SI
# print D7264-PO186000-013-01-, so dropping it anywhere but the front breaks
# one spelling or the other.
_LEADING_LABEL_WORDS = {"PO", "P.O.", "P/0"}

# Longest first, so "PO161538" loses "PO" rather than just "P". Bare "P" is
# deliberately absent: P106420232 is a real McDermott code, not a label.
_LEADING_PREFIXES = ("PURCHASEORDER", "PURCHASEORDERS", "PONO", "PO.NO", "PO")


def normalize_po_no(raw: str) -> str:
    """Normalize a PO number to a stable grouping key.

    Built from the real vendor sample inventory (NotebookLM Section 2): the same
    PO is printed as "161538", "PO 161538" and "PO, Rev # 161538,0" on the PO
    header, but as a bare "161538" on its delivery notes and invoices. Without
    this, one real PO splits into several sets that can never reconcile.

    Steps: drop a trailing ERP revision counter, drop label words, strip a
    leading "PO"-style prefix, then flatten to alphanumerics and upper-case.

    Two properties are load-bearing and covered by tests:
      - different spellings of one PO collapse to one key
      - genuinely different POs never collide
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        # A non-string (int, Decimal, object) is not a PO reference; refuse it
        # rather than stringifying it into a plausible-looking but wrong key.
        raise TypeError(f"PO number must be a string, got {type(raw).__name__}")
    s = raw.strip()
    if not s:
        return ""
    # SAP revision counter: "PO, Rev # 161538,0" -> "PO, Rev # 161538"
    if "," in s:
        s = s.rsplit(",", 1)[0]
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", s) if t]
    kept: list[str] = []
    for i, t in enumerate(toks):
        up = t.upper()
        if i == 0:
            if up in _LEADING_LABEL_WORDS or up in _LABEL_WORDS:
                continue
            for p in _LEADING_PREFIXES:
                if up.startswith(p) and len(up) > len(p):
                    t = t[len(p) :]
                    break
            if t:
                kept.append(t)
            continue
        if up in _LABEL_WORDS:
            continue
        kept.append(t)
    return "".join(kept).upper()


def effective_dn_no(line, document) -> str | None:
    """The delivery-note number that applies to one line.

    Vendors either print the DN number once in the header or against every
    individual row. A line-wise value overrides the document-level one for
    that row; both raw values are kept so the evidence is never lost.
    """
    return (getattr(line, "dn_no", None) or getattr(document, "dn_no", None) or None) or None


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
    decision 2026-09-05). Returns touched PO Set ids.

    A document flagged `po_reference_ambiguous` (more than one PO number
    printed) is never attached: guessing would strand the other POs' invoices.
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
                Document.po_no_normalized.isnot(None),
            )
            .all()
        )
        for doc in unattached:
            if doc.po_reference_ambiguous:
                # More than one PO number is printed on this document. Attaching
                # it to any one PO would silently strand the others, so it stays
                # unattached and the set is surfaced for a human.
                continue
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
