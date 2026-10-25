"""Settings page: review and change the settings an operator may touch (docs/SETTINGS_SPEC.md).

GET  /settings  the current values, what each does and when a change takes effect.
POST /settings  validate and save. A bad value is refused with a message beside the field and
                the file on disk is untouched; see `app.services.settings_admin`.

Like the rest of the app there is no login (PRODUCT section 8); the Origin guard and the allowed
hosts apply, and every save is written to the audit log.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse

from app.api.templating import templates as _templates
from app.core import config as config_module
from app.services import settings_admin as svc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["settings"])

#: A form carries one field per setting plus the file version; anything bigger is not ours.
_MAX_FORM_FIELDS = 200


def _render(
    request: Request,
    *,
    submitted: dict[str, str] | None = None,
    result: svc.SaveResult | None = None,
    status: int = 200,
) -> HTMLResponse:
    path = svc.config_path()
    cfg = config_module.load_config()
    model = svc.page_model(cfg)
    poller = getattr(request.app.state, "poller", None)
    return _templates.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            **model,
            "version": svc.file_version(path),
            "disk_problem": svc.disk_problem(path),
            "poller_running": poller is not None and cfg.schedule.poll_enabled,
            "submitted": submitted or {},
            "result": result,
            "errors": result.errors if result else {},
        },
        status_code=status,
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    return _render(request)


@router.post("/settings", response_class=HTMLResponse)
async def settings_save(request: Request) -> HTMLResponse:
    form = await request.form()
    if len(form) > _MAX_FORM_FIELDS:
        return _render(request, status=413)
    submitted = {k: str(form.getlist(k)[-1]) for k in form if isinstance(k, str)}
    version = submitted.pop("config_version", "")
    result = await run_in_threadpool(svc.save, submitted, version=version)
    if result.changed:
        _start_poller_if_switched_on(request)
    status = 200 if result.ok else 409 if result.general else 422
    return _render(
        request,
        submitted=submitted if not result.ok and not result.stale else None,
        result=result,
        status=status,
    )


def _start_poller_if_switched_on(request: Request) -> None:
    """Switching the watcher on needs no restart: start it if the service started without one."""
    if getattr(request.app.state, "poller", None) is not None:
        return
    try:
        from app.services.poller import start_if_enabled

        request.app.state.poller = start_if_enabled(config_module.load_config())
    except Exception:
        logger.warning("Could not start the input poller after a settings change", exc_info=True)
