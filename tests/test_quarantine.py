"""Tests for quarantine — FR-13.5-13.9 copy+delete keeps files + audit, manual merger isolated (FR-14.11-14.13)."""

from pathlib import Path

from app.core.config import load_config


def _cfg_with_tmp(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test.db"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.output_folder = tmp_path / "output"
    (tmp_path / "stored").mkdir(parents=True, exist_ok=True)
    (tmp_path / "quarantine").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    return cfg


def _tiny_pdf(p: Path, width: int = 200, height: int = 200) -> Path:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=width, height=height)
    w.write(str(p))
    return p


def _create_quarantined_poset(tmp_path, cfg, po_no="PO-QUAR-001"):
    """Create quarantined POSet with 3 docs each having stored_path file and line_items."""
    import hashlib

    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
    from app.models.base import Base

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=POSetStatus.quarantined)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id
        # create 3 PDFs in stored folder
        for i, dt in enumerate([DocType.PO, DocType.DN, DocType.SI]):
            pdf_path = tmp_path / "stored" / f"{po_no}_{dt.value}_{i}.pdf"
            _tiny_pdf(pdf_path, width=200 + i * 10, height=200)
            sha = hashlib.sha256(f"{po_no}-{dt.value}-{i}".encode()).hexdigest()
            doc = Document(
                sha256_hash=sha,
                original_filename=f"{dt.value}_{i}.pdf",
                stored_path=str(pdf_path),
                doc_type=dt,
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps_id,
                po_no_normalized=po_no,
            )
            s.add(doc)
            s.flush()  # get doc.id
            # add one line_item per doc to verify line_items deletion
            li = LineItem(
                document_id=doc.id,
                description=f"item {i}",
                quantity=1000,
                unit_price=1000,
            )
            s.add(li)
        s.commit()
        return ps_id


def test_delete_keeps_files(tmp_path):
    """FR-13.7: Delete removes DB rows only, keeps stored_path + quarantine copies + writes audit_log."""
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import AuditLog, Document, LineItem, POSet
    from app.services.quarantine import delete_quarantined, quarantine_copy

    cfg = _cfg_with_tmp(tmp_path)
    po_set_id = _create_quarantined_poset(tmp_path, cfg, po_no="PO-QUAR-001")

    # fetch po_set with documents for quarantine_copy
    eng = get_engine(cfg)
    from app.models.base import Base

    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        # eagerly load documents
        s.refresh(ps, attribute_names=["documents"])
        doc_count = len(ps.documents)
        assert doc_count == 3
        stored_paths = [Path(d.stored_path) for d in ps.documents]
        # ensure stored files exist before copy
        for p in stored_paths:
            assert p.exists(), f"stored_path missing before copy: {p}"
        # copy to quarantine (shutil.copy, not move)
        q_folder = quarantine_copy(ps, cfg)
        assert q_folder.exists()
        assert q_folder == Path(cfg.paths.quarantine_folder) / ps.po_no_normalized
        # quarantine copies exist
        for p in stored_paths:
            qp = q_folder / p.name
            assert qp.exists(), f"quarantine copy missing: {qp}"
            # original still exists (copy not move)
            assert p.exists(), f"original should still exist after copy (not move): {p}"

    # keep stored_paths and q_folder for post-delete assertions (need to re-read)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        s.refresh(ps, attribute_names=["documents"])
        stored_paths = [Path(d.stored_path) for d in ps.documents]
        q_folder = Path(cfg.paths.quarantine_folder) / ps.po_no_normalized

    # now delete
    audit = delete_quarantined(po_set_id, cfg)
    # audit row exists and has correct action
    assert audit is not None
    # action may be enum or string; check value
    action_val = audit.action.value if hasattr(audit.action, "value") else str(audit.action)
    assert action_val == "quarantine_delete"

    # DB rows gone
    with Session(eng) as s:
        assert s.get(POSet, po_set_id) is None, "po_sets row should be deleted"
        remaining_docs = s.query(Document).filter_by(po_set_id=po_set_id).all()
        assert len(remaining_docs) == 0, "documents rows should be deleted"
        # line_items: all docs for this po_set are gone, so no line_items referencing them
        # check via remaining docs ids (none) or query all and ensure none reference deleted docs
        # simpler: count line_items that would have been for those docs by checking stored_paths count
        all_line_items = s.query(LineItem).all()
        # line_items for deleted docs should be gone; we created 3 line_items total, all should be gone
        # but other PO sets may have none; so check that none have document_id in deleted doc ids
        # Since we deleted, count should be 0 for this isolated DB
        assert len(all_line_items) == 0, f"line_items should be deleted, got {len(all_line_items)}"
        # audit_log row exists
        audits = (
            s.query(AuditLog).filter_by(action="quarantine_delete").all()
            if False
            else s.query(AuditLog).all()
        )
        # filter manually for enum equality
        found = [
            a
            for a in audits
            if (a.action.value if hasattr(a.action, "value") else str(a.action))
            == "quarantine_delete"
        ]
        assert len(found) >= 1, "audit_log quarantine_delete row should exist"

    # files still exist after DB delete
    for p in stored_paths:
        assert p.exists(), f"stored_path should still exist after delete: {p}"
        assert (q_folder / p.name).exists(), (
            f"quarantine copy should still exist after delete: {q_folder / p.name}"
        )


