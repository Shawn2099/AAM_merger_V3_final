"""P1 TDD — VLM extraction: COMBINED single call + retry [2,5,15] + manual-only guard (FR-6.1-6.8)."""

from __future__ import annotations


def test_is_manual_only_never_vlm():
    from app.services.extraction import is_manual_only

    assert is_manual_only("CUSTOMS") is True
    assert is_manual_only("SHIPPING") is True
    assert is_manual_only("COMMERCIAL_INVOICE") is True
    assert is_manual_only("PO") is False
    assert is_manual_only("DN") is False
    assert is_manual_only("SI") is False
    assert is_manual_only("COMBINED") is False
    assert is_manual_only("UNKNOWN") is False


def test_extract_document_single_failure_raises(tmp_path, monkeypatch):
    """SPEC §5.2 / FR-6.5: extract_document performs single attempt, records attempt count and failed status on error."""
    from pathlib import Path

    import pytest
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus
    from app.models.base import Base
    from app.services.extraction import extract_document

    cfg = load_config("config.example.yaml")
    db_path = tmp_path / "test.db"
    cfg.paths.database_path = str(db_path)
    cfg.paths.stored_documents_folder = str(tmp_path / "stored")
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="abc123",
            original_filename="po.pdf",
            stored_path=str(tmp_path / "stored" / "po.pdf"),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    # mock VLM to fail
    monkeypatch.setattr(
        "app.services.extraction._call_vlm",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("vlm fail")),
    )

    with pytest.raises(RuntimeError, match="vlm fail"):
        extract_document(doc_id, cfg)

    with Session(eng) as s:
        d = s.get(Document, doc_id)
        assert d.extraction_attempt_count == 1
        assert d.extraction_status == ExtractionStatus.failed


def test_combine_single_vlm_call(tmp_path, monkeypatch):
    """FR-6.3: COMBINED uses single VLM call, not 3."""
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus
    from app.models.base import Base
    from app.services.extraction import extract_document

    cfg = load_config("config.example.yaml")
    db_path = tmp_path / "test2.db"
    cfg.paths.database_path = str(db_path)
    cfg.paths.stored_documents_folder = str(tmp_path / "stored2")
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)
    cfg.extraction.retry_backoff_seconds = [2, 5, 15]

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="def456",
            original_filename="combined.pdf",
            stored_path=str(tmp_path / "stored2" / "combined.pdf"),
            doc_type=DocType.COMBINED,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    calls = []

    def fake_vlm(*a, **kw):
        calls.append(1)
        return {"po_no_raw": "PO123", "line_items": []}

    monkeypatch.setattr("app.services.extraction._call_vlm", fake_vlm)

    doc = extract_document(doc_id, cfg)
    assert len(calls) == 1
    assert doc.extraction_status == ExtractionStatus.valid
    assert doc.extraction_attempt_count == 1


def test_manual_only_skips_vlm(tmp_path, monkeypatch):
    """FR-6.5: CUSTOMS/SHIPPING/COMMERCIAL_INVOICE never call VLM."""
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus
    from app.models.base import Base
    from app.services.extraction import extract_document

    cfg = load_config("config.example.yaml")
    db_path = tmp_path / "test3.db"
    cfg.paths.database_path = str(db_path)
    cfg.paths.stored_documents_folder = str(tmp_path / "stored3")
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="ghi789",
            original_filename="customs.pdf",
            stored_path=str(tmp_path / "stored3" / "customs.pdf"),
            doc_type=DocType.CUSTOMS,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    def should_not_be_called(*a, **kw):
        raise AssertionError("VLM should not be called for CUSTOMS")

    monkeypatch.setattr("app.services.extraction._call_vlm", should_not_be_called)

    doc = extract_document(doc_id, cfg)
    # manual docs stay pending or valid without VLM? spec says not extracted via VLM, keep pending
    assert doc.extraction_attempt_count == 0


def test_attempt_count_cap_at_three(tmp_path, monkeypatch):
    """SPEC §6.4: extraction_attempt_count is capped at 3; does not call VLM if >= 3."""
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus
    from app.models.base import Base
    from app.services.extraction import extract_document

    cfg = load_config("config.example.yaml")
    db_path = tmp_path / "test_cap.db"
    cfg.paths.database_path = str(db_path)
    cfg.paths.stored_documents_folder = str(tmp_path / "stored_cap")
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="cap123",
            original_filename="fail_po.pdf",
            stored_path=str(tmp_path / "stored_cap" / "fail_po.pdf"),
            doc_type=DocType.PO,
            extraction_status=ExtractionStatus.pending,
            extraction_attempt_count=3,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    def should_not_be_called(*a, **kw):
        raise AssertionError("VLM should not be called when attempt count >= 3")

    monkeypatch.setattr("app.services.extraction._call_vlm", should_not_be_called)

    doc = extract_document(doc_id, cfg)
    assert doc.extraction_status == ExtractionStatus.failed
    assert doc.extraction_attempt_count == 3


