"""Quarantine — FR-13.5-13.9 copy+delete keeps files + audit, manual isolated (FR-14.11-14.13)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models import (
    AuditAction,
    AuditLog,
    Document,
    ExtractionStatus,
    LineItem,
    POSet,
    POSetStatus,
)
from app.models.base import Base
from app.services.manual_merge import manual_merge  # noqa: F401  (re-export, FR-14.11)

logger = logging.getLogger(__name__)

# Windows reserved device names. The production host is Win2016, so a PO
# number normalising to one of these would create an unusable folder.
_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_MAX_FOLDER_LEN = 80


def _source_file(doc) -> Path | None:
    """The document's stored PDF, or None when the row cannot name a file.

    `Path("")` is `"."`, which exists and would make `shutil.copy` try to copy
    the working directory. A malformed/blank stored_path is a missing source,
    not the cwd.
    """
    raw = getattr(doc, "stored_path", None)
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_file() else None


def _set_fingerprint(po_set) -> str:
    """A stable, collision-free discriminator for one PO set.

    Derived from the set's document hashes rather than its id: SQLite reuses
    the id of a deleted row, so a new set could otherwise inherit an old set's
    folder and overwrite its QUARANTINE.txt. Re-quarantining the same set gives
    the same fingerprint, so the folder stays stable across runs.
    """
    import hashlib

    hashes = sorted((getattr(d, "sha256_hash", "") or "") for d in (po_set.documents or []))
    joined = "|".join(hashes)
    return hashlib.sha1(joined.encode("utf-8", "replace")).hexdigest()[:8]


def _safe_po_folder(
    po_no: str | None, po_set_id: int | None = None, fingerprint: str | None = None
) -> str:
    """A filesystem-safe folder name for a PO set.

    Keeps the PO number readable: hyphens, underscores and dots survive, which
    matters because this is a folder a person opens and reads. Anything that
    could escape the quarantine root (separators, dots-only names) is stripped,
    and Windows reserved device names are prefixed. In production
    `po_no_normalized` is already alphanumeric, so this is a no-op there and a
    guard for values written directly to the database.

    A blank PO number falls back to UNIDENTIFIED_PO plus a content fingerprint.
    Without one, every unnumbered set would share a folder, pooling unrelated
    PDFs and overwriting each other's QUARANTINE.txt. The id is deliberately
    not used for this: SQLite reuses the id of a deleted row.
    """
    raw = "".join(ch for ch in (po_no or "") if ch.isalnum() or ch in "-_.")
    raw = raw.strip(".-")
    if not raw or set(raw) <= {"."}:
        tag = fingerprint or (f"{po_set_id}" if po_set_id is not None else None)
        return f"UNIDENTIFIED_PO_{tag}"[:_MAX_FOLDER_LEN] if tag else "UNIDENTIFIED_PO"
    if raw.upper() in _RESERVED_NAMES:
        raw = f"_{raw}"
    return raw[:_MAX_FOLDER_LEN]


def _format_qty(value) -> str:
    """Render a scaled-x1000 integer back as a printed quantity."""
    if value is None:
        return "-"
    return f"{int(value) / 1000:g}"


# One sentence per reason code telling the operator what to do next. Unknown
# codes get the generic sentence, so a new reason never produces an empty line.
WHAT_TO_DO: dict[str, str] = {
    "document_extraction_failed": "Open the named file; Re-Extract the set or replace the file.",
    "unclassified_document_in_set": "Reclassify the unclassified document, or remove it.",
    "multiple_po_documents": (
        "Decide which PO document is correct. A PO file cannot be removed from the set; "
        "use Force Merge if both are needed."
    ),
    "po_reference_mismatch": "Check the PO number printed on each document; fix the odd one out.",
    "duplicate_document_number": "Check if the same DN/SI was scanned twice; remove the duplicate.",
    "po_document_has_no_line_items": "Check the PO has every page; add missing ones, re-extract.",
    "non_positive_quantity": "Open the document with the bad quantity; fix or replace it.",
    "unmatched_vendor_line": "Compare DN/SI line numbers with the PO; fix or force-merge.",
    "quantity_mismatch": "Compare PO, DN and SI quantities on the lines listed; fix the document.",
    "vendor_document_has_no_line_items": "Open the DN/SI with no items; replace it.",
    "no_line_item_evidence": "Check the documents are readable and have line items; re-extract.",
    "packet_naming_failed": "Two packets would get the same name; resolve the PO number clash.",
    "packet_source_unreadable": "Restore or re-upload the missing PDF, then retry the merge.",
    "packet_write_failed": "Check the output folder is writable and has free space; retry.",
}
_GENERIC_WHAT_TO_DO = "Review the listed documents, fix the source files and re-run the sync."


def _reason_sentence(reason: str | None) -> str:
    from app.services.reconciliation import REASON_TEXT  # lazy: reconciliation imports us

    code = (reason or "").split(":", 1)[0]
    return REASON_TEXT.get(code) or (reason or "No reason recorded")


def _doc_number(doc) -> str:
    """The document's own number as printed (DN, SI/invoice, or the PO for a PO)."""
    kind = getattr(doc.doc_type, "value", str(doc.doc_type))
    if kind == "DN":
        return doc.dn_no or "-"
    if kind == "SI":
        return doc.si_no or doc.invoice_no or "-"
    return doc.po_no_raw or "-"


def _document_rows(docs, names: dict[int, str]) -> list[str]:
    """Table of the documents: original filename, type, number, PO as printed."""
    header = f"{'File':<42}{'Type':<10}{'Doc number':<22}PO as printed"
    out = [header, "-" * 90]
    for d in docs:
        kind = getattr(d.doc_type, "value", str(d.doc_type))
        out.append(
            f"{(names.get(d.id) or d.original_filename or '?')[:40]:<42}{kind:<10}"
            f"{_doc_number(d)[:20]:<22}{d.po_no_raw or '-'}"
        )
    return out


_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_FILENAME_LEN = 100


def _safe_filename(name: str | None, fallback: str) -> str:
    """The operator's filename made safe to create on Windows, or `fallback`.

    Strips path parts, characters Windows refuses (`<>:"/\\|?*` and control
    characters; `:` would otherwise write an alternate data stream), trailing
    dots/spaces, reserved device stems (CON, NUL, ...), and caps the length so a
    quarantine folder path stays inside MAX_PATH on Server 2016.
    """
    base = re.split(r"[/\\]", name or "")[-1].strip()
    base = _BAD_FILENAME_CHARS.sub("_", base).strip(" .")
    if not base:
        return fallback
    stem, suffix = Path(base).stem, Path(base).suffix
    if stem.upper() in _RESERVED_NAMES:
        stem = f"_{stem}"
    if len(stem) + len(suffix) > _MAX_FILENAME_LEN:
        stem = stem[: max(1, _MAX_FILENAME_LEN - len(suffix))]
    return f"{stem}{suffix}"


