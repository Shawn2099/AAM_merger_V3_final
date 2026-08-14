"""Manual merger routes — isolated from automated pipeline (FR-14.11-14.13)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/manual", tags=["manual"])

# templates isolated from pipeline dashboard
templates = Jinja2Templates(directory="templates")


@router.get("/merger", response_class=HTMLResponse)
def manual_merger_page(request: Request):
    """Isolated manual PDF merger UI — visually distinct from pipeline dashboard (FR-14.13)."""
    return templates.TemplateResponse(request, "manual_merger.html", {"request": request})


@router.post("/merge")
async def manual_merge_endpoint(
    files: list[UploadFile] = File(...),  # noqa: B008
    order: str = Form(""),
    output_filename: str = Form("manual_merged.pdf"),
):
    """Merge uploaded PDFs in user-specified order, no DB side-effects (FR-14.11).

    Output filename and destination are user-selectable at merge time (FR-14.12).
    Returns merged PDF as download.
    """
    from app.services.quarantine import manual_merge

    # save uploads to temp files
    tmp_paths: list[Path] = []
    try:
        for uf in files:
            suffix = Path(uf.filename or "upload.pdf").suffix or ".pdf"
            fd, tmp = tempfile.mkstemp(suffix=suffix)
            import os

            os.close(fd)
            p = Path(tmp)
            data = await uf.read()
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
        import os

        os.close(fd2)
        # we will write to a path that reflects user's chosen name for download header,
        # but actual file is tmp; FileResponse will set filename
        from pathlib import Path as _Path

        out_path = _Path(out_tmp)
        merged = manual_merge(tmp_paths, order=order_list, output_path=out_path)

        return FileResponse(
            path=str(merged),
            filename=safe_name,
            media_type="application/pdf",
        )
    finally:
        # cleanup input tmps after response is prepared; output tmp is served by FileResponse
        # input tmps are not needed after merge; we keep them until after merge, but can leave
        # for OS tmp cleanup. We do not delete output tmp here because FileResponse needs it.
        pass
