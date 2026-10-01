"""Ingestion — SHA-256 dedup, permanent storage, stability check, input clearing (FR-4.1-4.8)."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import AppConfig
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus


def is_file_stable(p: Path, interval: int, count: int) -> bool:
    if not p.exists():
        return False
    sizes = []
    for _ in range(count):
        if not p.exists():
            return False
        sizes.append(p.stat().st_size)
        time.sleep(interval)
    return len(set(sizes)) == 1 and sizes[0] >= 0


def ingest_file(src: Path, cfg: AppConfig) -> Document:
    data = src.read_bytes()
    h = hashlib.sha256(data).hexdigest()
    eng = get_engine(cfg)
    from app.models.base import Base

    Base.metadata.create_all(eng)
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
        # Split-child linkage (PLAN Rev 2 §4.5): `<sha16>_p<i>.pdf` filenames
        # resolve to the sterile parent via SHA prefix. Crash-safe, no sidecar.
        # A doc that is itself a split parent never becomes a child.
        parent_id: int | None = None
        try:
            from app.services.splitting import parse_child_filename

            sha16, _ = parse_child_filename(src.name)
            parent_row = (
                s.query(Document)
                .filter(
                    Document.is_split_parent.is_(True),
                    Document.sha256_hash.like(f"{sha16}%"),
                )
                .order_by(Document.id.asc())
                .first()
            )
            if parent_row is not None:
                parent_id = parent_row.id
        except ValueError:
            pass  # ordinary filename — no linkage
        except Exception:
            pass  # linkage must never fail an ingest
        doc = Document(
            sha256_hash=h,
            original_filename=src.name,
            stored_path=str(stored),
            doc_type=DocType.UNKNOWN,
            extraction_status=ExtractionStatus.pending,
            parent_document_id=parent_id,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)
        return doc


def find_input_pdfs(input_folder: Path | str) -> list[Path]:
    """Every PDF in the input folder, once each, on any platform.

    Do NOT write this as `glob("*.pdf") + glob("*.PDF")`. pathlib's glob is
    CASE-INSENSITIVE on Windows, so both patterns match the same files and the
    input set is enumerated twice. That is not harmless: `sync_flow` extracts
    after ingesting, so every document was sent to the VLM a second time on
    every sync — double the API cost, on the one platform this is deployed to.
    It reproduces only on Windows, so Linux development hides it.
    """
    folder = Path(input_folder)
    if not folder.exists():
        return []
    return sorted(f for f in folder.iterdir() if f.is_file() and f.suffix.lower() == ".pdf")


def delete_input_files(po_set: POSet, input_folder: Path | str) -> list[str]:
    """FR-4.8: delete input files once PO Set is merged and extracted data is persisted.

    Matches by original_filename and by SHA-256 hash for renamed duplicates.
    Never deletes stored_path copies.
    """
    deleted: list[str] = []
    if not (po_set.status == POSetStatus.merged and po_set.merged_output_path):
        return deleted

    in_dir = Path(input_folder)
    if not in_dir.exists():
        return deleted

    # Collect valid document hashes and filenames
    valid_hashes = {
        doc.sha256_hash
        for doc in (po_set.documents or [])
        if doc.extraction_status == ExtractionStatus.valid and doc.sha256_hash
    }
    valid_names = {
        doc.original_filename
        for doc in (po_set.documents or [])
        if doc.extraction_status == ExtractionStatus.valid and doc.original_filename
    }

    valid_real_hashes = {h for h in valid_hashes if len(h) == 64}

    # 1. Delete by direct filename (verifying content hash matches if
    # real 64-char sha256 is present)
    for name in valid_names:
        target = in_dir / name
        if target.exists():
            try:
                target_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                if not valid_real_hashes or target_hash in valid_real_hashes:
                    target.unlink(missing_ok=True)
                    deleted.append(name)
            except Exception:
                pass

    # 2. Delete any remaining duplicate files matching the SHA-256 hashes
    if valid_hashes:
        for f in find_input_pdfs(in_dir):
            if not f.exists():
                continue
            try:
                f_hash = hashlib.sha256(f.read_bytes()).hexdigest()
                if f_hash in valid_hashes:
                    f.unlink(missing_ok=True)
                    if f.name not in deleted:
                        deleted.append(f.name)
            except Exception:
                pass

    return deleted
