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

#: Leading labels that must be removed from the RAW string before tokenising.
#: `_LEADING_LABEL_WORDS` cannot do it: the tokeniser splits on punctuation, so
#: "P.O. 161538" becomes tokens ["P", "O", "161538"] and the entries "P.O." and
#: "P/0" never matched anything. LPO ("Local Purchase Order") is a label too —
#: a real file prints `LPO26/2360` where its DN prints `PO 26/2360`.
#: `(?![A-Za-z])` keeps "POST123"/"P106420232" intact: the next char must not
#: be a letter for this to be a label rather than part of a code.
_LEADING_LABEL_RE = re.compile(r"^\s*(?:P\.\s*O\.?|P/0|LPO|PO)(?![A-Za-z])", re.IGNORECASE)

#: A trailing revision token: "161538 Rev 2", "PO No. 161538 Rev. 0". Without
#: this the trailing digit stays and "161538 Rev 2" keys as "1615382", which
#: collides with a genuinely different PO numbered 1615382.
_REVISION_RE = re.compile(r"\b(?:rev(?:ision)?)\b\.?\s*#?\s*\d{1,3}\b", re.IGNORECASE)


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
    # Labels that punctuation would otherwise split into tokens ("P.O.",
    # "P/0", "LPO"). Done on the raw string, before tokenising.
    s = _LEADING_LABEL_RE.sub("", s, count=1).strip()
    # A trailing revision counter: "161538 Rev 2" -> "161538".
    s = _REVISION_RE.sub(" ", s)
    # SAP revision counter: "PO, Rev # 161538,0" -> "PO, Rev # 161538".
    # Only a plausible revision is stripped: "1,000" is a printed one thousand,
    # not PO 1 revision 000 (the old `\d{1,3}` did exactly that, keying the PO
    # as "1" and colliding with a different PO numbered 1). Indian grouping
    # ("1,00,000") is preserved for the same reason.
    if "," in s:
        before, after = s.rsplit(",", 1)
        if re.search(r"\d", before) and re.match(r"^\s*(?:0|[1-9]\d{0,2})\s*$", after):
            s = before
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


#: The only document type that may mint a PO Set. A PO Set is expected to
#: hold exactly one PO; every other type attaches to an existing set or
#: waits visibly unattached (BLOCKER-5).
ANCHOR_TYPES = ("PO",)

#: Documents that must carry a PO reference of their own: a PO carries its
#: own number, a DN and an SI carry the PO they belong to. A document in
#: this list that arrives without one is **held** — valid, unattached and
#: visible in the holding area for the operator to name its PO — not
#: quarantined: it was read successfully, so it is not broken, and marking
#: it failed would hide it from the sweeps that could still place it.
NEEDS_PO_REFERENCE = ("PO", "DN", "SI")


def group_document(doc, cfg) -> tuple[int | None, str | None]:
    """Place a freshly-read document in its PO Set.

    Returns `(po_set_id, problem)`.

    `problem` is None when the document was placed, or deliberately left
    unattached. Otherwise it is one of:

      "missing_po_number"   - the document carries no PO reference at all.
                               It was read successfully, so it is not
                               broken: it is HELD (valid, unattached,
                               visible in the holding area) for the
                               operator to name the PO it belongs to, or
                               for a later document's content to place
                               it. It is not quarantined.
      "ambiguous_po_number" - the document shows more than one PO number.
                               A PO is the only type that mints a set, so
                               an ambiguous PO would mint one under
                               whichever number was named first and strand
                               the others. It waits for a human.
    """
    dtype = doc.doc_type.value if hasattr(doc.doc_type, "value") else str(doc.doc_type)
    if not doc.po_no_normalized:
        if dtype in NEEDS_PO_REFERENCE:
            return None, "missing_po_number"
        return None, None
    if doc.po_reference_ambiguous:
        # Every PO-bearing type, not just PO. A DN or SI that prints two PO
        # numbers must not be attached to whichever one the model listed first:
        # that silently strands the other PO and can push its lines into the
        # wrong set. The two attach sweeps already refuse all types; this path
        # is the third one and used to be the exception.
        return None, "ambiguous_po_number"
    ps = get_or_create_po_set(
        doc.po_no_raw or doc.po_no_normalized,
        cfg,
        create=dtype in ANCHOR_TYPES,
    )
    if ps is None:
        return None, None
    return ps.id, None


