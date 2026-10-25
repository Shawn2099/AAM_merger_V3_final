"""The isolated manual PDF merger (FR-14.11 - FR-14.13).

Deliberately outside the pipeline: this module never opens a session, never
writes a PO Set and never calls the VLM.  It also does not trust its input:
vendor PDFs can be truncated, password-protected or carry page-level
JavaScript, so every file is opened, validated and sanitised before its pages
are copied into the packet.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject

from app.core.limits import MAX_MANUAL_MERGE_PAGES

logger = logging.getLogger(__name__)

#: A single merge may not produce more pages than this.  It bounds pypdf's
#: CPU and memory on the 2-core production host; a CA packet is nowhere near it.
MAX_TOTAL_PAGES = MAX_MANUAL_MERGE_PAGES

_DEFAULT_NAME = "merged-document.pdf"
MAX_PDF_NAME_STEM = 100
INVALID_PDF_NAME_CHARS = set('<>:"/\\|?*')

#: Prefix for every staging file this tool creates. The sweep deletes only
#: `mm-*.pdf` in the configured folder, never a real document.
TEMP_FILE_PREFIX = "mm-"
_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

#: The name used when an API caller omits `output_filename` entirely.
DEFAULT_PDF_NAME = _DEFAULT_NAME

#: Page/annotation actions that can execute code or launch a local program.
#: Ordinary links (/URI) and document jumps (/GoTo) are kept.
_DANGEROUS_ACTIONS = {"/JavaScript", "/Launch"}


class ManualMergeError(ValueError):
    """A merge that failed for a reason the operator can act on.

    Subclasses ValueError so existing callers/tests that expected a plain
    ValueError keep working.
    """

    def __init__(
        self,
        message: str,
        code: str,
        filename: str | None = None,
        status: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.filename = filename
        self.status = status


def safe_pdf_name(raw: str | None) -> str:
    """Turn operator input into a safe, readable download name.

    Keeps spaces, parentheses and Unicode; removes path separators, control
    characters and Windows-reserved punctuation; always ends in ``.pdf``.
    """
    name = str(raw or "")
    # Both separators, because the browser may send either.
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(
        ch for ch in name if ch >= " " and ch != "\x7f" and ch not in INVALID_PDF_NAME_CHARS
    )
    name = re.sub(r"\s+", " ", name).strip(" .")

    if not name:
        return _DEFAULT_NAME
    if name.lower() == "pdf":
        # ".pdf" or "pdf" leaves no usable stem.
        return _DEFAULT_NAME
    if not name.lower().endswith(".pdf"):
        name += ".pdf"

    stem = name[:-4].strip(" .")
    if not stem:
        return _DEFAULT_NAME
    # Windows reserves these device names with any extension (CON.pdf too).
    if stem.split(".")[0].upper() in _RESERVED_NAMES:
        stem = f"_{stem}"
    return f"{stem[:MAX_PDF_NAME_STEM]}.pdf"


def _display_char(ch: str) -> str:
    """A readable label for a character Windows rejects in a filename."""
    if ch == '"':
        return "the double quote"
    if ch == "\\":
        return "the backslash"
    if ch == " ":
        return "the space"
    if ord(ch) < 32 or ord(ch) == 127:
        return "a control character"
    return f'"{ch}"'


def _join_words(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def validate_pdf_name(raw: str | None) -> str:
    """Strict, operator-facing validation; raises ``bad_filename``.

    The browser mirrors these rules as the user types, so an invalid name is
    caught before submission.  The server repeats the check because a client
    is never trusted: a name that cannot be saved on Windows must be refused,
    not silently rewritten.
    """
    text = str(raw or "").strip()
    if not text:
        raise ManualMergeError("Please give the merged file a name.", "bad_filename")

    stem = text[:-4] if text.lower().endswith(".pdf") else text
    if not stem:
        raise ManualMergeError("Please add a name before .pdf.", "bad_filename")
    if len(stem) > MAX_PDF_NAME_STEM:
        raise ManualMergeError(
            f"Please keep the file name to {MAX_PDF_NAME_STEM} characters or fewer (before .pdf).",
            "bad_filename",
        )

    bad: list[str] = []
    for ch in stem:
        if (ch in INVALID_PDF_NAME_CHARS or ord(ch) < 32 or ord(ch) == 127) and ch not in bad:
            bad.append(ch)
    if bad:
        shown = _join_words([_display_char(ch) for ch in bad])
        raise ManualMergeError(
            f"Please remove {shown} from the file name.",
            "bad_filename",
        )

    if stem[0] == "." or stem[-1] == ".":
        raise ManualMergeError("The file name can't start or end with a dot.", "bad_filename")
    if stem[0] == " " or stem[-1] == " ":
        raise ManualMergeError("The file name can't start or end with a space.", "bad_filename")
    if stem.split(".")[0].upper() in _RESERVED_NAMES:
        raise ManualMergeError(
            "That name is reserved by Windows — please choose another.",
            "bad_filename",
        )

    normalised = re.sub(r"\s+", " ", stem)
    return f"{normalised}.pdf"


def _action_of(annot: DictionaryObject):
    action = annot.get("/A")
    return action.get_object() if hasattr(action, "get_object") else action


def _strip_active_content(page) -> None:
    """Remove JavaScript/launch actions from a page and its annotations.

    pypdf copies page dictionaries verbatim, so a hostile vendor PDF would
    otherwise carry its payload into the delivered packet.  We deliberately
    keep /URI links, which are not executable.
    """
    for key in ("/AA", "/JS", "/JavaScript"):
        page.pop(key, None)

    annots = page.get("/Annots")
    if annots is None:
        return
    annots = annots.get_object() if hasattr(annots, "get_object") else annots
    if not isinstance(annots, ArrayObject):
        return

    kept = ArrayObject()
    for ref in annots:
        annot = ref.get_object() if hasattr(ref, "get_object") else ref
        if not isinstance(annot, DictionaryObject):
            kept.append(ref)
            continue
        for key in ("/AA", "/JS", "/JavaScript"):
            annot.pop(key, None)
        action = _action_of(annot)
        if isinstance(action, DictionaryObject) and str(action.get("/S")) in _DANGEROUS_ACTIONS:
            continue  # drop the whole annotation, not just the action
        kept.append(ref)
    page[NameObject("/Annots")] = kept


def _open_pdf(path: Path, name: str) -> PdfReader:
    try:
        return PdfReader(str(path))
    except Exception as exc:  # pypdf raises several unrelated types
        raise ManualMergeError(
            f"We couldn't read {name}. It may be truncated or damaged — "
            "try re-saving it and upload it again.",
            "unreadable",
            name,
        ) from exc


def resolve_temp_folder(configured: Path | None, database_path: str | Path) -> Path:
    """The configured staging folder, or one beside the database.

    The database folder is local to the host (SQLite/WAL cannot live on SMB),
    so the fallback is always on local disk. Created on demand.
    """
    folder = Path(configured) if configured else Path(database_path).parent / "manual_merge_tmp"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def sweep_temp_folder(folder: Path, older_than_hours: int) -> int:
    """Delete this tool's abandoned staging files; return how many.

    Only `mm-*.pdf` files older than the threshold are touched. A real
    document dropped in the folder, a sub-directory, or a symlink is never
    deleted, so a misconfigured folder cannot lose pipeline data.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return 0

    cutoff = time.time() - older_than_hours * 3600
    removed = 0
    for path in folder.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        if not path.name.startswith(TEMP_FILE_PREFIX) or path.suffix.lower() != ".pdf":
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
        except OSError:
            logger.warning("Could not remove stale manual-merge temp file %s", path, exc_info=True)
    if removed:
        logger.info("Removed %d stale manual-merge temp file(s) from %s", removed, folder)
    return removed


