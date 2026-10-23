"""Manual merger routes — isolated from automated pipeline (FR-14.11-14.13)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/manual", tags=["manual"])

# templates isolated from pipeline dashboard
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[4] / "templates")
)


@router.get("/merger", response_class=HTMLResponse)
def manual_merger_page(request: Request):
    """Isolated manual PDF merger UI — visually distinct from pipeline dashboard (FR-14.13)."""
    return templates.TemplateResponse(request, "manual_merger.html", {"request": request})


@router.post("/merge")
def manual_merge_endpoint(
    request: Request,
    files: list[UploadFile] = File(...),  # noqa: B008
    order: str = Form(""),
    output_filename: str = Form("manual_merged.pdf"),
):
    try:
        return _manual_merge_impl(files, order, output_filename)
    except HTTPException as exc:
        if "text/html" in (request.headers.get("accept") or ""):
            return HTMLResponse(
                content=_error_page(str(exc.detail)), status_code=exc.status_code
            )
        raise


def _error_page(message: str) -> str:
    """A minimal standalone page so a browser form error keeps a way back."""
    import html as _html

    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Manual merge — not completed</title>"
        "<style>body{font-family:system-ui,Segoe UI,sans-serif;background:#fffbf5;"
        "color:#1c1917;padding:48px;}main{max-width:640px;margin:0 auto;"
        "background:#fff;border:1px solid #fde68a;border-radius:8px;padding:24px;}"
        "h1{font-size:1.2rem;margin-bottom:8px;}a{color:#b45309;}</style></head>"
        "<body><main><h1>Manual merge was not completed</h1>"
        f"<p>{_html.escape(message)}</p>"
        "<p><a href='/manual/merger'>&larr; Back to the manual merger</a></p>"
        "</main></body></html>"
    )


def _manual_merge_impl(
    files: list[UploadFile],
    order: str,
    output_filename: str,
):
    """Merge uploaded PDFs in user-specified order, no DB side-effects (FR-14.11).

    Output filename and destination are user-selectable at merge time (FR-14.12).
    Returns merged PDF as download.
    """
    import contextlib
    import os

    from starlette.background import BackgroundTask

    from app.core.limits import MAX_MANUAL_MERGE_FILES, MAX_UPLOAD_BYTES
    from app.services.quarantine import manual_merge

    def _cleanup(*paths) -> None:
        for p in paths:
            if p is None:
                continue
            with contextlib.suppress(OSError):
                Path(p).unlink(missing_ok=True)

    # Bound the request before reading anything into memory (audit P2-15).
    if not files:
        raise HTTPException(status_code=422, detail="no files uploaded")
    if len(files) > MAX_MANUAL_MERGE_FILES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"too many files: {len(files)} uploaded, "
                f"max {MAX_MANUAL_MERGE_FILES} per manual merge"
            ),
        )

    # save uploads to temp files (bounded, PDF-only)
    tmp_paths: list[Path] = []
    out_path: Path | None = None
    total_bytes = 0
    try:
        for uf in files:
            safe_filename = Path(uf.filename or "upload.pdf").name
            if Path(safe_filename).suffix.lower() != ".pdf":
                raise HTTPException(status_code=422, detail="only .pdf uploads accepted")
            data = uf.file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=422,
                    detail=f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB upload cap",
                )
            total_bytes += len(data)
            if total_bytes > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "uploads exceed the "
                        f"{MAX_UPLOAD_BYTES // (1024 * 1024)}MB total cap "
                        "for one manual merge"
                    ),
                )
            if not data.startswith(b"%PDF"):
                raise HTTPException(status_code=422, detail="not a PDF file")
            fd, tmp = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            p = Path(tmp)
            p.write_bytes(data)
            tmp_paths.append(p)

        # parse order: comma-separated ints e.g. "2,0,1" or empty for natural order
        if order.strip():
            order_list = [int(x.strip()) for x in order.split(",") if x.strip() != ""]
        else:
            order_list = list(range(len(tmp_paths)))

        # sanitize output filename
        safe_name = "".join(c for c in output_filename if c.isalnum() or c in ("-", "_", "."))
        if not safe_name:
            safe_name = "manual_merged.pdf"
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"

        fd2, out_tmp = tempfile.mkstemp(suffix=".pdf")
        os.close(fd2)
        # we will write to a path that reflects user's chosen name for download header,
        # but actual file is tmp; FileResponse will set filename
        from pathlib import Path as _Path

        out_path = _Path(out_tmp)
        merged = manual_merge(tmp_paths, order=order_list, output_path=out_path)

        # output tmp is served by FileResponse: delete after send (W-11).
        return FileResponse(
            path=str(merged),
            filename=safe_name,
            media_type="application/pdf",
            background=BackgroundTask(_cleanup, *[*tmp_paths, out_path]),
        )
    except HTTPException:
        _cleanup(*(tmp_paths + ([out_path] if out_path is not None else [])))
        raise
    except Exception as e:
        _cleanup(*(tmp_paths + ([out_path] if out_path is not None else [])))
        raise HTTPException(status_code=422, detail=f"manual merge failed: {e}") from e
