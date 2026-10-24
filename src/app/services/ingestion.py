"""Ingestion — SHA-256 dedup, permanent storage, stability check, input clearing (FR-4.1-4.8)."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import time
import uuid
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.core.config import AppConfig
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus

logger = logging.getLogger(__name__)


def is_file_stable(p: Path, interval: int, count: int) -> bool:
    if not p.exists():
        return False
    sizes = []
    for _ in range(count):
        if not p.exists():
            return False
        sizes.append(p.stat().st_size)
        time.sleep(interval)
    # A zero-byte file is never a finished upload, so it must not pass as stable.
    return len(set(sizes)) == 1 and sizes[0] > 0


_INCOMPLETE_PDF_REASON = (
    "incomplete or unreadable PDF (truncated copy, corrupt, or password-protected)"
)


def _incomplete_pdf_problem(data: bytes) -> str | None:
    """Return why these bytes are not a complete, openable PDF, or None if they are.

    pypdf itself needs the `%%EOF` / xref structure (it searches the tail of the
    file), so a truncated copy fails to open here. A separate fixed-window
    `%%EOF` test was dropped: it added no detection and rejected legitimate
    files that carry long trailing padding.
    """
    try:
        n_pages = len(PdfReader(io.BytesIO(data), strict=False).pages)
    except Exception as e:
        return f"pypdf could not open it: {type(e).__name__}: {e}"
    if n_pages < 1:
        return "pypdf found no pages"
    return None


#: A file modified more recently than this may still be mid-copy (a paused SMB or
#: USB transfer passes the size-stability poll), so it is left in place for the
#: next scan. An older incomplete file is permanently bad and is quarantined
#: with a visible reason instead of warning in the log every night.
_RECENT_WRITE_SECONDS = 300


def _reject_incomplete_pdf(src: Path, cfg: AppConfig, problem: str) -> None:
    """Refuse an incomplete PDF.

    A file written in the last few minutes may still be copying: it gets NO row
    and stays in the input folder to be retried, so the truncated bytes never
    become a permanent SHA duplicate and the finished file ingests as a normal
    new document. An older one is permanently bad: it is quarantined with a
    reason file and listed as a failed row (R-03) so the operator can see it.
    """
    reason = f"{_INCOMPLETE_PDF_REASON}: {problem}"
    try:
        recent = (time.time() - src.stat().st_mtime) < _RECENT_WRITE_SECONDS
    except OSError:
        recent = False
    if recent:
        logger.warning("Input %s not ingested (may still be copying; will retry): %s", src, reason)
        raise ValueError(f"{src.name}: {reason}")
    _reject_input(src, cfg, f"{src.name}: {reason}")


def _quarantine_unreadable(src: Path, cfg: AppConfig, exc: Exception) -> Path | None:
    """Move an input file that cannot even be read out of the scan loop.

    Without this, a PermissionError/MemoryError/OSError happened before any
    Document row existed: no holding-area row, no attempt counter, no
    quarantine — the same file was re-read every night forever. The reason file
    is the trace, because there is no DB row to carry it.

    Also used for a file that is readable but cannot be a PDF (wrong magic,
    over the size cap): extracting it would burn three VLM attempts and end in
    the same place, so it is rejected on the way in instead.
    """
    try:
        dest_dir = Path(cfg.paths.quarantine_folder) / "_documents"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{src.stem}_{uuid.uuid4().hex[:8]}{src.suffix}"
        shutil.move(str(src), str(dest))
        dest.with_suffix(dest.suffix + ".reason.txt").write_text(
            f"Could not read input file before ingestion: {type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
        return dest
    except Exception:
        logger.warning("Could not move unreadable input file %s to quarantine", src, exc_info=True)
        return None


def _record_rejected_document(original_name: str, dest: Path, reason: str, cfg: AppConfig) -> None:
    """Give a permanently rejected input file a `failed` row so the operator sees it.

    Without a row the only trace of a non-PDF, a truncated or encrypted copy, or
    an over-cap file is a reason file on disk. The row is UNKNOWN, unattached and
    already at the attempt cap, so it lists on the Unclassified page as a failure
    and the VLM is never called for it. `stored_path` is the quarantined copy.
    Never raises: the caller is already refusing the file.
    """
    from app.services.extraction import _attempt_cap

    try:
        digest = hashlib.sha256()
        with dest.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        sha = digest.hexdigest()
        eng = get_engine(cfg)
        from app.models.base import Base

        Base.metadata.create_all(eng)
        with Session(eng) as s:
            exists = (
                s.query(Document.id)
                .filter(Document.sha256_hash == sha, Document.archived_at.is_(None))
                .first()
            )
            if exists is not None:
                return  # the same bytes were rejected (or ingested) before
            s.add(
                Document(
                    sha256_hash=sha,
                    original_filename=original_name,
                    stored_path=str(dest),
                    doc_type=DocType.UNKNOWN,
                    extraction_status=ExtractionStatus.failed,
                    extraction_attempt_count=_attempt_cap(cfg),
                    raw_extraction_json=json.dumps({"ingest_rejected": reason}),
                )
            )
            s.commit()
    except Exception:
        logger.warning("Could not record rejected input %s", original_name, exc_info=True)


def _reject_input(src: Path, cfg: AppConfig, reason: str) -> None:
    """Quarantine a readable input file that is not an acceptable PDF."""
    exc = ValueError(reason)
    name = src.name
    dest = _quarantine_unreadable(src, cfg, exc)
    if dest is not None:
        _record_rejected_document(name, dest, reason, cfg)
    raise exc


def ingest_file(src: Path, cfg: AppConfig) -> Document:
    # A `*.pdf` suffix proves nothing: a zero-byte file, an HTML error page
    # saved as .pdf, or a truncated download would otherwise be stored and sent
    # to the VLM up to three times, costing API calls and ending as `failed`.
    # Reject it here, before a row exists, and leave a reason file behind.
    #
    # The size cap reuses the same limit as the browser upload paths. Read it
    # from the module (not a module-level import) so tests and operators can
    # patch one symbol, as tests/test_dashboard.py already does.
    from app.core import limits

    # A transient OSError (antivirus/backup holding the file, a flaky share) says
    # nothing about the content: leave the file in place for the next sync.
    try:
        size = src.stat().st_size
    except OSError:
        logger.warning("Could not stat %s; leaving it for the next sync", src, exc_info=True)
        raise
    except Exception as e:
        _quarantine_unreadable(src, cfg, e)
        raise
    if size > limits.MAX_UPLOAD_BYTES:
        _reject_input(
            src,
            cfg,
            f"file is {size} bytes, over the {limits.MAX_UPLOAD_BYTES}-byte upload cap",
        )
    try:
        data = src.read_bytes()
    except OSError:
        logger.warning("Could not read %s; leaving it for the next sync", src, exc_info=True)
        raise
    except Exception as e:
        _quarantine_unreadable(src, cfg, e)
        raise
    if b"%PDF-" not in data[:1024]:
        _reject_input(
            src,
            cfg,
            "no %PDF- header in the first 1024 bytes; it is not a PDF",
        )
    # A partial copy keeps its header, so it passes the check above. Verify it
    # is complete BEFORE hashing: otherwise the truncated bytes are stored and
    # sent to the VLM, and the finished file later lands as a second document.
    problem = _incomplete_pdf_problem(data)
    if problem is not None:
        _reject_incomplete_pdf(src, cfg, problem)
    h = hashlib.sha256(data).hexdigest()
    eng = get_engine(cfg)
    from app.models.base import Base

    Base.metadata.create_all(eng)
    with Session(eng) as s:
        existing = (
            s.query(Document)
            .filter_by(sha256_hash=h)
            .filter(Document.archived_at.is_(None))
            .first()
        )
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
                    Document.archived_at.is_(None),
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


def find_input_pdfs(
    input_folder: Path | str, exclude: tuple[Path | str, ...] | list[Path | str] = ()
) -> list[Path]:
    """Every PDF in the input folder AND its subfolders, once each, on any platform.

    Subfolders are read (R-17): a vendor or an operator may drop files in a
    folder per supplier or per day. Hidden folders (a leading dot) and any
    folder in `exclude` (the other configured data folders, when they happen to
    sit inside input) are skipped. Zero-byte files are listed here but never
    pass the stability check, so they are skipped until they have content.

    Do NOT write this as `glob("*.pdf") + glob("*.PDF")`. pathlib's glob is
    CASE-INSENSITIVE on Windows, so both patterns match the same files and the
    input set is enumerated twice. That is not harmless: `sync_flow` extracts
    after ingesting, so every document was sent to the VLM a second time on
    every sync — double the API cost, on the one platform this is deployed to.
    It reproduces only on Windows, so Linux development hides it.
    """
    import os

    folder = Path(input_folder)
    if not folder.exists():
        return []
    skip = []
    for raw in exclude:
        try:
            skip.append(Path(raw).resolve())
        except OSError:
            continue
    found: list[Path] = []
    for root, dirs, files in os.walk(folder):
        root_path = Path(root)
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and (root_path / d).resolve() not in skip
        )
        found.extend(root_path / f for f in files if (root_path / f).suffix.lower() == ".pdf")
    return sorted(found)


def input_pdfs(cfg: AppConfig) -> list[Path]:
    """`find_input_pdfs` for the configured input folder, minus the other data folders."""
    paths = cfg.paths
    others = [
        getattr(paths, name, None)
        for name in (
            "output_folder",
            "quarantine_folder",
            "stored_documents_folder",
            "combined_folder",
            "archive_folder",
            "log_folder",
        )
    ]
    return find_input_pdfs(paths.input_folder, exclude=[o for o in others if o])


def locate_input_copy(
    cfg: AppConfig, filename: str | None, sha256: str | None = None
) -> Path | None:
    """The input-folder file (top level first, then subfolders) for a document, or None.

    Matches on the file NAME; when `sha256` is given the bytes must hash to it, so
    a same-named unrelated file is never taken for the document.
    """
    if not filename:
        return None
    name = Path(filename).name
    candidates = [Path(cfg.paths.input_folder) / name]
    candidates += [p for p in input_pdfs(cfg) if p.name == name and p not in candidates]
    for cand in candidates:
        try:
            if not cand.is_file():
                continue
            if sha256 is None or hashlib.sha256(cand.read_bytes()).hexdigest() == sha256:
                return cand
        except OSError:
            continue
    return None


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
