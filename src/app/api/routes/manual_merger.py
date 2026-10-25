"""Manual merger routes — isolated from the automated pipeline (FR-14.11-14.13).

The page is standalone in *function* (no AI, no database, no PO association)
while using the application's normal shell, because it is still part of the
same program.  The endpoint is deliberately defensive: the request body is
bounded before multipart parsing, every file is validated as a real PDF, and
failures name the offending file so the UI can point at it.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.core.config import ManualMergeConfig, load_config
from app.core.http_errors import BodyTooLarge, error_response, wants_html
from app.services.manual_merge import (
    DEFAULT_PDF_NAME,
    TEMP_FILE_PREFIX,
    ManualMergeError,
    manual_merge,
    resolve_temp_folder,
    sweep_temp_folder,
    validate_pdf_name,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/manual", tags=["manual"])

# templates isolated from the pipeline dashboard
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[4] / "templates"))

_MAX_FORM_FIELD_BYTES = 64 * 1024
_MAX_FORM_FIELDS = 8


@router.get("/merger", response_class=HTMLResponse)
def manual_merger_page(request: Request):
    """The Merge PDFs tool.  Functionally isolated from the pipeline."""
    mm = load_config().manual_merge
    return templates.TemplateResponse(
        request,
        "manual_merger.html",
        {
            "request": request,
            "max_files": mm.max_files,
            "max_bytes": mm.max_total_bytes,
            "max_mb": mm.max_total_mb,
            "max_pages": mm.max_pages,
            "default_name": DEFAULT_PDF_NAME,
        },
    )


@router.post("/merge")
async def manual_merge_endpoint(request: Request):
    """Merge uploaded PDFs in the order they arrive.  No DB side-effects."""
    cfg = await run_in_threadpool(load_config)  # reads/stats the YAML: not on the event loop
    mm = cfg.manual_merge
    try:
        form = await request.form(
            max_files=mm.max_files + 1,
            max_fields=_MAX_FORM_FIELDS,
            max_part_size=_MAX_FORM_FIELD_BYTES,
        )
    except BodyTooLarge:
        return _error(
            request,
            413,
            "too_large",
            f"That upload is larger than the {mm.max_total_mb} MB limit. "
            "Please remove a file or use smaller ones.",
        )
    except MultiPartException as exc:
        if "Too many files" in str(exc):
            return _error(
                request,
                422,
                "too_many_files",
                f"Too many files — you can merge up to {mm.max_files} at a time.",
            )
        return _error(
            request,
            400,
            "bad_request",
            "We couldn't read that upload. Please reload the page and try again.",
        )

    try:
        uploads = [
            value
            for value in form.getlist("files")
            if isinstance(value, UploadFile) and getattr(value, "filename", None)
        ]
        order_raw = str(form.get("order") or "")
        raw_name = form.get("output_filename")
        # A missing field keeps the friendly default; an explicitly blank
        # field is invalid input and is rejected by validate_pdf_name.
        output_filename = DEFAULT_PDF_NAME if raw_name is None else str(raw_name)
        return await run_in_threadpool(
            _manual_merge_impl,
            uploads,
            order_raw,
            output_filename,
            mm,
            cfg.paths.database_path,
        )
    except ManualMergeError as exc:
        return _error(request, exc.status, exc.code, str(exc), exc.filename)
    except FileNotFoundError as exc:
        logger.warning("Manual merge could not read an upload: %s", exc)
        return _error(
            request,
            422,
            "missing_file",
            "One of the files couldn't be read. Please add it again.",
        )
    except Exception:
        logger.exception("Manual merge failed unexpectedly")
        return _error(
            request,
            500,
            "merge_failed",
            "Something went wrong and nothing was downloaded. Please try again.",
        )
    finally:
        await form.close()


def _error(
    request: Request,
    status: int,
    code: str,
    message: str,
    filename: str | None = None,
):
    """HTML for a browser form post, JSON for the XHR client."""
    if wants_html(request):
        return templates.TemplateResponse(
            request,
            "manual_merger_error.html",
            {
                "request": request,
                "message": message,
                "filename": filename,
                "status": status,
            },
            status_code=status,
        )
    return error_response(request, status, code, message, filename)


def _new_temp(folder: Path) -> Path:
    """Create one staging file in our own folder."""
    try:
        fd, tmp = tempfile.mkstemp(prefix=TEMP_FILE_PREFIX, suffix=".pdf", dir=str(folder))
        os.close(fd)
        return Path(tmp)
    except OSError as exc:
        raise ManualMergeError(
            "The server's temporary folder isn't writable. "
            "Please check the manual_merge.temp_folder setting.",
            "staging_failed",
        ) from exc


def _manual_merge_impl(
    files: list[UploadFile],
    order_raw: str,
    output_filename: str,
    mm: ManualMergeConfig,
    database_path: Path,
):
    """Read, validate and merge uploads.  Runs in the threadpool."""
    if not files:
        raise ManualMergeError(
            "Please add at least one PDF and try again.",
            "no_files",
        )
    if len(files) > mm.max_files:
        raise ManualMergeError(
            f"Too many files — you can merge up to {mm.max_files} at a time "
            f"({len(files)} were added).",
            "too_many_files",
        )

    # Validate the output name before touching the files: a bad name fails
    # fast and creates no temp files.
    safe_name = validate_pdf_name(output_filename)

    try:
        temp_folder = resolve_temp_folder(mm.temp_folder, database_path)
    except OSError as exc:
        raise ManualMergeError(
            "The server's temporary folder couldn't be created. "
            "Please check the manual_merge.temp_folder setting.",
            "staging_failed",
        ) from exc
    sweep_temp_folder(temp_folder, mm.sweep_after_hours)

    tmp_paths: list[Path] = []
    out_path: Path | None = None
    total_bytes = 0
    names: list[str] = []

    try:
        for upload in files:
            name = Path(upload.filename or "upload.pdf").name
            names.append(name)
            if Path(name).suffix.lower() != ".pdf":
                raise ManualMergeError(
                    f"{name} isn't a PDF. Please add PDF files only.",
                    "not_pdf",
                    name,
                )

            data = upload.file.read(mm.max_total_bytes + 1)
            if len(data) > mm.max_total_bytes:
                raise ManualMergeError(
                    f"{name} is larger than the {mm.max_total_mb} MB limit. "
                    "Please use a smaller file.",
                    "too_large",
                    name,
                    status=413,
                )
            total_bytes += len(data)
            if total_bytes > mm.max_total_bytes:
                raise ManualMergeError(
                    f"These files add up to more than {mm.max_total_mb} MB. "
                    "Please remove one or use smaller files.",
                    "too_large",
                    status=413,
                )
            # The header may legitimately sit anywhere in the first 1024 bytes
            # (BOM, blank lines, scanner padding).  Ingestion uses the same rule.
            if b"%PDF-" not in data[:1024]:
                raise ManualMergeError(
                    f"We couldn't read {name}. It may be damaged or not a PDF.",
                    "not_pdf",
                    name,
                )

            path = _new_temp(temp_folder)
            tmp_paths.append(path)  # before the write, so a failed write is still cleaned up
            path.write_bytes(data)

        order: list[int] | None = None
        if order_raw.strip():
            try:
                order = [int(part.strip()) for part in order_raw.split(",") if part.strip()]
            except ValueError as exc:
                raise ManualMergeError(
                    "We couldn't read the file order. Please reload the page and try again.",
                    "bad_order",
                ) from exc

        out_path = _new_temp(temp_folder)

        merged = manual_merge(
            tmp_paths,
            order=order,
            output_path=out_path,
            display_names=names,
            max_pages=mm.max_pages,
        )

        return FileResponse(
            path=str(merged),
            filename=safe_name,
            media_type="application/pdf",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
            # Inputs and the output temp are removed only after the bytes are sent.
            background=BackgroundTask(_cleanup, *tmp_paths, out_path),
        )
    except Exception:
        _cleanup(*tmp_paths, out_path)
        raise


def _cleanup(*paths) -> None:
    for path in paths:
        if path is None:
            continue
        with contextlib.suppress(OSError):
            Path(path).unlink(missing_ok=True)