def manual_merge(
    files: list[Path],
    order: list[int] | None,
    output_path: Path | None = None,
    *,
    display_names: list[str] | None = None,
    max_pages: int | None = None,
) -> Path:
    """Concatenate PDFs in ``order`` into one download-ready file.

    No database, no PO association, no audit row (FR-14.11).  Raises
    :class:`ManualMergeError` with a ``code`` and the offending filename when a
    file cannot be used, so the UI can point at the right card.

    Any encrypted PDF is refused, including one with an empty user password:
    the tool never decrypts and never silently drops permission flags.
    """
    paths = [Path(f) for f in files]
    names = list(display_names) if display_names is not None else [p.name for p in paths]
    page_limit = max_pages if max_pages is not None else MAX_TOTAL_PAGES

    if not paths:
        raise ManualMergeError("Please add at least one PDF and try again.", "no_files")

    if order is None:
        order = list(range(len(paths)))
    if len(order) != len(paths):
        raise ManualMergeError(
            f"order length {len(order)} != files length {len(paths)}", "bad_order"
        )
    if sorted(order) != list(range(len(paths))):
        raise ManualMergeError(
            f"order must be permutation of 0..{len(paths) - 1}, got {order}", "bad_order"
        )

    if output_path is None:
        fd, tmp = tempfile.mkstemp(prefix=TEMP_FILE_PREFIX, suffix=".pdf")
        os.close(fd)
        output_path = Path(tmp)
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    writer = PdfWriter()
    total_pages = 0
    for index in order:
        path = paths[index]
        name = names[index] if index < len(names) else path.name
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        reader = _open_pdf(path, name)
        try:
            if reader.is_encrypted:
                raise ManualMergeError(
                    f"{name} is password-protected. Please upload an unlocked copy "
                    "(remove the password and save the file again).",
                    "encrypted",
                    name,
                )
            try:
                page_count = len(reader.pages)
            except Exception as exc:
                raise ManualMergeError(
                    f"We couldn't read {name}. Its page structure is damaged — "
                    "try re-saving it and upload it again.",
                    "unreadable",
                    name,
                ) from exc
            if not page_count:
                raise ManualMergeError(f"{name} has no pages to merge.", "no_pages", name)
            total_pages += page_count
            if total_pages > page_limit:
                raise ManualMergeError(
                    f"That would be more than {page_limit:,} pages. "
                    "Please split it into smaller merges.",
                    "too_many_pages",
                )
            try:
                for page in reader.pages:
                    _strip_active_content(page)
                    writer.add_page(page)
            except Exception as exc:
                raise ManualMergeError(
                    f"We couldn't read {name}. One of its pages is damaged — "
                    "try re-saving it and upload it again.",
                    "unreadable",
                    name,
                ) from exc
        finally:
            reader.close()

    writer.write(str(output_path))

    try:
        check = PdfReader(str(output_path))
        try:
            written = len(check.pages)
        finally:
            check.close()
    except Exception as exc:
        raise ManualMergeError(
            "Something went wrong while merging, and nothing was downloaded. Please try again.",
            "merge_failed",
        ) from exc
    if written != total_pages:
        raise ManualMergeError(
            "Something went wrong while merging, and nothing was downloaded. Please try again.",
            "merge_failed",
        )
    return output_path