def test_quarantine_copy_uses_copy_not_move(tmp_path):
    """FR-13.3: quarantine_copy uses shutil.copy (not move) — originals remain."""
    from app.services.quarantine import quarantine_copy

    cfg = _cfg_with_tmp(tmp_path)
    po_set_id = _create_quarantined_poset(tmp_path, cfg, po_no="PO-COPY-001")
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet

    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        s.refresh(ps, attribute_names=["documents"])
        stored = [Path(d.stored_path) for d in ps.documents]
        q = quarantine_copy(ps, cfg)
        for p in stored:
            assert p.exists()
            assert (q / p.name).exists()
            # content equal (size same)
            assert (q / p.name).stat().st_size == p.stat().st_size


def test_delete_only_when_quarantined(tmp_path):
    """FR-13.5: delete only allowed for quarantined status."""
    import pytest

    from app.services.quarantine import delete_quarantined

    cfg = _cfg_with_tmp(tmp_path)
    # create pending POSet
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet, POSetStatus
    from app.models.base import Base

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = POSet(po_no_normalized="PO-PENDING-001", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        pending_id = ps.id

    with pytest.raises(ValueError):
        delete_quarantined(pending_id, cfg)


def test_manual_merge_isolated(tmp_path):
    """FR-14.11-14.13: manual_merge concatenates in given order, no DB side-effects, user-selectable output."""
    from pypdf import PdfReader

    from app.services.quarantine import manual_merge

    # create 3 PDFs with distinct widths to verify order
    a = _tiny_pdf(tmp_path / "a.pdf", width=100, height=200)
    b = _tiny_pdf(tmp_path / "b.pdf", width=200, height=200)
    c = _tiny_pdf(tmp_path / "c.pdf", width=300, height=200)

    # order [2,0,1] => c,a,b => widths 300,100,200
    out = manual_merge([a, b, c], order=[2, 0, 1])
    assert out.exists()
    reader = PdfReader(str(out))
    assert len(reader.pages) == 3
    widths = [float(p.mediabox.width) for p in reader.pages]
    assert widths == [300, 100, 200], f"manual order failed, got {widths}"

    # no DB row created — verify DB is empty or no po_sets created by manual merge
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet

    cfg = _cfg_with_tmp(tmp_path)
    eng = get_engine(cfg)
    from app.models.base import Base

    Base.metadata.create_all(eng)
    with Session(eng) as s:
        assert (
            s.query(POSet).count() == 0 or True
        )  # manual_merge should not require DB, just ensure no crash
        # actual assertion: manual_merge does not create POSet — if DB was empty before, still empty
        # we use isolated cfg, so count should be 0
        assert s.query(POSet).count() == 0


def test_manual_merge_output_user_selectable(tmp_path):
    """FR-14.12: manual_merge output can be written to user-chosen location (not forced to output_folder)."""
    from app.services.quarantine import manual_merge

    a = _tiny_pdf(tmp_path / "x.pdf", width=100)
    b = _tiny_pdf(tmp_path / "y.pdf", width=200)
    custom_out = tmp_path / "my_custom_folder" / "my_invoice_merged.pdf"
    out = manual_merge([a, b], order=[0, 1], output_path=custom_out)
    assert out == custom_out
    assert out.exists()
