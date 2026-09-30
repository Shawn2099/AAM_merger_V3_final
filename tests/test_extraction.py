"""P1 TDD — VLM extraction: COMBINED single call + retry [2,5,15] + manual-only guard (FR-6.1-6.8)."""

from __future__ import annotations


def test_is_manual_only_never_vlm():
    from app.services.extraction import is_manual_only

    assert is_manual_only("CUSTOMS") is True
    assert is_manual_only("SHIPPING") is True
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
    """FR-6.5: CUSTOMS/SHIPPING never call the VLM."""
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


def test_call_vlm_maps_a_realistic_response(tmp_path, monkeypatch):
    """Runs the REAL `_call_vlm`, faking only the network.

    This is the regression guard for the bug that made the whole product
    non-functional: `_call_vlm` built its return dict by reading attributes the
    schema never declared (`item_code`, `uom`, `total_price`, `line_type`), so
    ANY response carrying a line item raised AttributeError and the document was
    marked `failed`. Every other extraction test mocks `_call_vlm` itself, so
    that mapping code was never executed.

    The fake instructor client returns a real `_VLMPageExtraction`, so the
    mapping, the key names, and the caller's expectations are all exercised.
    """
    import app.services.extraction as ext

    pdf = tmp_path / "po.pdf"
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(str(pdf))

    response = ext._VLMPageExtraction(
        document_type="PO",
        has_po_section=True,
        document_number="210851",
        po_reference=None,
        po_reference_ambiguous=False,
        vendor_name="ACME TRADING",
        line_items=[
            ext._VLMLineItem(
                line_item_no="1",
                description="WASHER, FLAT SAE 1/4 IN",
                quantity="12.45000000",
                unit_price="1620.00",
                dn_no=None,
            ),
            ext._VLMLineItem(
                line_item_no="2",
                description="NUT, HEX 9/16",
                quantity="50",
                unit_price="350.00",
                dn_no="GDN-9",
            ),
        ],
    )

    seen = {}

    class FakeCompletions:
        def create(self, **kwargs):
            seen.update(kwargs)
            return response

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(ext.instructor, "from_openai", lambda *a, **kw: FakeClient())

    out = ext._call_vlm(str(pdf), "PO", _cfg_with_vlm(tmp_path))

    # the call really was made, with the PDF attached and the right model
    assert seen["model"] == _cfg_with_vlm(tmp_path).vlm.model
    assert seen["response_model"] is ext._VLMPageExtraction
    assert seen["max_retries"] == 0
    user_content = seen["messages"][1]["content"]
    assert user_content[1]["type"] == "file"
    assert user_content[1]["file"]["file_data"].startswith("data:application/pdf;base64,")

    # every key the caller reads must be present
    for key in (
        "document_type",
        "has_po_section",
        "has_dn_section",
        "has_si_section",
        "document_number",
        "po_no_raw",
        "po_reference",
        "po_reference_ambiguous",
        "vendor_name",
        "line_items",
    ):
        assert key in out, f"_call_vlm did not return {key!r}"

    # PO carries no po_reference, so po_no_raw falls back to its own number
    assert out["po_no_raw"] == "210851"
    assert out["po_reference"] is None
    assert out["po_reference_ambiguous"] is False

    # line items keep only the six schema fields, as raw strings
    assert len(out["line_items"]) == 2
    first = out["line_items"][0]
    assert set(first) == {"line_item_no", "description", "quantity", "unit_price", "dn_no"}
    assert first["quantity"] == "12.45000000", "quantity must be passed through verbatim"
    assert first["unit_price"] == "1620.00"
    assert out["line_items"][1]["dn_no"] == "GDN-9"


def test_call_vlm_maps_every_declared_schema_field(tmp_path, monkeypatch):
    """The mapping may only read attributes the schema actually declares.

    Guards the original failure mode directly: if someone adds a field to the
    response and forgets it in the mapping (or removes one and forgets the
    mapping), this fails instead of the pipeline failing in production.
    """
    import app.services.extraction as ext

    doc_fields = set(ext._VLMPageExtraction.model_fields)
    line_fields = set(ext._VLMLineItem.model_fields)

    # keys the caller (extract_document) reads off the result dict
    caller_reads = {
        "document_type",
        "has_po_section",
        "has_dn_section",
        "has_si_section",
        "po_reference_ambiguous",
        "document_number",
        "po_reference",
        "line_items",
    }
    assert caller_reads <= doc_fields, (
        f"caller reads keys the schema lacks: {caller_reads - doc_fields}"
    )

    caller_reads_line = {"line_item_no", "description", "quantity", "unit_price", "dn_no"}
    assert caller_reads_line <= line_fields, (
        f"caller reads line keys the schema lacks: {caller_reads_line - line_fields}"
    )


def test_call_vlm_fails_closed_without_an_api_key(tmp_path, monkeypatch):
    """No key -> RuntimeError before any network call. Never a silent pass."""
    import app.services.extraction as ext

    pdf = tmp_path / "po.pdf"
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(str(pdf))

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(ext, "Path", _no_dotenv_path(), raising=False)

    def _boom(*a, **kw):
        raise AssertionError("must not reach the API without a key")

    monkeypatch.setattr(ext.instructor, "from_openai", _boom)

    import pytest

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        ext._call_vlm(str(pdf), "PO", _cfg_with_vlm(tmp_path))


