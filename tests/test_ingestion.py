import io
from pathlib import Path

from pypdf import PdfWriter

from app.core.config import load_config
from app.services.ingestion import ingest_file


def _pdf_bytes() -> bytes:
    """A real one-page PDF: ingestion now rejects non-%PDF- input (P2-23)."""
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_is_file_stable(tmp_path, monkeypatch):
    import importlib

    # Resolve the LIVE module, not the function imported at collection time:
    # another test may reload app.services.ingestion, leaving the imported
    # function bound to a stale module whose globals the autouse fixture does
    # not patch.
    ingestion = importlib.import_module("app.services.ingestion")
    p = tmp_path / "a.pdf"
    p.write_bytes(b"x")
    monkeypatch.setattr(ingestion, "MIN_FILE_AGE_SECONDS", 0)
    # A file created "now" can have a Windows creation time a hair in the future;
    # the age rule is not what this test is about. Pin the clock to mtime.
    monkeypatch.setattr(ingestion, "_last_touched", lambda st: st.st_mtime)
    # 2sx2 stable if not growing
    assert ingestion.is_file_stable(p, interval=0, count=2) is True  # real 2s


def test_dedup_sha256(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.input_folder = tmp_path / "input"
    (tmp_path / "stored").mkdir()
    (tmp_path / "input").mkdir()
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    data = _pdf_bytes()
    a.write_bytes(data)
    b.write_bytes(data)
    d1 = ingest_file(a, cfg)
    d2 = ingest_file(b, cfg)
    assert d1.sha256_hash == d2.sha256_hash
    assert d1.id == d2.id  # dedup: same hash -> same row


def test_stored_path_never_deleted(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    (tmp_path / "stored").mkdir()
    p = tmp_path / "orig.pdf"
    p.write_bytes(_pdf_bytes())
    d = ingest_file(p, cfg)
    assert Path(d.stored_path).exists()


def test_delete_input_files_when_merged_and_valid(tmp_path):
    import hashlib

    from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
    from app.services.ingestion import delete_input_files

    payload = b"pdf data"
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    in_file = input_dir / "invoice.pdf"
    in_file.write_bytes(payload)

    stored_dir = tmp_path / "stored"
    stored_dir.mkdir(parents=True, exist_ok=True)
    stored_file = stored_dir / "stored_invoice.pdf"
    stored_file.write_bytes(payload)

    doc = Document(
        id=1,
        sha256_hash=hashlib.sha256(payload).hexdigest(),
        original_filename="invoice.pdf",
        stored_path=str(stored_file),
        doc_type=DocType.SI,
        extraction_status=ExtractionStatus.valid,
    )
    ps = POSet(
        id=1,
        po_no_normalized="PO1",
        status=POSetStatus.merged,
        merged_output_path=str(tmp_path / "out.pdf"),
        documents=[doc],
    )

    deleted = delete_input_files(ps, input_dir)
    assert "invoice.pdf" in deleted
    assert not in_file.exists()
    assert stored_file.exists()  # stored_path permanently kept (FR-4.8)


def test_delete_input_files_keeps_a_same_named_file_with_different_bytes(tmp_path):
    """The content hash is the second line of defence after the filename."""
    import hashlib

    from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
    from app.services.ingestion import delete_input_files

    payload = b"pdf data"
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    in_file = input_dir / "invoice.pdf"
    in_file.write_bytes(b"a different document with the same name")

    stored_file = tmp_path / "stored_invoice.pdf"
    stored_file.write_bytes(payload)

    doc = Document(
        id=1,
        sha256_hash=hashlib.sha256(payload).hexdigest(),
        original_filename="invoice.pdf",
        stored_path=str(stored_file),
        doc_type=DocType.SI,
        extraction_status=ExtractionStatus.valid,
    )
    ps = POSet(
        id=1,
        po_no_normalized="PO1",
        status=POSetStatus.merged,
        merged_output_path=str(tmp_path / "out.pdf"),
        documents=[doc],
    )

    deleted = delete_input_files(ps, input_dir)
    assert deleted == []
    assert in_file.exists(), "a same-named file with different bytes must survive"


def test_delete_input_files_skipped_if_not_merged(tmp_path):
    from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
    from app.services.ingestion import delete_input_files

    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    in_file = input_dir / "invoice.pdf"
    in_file.write_bytes(b"pdf data")

    doc = Document(
        id=1,
        sha256_hash="h1",
        original_filename="invoice.pdf",
        stored_path="stored/path.pdf",
        doc_type=DocType.SI,
        extraction_status=ExtractionStatus.valid,
    )
    ps = POSet(
        id=1,
        po_no_normalized="PO1",
        status=POSetStatus.mismatched,
        documents=[doc],
    )

    deleted = delete_input_files(ps, input_dir)
    assert len(deleted) == 0
    assert in_file.exists()


def test_delete_input_files_by_hash_match(tmp_path):
    """FR-4.8: delete input files also clears duplicate/renamed files in input matching SHA256."""
    import hashlib

    from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
    from app.services.ingestion import delete_input_files

    input_dir = tmp_path / "input_hash"
    input_dir.mkdir(parents=True, exist_ok=True)

    data = b"identical pdf content"
    sha = hashlib.sha256(data).hexdigest()

    # Renamed file in input folder
    renamed_input = input_dir / "renamed_copy.pdf"
    renamed_input.write_bytes(data)

    stored_file = tmp_path / "stored.pdf"
    stored_file.write_bytes(data)

    doc = Document(
        id=1,
        sha256_hash=sha,
        original_filename="original.pdf",
        stored_path=str(stored_file),
        doc_type=DocType.SI,
        extraction_status=ExtractionStatus.valid,
    )
    ps = POSet(
        id=1,
        po_no_normalized="PO_HASH",
        status=POSetStatus.merged,
        merged_output_path=str(tmp_path / "out.pdf"),
        documents=[doc],
    )

    deleted = delete_input_files(ps, input_dir)
    assert "renamed_copy.pdf" in deleted
    assert not renamed_input.exists()
    assert stored_file.exists()
