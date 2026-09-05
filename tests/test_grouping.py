def test_normalize():
    from app.services.grouping import normalize_po_no

    assert normalize_po_no("PO-1234") == "PO1234"
    assert normalize_po_no("po 1234") == "PO1234"
    assert normalize_po_no("PO/1234") == "PO1234"
    assert normalize_po_no("22398, 0") == "223980"
    assert normalize_po_no("100-060-0000") == "1000600000"


def test_grouping_same_set():
    from app.core.config import load_config
    from app.services.grouping import get_or_create_po_set

    cfg = load_config("config.example.yaml")
    a = get_or_create_po_set("PO-1234", cfg)
    b = get_or_create_po_set("po 1234", cfg)
    assert a.id == b.id


def test_grouping_post_merge_starts_new_set(tmp_path):
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import POSet, POSetStatus
    from app.models.base import Base
    from app.services.grouping import get_or_create_po_set

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test_grouping.db"
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    # 1. Initial set created
    set1 = get_or_create_po_set("PO-9999", cfg)
    assert set1.status == POSetStatus.pending

    # 2. Re-fetching reuses open set
    set1_refetch = get_or_create_po_set("PO-9999", cfg)
    assert set1_refetch.id == set1.id

    # 3. Mark set1 as merged (permanently closed)
    with Session(eng) as s:
        db_set1 = s.get(POSet, set1.id)
        db_set1.status = POSetStatus.merged
        s.commit()

    # 4. New document with same PO must start a new set (FR-14.6)
    set2 = get_or_create_po_set("PO-9999", cfg)
    assert set2.id != set1.id
    assert set2.status == POSetStatus.pending


def test_resolve_unattached_documents_by_sibling_prefix(tmp_path):
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
    from app.models.base import Base
    from app.services.grouping import resolve_unattached_documents

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test_unattached.db"
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps = POSet(po_no_normalized="TOGAPO2526163", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

        # SI is already grouped
        doc_si = Document(
            sha256_hash="h_si_477",
            original_filename="SIV-DTS-25-477-pages-1.pdf",
            stored_path="dummy1.pdf",
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized="TOGAPO2526163",
            dn_no="GDN-DTS-25-505",
        )
        # DN is unattached (po_no_raw is None)
        doc_dn = Document(
            sha256_hash="h_dn_477",
            original_filename="SIV-DTS-25-477-pages-2.pdf",
            stored_path="dummy2.pdf",
            doc_type=DocType.DN,
            extraction_status=ExtractionStatus.valid,
            po_set_id=None,
            po_no_normalized=None,
            dn_no="GDN-DTS-25-505",
        )
        s.add_all([doc_si, doc_dn])
        s.commit()

    touched = resolve_unattached_documents(cfg)
    assert ps_id in touched

    with Session(eng) as s:
        dn_after = s.query(Document).filter(Document.sha256_hash == "h_dn_477").first()
        assert dn_after.po_set_id == ps_id
        assert dn_after.po_no_normalized == "TOGAPO2526163"


def test_resolve_ambiguous_dn_stays_unattached(tmp_path):
    """Same dn_no claimed by two different open sets → no first-wins guess;
    the DN waits for the operator (human decision 2026-09-05)."""
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
    from app.models.base import Base
    from app.services.grouping import resolve_unattached_documents

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test_ambig.db"
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    with Session(eng) as s:
        ps_a = POSet(po_no_normalized="PO_AAAA", status=POSetStatus.pending)
        ps_b = POSet(po_no_normalized="PO_BBBB", status=POSetStatus.pending)
        s.add_all([ps_a, ps_b])
        s.commit()
        s.refresh(ps_a)
        s.refresh(ps_b)
        for ps_id, tag in ((ps_a.id, "a"), (ps_b.id, "b")):
            s.add(
                Document(
                    sha256_hash=f"h_anchor_{tag}",
                    original_filename=f"anchor_{tag}.pdf",
                    stored_path="dummy.pdf",
                    doc_type=DocType.SI,
                    extraction_status=ExtractionStatus.valid,
                    po_set_id=ps_id,
                    dn_no="DN-SHARED",
                )
            )
        s.add(
            Document(
                sha256_hash="h_orphan",
                original_filename="orphan.pdf",
                stored_path="dummy2.pdf",
                doc_type=DocType.DN,
                extraction_status=ExtractionStatus.valid,
                po_set_id=None,
                dn_no="DN-SHARED",
            )
        )
        s.commit()

    resolve_unattached_documents(cfg)

    with Session(eng) as s:
        orphan = s.query(Document).filter(Document.sha256_hash == "h_orphan").first()
        assert orphan.po_set_id is None


def test_dn_never_mints_orphan_set(tmp_path):
    """BLOCKER-5: DN/SI must not mint sets from decoy codes — they wait
    unattached until a PO/COMBINED anchors the key (wait indefinitely)."""
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, POSet
    from app.models.base import Base
    from app.services.grouping import attach_unattached_to_open_sets, get_or_create_po_set

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test_decoy.db"
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    # DN key with no open set: attach-only returns None, mints nothing
    assert get_or_create_po_set("PO_DECOY", cfg, create=False) is None
    with Session(eng) as s:
        assert s.query(POSet).count() == 0

    # PO anchors the key (mints)
    ps = get_or_create_po_set("PO_DECOY", cfg, create=True)
    assert ps is not None

    with Session(eng) as s:
        s.add(
            Document(
                sha256_hash="h_decoy_dn",
                original_filename="dn.pdf",
                stored_path="dummy.pdf",
                doc_type=DocType.DN,
                extraction_status=ExtractionStatus.valid,
                po_set_id=None,
                po_no_normalized="PODECOY",
                dn_no="DN-X",
            )
        )
        s.add(
            Document(
                sha256_hash="h_lonely_dn",
                original_filename="lonely.pdf",
                stored_path="dummy2.pdf",
                doc_type=DocType.DN,
                extraction_status=ExtractionStatus.valid,
                po_set_id=None,
                po_no_normalized="PONOANCHOR",
                dn_no="DN-Y",
            )
        )
        s.commit()

    touched = attach_unattached_to_open_sets(cfg)
    assert ps.id in touched
    with Session(eng) as s:
        decoy = s.query(Document).filter(Document.sha256_hash == "h_decoy_dn").first()
        assert decoy.po_set_id == ps.id
        lonely = s.query(Document).filter(Document.sha256_hash == "h_lonely_dn").first()
        assert lonely.po_set_id is None  # waits indefinitely, stays visible
        assert s.query(POSet).count() == 1  # no orphan set minted
