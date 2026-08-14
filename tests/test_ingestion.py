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