def _copy_documents(folder: Path, docs) -> dict[int, str]:
    """Copy each document's stored PDF into `folder` under its ORIGINAL filename.

    A clash (two documents with the same original name, or a name this folder
    uses for its own files) gets `_2`, `_3`. Order is by document id, so
    re-quarantining the same set reproduces the same names. Returns
    {document_id: filename written}.
    """
    reserved = {"quarantine.txt", ".po_set_id"}
    used: set[str] = set()
    names: dict[int, str] = {}
    for doc in sorted(docs, key=lambda d: d.id or 0):
        src = _source_file(doc)
        if src is None:
            continue
        base = _safe_filename(doc.original_filename, src.name)
        stem, suffix = Path(base).stem, Path(base).suffix or src.suffix
        candidate, n = f"{stem}{suffix}", 1
        while candidate.lower() in used or candidate.lower() in reserved:
            n += 1
            candidate = f"{stem}_{n}{suffix}"
        used.add(candidate.lower())
        try:
            shutil.copy(str(src), str(folder / candidate))
        except OSError:
            # A name the file system still refuses must not stop the report: the
            # stored name (a hash) always works.
            logger.warning("Could not copy %s as %s; using its stored name", src, candidate)
            candidate = src.name
            shutil.copy(str(src), str(folder / candidate))
        names[doc.id] = candidate
    return names


def _record_quarantine_audit(
    po_set_id: int | None, cfg, reason, text: str, *, source: str = "system", **extra
) -> None:
    """Persist what the operator is told, so the audit trail survives the folder."""
    eng = get_engine(cfg)
    with Session(eng) as s:
        s.add(
            AuditLog(
                po_set_id=po_set_id,
                action=AuditAction.quarantined,
                detail=json.dumps({"reason": reason, "report": text, **extra}),
                source=source,
            )
        )
        s.commit()


def _write_quarantine_report(
    folder: Path,
    po_no: str | None,
    status: str,
    reason: str | None,
    detail: str | None,
    flags: list[dict] | None,
    po_line_qty: dict | None = None,
    documents: list[str] | None = None,
) -> str:
    """Make the folder self-describing. Returns the text written.

    A quarantined folder is a case a human has to work through. Writing the
    reason and the per-line numbers next to the PDFs means whoever opens the
    folder can see what the engine could not decide, instead of having to
    re-derive it.
    """
    lines: list[str] = [
        "QUARANTINED PO SET",
        "=" * 60,
        f"PO number   : {po_no or '(none)'}",
        f"Status      : {status}",
        f"Reason      : {reason or '(none recorded)'}",
        f"Detected    : {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')} ({_ist_stamp()} IST)",
        "",
        "What happened",
        "-" * 60,
        _reason_sentence(reason),
    ]
    if detail:
        lines.append(f"Detail: {detail}")
    if documents:
        lines += ["", "Documents in this set", "-" * 60, *documents]

    # Every PO line, so the reviewer sees the whole set rather than only the
    # rows that failed. Flags then overlay the vendor-side numbers.
    rows: dict[str, dict] = {
        str(k): {"po": v, "dn": None, "si": None} for k, v in (po_line_qty or {}).items()
    }
    notes: list[str] = []
    for f in flags or []:
        if f.get("type") in ("line_mapping", "line_mapping_rejected"):
            notes.append(str(f.get("message") or ""))
            continue
        key = str(f.get("line_item_no") or "?")
        row = rows.setdefault(key, {"po": None, "dn": None, "si": None})
        pool = (f.get("pool") or "").upper()
        if f.get("type") == "identification":
            row["note"] = f.get("reason") or "unmatched"
            if pool in ("DN", "SI"):
                row[pool.lower()] = f.get("vendor_quantity")
        elif pool in ("DN", "SI"):
            row[pool.lower()] = f.get("vendor_quantity")

    if rows:
        lines += [
            "",
            "Line-item quantities (PO vs sum of vendor lines)",
            "-" * 60,
            f"{'Line':<12}{'PO':>12}{'DN':>12}{'SI':>12}   Note",
        ]
        for key in sorted(rows, key=lambda k: (len(k), k)):
            r = rows[key]
            lines.append(
                f"{key:<12}{_format_qty(r['po']):>12}{_format_qty(r['dn']):>12}"
                f"{_format_qty(r['si']):>12}   {r.get('note', '')}"
            )
        lines += [
            "",
            "These are the exact numbers the engine compared. Reconciliation",
            "requires PO == sum(DN) and PO == sum(SI) for every line.",
        ]
    else:
        lines += ["", "(no line-item numbers were recorded for this set)"]
    if notes:
        lines += ["", "Line numbers", "-" * 60, *notes]

    lines += [
        "",
        "What to do",
        "-" * 60,
        WHAT_TO_DO.get((reason or "").split(":", 1)[0], _GENERIC_WHAT_TO_DO),
        "Force-merge ships the packet as-is and needs a written justification.",
        "",
    ]
    text = "\n".join(lines)
    (folder / "QUARANTINE.txt").write_text(text, encoding="utf-8")
    return text


def _po_line_quantities(po_set) -> dict[str, int]:
    """Every PO line's quantity, keyed by line number."""
    from app.services.matching import normalize_line_no

    out: dict[str, int] = {}
    for doc in po_set.documents or []:
        if (doc.doc_type.value if hasattr(doc.doc_type, "value") else str(doc.doc_type)) != ("PO"):
            continue
        for li in doc.line_items or []:
            key = normalize_line_no(li.line_item_no)
            if key:
                out[key] = out.get(key, 0) + (li.quantity or 0)
    return out


def _quarantine_set_folder(ps, cfg) -> Path:
    """The folder for one set, disambiguated when a PO key is reused.

    A duplicate PO after a merged set is allowed (the merged set is
    historical), so two sets can share a PO number. Keying the folder only by
    the number made the second set overwrite the first set's PDFs and
    QUARANTINE.txt. The marker records the owner; a different owner gets the
    content fingerprint appended. Re-quarantining the same set reuses its
    folder.
    """
    base = Path(cfg.paths.quarantine_folder)
    name = _safe_po_folder(ps.po_no_normalized, ps.id, _set_fingerprint(ps))
    folder = base / name
    marker = folder / ".po_set_id"
    if folder.exists():
        try:
            owner = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
        except OSError:
            owner = None
        if owner != str(ps.id):
            # A third set can share both the PO and the content fingerprint
            # (archive -> re-upload identical bytes -> quarantine). Keep
            # incrementing until the folder is either free or already ours.
            fp = (_set_fingerprint(ps) or str(ps.id))[:8]
            counter = 2
            while True:
                suffix = f"__{fp}" if counter == 2 else f"__{fp}_{counter}"
                candidate = base / f"{name[:70]}{suffix}"
                if not candidate.exists():
                    folder = candidate
                    break
                candidate_marker = candidate / ".po_set_id"
                try:
                    candidate_owner = (
                        candidate_marker.read_text(encoding="utf-8").strip()
                        if candidate_marker.is_file()
                        else None
                    )
                except OSError:
                    candidate_owner = None
                if candidate_owner == str(ps.id):
                    folder = candidate
                    break
                counter += 1
    # The marker belongs to the folder actually chosen, which may be a candidate.
    marker = folder / ".po_set_id"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        marker.write_text(str(ps.id), encoding="utf-8")
    except OSError:
        logger.warning("Could not write quarantine owner marker in %s", folder)
    return folder


