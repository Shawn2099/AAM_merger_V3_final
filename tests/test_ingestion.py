from pathlib import Path

from app.core.config import load_config
from app.services.ingestion import ingest_file, is_file_stable


def test_is_file_stable(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"x")
    # 2sx2 stable if not growing
    assert is_file_stable(p, interval=0, count=2) is True  # 0 for fast test, real 2s


def test_dedup_sha256(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.input_folder = tmp_path / "input"
    (tmp_path / "stored").mkdir()
    (tmp_path / "input").mkdir()
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    d1 = ingest_file(a, cfg)
    d2 = ingest_file(b, cfg)
    assert d1.sha256_hash == d2.sha256_hash
    assert d1.id == d2.id  # dedup: same hash -> same row


def test_stored_path_never_deleted(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    (tmp_path / "stored").mkdir()
    p = tmp_path / "orig.pdf"
    p.write_bytes(b"data")
    d = ingest_file(p, cfg)
    assert Path(d.stored_path).exists()


def test_delete_input_files_when_merged_and_valid(tmp_path):
    from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus
    from app.services.ingestion import delete_input_files

    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    in_file = input_dir / "invoice.pdf"
    in_file.write_bytes(b"pdf data")

    stored_dir = tmp_path / "stored"
    stored_dir.mkdir(parents=True, exist_ok=True)
    stored_file = stored_dir / "stored_invoice.pdf"
    stored_file.write_bytes(b"pdf data")

    doc = Document(
        id=1,
        sha256_hash="h1",
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
