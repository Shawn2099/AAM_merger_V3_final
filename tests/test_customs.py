"""Tests for customs gate â€” FR-12.1-12.4."""

from app.core.config import load_config


def _cfg_with_tmp_db(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test.db"
    # ensure storage dirs exist (some services need it)
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    (tmp_path / "stored").mkdir(parents=True, exist_ok=True)
    return cfg


def _create_po_set(tmp_path, po_no="PO-1234", status=None):
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet, POSetStatus
    from app.models.base import Base

    cfg = _cfg_with_tmp_db(tmp_path)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    if status is None:
        status = POSetStatus.pending
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=status)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id
    return ps_id, cfg


def _add_doc(tmp_path, cfg, po_set_id, doc_type_str):
    import hashlib

    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus
    from app.models.base import Base

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        from app.models import POSet

        s.get(POSet, po_set_id)
        h = hashlib.sha256(
            f"{po_set_id}-{doc_type_str}-{s.query(Document).count()}".encode()
        ).hexdigest()
        doc = Document(
            sha256_hash=h,
            original_filename=f"{doc_type_str}.pdf",
            stored_path=str(tmp_path / f"{h}.pdf"),
            doc_type=DocType(doc_type_str),
            extraction_status=ExtractionStatus.valid,
            po_set_id=po_set_id,
        )
        s.add(doc)
        s.commit()


def test_toggle_blocked(tmp_path):
    """Toggling the gate on records the requirement without inventing a verdict.

    `blocked_customs` means "quantities reconciled, customs pair missing". A
    set that has not reconciled must keep its real status until the next
    reconcile says otherwise.
    """
    from app.services.customs import is_blocked, toggle_customs

    po_set_id, cfg = _create_po_set(tmp_path, "PO-1234")
    ps = toggle_customs(po_set_id, cfg)
    assert ps.has_customs_toggle is True
    assert ps.status.value == "pending"
    assert is_blocked(ps) is True  # needs CUSTOMS+SHIPPING


def test_toggle_flip(tmp_path):
    from app.services.customs import toggle_customs

    po_set_id, cfg = _create_po_set(tmp_path, "PO-FLIP")
    ps = toggle_customs(po_set_id, cfg)
    assert ps.has_customs_toggle is True
    ps2 = toggle_customs(po_set_id, cfg)
    assert ps2.has_customs_toggle is False


def test_requires_two_docs(tmp_path):
    from app.services.customs import is_blocked, toggle_customs

    po_set_id, cfg = _create_po_set(tmp_path, "PO-2DOCS")
    ps = toggle_customs(po_set_id, cfg)
    assert is_blocked(ps) is True

    _add_doc(tmp_path, cfg, po_set_id, "CUSTOMS")
    # reload ps
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet

    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        assert is_blocked(ps) is True  # only 1 of 2

    _add_doc(tmp_path, cfg, po_set_id, "SHIPPING")
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        assert is_blocked(ps) is False  # both present -> not blocked


def test_only_customs_and_shipping_satisfy_the_gate(tmp_path):
    """The gate requires CUSTOMS *and* SHIPPING, and nothing else substitutes.

    COMMERCIAL_INVOICE was removed from the product, so it can no longer be
    attached at all. This test pins the narrower rule it used to illustrate:
    a single customs-family document never clears the gate.
    """
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet
    from app.services.customs import is_blocked, toggle_customs

    po_set_id, cfg = _create_po_set(tmp_path, "PO-CUST")
    ps = toggle_customs(po_set_id, cfg)
    assert is_blocked(ps) is True

    # CUSTOMS alone is not enough - the gate wants a pair.
    _add_doc(tmp_path, cfg, po_set_id, "CUSTOMS")
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        assert is_blocked(ps) is True

    # Only adding SHIPPING clears it.
    _add_doc(tmp_path, cfg, po_set_id, "SHIPPING")
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        assert is_blocked(ps) is False


def test_toggle_any_status(tmp_path):
    """FR-12.1: toggle allowed regardless of current status. On a merged set
    the status stays merged — the packet is rebuilt by the caller when both
    documents are present; it is never shown as awaiting customs."""
    from app.models import POSetStatus
    from app.services.customs import is_blocked, toggle_customs

    for st in [
        POSetStatus.pending,
        POSetStatus.mismatched,
        POSetStatus.quarantined,
        POSetStatus.blocked_customs,
    ]:
        po_set_id, cfg = _create_po_set(tmp_path, f"PO-ANY-{st.value}", status=st)
        ps = toggle_customs(po_set_id, cfg)
        assert ps.has_customs_toggle is True
        assert ps.status == st, (st, ps.status)
        assert is_blocked(ps) is True
        # Toggling off clears the flag; only the gate's own blocked state
        # returns to pending, never a quarantine or mismatch.
        ps2 = toggle_customs(po_set_id, cfg)
        assert ps2.has_customs_toggle is False
        expected2 = POSetStatus.pending if st == POSetStatus.blocked_customs else st
        assert ps2.status == expected2, (st, ps2.status)
        assert is_blocked(ps2) is False


def test_toggle_allowed_on_a_merged_set_and_status_stays_merged(tmp_path):
    """The customs requirement can be switched on after delivery. The status
    must not regress to blocked_customs: the packet is already delivered, and
    the append path rebuilds it when both documents are attached."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet, POSetStatus
    from app.services.customs import toggle_customs

    po_set_id, cfg = _create_po_set(tmp_path, "PO-MERGED", status=POSetStatus.merged)
    ps = toggle_customs(po_set_id, cfg)

    assert ps.has_customs_toggle is True
    assert ps.status == POSetStatus.merged
    eng = get_engine(cfg)
    with Session(eng) as s:
        assert s.get(POSet, po_set_id).status == POSetStatus.merged

    # Turning it back off also leaves the delivered status alone.
    ps2 = toggle_customs(po_set_id, cfg)
    assert ps2.has_customs_toggle is False
    assert ps2.status == POSetStatus.merged


def test_is_blocked_without_toggle_false(tmp_path):
    """Without toggle, never blocked even with no customs docs."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet
    from app.services.customs import is_blocked

    po_set_id, cfg = _create_po_set(tmp_path, "PO-NOTOGGLE")
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        assert ps.has_customs_toggle is False
        assert is_blocked(ps) is False

    # documents attached to an untoggled set can never block it
    _add_doc(tmp_path, cfg, po_set_id, "CUSTOMS")
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        assert is_blocked(ps) is False