def quarantine_copy(
    po_set, cfg, reason: str | None = None, detail: str | None = None, flags: list | None = None
) -> Path:
    """Copy every document tied to po_no into quarantine/<PO_NO>/ (FR-13.3).

    Uses shutil.copy (not move) so stored_path and quarantine copies both remain.
    Writes a QUARANTINE.txt summary so the folder explains itself.
    Returns quarantine folder Path.
    Accepts POSet object or po_set_id int.
    """
    if isinstance(po_set, int):
        eng = get_engine(cfg)
        Base.metadata.create_all(eng)
        with Session(eng) as s:
            ps = s.get(POSet, po_set)
            if ps is None:
                raise ValueError(f"POSet {po_set} not found")
            s.refresh(ps, attribute_names=["documents"])
            # copy within session context while documents are loaded
            folder = _quarantine_set_folder(ps, cfg)
            docs = list(ps.documents or [])
            names = _copy_documents(folder, docs)
            doc_rows = _document_rows(docs, names)
            set_id = ps.id
            status = ps.status.value if hasattr(ps.status, "value") else str(ps.status)
            po_no = ps.po_no_normalized
            po_line_qty = _po_line_quantities(ps)
    else:
        folder = _quarantine_set_folder(po_set, cfg)
        docs = list(po_set.documents or [])
        names = _copy_documents(folder, docs)
        doc_rows = _document_rows(docs, names)
        set_id = po_set.id
        status = po_set.status.value if hasattr(po_set.status, "value") else str(po_set.status)
        po_no = po_set.po_no_normalized
        po_line_qty = _po_line_quantities(po_set)

    text = _write_quarantine_report(
        folder, po_no, status, reason, detail, flags, po_line_qty, doc_rows
    )
    _record_quarantine_audit(set_id, cfg, reason, text, folder=str(folder))
    return folder


def quarantine_document(doc_id: int, cfg, reason: str, *, source: str = "system") -> Path:
    """Quarantine a single document that could not be processed.

    A document whose extraction permanently failed is broken, and nothing about
    it can be confirmed, so it is taken out of the running rather than left
    where the next sync will pick it up again. `ingest_file` copies into
    `stored/` and leaves the original in the input folder, and input is only
    cleared when a PO Set merges — so without this, a dead PDF is re-hashed,
    de-duplicated to the same failed row and re-counted as an error on every
    single run, forever.

    The stored copy is kept (never moved) so the document row stays valid and
    an operator can still attach and retry it. Only the input-folder copy is
    removed, which is what stops the loop.

    Returns the quarantine folder for the document.
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            raise ValueError(f"Document {doc_id} not found")
        stored = Path(doc.stored_path)
        name = stored.name
        original = doc.original_filename
        doc_id_out = doc.id
        attempts = doc.extraction_attempt_count or 0
        sha = doc.sha256_hash
        reject_reason = doc.ingest_reject_reason
        doc_rows = _document_rows([doc], {})
        s.expunge(doc)

    folder = Path(cfg.paths.quarantine_folder) / "_documents" / name.replace(".pdf", "")
    folder.mkdir(parents=True, exist_ok=True)
    # The copy keeps the operator's own filename (the folder is keyed by hash).
    out_name = _safe_filename(original, name)
    if out_name.lower() in ("quarantine.txt", ".po_set_id"):
        out_name = f"document_{out_name}"
    from app.services.ingestion import locate_input_copy

    input_copy = locate_input_copy(cfg, original, sha)
    try:
        if stored.exists():
            shutil.copy(str(stored), str(folder / out_name))
    except OSError:
        logger.warning("Could not copy stored file %s", stored, exc_info=True)
    # Remove the input copy here, not only in the sync caller: the report below
    # tells the operator it was removed, and `extraction._handle_combined`
    # calls this directly. Match on content, so a same-named unrelated file is
    # never swept away. The input copy is never removed unless a copy of it now
    # exists in the quarantine folder (the stored file may be missing).
    if input_copy is not None and input_copy.is_file():
        try:
            import hashlib

            data = input_copy.read_bytes()
            if hashlib.sha256(data).hexdigest() == sha:
                if not (folder / out_name).is_file():
                    (folder / out_name).write_bytes(data)
                if (folder / out_name).is_file():
                    input_copy.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove input copy %s", input_copy, exc_info=True)
    # A renamed byte-identical copy would be re-hashed and re-counted as an error on every
    # sync, forever. Once a quarantine copy exists, remove every input file with the same
    # content (the quarantine copy is the durable one).
    if (folder / out_name).is_file():
        import hashlib

        from app.services.ingestion import input_pdfs

        for other in input_pdfs(cfg):
            try:
                if other.stat().st_size == (folder / out_name).stat().st_size and (
                    hashlib.sha256(other.read_bytes()).hexdigest() == sha
                ):
                    other.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove duplicate input copy %s", other, exc_info=True)
    # The text must match the case: a rejected-on-arrival file was never read,
    # a combined file WAS read but holds several documents, and only a real
    # extraction failure has an attempt count against the configured cap.
    from app.services.extraction import _attempt_cap

    attempts_line: list[str] = []
    if reject_reason:
        what_happened = (
            f"This file was rejected on arrival ({reject_reason}) and was never read.",
            "It has been removed from the input folder so it is not retried nightly.",
        )
    elif reason.startswith(("combined_document", "child_recombined", "split_failed")):
        what_happened = (
            "This file was read, but it holds several documents in one PDF and was not split,",
            "so none of its content was used. A person must separate it into single documents.",
            "It has been removed from the input folder so it is not retried nightly.",
        )
    else:
        attempts_line = [
            f"Attempts    : {attempts}/{_attempt_cap(cfg)} "
            "(cap reached, no further automatic attempts)"
        ]
        what_happened = (
            "This file could not be read, so nothing about it could be verified.",
            "It has been removed from the input folder so it is not retried nightly.",
        )
    if reason.startswith(SET_ASIDE_REASON_PREFIX):
        attempts_line = []
        what_happened = (
            "An operator moved this file from the holding page.",
            "It is no longer in the input folder, so the nightly scan will not read it.",
        )
        what_to_do = (
            "Fix or split the file, then put it back in the input folder.",
            "It is read again on the next scan.",
        )
    elif reason.startswith(("combined_document", "child_recombined", "split_failed")):
        what_to_do = (
            "Split this file into single documents (one PO, one DN or one SI each),",
            "then put the parts in the input folder. They are read on the next scan.",
        )
    else:
        what_to_do = (
            "Enter its PO number on the Unclassified page to attach it to a PO Set,",
            "then press Re-extract on that set. Or replace the file with a readable copy.",
        )
    text = "\n".join(
        [
            "QUARANTINED DOCUMENT",
            "=" * 60,
            f"File        : {original}",
            f"Document id : {doc_id_out}",
            *attempts_line,
            f"Reason      : {reason}",
            f"Detected    : {_utc_and_ist()}",
            "",
            "What happened",
            "-" * 60,
            *what_happened,
            "",
            "Documents in this set",
            "-" * 60,
            *doc_rows,
            "",
            "What to do",
            "-" * 60,
            *what_to_do,
            "",
        ]
    )
    (folder / "QUARANTINE.txt").write_text(text, encoding="utf-8")
    _record_quarantine_audit(
        None, cfg, reason, text, source=source, document_id=doc_id_out, folder=str(folder)
    )
    return folder


#: Reason written when an operator moves a document from the holding page.
SET_ASIDE_REASON_PREFIX = "operator_set_aside"
SET_ASIDE_REASON = f"{SET_ASIDE_REASON_PREFIX}: moved from the holding page"


class SetAsideRefused(Exception):
    """An operator asked to move a document that has to stay where it is."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _remove_input_duplicates(cfg, sha: str) -> None:
    """Remove every input-folder file whose content matches `sha`.

    The quarantine copy is the durable one, so an identical input copy is removed.
    """
    from app.services.ingestion import input_pdfs

    for other in input_pdfs(cfg):
        try:
            if hashlib.sha256(other.read_bytes()).hexdigest() == sha:
                other.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove duplicate input copy %s", other, exc_info=True)


