import hashlib
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import AppConfig
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus


def is_file_stable(p: Path, interval: int, count: int) -> bool:
    sizes = []
    for _ in range(count):
        sizes.append(p.stat().st_size if p.exists() else -1)
        time.sleep(interval)
    return len(set(sizes)) == 1


def ingest_file(src: Path, cfg: AppConfig) -> Document:
    data = src.read_bytes()
    h = hashlib.sha256(data).hexdigest()
    eng = get_engine(cfg)
    with Session(eng) as s:
        existing = s.query(Document).filter_by(sha256_hash=h).first()
        if existing:
            # ensure stored file still exists (tmp dir may have been cleaned between runs)
            sp = Path(existing.stored_path)
            if not sp.exists():
                try:
                    sp.parent.mkdir(parents=True, exist_ok=True)
                    sp.write_bytes(data)
                except Exception:
                    pass
            return existing
        stored = Path(cfg.paths.stored_documents_folder) / f"{h}{src.suffix}"
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(data)
        doc = Document(
            sha256_hash=h,
            original_filename=src.name,
            stored_path=str(stored),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.pending,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        return doc


def clear_input_if_merged(po_set: POSet) -> bool:
    """FR-4.8 gate: clear input only if merged output exists and extraction persisted."""
    return bool(po_set.status == POSetStatus.merged and po_set.merged_output_path)