def get_or_create_po_set(po_no: str, cfg, create: bool = True):
    from sqlalchemy.exc import IntegrityError
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
            .filter(
                POSet.po_no_normalized == norm,
                POSet.status != POSetStatus.merged,
                POSet.archived_at.is_(None),
            )
            .order_by(POSet.id.asc())
            .first()
        )
        if ps is not None:
            return ps
        if not create:
            # Attach-only (BLOCKER-5): DN/SI/UNKNOWN docs must never mint
            # orphan sets from decoy codes — they wait visibly unattached
            # (unclassified view) until a PO anchors the key.
            return None
        ps = POSet(po_no_normalized=norm, status=POSetStatus.pending)
        s.add(ps)
        try:
            s.commit()
        except IntegrityError:
            # The unique open-set index is the referee: two workers that both
            # missed the SELECT cannot both insert. The loser joins the winner
            # instead of surfacing a 500 or minting a duplicate set.
            s.rollback()
            ps = (
                s.query(POSet)
                .filter(
                    POSet.po_no_normalized == norm,
                    POSet.status != POSetStatus.merged,
                    POSet.archived_at.is_(None),
                )
                .order_by(POSet.id.asc())
                .first()
            )
            if ps is None:
                raise
            return ps
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
                Document.is_split_parent.is_(False),
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
                    POSet.archived_at.is_(None),
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


def _anchor_from_po_line_ref(session, dn_no: str | None) -> tuple[int | None, str | None]:
    """Find the PO Set whose PO names this delivery-note number on a row.

    Some POs print, per line, which delivery note will cover that line. A
    delivery note that arrives without a printed PO number still knows its own
    number in `documents.dn_no`, so that per-row reference is the only route
    from the note back to its PO Set.

    Why the PO is the anchor: only PO documents mint sets
    (`app/flows/sync.py::_ANCHOR_TYPES`), so a PO in the database is attached
    to a set by construction. Anchoring on a sibling delivery note instead
    would be circular — a DN is frequently unattached for exactly the reason
    this path exists.

    Returns (po_set_id, po_no_normalized) when every PO carrying this
    reference belongs to the SAME set, else (None, None). Ambiguity is
    refused rather than resolved first-wins: a delivery note spanning two POs
    has no single correct set, and guessing it would attach a document to a
    transaction it may not belong to.
    """
    if not dn_no:
        return None, None

    from app.models import DocType, Document, LineItem

    po_doc_ids = {
        doc_id
        for (doc_id,) in session.query(LineItem.document_id)
        .join(Document, Document.id == LineItem.document_id)
        .filter(LineItem.dn_no == dn_no, Document.archived_at.is_(None))
        .distinct()
        .all()
    }
    if not po_doc_ids:
        return None, None

    anchor_rows = (
        session.query(Document)
        .filter(
            Document.id.in_(po_doc_ids),
            Document.doc_type == DocType.PO,
            Document.po_set_id.isnot(None),
            Document.archived_at.is_(None),
        )
        .all()
    )
    anchor_sets = {r.po_set_id for r in anchor_rows if r.po_set_id}
    if len(anchor_sets) != 1:
        return None, None

    only_set = anchor_sets.pop()
    anchor = next(r for r in anchor_rows if r.po_set_id == only_set)
    return only_set, anchor.po_no_normalized


def resolve_unattached_documents(cfg) -> set[int]:
    """Group unattached documents (e.g. Delivery Notes without printed PO) into PO Sets.

    Two strategies, both of them **content-based**. A filename is not
    evidence: the same bytes can be renamed, and a batch export can name
    two unrelated documents alike. Nothing is placed on the strength of
    what a file is called.

    1. Cross-reference: the document's own DN number matches the DN number
       of a document that is already in an open set.
    2. PO line reference: the document's DN number is printed against rows
       of a PO that is already attached (see _anchor_from_po_line_ref).

    A document that shows more than one PO number (`po_reference_ambiguous`)
    is never attached here either: the attach sweep refuses it, and a
    document that cannot be identified must not be placed by anything.

    A document neither strategy can place stays unattached, visible in the
    holding area, for a human to name its PO.
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
                Document.is_split_parent.is_(False),
            )
            .all()
        )
        for doc in unattached:
            if doc.po_reference_ambiguous:
                # More than one PO number is printed on this document.
                # `attach_unattached_to_open_sets` refuses to attach such a
                # document; the same rule applies here, or this sweep would
                # attach by a filename resemblance what the other sweep
                # deliberately leaves for a human.
                continue
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
                        Document.archived_at.is_(None),
                    )
                    .all()
                )
                anchor_sets = {a.po_set_id for a in anchors if a.po_set_id}
                if len(anchor_sets) == 1:
                    anchor = next(a for a in anchors if a.po_set_id in anchor_sets)
                    matched_ps_id = anchor.po_set_id
                    matched_po_norm = anchor.po_no_normalized

            # 2. Match via the PO's per-line delivery-note reference: the
            # number is printed on the document itself, not inferred from a
            # name.
            if not matched_ps_id:
                matched_ps_id, matched_po_norm = _anchor_from_po_line_ref(s, doc.dn_no)

            if matched_ps_id:
                ps = s.get(POSet, matched_ps_id)
                if (
                    ps
                    and ps.status != POSetStatus.merged
                    and ps.archived_at is None
                ):
                    doc.po_set_id = matched_ps_id
                    if not doc.po_no_normalized and matched_po_norm:
                        doc.po_no_normalized = matched_po_norm
                    touched.add(matched_ps_id)

        s.commit()
    return touched