def set_aside_document(doc_id: int, cfg) -> Path | None:
    """Move an unattached document off the holding list into the quarantine folder.

    The whole move runs under the sync lock, so a sync cannot attach or read the
    document in the middle of it. Returns the quarantine folder, or None when the
    document was already moved.
    """
    from app.services import sync_lock

    lock = sync_lock.acquire_sync_lock()
    if lock is None:
        raise SetAsideRefused(
            409,
            "Moves and removals are paused while a sync reads documents. "
            "They resume when the sync finishes; a large batch can take an hour or more.",
        )
    try:
        return _set_aside_locked(doc_id, cfg)
    finally:
        sync_lock.release_sync_lock(lock)


def _set_aside_locked(doc_id: int, cfg) -> Path | None:
    """The set-aside steps. The caller holds the sync lock.

    The stored copy and the quarantine copy are kept. If the document already has
    a quarantine copy (a rejected file, or an earlier quarantine), its original
    reason is kept and no second copy is made.
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            raise SetAsideRefused(404, f"Document {doc_id} does not exist.")
        if doc.archived_at is not None:
            return None
        if doc.po_set_id is not None:
            raise SetAsideRefused(
                409, "This document is attached to a PO Set and cannot be moved from here."
            )
        if doc.extraction_status == ExtractionStatus.processing:
            raise SetAsideRefused(
                409, "This document is being read right now; try again when it finishes."
            )
        stem = Path(doc.stored_path).name.replace(".pdf", "")
        folder = Path(cfg.paths.quarantine_folder) / "_documents" / stem
        sha = doc.sha256_hash
        rejected = doc.ingest_reject_reason
        stored = Path(doc.stored_path)
        stored_exists = stored.is_file()
        in_quarantine_root = stored.resolve().is_relative_to(
            Path(cfg.paths.quarantine_folder).resolve()
        )

    report = folder / "QUARANTINE.txt"
    has_copy = report.is_file() or (rejected is not None and in_quarantine_root)
    if has_copy:
        # Never remove an input copy unless a quarantine PDF really exists (review L3).
        pdf_present = (folder.is_dir() and any(folder.glob("*.pdf"))) or (
            rejected is not None and in_quarantine_root and stored_exists
        )
        if not pdf_present:
            if not stored_exists:
                raise SetAsideRefused(
                    409,
                    "The quarantine copy and the stored file are both missing; nothing was moved.",
                )
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy(str(stored), str(folder / stored.name))
        text = (
            report.read_text(encoding="utf-8")
            if report.is_file()
            else f"Rejected on arrival: {rejected}"
        )
        _remove_input_duplicates(cfg, sha)
        _record_quarantine_audit(
            None,
            cfg,
            SET_ASIDE_REASON,
            text,
            source="operator",
            document_id=doc_id,
            folder=str(folder),
            set_aside=True,
        )
    else:
        folder = quarantine_document(doc_id, cfg, SET_ASIDE_REASON, source="operator")

    with Session(eng) as s:
        result = s.execute(
            update(Document)
            .where(
                Document.id == doc_id,
                Document.archived_at.is_(None),
                Document.po_set_id.is_(None),
                Document.extraction_status != ExtractionStatus.processing,
            )
            .values(archived_at=datetime.now(UTC))
        )
        s.commit()
        if (getattr(result, "rowcount", 0) or 0) == 0:
            raise SetAsideRefused(
                409, "The document changed while it was being moved. Reload the page and try again."
            )
    return folder


MIN_JUSTIFICATION_CHARS = 20


def validate_justification(text: str | None) -> str | None:
    """Normalize an optional operator justification (v20.5 §audit).

    None/absent stays None so existing callers and the UI keep working. When
    supplied it must be a real sentence-ish note, not a one-word checkbox.
    """
    if text is None:
        return None
    cleaned = str(text).strip()
    if not cleaned:
        return None
    if len(cleaned) < MIN_JUSTIFICATION_CHARS:
        raise ValueError(
            f"Justification must be at least {MIN_JUSTIFICATION_CHARS} characters "
            f"(got {len(cleaned)})"
        )
    return cleaned


def _json_default(value):
    """Serialize DB values without losing the archive's audit detail."""
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def _write_json(path: Path, payload) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )


def _archive_file_name(doc: Document) -> str:
    """A collision-free archive filename.

    Two documents can legitimately share an original filename (batch exports
    reuse names), so the archive uses the immutable document id and a hash
    prefix rather than trusting the vendor's name.
    """
    source_name = Path(doc.stored_path).name if doc.stored_path else "missing.pdf"
    prefix = f"{doc.id:06d}_{(doc.sha256_hash or '')[:12]}"
    return f"{prefix}_{source_name}"


def _copy_doc_to_archive(
    doc: Document,
    files_dir: Path,
    file_rows: list[dict],
    missing_files: list[dict],
) -> None:
    """Copy one document's stored PDF and append its manifest row."""
    source_path = _source_file(doc)
    row = {
        "document_id": doc.id,
        "sha256_hash": doc.sha256_hash,
        "original_filename": doc.original_filename,
        "stored_path": doc.stored_path,
        "archived_name": None,
    }
    if source_path is None:
        missing_files.append({"document_id": doc.id, "stored_path": doc.stored_path})
        file_rows.append(row)
        return
    dest = files_dir / _archive_file_name(doc)
    shutil.copy2(source_path, dest)
    row["archived_name"] = dest.name
    file_rows.append(row)


def _find_existing_archive(archive_root: Path, po_set_id: int, po_no: str | None) -> Path | None:
    """A complete archive left by a previous attempt whose DB commit failed.

    Reusing it makes a retry idempotent instead of writing a second copy of
    every PDF. Only folders with both manifests AND a matching manifest count;
    a half-written staging folder or another database's folder with the same
    numeric id is ignored.
    """
    if not archive_root.exists():
        return None
    for candidate in sorted(archive_root.iterdir(), reverse=True):
        if not candidate.is_dir() or not candidate.name.endswith(f"_{po_set_id}"):
            continue
        if not ((candidate / "ARCHIVE.txt").is_file() and (candidate / "manifest.json").is_file()):
            continue
        try:
            manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("po_set_id") != po_set_id:
            continue
        if manifest.get("po_no_normalized") != (po_no or ""):
            continue
        # A manifest is not proof that its files survived. A partially deleted
        # export must not be adopted as the archive.
        if any(
            row.get("archived_name") and not (candidate / "files" / row["archived_name"]).is_file()
            for row in manifest.get("files", [])
        ):
            continue
        return candidate
    return None