def _no_dotenv_path():
    """A Path stub whose .exists() is always False, so no .env is discovered."""
    from pathlib import Path as RealPath

    class NoDotenvPath(RealPath):
        def exists(self):
            return False

    return NoDotenvPath


def _cfg_with_vlm(tmp_path):
    from app.core.config import load_config

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "vlm.db")
    return cfg


def test_prompts_are_clean_utf8():
    """The prompt text is sent to the model verbatim — it must not be corrupted.

    A file round-tripped through the wrong encoding turns every em-dash into
    `a<dash>` and every arrow into `a<arrow>`. That damage is invisible in a
    diff review, still passes every behavioural test, and quietly degrades the
    instructions the model actually receives. The whole prompt was corrupted
    once this way; this makes it loud.
    """
    import pathlib

    # Mojibake markers written as escapes so this test file cannot itself be
    # the thing that gets corrupted.
    markers = (
        "â€",  # truncated UTF-8 sequence, e.g. a mangled em-dash
        "Â§",  # section sign
        "â†",  # rightwards arrow
    )
    for name in ("src/app/services/extraction.py", "AAM_merger_V3_PRODUCT.md"):
        text = pathlib.Path(name).read_text(encoding="utf-8")
        assert "�" not in text, f"{name} contains replacement characters"
        for marker in markers:
            assert marker not in text, f"{name} contains mojibake {marker!r}"

    # the two instructions the model depends on most must be intact
    from app.services.extraction import _PAGE_PROMPT

    assert "→ po_reference" in _PAGE_PROMPT, "STEP 1 arrow is corrupted"
    assert "STEP 4 — QUANTITY" in _PAGE_PROMPT, "STEP 4 heading is corrupted"


def test_prompt_requires_a_number_only_quantity():
    """The `quantity` contract is digits only — no unit, no UOM, no currency.

    This is a contract with the model, not a code path, so it is guarded by
    asserting the instruction survives. A vendor cell reading "12.5 EA" must
    come back as "12.5"; if the prompt stops saying so, the strict parser
    rejects the row and the set quarantines for no legible reason.

    If UOM is ever to be extracted, this test and PRODUCT doc section 4 are the
    two things that must change together.
    """
    from app.services.extraction import _PAGE_PROMPT, _SYSTEM_PROMPT, _VLMLineItem

    step4 = _PAGE_PROMPT.split("STEP 4")[1]
    assert "NUMBER ONLY" in step4
    assert "UOM" in step4
    assert "12.5 EA" in step4, "the worked example for stripping a unit is missing"

    assert "NUMBER-ONLY" in _SYSTEM_PROMPT

    # the schema field carries the same contract for the structured-output path
    assert "no unit" in _VLMLineItem.model_fields["quantity"].description.lower()


def test_uom_is_not_part_of_the_extraction_schema():
    """No UOM, no part number, no line type, no confidence — by design.

    All four existed at some point and were removed. `quantity` is the only
    numeric line field that participates in reconciliation.
    """
    from app.services.extraction import _VLMLineItem

    assert set(_VLMLineItem.model_fields) == {
        "line_item_no",
        "description",
        "quantity",
        "dn_no",
        "unit_price",
    }
    for gone in ("uom", "part_no", "item_code", "line_type", "total_price", "confidence"):
        assert gone not in _VLMLineItem.model_fields


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


def test_combined_without_components_quarantines_no_raise(tmp_path, monkeypatch):
    """PLAN Rev 2 Step 4: the FR-6.7 3-section gate is deleted. A COMBINED
    response whose ranges cannot be verified quarantines the parent and
    returns normally (never raises into the Prefect retry envelope)."""
    from pathlib import Path

    from pypdf import PdfWriter
    from sqlalchemy.orm import Session

    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import DocType, Document, ExtractionStatus
    from app.models.base import Base
    from app.services.extraction import extract_document

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "test_comb.db")
    cfg.paths.stored_documents_folder = str(tmp_path / "stored_comb")
    cfg.paths.quarantine_folder = str(tmp_path / "quarantine_comb")
    cfg.paths.combined_folder = str(tmp_path / "combined_comb")
    tmp_path.mkdir(parents=True, exist_ok=True)

    pdf = tmp_path / "partial_comb.pdf"
    w = PdfWriter()
    w.add_blank_page(width=100, height=100)
    with open(pdf, "wb") as f:
        w.write(f)

    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = Document(
            sha256_hash="comb_partial",
            original_filename="partial_comb.pdf",
            stored_path=str(pdf),
            doc_type=DocType.COMBINED,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        doc_id = doc.id

    # Mock VLM returning COMBINED with no verifiable components.
    monkeypatch.setattr(
        "app.services.extraction._call_vlm",
        lambda *a, **kw: {
            "document_type": "COMBINED",
            "has_po_section": True,
            "has_dn_section": True,
            "has_si_section": False,
            "page_count": 1,
            "components": [],
            "po_no_raw": "PO100",
            "line_items": [{"line_item_no": "1", "description": "ITEM", "quantity": 50000}],
        },
    )

    extract_document(doc_id, cfg)  # must NOT raise

    with Session(eng) as s:
        d = s.get(Document, doc_id)
        assert d.extraction_status == ExtractionStatus.failed
        assert d.line_items == []
    assert (Path(cfg.paths.quarantine_folder) / "_documents").exists()


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