def test_vlm_skip_maps_to_unknown(tmp_path, monkeypatch):
    """VLM SKIP type (blank/T&C pages) maps to DocType.UNKNOWN holding area (FR-5.3)."""
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus
    from app.models.base import Base
    from app.services.extraction import extract_document

    cfg = load_config("config.example.yaml")
    db_path = tmp_path / "test_skip.db"
    cfg.paths.database_path = str(db_path)
    cfg.paths.stored_documents_folder = str(tmp_path / "stored_skip")
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="skip123",
            original_filename="blank.pdf",
            stored_path=str(tmp_path / "stored_skip" / "blank.pdf"),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    monkeypatch.setattr(
        "app.services.extraction._call_vlm",
        lambda *a, **kw: {"document_type": "SKIP", "po_no_raw": None, "line_items": []},
    )

    doc = extract_document(doc_id, cfg)
    assert doc.doc_type == DocType.UNKNOWN
    assert doc.extraction_status == ExtractionStatus.valid


def test_parse_scaled_int():
    from app.services.extraction import _parse_scaled_int

    # Raw strings as Luna returns them -- code scales x1000 via Decimal
    assert _parse_scaled_int("50") == 50000
    assert _parse_scaled_int("16.5") == 16500
    assert _parse_scaled_int("16.50") == 16500
    assert _parse_scaled_int("1.5") == 1500
    assert _parse_scaled_int("1") == 1000
    assert _parse_scaled_int("1.00") == 1000
    assert _parse_scaled_int(None) == 0
    assert _parse_scaled_int("0.01") == 10


def test_combined_missing_sections_fails(tmp_path, monkeypatch):
    """SPEC §7.3 FR-6.7: COMBINED without all 3 sections fails extraction."""
    import pytest
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus
    from app.models.base import Base
    from app.services.extraction import extract_document

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "test_comb.db")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored_comb")
    tmp_path.mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="comb_partial",
            original_filename="partial_comb.pdf",
            stored_path=str(tmp_path / "partial_comb.pdf"),
            doc_type=DocType.COMBINED,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    # Mock VLM returning COMBINED with missing SI section
    monkeypatch.setattr(
        "app.services.extraction._call_vlm",
        lambda *a, **kw: {
            "document_type": "COMBINED",
            "has_po_section": True,
            "has_dn_section": True,
            "has_si_section": False,  # Missing SI section!
            "po_no_raw": "PO100",
            "line_items": [{"line_item_no": "1", "description": "ITEM", "quantity": 50000}],
        },
    )

    with pytest.raises(ValueError, match="COMBINED document missing required PO, DN, or SI"):
        extract_document(doc_id, cfg)

    with Session(eng) as s:
        d = s.get(Document, doc_id)
        assert d.extraction_status == ExtractionStatus.failed


def test_real_sample_regression_siv_ars_25_7230(tmp_path, monkeypatch):
    """Regression test for real vendor sample SIV-ARS-25-7230 (quantity==50000)."""
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus, LineItem
    from app.models.base import Base
    from app.services.extraction import extract_document

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "test_siv.db")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored_siv")
    tmp_path.mkdir(parents=True, exist_ok=True)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="siv_ars_hash",
            original_filename="SIV-ARS-25-7230.pdf",
            stored_path=str(tmp_path / "SIV-ARS-25-7230.pdf"),
            doc_type=DocType.SI,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    monkeypatch.setattr(
        "app.services.extraction._call_vlm",
        lambda *a, **kw: {
            "document_type": "SI",
            "document_number": "SIV-ARS-25-7230",
            "po_no_raw": "4500043712",
            "line_items": [
                {
                    "line_item_no": "10",
                    "description": "CRC Lectra Cleaner: 400ML Aerosol Can",
                    "quantity": "50",
                    "unit_price": "26.00",
                }
            ],
        },
    )

    extracted = extract_document(doc_id, cfg)
    assert extracted.extraction_status == ExtractionStatus.valid
    assert extracted.po_no_normalized == "4500043712"
    assert extracted.si_no == "SIV-ARS-25-7230"

    with Session(eng) as s:
        items = s.query(LineItem).filter_by(document_id=doc_id).all()
        assert len(items) == 1
        assert items[0].quantity == 50000
        assert items[0].unit_price == 26000


def test_raw_json_column_exists(tmp_path):
    from sqlalchemy import inspect

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models.base import Base

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "t.db")
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    cols = [c["name"] for c in inspect(eng).get_columns("documents")]
    assert "raw_extraction_json" in cols