def _write_archive_manifest(
    folder: Path,
    ps: POSet,
    docs: list[Document],
    line_items: list[LineItem],
    audits: list[AuditLog],
    *,
    source: str,
    note: str | None,
    archived_at: datetime,
    archive_path: Path,
    file_rows: list[dict],
    missing_files: list[dict],
) -> None:
    """Write the self-describing export next to the copied PDFs."""
    po_no = ps.po_no_normalized
    status = ps.status.value if hasattr(ps.status, "value") else str(ps.status)
    reason = ps.reconcile_reason or note or "operator archive"

    _write_json(
        folder / "po_set.json",
        {
            "id": ps.id,
            "po_no_normalized": po_no,
            "status": status,
            "has_customs_toggle": ps.has_customs_toggle,
            "customs_doc_count": ps.customs_doc_count,
            "merged_output_path": ps.merged_output_path,
            "reconcile_reason": ps.reconcile_reason,
            "description_match_count": ps.description_match_count,
            "line_mapping_rule": ps.line_mapping_rule,
            "merged_at": ps.merged_at,
            "locked_by_action": ps.locked_by_action,
            "locked_at": ps.locked_at,
            "created_at": ps.created_at,
            "updated_at": ps.updated_at,
            "archived_at": archived_at,
            "archived_by": source,
            "archive_reason": reason,
            "archive_path": str(archive_path),
        },
    )
    _write_json(
        folder / "documents.json",
        [
            {
                "id": d.id,
                "sha256_hash": d.sha256_hash,
                "original_filename": d.original_filename,
                "stored_path": d.stored_path,
                "doc_type": d.doc_type,
                "raw_extraction_json": d.raw_extraction_json,
                "po_no_raw": d.po_no_raw,
                "po_no_normalized": d.po_no_normalized,
                "po_no_source": d.po_no_source,
                "po_reference_ambiguous": d.po_reference_ambiguous,
                "dn_no": d.dn_no,
                "si_no": d.si_no,
                "invoice_no": d.invoice_no,
                "extraction_status": d.extraction_status,
                "extraction_attempt_count": d.extraction_attempt_count,
                "po_set_id": d.po_set_id,
                "parent_document_id": d.parent_document_id,
                "is_split_parent": d.is_split_parent,
                "split_completed_at": d.split_completed_at,
                "archived_at": archived_at,
                "created_at": d.created_at,
                "updated_at": d.updated_at,
            }
            for d in docs
        ],
    )
    _write_json(
        folder / "line_items.json",
        [
            {
                "id": li.id,
                "document_id": li.document_id,
                "line_item_no": li.line_item_no,
                "description": li.description,
                "quantity": li.quantity,
                "dn_no": li.dn_no,
            }
            for li in line_items
        ],
    )
    _write_json(
        folder / "audit_log.json",
        [
            {
                "id": a.id,
                "po_set_id": a.po_set_id,
                "action": a.action,
                "detail": a.detail,
                "justification": a.justification,
                "timestamp": a.timestamp,
                "source": a.source,
            }
            for a in audits
        ],
    )
    _write_json(
        folder / "manifest.json",
        {
            "po_no_normalized": po_no,
            "po_set_id": ps.id,
            "archived_at": archived_at,
            "archived_by": source,
            "archive_reason": reason,
            "document_count": len(docs),
            "line_item_count": len(line_items),
            "audit_count": len(audits),
            "files": file_rows,
            "missing_files": missing_files,
        },
    )
    lines = [
        "ARCHIVED PO SET",
        "=" * 60,
        f"PO number      : {po_no or '(none)'}",
        f"PO Set id      : {ps.id}",
        f"Status         : {status}",
        f"Reason         : {reason}",
        f"Archived at    : {_utc_and_ist(archived_at)}",
        f"Archived by    : {source}",
        f"Justification  : {note or '(none)'}",
        f"Documents      : {len(docs)}",
        f"Line items     : {len(line_items)}",
        f"Audit rows     : {len(audits)}",
        f"Files copied   : {len(file_rows)}",
        f"Files missing  : {len(missing_files)}",
        "",
        "This set is NOT deleted. The database rows remain and are hidden from",
        "the active worklists. Re-ingesting these exact bytes creates a new",
        "active document; the archived row no longer reserves the hash.",
    ]
    if missing_files:
        lines += ["", "Missing stored files (recorded, not silently ignored):"]
        for item in missing_files:
            lines.append(f"  - document {item['document_id']}: {item['stored_path']}")
    (folder / "ARCHIVE.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def archive_quarantined(
    po_set_id: int, cfg, justification: str | None = None, source: str = "system"
) -> AuditLog:
    """Archive a quarantined PO Set without deleting a single row.

    The exported folder contains every stored PDF (copied, never moved), the
    full PO Set/document/line-item/audit rows as JSON, a hash manifest and a
    human-readable ARCHIVE.txt. The set's documents and the set itself are
    marked `archived_at`; worklists and grouping ignore them.

    Input-folder copies whose CONTENT matches one of the set's documents are
    moved into the export's ``input/`` folder so the next sync cannot
    resurrect the set. A newer active document with the same bytes is left
    alone: re-uploading after an archive is a supported workflow.
    """
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.get(POSet, po_set_id)
        if ps is None:
            raise ValueError(f"POSet {po_set_id} not found")

        # Idempotent: a second Delete click must not write a second export.
        if ps.archived_at is not None:
            existing = (
                s.query(AuditLog)
                .filter(
                    AuditLog.po_set_id == po_set_id,
                    AuditLog.action == AuditAction.quarantine_delete,
                )
                .order_by(AuditLog.id.desc())
                .first()
            )
            if existing is not None:
                return existing
            audit = AuditLog(
                po_set_id=po_set_id,
                action=AuditAction.quarantine_delete,
                detail=json.dumps(
                    {
                        "archived": True,
                        "already_archived": True,
                        "archive_path": ps.archive_path,
                    }
                ),
                source=source,
                justification=validate_justification(justification),
            )
            s.add(audit)
            s.commit()
            s.refresh(audit)
            return audit

        note = validate_justification(justification)
        status_val = ps.status.value if hasattr(ps.status, "value") else str(ps.status)
        if status_val != POSetStatus.quarantined.value:
            raise ValueError(f"POSet {po_set_id} is not quarantined (status={status_val})")

        docs = s.query(Document).filter_by(po_set_id=po_set_id).order_by(Document.id).all()
        doc_ids = [d.id for d in docs]
        line_items = (
            s.query(LineItem).filter(LineItem.document_id.in_(doc_ids)).order_by(LineItem.id).all()
            if doc_ids
            else []
        )
        audits = (
            s.query(AuditLog).filter(AuditLog.po_set_id == po_set_id).order_by(AuditLog.id).all()
        )

        archive_root = Path(cfg.paths.archive_folder)
        archive_root.mkdir(parents=True, exist_ok=True)
        archived_at = datetime.now(UTC)

        file_rows: list[dict] = []
        missing_files: list[dict] = []

        final_folder = _find_existing_archive(archive_root, po_set_id, ps.po_no_normalized)
        if final_folder is None:
            import uuid

            staging = archive_root / f".tmp-{uuid.uuid4().hex}"
            files_dir = staging / "files"
            files_dir.mkdir(parents=True, exist_ok=True)

            try:
                for doc in docs:
                    _copy_doc_to_archive(doc, files_dir, file_rows, missing_files)

                # A concurrent sync/reclassify can attach a document while the
                # copy runs. Re-read and include late arrivals so the manifest
                # does not describe a different set than the DB commit marks.
                for _ in range(2):
                    current_docs = (
                        s.query(Document).filter_by(po_set_id=po_set_id).order_by(Document.id).all()
                    )
                    seen = {row["document_id"] for row in file_rows}
                    late = [d for d in current_docs if d.id not in seen]
                    if not late:
                        docs = current_docs
                        break
                    for doc in late:
                        _copy_doc_to_archive(doc, files_dir, file_rows, missing_files)
                    docs = current_docs

                doc_ids = [d.id for d in docs]
                line_items = (
                    s.query(LineItem)
                    .filter(LineItem.document_id.in_(doc_ids))
                    .order_by(LineItem.id)
                    .all()
                    if doc_ids
                    else []
                )
                audits = (
                    s.query(AuditLog)
                    .filter(AuditLog.po_set_id == po_set_id)
                    .order_by(AuditLog.id)
                    .all()
                )

                safe = _safe_po_folder(ps.po_no_normalized, ps.id)
                stamp = archived_at.strftime("%Y%m%dT%H%M%SZ")
                final_folder = archive_root / f"{safe}_{stamp}_{ps.id}"
                if final_folder.exists():
                    # Keep the id LAST: _find_existing_archive matches the
                    # suffix, and a post-commit retry must find this folder.
                    final_folder = archive_root / f"{safe}_{stamp}_{uuid.uuid4().hex[:6]}_{ps.id}"
                _write_archive_manifest(
                    staging,
                    ps,
                    docs,
                    line_items,
                    audits,
                    source=source,
                    note=note,
                    archived_at=archived_at,
                    archive_path=final_folder,
                    file_rows=file_rows,
                    missing_files=missing_files,
                )
                staging.rename(final_folder)
            except Exception:
                # No DB row has changed yet; remove only the half-written
                # staging folder. A complete folder is left for a safe retry.
                shutil.rmtree(staging, ignore_errors=True)
                raise
        else:
            # Retry after a commit failure: reuse the completed export and
            # recover its file/missing counts for the audit row.
            try:
                manifest = json.loads((final_folder / "manifest.json").read_text(encoding="utf-8"))
                file_rows = list(manifest.get("files", []))
                missing_files = list(manifest.get("missing_files", []))
            except (OSError, ValueError):
                file_rows = []
                missing_files = []

        # Recompute against the rows that exist now, not the snapshot taken
        # before the copy. The bulk mark below is the authority for the DB.
        doc_ids = [d.id for d in docs]
        valid_hashes = {d.sha256_hash for d in docs if d.sha256_hash}
        # A newer active document with the same bytes is NOT part of this
        # archive's input sweep. Re-uploading identical content after archiving
        # is intentional; sweeping it away would ingest the new file and then
        # immediately remove it, looking like data loss.
        active_hashes = {
            h
            for (h,) in s.query(Document.sha256_hash)
            .filter(
                Document.sha256_hash.in_(valid_hashes),
                Document.archived_at.is_(None),
                ~Document.id.in_(doc_ids),
            )
            .all()
            if h
        }

        # Move content-matched input copies into the archive. This runs for a
        # fresh export AND for a retry that reuses a complete folder, so a
        # commit failure cannot leave the input file able to re-create the set.
        moved: list[tuple[Path, Path]] = []
        input_dir = final_folder / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        input_root = Path(cfg.paths.input_folder)
        if input_root.exists():
            import hashlib

            from app.services.ingestion import input_pdfs

            for source_file in input_pdfs(cfg):
                try:
                    digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
                except Exception:
                    continue
                # Never sweep a file that a newer ACTIVE document still
                # claims: re-uploading identical bytes after archive is
                # supported, and moving its input copy would look like loss.
                if digest not in valid_hashes or digest in active_hashes:
                    continue
                dest = input_dir / source_file.name
                counter = len(moved) + 1
                while dest.exists():
                    dest = input_dir / (f"{source_file.stem}_{counter}{source_file.suffix}")
                    counter += 1
                try:
                    shutil.move(str(source_file), str(dest))
                except Exception:
                    for original, moved_to in reversed(moved):
                        try:
                            if moved_to.exists() and not original.exists():
                                shutil.move(str(moved_to), str(original))
                        except Exception:
                            logger.warning(
                                "Could not restore input file %s", original, exc_info=True
                            )
                    raise
                moved.append((source_file, dest))

        # Re-read through the same session: the folder is complete now.
        ps.archived_at = archived_at
        ps.archived_by = source
        ps.archive_reason = ps.reconcile_reason or note or "operator archive"
        ps.archive_path = str(final_folder)
        # One UPDATE, not a loop over the stale snapshot: a document attached
        # during the copy above must not stay ACTIVE inside an archived set
        # (it would reserve its hash and be invisible in every worklist).
        s.query(Document).filter(
            Document.po_set_id == po_set_id,
            Document.archived_at.is_(None),
        ).update({Document.archived_at: archived_at}, synchronize_session=False)

        document_count = s.query(Document).filter_by(po_set_id=po_set_id).count()
        line_item_count = (
            s.query(LineItem).filter(LineItem.document_id.in_(doc_ids)).count() if doc_ids else 0
        )
        detail = json.dumps(
            {
                "archived": True,
                "po_no_normalized": ps.po_no_normalized,
                "document_count": document_count,
                "line_item_count": line_item_count,
                "archive_path": str(final_folder),
                "missing_files": len(missing_files),
                "moved_input_count": len(moved),
            }
        )
        audit = AuditLog(
            po_set_id=po_set_id,
            action=AuditAction.quarantine_delete,
            detail=detail,
            source=source,
            justification=note,
        )
        s.add(audit)
        s.commit()
        s.refresh(audit)
        return audit


def delete_quarantined(
    po_set_id: int, cfg, justification: str | None = None, source: str = "system"
) -> AuditLog:
    """Backward-compatible name for the Delete route: archive, never delete."""
    return archive_quarantined(po_set_id, cfg, justification=justification, source=source)


class SetRemovalRefused(Exception):
    """An operator asked to remove a PO Set that must stay as it is."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


#: The folder that keeps every removed set: copies, a JSON export and REMOVED.txt.
REMOVED_SETS_FOLDER = "_removed_sets"


def _safe_po_name(po_no: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", po_no or "NO_PO")[:60] or "NO_PO"


def _unique_name(folder: Path, name: str) -> str:
    """A file name not yet used in `folder`: `a.pdf`, then `a (2).pdf`, and so on."""
    base_name = Path(name).name
    if not base_name or base_name in (".", ".."):
        base_name = "document.pdf"
    candidate, stem, suffix, n = base_name, Path(base_name).stem, Path(base_name).suffix, 2
    while (folder / candidate).exists():
        candidate = f"{stem} ({n}){suffix}"
        n += 1
    return candidate


def _same_bytes(path: Path, data: bytes) -> bool:
    return hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(data).digest()


def _restore_input_copies(moved: list[tuple[Path, Path]]) -> list[str]:
    """Put back input-folder files that were moved out by a removal that did not finish.

    Returns the names that could not be put back. The caller must keep the copies on
    disk when any name is returned (review M2).
    """
    failed: list[str] = []
    for copy, original in moved:
        partial = original.with_name(original.name + ".restoring")
        try:
            # Never overwrite a file the operator put there meanwhile: restore under a
            # free name instead. Write beside the target and rename, so a crash cannot
            # leave a truncated PDF (review L3, review 7).
            target = original
            if target.exists():
                target = original.with_name(_unique_name(original.parent, original.name))
            partial = target.with_name(target.name + ".restoring")
            partial.write_bytes(copy.read_bytes())
            partial.replace(target)
            copy.unlink(missing_ok=True)
        except OSError:
            partial.unlink(missing_ok=True)
            failed.append(original.name)
            logger.warning("Could not restore input copy %s", original, exc_info=True)
    return failed


def _utc_and_ist(moment: datetime | None = None) -> str:
    """A time shown as UTC and as IST, for the operator-facing text reports (decision 12)."""
    moment = moment or datetime.now(UTC)
    aware = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
    ist = aware.astimezone(timezone(timedelta(hours=5, minutes=30)))
    return f"{aware.strftime('%Y-%m-%d %H:%M:%S')} UTC ({ist.strftime('%Y-%m-%d %H:%M:%S')} IST)"


def _ist_stamp() -> str:
    """The current time in IST, for operator-facing text (decision 12)."""
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S")


def _mark_removal_failed(folder: Path, reason: str) -> None:
    """A removal that did not finish keeps its folder, and the folder says so (review L1).

    The report that says the set was removed is replaced, because the set still exists.
    """
    lines = [
        "THE REMOVAL DID NOT FINISH",
        "=" * 60,
        reason,
        "",
        "The PO Set is still in the database and nothing was deleted.",
        "Check the files in this folder before trying again.",
        "",
    ]
    try:
        (folder / "REMOVED.txt").unlink(missing_ok=True)
        (folder / "REMOVAL_IN_PROGRESS.txt").unlink(missing_ok=True)
        (folder / "REMOVAL_FAILED.txt").write_text(chr(10).join(lines), encoding="utf-8")
    except OSError:
        logger.warning("Could not write the removal failure report in %s", folder, exc_info=True)


def _set_row_export(ps: POSet) -> dict:
    out: dict = {}
    for column in POSet.__table__.columns:
        value = getattr(ps, column.name)
        out[column.name] = value.isoformat() if hasattr(value, "isoformat") else value
    return out


def remove_set_to_quarantine(
    po_set_id: int, cfg, *, source: str = "operator", note: str = ""
) -> dict:
    """Copy a PO Set's files to the quarantine folder, then delete its rows (step 13).

    Order: copy and verify every stored PDF, and write a JSON export and REMOVED.txt,
    into a staging folder; rename it into place; move the input-folder copies in;
    only then, in one transaction, write the audit row and delete the set's rows.
    Stored copies in `data/stored` are never deleted. A missing stored PDF does not
    stop the removal: it is listed in REMOVED.txt, the audit row and the result.
    """
    from app.services import sync_lock
    from app.services.ingestion import input_pdfs

    lock = sync_lock.acquire_sync_lock()
    if lock is None:
        raise SetRemovalRefused(
            409,
            "Moves and removals are paused while a sync reads documents. "
            "They resume when the sync finishes; a large batch can take an hour or more.",
        )
    try:
        eng = get_engine(cfg)
        Base.metadata.create_all(eng)
        with Session(eng) as s:
            ps = s.get(POSet, po_set_id)
            if ps is None:
                raise SetRemovalRefused(404, f"PO Set {po_set_id} does not exist.")
            if ps.archived_at is not None:
                raise SetRemovalRefused(409, "This PO Set is archived and read-only.")
            po_no = ps.po_no_normalized
            status_before = ps.status.value
            merged_output_file = ps.merged_output_path
            set_row = _set_row_export(ps)
            docs = s.query(Document).filter(Document.po_set_id == po_set_id).all()
            doc_ids = sorted(d.id for d in docs)
            doc_rows = [
                {
                    "id": d.id,
                    "filename": d.original_filename,
                    "doc_type": d.doc_type.value
                    if hasattr(d.doc_type, "value")
                    else str(d.doc_type),
                    "sha256": d.sha256_hash,
                    "stored_path": d.stored_path,
                    "archived_at": d.archived_at.isoformat() if d.archived_at else None,
                }
                for d in docs
            ]
            item_rows = (
                [
                    {
                        "document_id": li.document_id,
                        "line_item_no": li.line_item_no,
                        "description": li.description,
                        "quantity": li.quantity,
                    }
                    for li in s.query(LineItem).filter(LineItem.document_id.in_(doc_ids)).all()
                ]
                if doc_ids
                else []
            )
            audit_rows = [
                {
                    "id": a.id,
                    "action": a.action.value if hasattr(a.action, "value") else str(a.action),
                    "detail": a.detail,
                    "timestamp": a.timestamp.isoformat() if a.timestamp else None,
                    "source": a.source,
                }
                for a in s.query(AuditLog).filter(AuditLog.po_set_id == po_set_id).all()
            ]
            shas = {d["sha256"] for d in doc_rows}
            # Plan rule: an input copy is moved only when no other ACTIVE document holds its bytes.
            other_active = {
                row[0]
                for row in s.query(Document.sha256_hash)
                .filter(Document.archived_at.is_(None), ~Document.id.in_(doc_ids or [-1]))
                .all()
            }
            shas -= other_active

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        base = Path(cfg.paths.quarantine_folder) / REMOVED_SETS_FOLDER
        try:
            base.mkdir(parents=True, exist_ok=True)
            stage = base / f".tmp-{uuid.uuid4().hex}"
            final = base / f"{_safe_po_name(po_no)}_{stamp}_{po_set_id}"
            files_dir = stage / "files"
            files_dir.mkdir(parents=True)
        except OSError as exc:
            raise SetRemovalRefused(500, f"Could not create removal staging directory: {exc}") from exc
        moved: list[tuple[Path, Path]] = []
        missing: list[str] = []
        try:
            for d in doc_rows:
                src = Path(str(d["stored_path"]))
                if not src.is_file():
                    missing.append(str(d["filename"]))
                    continue
                data = src.read_bytes()
                name = _unique_name(files_dir, str(d["filename"] or src.name))
                (files_dir / name).write_bytes(data)
                if not _same_bytes(files_dir / name, data):
                    raise OSError(f"copy verification failed for {d['filename']}")
                d["quarantine_name"] = name

            if merged_output_file:
                merged_src = Path(str(merged_output_file))
                if merged_src.is_file():
                    out_dir = stage / "output"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_name = _unique_name(out_dir, merged_src.name)
                    (out_dir / out_name).write_bytes(merged_src.read_bytes())

            exports = {
                "po_set.json": set_row,
                "documents.json": doc_rows,
                "line_items.json": item_rows,
                "audit_log.json": audit_rows,
            }
            for file_name, payload in exports.items():
                (stage / file_name).write_text(
                    json.dumps(payload, indent=2, default=str), encoding="utf-8"
                )
            manifest = {
                "po_set_id": po_set_id,
                "po_no_normalized": po_no,
                "status_before": status_before,
                "removed_at_utc": stamp,
                "document_count": len(doc_rows),
                "line_item_count": len(item_rows),
                "missing_files": missing,
                "note": note,
                "folder": f"{REMOVED_SETS_FOLDER}/{final.name}",
            }
            (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            file_lines = [
                f"  {d.get('quarantine_name', '(missing)')}  <-  {d['filename']}" for d in doc_rows
            ]
            text = "\n".join(
                [
                    "PO SET REMOVED FROM THE PIPELINE",
                    "=" * 60,
                    f"PO number   : {po_no}",
                    f"PO Set id   : {po_set_id}",
                    f"Status was  : {status_before}",
                    f"Removed at  : {stamp} (UTC) / {_ist_stamp()} (IST)",
                    f"Note        : {note or '-'}",
                    "",
                    "Files (copied from data/stored)",
                    "-" * 60,
                    *(file_lines or ["  (none)"]),
                    "",
                    "Missing stored files" if missing else "Missing stored files: none",
                    *(f"  {name}" for name in missing),
                    "",
                    "How to restore",
                    "-" * 60,
                    "Put the files from files/ and input/ back in the input folder, then scan.",
                    "The documents are read again. CUSTOMS and SHIPPING files are re-uploaded",
                    "on the new set's page, not put in the input folder.",
                    "",
                ]
            )
            # A crash before the commit leaves this note, not a false "removed" report (review 5).
            (stage / "REMOVAL_IN_PROGRESS.txt").write_text(
                "THE REMOVAL IS IN PROGRESS OR DID NOT FINISH"
                + chr(10)
                + f"Started {stamp} (UTC). The set is removed only when REMOVED.txt appears."
                + chr(10),
                encoding="utf-8",
            )
            report_text = text
            stage.rename(final)

            input_dir = final / "input"
            unreadable: list[str] = []
            for pdf in input_pdfs(cfg):
                try:
                    data = pdf.read_bytes()
                except OSError:
                    # An unreadable input file might hold these bytes. Skipping it would let
                    # the rows go and the file re-form a set on the next sync (review 1).
                    unreadable.append(pdf.name)
                    continue
                if hashlib.sha256(data).hexdigest() not in shas:
                    continue
                input_dir.mkdir(exist_ok=True)
                copy = input_dir / _unique_name(input_dir, pdf.name)
                copy.write_bytes(data)
                if not _same_bytes(copy, data):
                    copy.unlink(missing_ok=True)
                    raise OSError(f"copy verification failed for input file {pdf.name}")
                pdf.unlink(missing_ok=True)
                moved.append((copy, pdf))
            if unreadable:
                raise OSError(f"input files could not be read: {', '.join(unreadable)}")
        except Exception as exc:
            # Any failure in step 1: put the moved inputs back, keep the folder and say
            # why (review 2). Nothing has been deleted from the database yet.
            failed = _restore_input_copies(moved)
            shutil.rmtree(stage, ignore_errors=True)
            reasons = ["The removal failed and nothing was deleted."]
            if failed:
                reasons.append(f"Input files not put back: {', '.join(failed)}.")
            if final.is_dir():
                reasons.append(f"The copies are in {REMOVED_SETS_FOLDER}/{final.name}.")
            message = " ".join(reasons)
            if final.is_dir():
                _mark_removal_failed(final, message)
            raise SetRemovalRefused(500, message) from exc

        try:
            with Session(eng) as s:
                current = sorted(
                    d.id for d in s.query(Document).filter(Document.po_set_id == po_set_id).all()
                )
                if current != doc_ids:
                    raise SetRemovalRefused(
                        409,
                        "A document arrived or changed while the set was being removed. "
                        f"Nothing was deleted. The copies are in {REMOVED_SETS_FOLDER}/"
                        f"{final.name}.",
                    )
                detail = {
                    "deleted": True,
                    "archived": False,
                    "removed_po_set_id": po_set_id,
                    "po_no_normalized": po_no,
                    "status_before": status_before,
                    "folder": f"{REMOVED_SETS_FOLDER}/{final.name}",
                    "document_count": len(doc_ids),
                    "line_item_count": len(item_rows),
                    "documents": [
                        {
                            "id": d["id"],
                            "filename": d["filename"],
                            "doc_type": d["doc_type"],
                            "sha256": d["sha256"],
                        }
                        for d in doc_rows
                    ],
                    "moved_input_count": len(moved),
                    "missing_files": missing,
                    "note": note,
                }
                s.add(
                    AuditLog(
                        po_set_id=None,
                        action=AuditAction.quarantine_delete,
                        source=source,
                        detail=json.dumps(detail),
                    )
                )
                if doc_ids:
                    s.execute(
                        update(Document)
                        .where(Document.parent_document_id.in_(doc_ids))
                        .values(parent_document_id=None)
                    )
                    s.execute(delete(LineItem).where(LineItem.document_id.in_(doc_ids)))
                    s.execute(delete(Document).where(Document.id.in_(doc_ids)))
                s.execute(delete(POSet).where(POSet.id == po_set_id))
                s.commit()
        except SetRemovalRefused as exc:
            failed = _restore_input_copies(moved)
            message = exc.message
            if failed:
                message += f" Input files not put back: {', '.join(failed)}."
            _mark_removal_failed(final, message)
            raise SetRemovalRefused(exc.status_code, message) from None
        except Exception as exc:
            # The transaction rolled back, so no row changed. Put the input files back
            # and keep the folder, so the operator can see where the copies are (A2).
            failed = _restore_input_copies(moved)
            detail = (
                "The removal failed and nothing was deleted. "
                f"The copies are in {REMOVED_SETS_FOLDER}/{final.name}."
            )
            if failed:
                detail += f" These input files could not be put back: {', '.join(failed)}."
            _mark_removal_failed(final, detail)
            raise SetRemovalRefused(500, detail) from exc

        # The commit is done: the completion report replaces the in-progress note.
        try:
            (final / "REMOVED.txt").write_text(report_text, encoding="utf-8")
            (final / "REMOVAL_IN_PROGRESS.txt").unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not write REMOVED.txt after commit", exc_info=True)

        if merged_output_file:
            try:
                Path(str(merged_output_file)).unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Could not unlink merged packet %s after commit; copy is in %s",
                    merged_output_file,
                    final,
                    exc_info=True,
                )

        # A future set with this id must not adopt the old quarantine folder (id reuse).
        for marker in Path(cfg.paths.quarantine_folder).glob("*/.po_set_id"):
            try:
                if marker.read_text(encoding="utf-8").strip() == str(po_set_id):
                    marker.write_text(f"removed:{po_set_id}:{stamp}", encoding="utf-8")
            except OSError:
                logger.warning("Could not update quarantine marker %s", marker, exc_info=True)

        return {
            "folder": str(final),
            "folder_name": f"{REMOVED_SETS_FOLDER}/{final.name}",
            "po_no_normalized": po_no,
            "document_count": len(doc_ids),
            "line_item_count": len(item_rows),
            "moved_input_count": len(moved),
            "missing_files": missing,
        }
    finally:
        sync_lock.release_sync_lock(lock)
