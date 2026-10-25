"""Settings page backend: which config keys an operator may change, and how a change is saved.

The page edits `config.yaml` in place. The rules (docs/SETTINGS_SPEC.md):

* Only the keys in `GROUPS` can be changed here. Paths, host names, the API key and
  schedule keys that only a deploy script reads are shown, never edited.
* A change is validated by the same models the service starts with (`AppConfig`), on a
  candidate copy of the file, before the real file is touched. A bad value is refused with a
  message that names the field; the file on disk stays as it was.
* The real file is replaced atomically, after a timestamped copy is kept in
  `config_backups/`. The saved file is loaded once more; if that fails the copy is put back.
* The file is edited line by line so the comments and the order of the file survive. The
  edited text is parsed again and must hold exactly the intended values, or nothing is written.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock, Timeout
from pydantic import ValidationError

from app.core import config as config_module
from app.core.config import AppConfig

BACKUP_FOLDER_NAME = "config_backups"
KEEP_BACKUPS = 20
LOCK_WAIT_SECONDS = 10

#: When a saved value takes effect.
LIVE = "live"  # within seconds, no restart
NEXT_SYNC = "next_sync"  # the next sync (or the next document read) uses it
RESTART = "restart"  # after the service is restarted

APPLY_TEXT = {
    LIVE: "Takes effect within a few seconds.",
    NEXT_SYNC: "Takes effect from the next sync.",
    RESTART: "Takes effect after the service is restarted.",
}

_MODEL_ID = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._:+-]+$")
_INT = re.compile(r"^[+-]?[0-9]+$")


@dataclass(frozen=True)
class Setting:
    key: str  # dotted path in the config, e.g. "schedule.poll_interval_seconds"
    label: str
    help: str
    kind: str  # "int" | "bool" | "choice" | "text"
    apply: str
    unit: str = ""
    choices: tuple[str, ...] = ()
    minimum: int | None = None  # None: taken from the config model
    maximum: int | None = None

    @property
    def section(self) -> str:
        return self.key.split(".")[0]


@dataclass(frozen=True)
class Group:
    title: str
    blurb: str
    settings: tuple[Setting, ...]


GROUPS: tuple[Group, ...] = (
    Group(
        "Watching the input folder",
        "How the service notices new files and when it merges a set.",
        (
            Setting(
                "schedule.poll_enabled",
                "Watch the input folder automatically",
                "On: new files are picked up on their own. Off: use the Scan button on the "
                "dashboard (the midnight sweep is separate).",
                "bool",
                LIVE,
            ),
            Setting(
                "schedule.poll_interval_seconds",
                "Check the folder every",
                "How often the folder is looked at. This only lists the folder; it makes no "
                "paid calls.",
                "int",
                LIVE,
                unit="seconds",
            ),
            Setting(
                "schedule.merge_settle_minutes",
                "Wait before merging a set",
                "A set found by the folder watcher merges only when its newest file is this "
                "old, so papers of one PO that arrive minutes apart are not merged early. "
                "0 = no wait. Scan, Merge Now and the midnight run never wait.",
                "int",
                LIVE,
                unit="minutes",
            ),
            Setting(
                "ingestion.stability_poll_interval_seconds",
                "Copy check: gap between looks",
                "A file is read only after its size stops changing. This is the gap between "
                "two looks at its size.",
                "int",
                NEXT_SYNC,
                unit="seconds",
            ),
            Setting(
                "ingestion.stability_poll_count",
                "Copy check: number of looks",
                "How many size readings must agree before a file counts as fully copied.",
                "int",
                NEXT_SYNC,
            ),
        ),
    ),
    Group(
        "Reading documents (AI)",
        "These control the paid reading step. A wrong model name makes documents fail to "
        "read; they are kept and retried, nothing is lost.",
        (
            Setting(
                "vlm.model",
                "Model",
                "The OpenRouter model id, as provider/name (for example openai/gpt-6-luna).",
                "text",
                NEXT_SYNC,
            ),
            Setting(
                "vlm.request_timeout_seconds",
                "Give up on one reading after",
                "Longest wait for the model to answer one document. Raising it may require "
                "raising the stale-reading limit below.",
                "int",
                NEXT_SYNC,
                unit="seconds",
            ),
            Setting(
                "vlm.raster_dpi",
                "Page image sharpness",
                "Resolution each page is sent at. 150 is the tested setting; higher costs "
                "more and is slower.",
                "int",
                NEXT_SYNC,
                unit="DPI",
            ),
            Setting(
                "vlm.reasoning_effort",
                "Reasoning effort",
                "How hard the model thinks. High is the tested setting.",
                "choice",
                NEXT_SYNC,
                choices=("minimal", "low", "medium", "high"),
            ),
            Setting(
                "vlm.max_pages",
                "Longest document read",
                "A PDF with more pages than this is quarantined instead of read.",
                "int",
                NEXT_SYNC,
                unit="pages",
                maximum=500,
            ),
            Setting(
                "extraction.max_retries",
                "Reading attempts per document",
                "Each attempt is a paid call. After this many failed attempts the document "
                "is marked failed and shown for action.",
                "int",
                NEXT_SYNC,
                unit="attempts",
            ),
            Setting(
                "extraction.stale_processing_seconds",
                "Treat a reading as dead after",
                "A document stuck 'being read' longer than this is released and read again. "
                "Must be longer than the worst case of one reading (shown if it is not).",
                "int",
                NEXT_SYNC,
                unit="seconds",
                maximum=86400,
            ),
            Setting(
                "extraction.split_combined",
                "Split combined PDFs",
                "Off (recommended): a PDF holding several documents is quarantined for a "
                "person to look at. On: it is cut into one file per document.",
                "bool",
                NEXT_SYNC,
            ),
        ),
    ),
    Group(
        "Matching and merging rules",
        "How a PO is compared with its delivery notes and invoices.",
        (
            Setting(
                "reconciliation.single_po_document",
                "Hold a set that has two PO documents",
                "On (recommended): a set with more than one PO document waits for a person "
                "and is never merged on its own.",
                "bool",
                NEXT_SYNC,
            ),
            Setting(
                "matching.fuzzy_description_threshold",
                "Description match strictness",
                "Used only for lines that carry no line number. Higher is stricter; 85 is "
                "the tested value.",
                "int",
                NEXT_SYNC,
                unit="%",
            ),
            Setting(
                "matching.line_number_fallback.step10",
                "Line numbers: PO 10, 20, 30 = DN/SI 1, 2, 3",
                "Accept the vendor's own line numbering when it is a clean times-10 pattern.",
                "bool",
                NEXT_SYNC,
            ),
            Setting(
                "matching.line_number_fallback.step10_reverse",
                "Line numbers: PO 1, 2, 3 = DN/SI 10, 20, 30",
                "The same pattern the other way round.",
                "bool",
                NEXT_SYNC,
            ),
            Setting(
                "matching.line_number_fallback.release_suffix",
                "Line numbers: PO 1-1, 2-1 = DN/SI 1, 2",
                "Accept a '-1' release suffix on the PO side.",
                "bool",
                NEXT_SYNC,
            ),
            Setting(
                "matching.line_number_fallback.release_suffix_reverse",
                "Line numbers: PO 1, 2 = DN/SI 1-1, 2-1",
                "Accept a '-1' release suffix on the delivery note / invoice side.",
                "bool",
                NEXT_SYNC,
            ),
        ),
    ),
    Group(
        "Locks",
        "Protection against two people changing one PO at the same time.",
        (
            Setting(
                "concurrency.po_set_lock_timeout_seconds",
                "Release an abandoned set lock after",
                "A set is locked while someone acts on it. A lock older than this is treated "
                "as abandoned. Must be longer than the worst-case reading of one document.",
                "int",
                LIVE,
                unit="seconds",
            ),
        ),
    ),
    Group(
        "Merge PDFs tool",
        "Limits of the stand-alone Merge PDFs page.",
        (
            Setting(
                "manual_merge.max_files",
                "Most files in one merge",
                "",
                "int",
                LIVE,
                unit="files",
            ),
            Setting(
                "manual_merge.max_pages",
                "Most pages in one merge",
                "",
                "int",
                LIVE,
                unit="pages",
            ),
            Setting(
                "manual_merge.max_total_mb",
                "Largest total upload",
                "The upload size guard is built when the service starts.",
                "int",
                RESTART,
                unit="MB",
            ),
            Setting(
                "manual_merge.sweep_after_hours",
                "Clear leftover temporary files after",
                "Only this tool's own abandoned temporary files are removed.",
                "int",
                LIVE,
                unit="hours",
            ),
            Setting(
                "manual_merge.sweep_on_startup",
                "Clear leftover temporary files at start-up",
                "",
                "bool",
                RESTART,
            ),
        ),
    ),
    Group(
        "Log file",
        "The service log is read by IT when something looks wrong.",
        (
            Setting(
                "logging.level",
                "Detail",
                "INFO is normal. DEBUG writes a lot more.",
                "choice",
                RESTART,
                choices=("DEBUG", "INFO", "WARNING", "ERROR"),
            ),
            Setting(
                "logging.max_file_size_mb",
                "Log file size before a new one starts",
                "",
                "int",
                RESTART,
                unit="MB",
            ),
            Setting(
                "logging.backup_count",
                "Old log files kept",
                "0 keeps one growing file and never deletes.",
                "int",
                RESTART,
            ),
        ),
    ),
)

#: Shown on the page but changed only in config.yaml (by IT) or by a deploy script.
READ_ONLY: tuple[tuple[str, str, str], ...] = (
    ("paths.input_folder", "Input folder", "Where new PDFs are dropped."),
    ("paths.output_folder", "Output folder", "Where merged packets are written."),
    ("paths.quarantine_folder", "Quarantine folder", "Where held documents are copied."),
    ("paths.stored_documents_folder", "Stored documents", "The service's own copies."),
    ("paths.database_path", "Database file", "Moving it needs a data migration."),
    ("paths.log_folder", "Log folder", ""),
    ("server.allowed_hosts", "Allowed host names", "Set by IT; it limits who can reach the page."),
    ("schedule.nightly_enabled", "Midnight sweep", "Changed with scripts/deploy_prefect.py."),
    ("prefect.schedule_timezone", "Midnight sweep time zone", "Changed with the deploy script."),
    ("merge.legal_order", "Order of pages in a packet", "Changed in config.yaml."),
    ("matching.locale", "Number format of documents", "Changed in config.yaml."),
    ("vlm.api_key_env_var", "API key variable", "The key itself is never shown or stored here."),
)

_BY_KEY = {s.key: s for g in GROUPS for s in g.settings}


class SettingsError(Exception):
    """The save could not be completed; `message` is safe to show to an operator."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def config_path() -> Path:
    return Path(os.getenv("AAM_CONFIG_PATH", "config.yaml"))


def file_version(path: Path) -> str:
    """Identifies the exact bytes the form was built from (optimistic concurrency)."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


# ---------------------------------------------------------------------------- reading


def _get(cfg: AppConfig, dotted: str) -> Any:
    value: Any = cfg
    for part in dotted.split("."):
        value = getattr(value, part)
    return value


def _model_bounds(setting: Setting) -> tuple[int | None, int | None]:
    """Minimum and maximum from the config model, overridden by the setting's own."""
    lo, hi = setting.minimum, setting.maximum
    model: Any = AppConfig
    info = None
    for part in setting.key.split("."):
        fields = model.model_fields
        info = fields[part]
        model = info.annotation
    if info is not None:
        for meta in info.metadata:
            if lo is None and getattr(meta, "ge", None) is not None:
                lo = int(meta.ge)
            if hi is None and getattr(meta, "le", None) is not None:
                hi = int(meta.le)
    return lo, hi


def describe(setting: Setting, cfg: AppConfig) -> dict[str, Any]:
    lo, hi = _model_bounds(setting) if setting.kind == "int" else (None, None)
    return {
        "key": setting.key,
        "label": setting.label,
        "help": setting.help,
        "kind": setting.kind,
        "unit": setting.unit,
        "choices": setting.choices,
        "min": lo,
        "max": hi,
        "apply": setting.apply,
        "apply_text": APPLY_TEXT[setting.apply],
        "value": _get(cfg, setting.key),
    }


def _display(value: Any) -> str:
    if isinstance(value, bool):
        return "On" if value else "Off"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if value is None:
        return "—"
    return str(value)


def page_model(cfg: AppConfig) -> dict[str, Any]:
    """Everything the template shows, from the config the service is running on."""
    groups = [
        {
            "title": g.title,
            "blurb": g.blurb,
            "settings": [describe(s, cfg) for s in g.settings],
        }
        for g in GROUPS
    ]
    read_only = [
        {"label": label, "value": _display(_get(cfg, key)), "note": note}
        for key, label, note in READ_ONLY
    ]
    return {
        "groups": groups,
        "read_only": read_only,
        "api_key_name": cfg.vlm.api_key_env_var,
        "api_key_set": bool(os.getenv(cfg.vlm.api_key_env_var)),
    }


def disk_problem(path: Path) -> str | None:
    """Why the file on disk would not start the service, or None if it is fine."""
    try:
        config_module.load_config_file(path, remember=False)
    except Exception as exc:
        return _plain(exc)
    return None


# ---------------------------------------------------------------------------- parsing


def _parse(setting: Setting, raw: str) -> tuple[Any, str | None]:
    """The typed value of a submitted field, or an error message in plain words."""
    text = (raw or "").strip()
    if setting.kind == "bool":
        if text not in ("true", "false"):
            return None, "Choose on or off."
        return text == "true", None
    if setting.kind == "choice":
        if text not in setting.choices:
            return None, "Choose one of: " + ", ".join(setting.choices) + "."
        return text, None
    if setting.kind == "text":
        if not text:
            return None, "This cannot be empty."
        if setting.key == "vlm.model" and not _MODEL_ID.match(text):
            return None, "Use the form provider/name, for example openai/gpt-6-luna."
        return text, None
    if not text:
        return None, "Enter a number."
    if not _INT.match(text):
        return None, "Enter a whole number (digits only)."
    if len(text.lstrip("+-")) > 12:
        return None, "That number is too large."
    value = int(text)
    lo, hi = _model_bounds(setting)
    if lo is not None and value < lo:
        return None, f"Must be at least {lo}."
    if hi is not None and value > hi:
        return None, f"Must be at most {hi}."
    return value, None


def _plain(exc: Exception) -> str:
    """A validation failure as one sentence an operator can act on."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors():
            where = ".".join(str(p) for p in err.get("loc", ()))
            msg = str(err.get("msg", "")).removeprefix("Value error, ")
            parts.append(f"{where}: {msg}" if where else msg)
        return "; ".join(parts) or "the settings are not valid"
    if isinstance(exc, yaml.YAMLError):
        return "the file is not valid YAML"
    return str(exc) or exc.__class__.__name__


# ---------------------------------------------------------------------------- editing text


class _LayoutError(Exception):
    pass


_SECTION = re.compile(r"^\s*[A-Za-z_]\w*:\s*(#.*)?$")
_VALUE = re.compile(
    r"""^(?P<head>\s*[A-Za-z_]\w*:\s+)
        (?P<value>"(?:[^"\\]|\\.)*"|'[^']*'|[^#\s][^#]*?)
        (?P<tail>\s+\#.*|\s*)$""",
    re.VERBOSE,
)


def _yaml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value), ensure_ascii=True)


def _block(lines: list[str], header: int, indent: int) -> tuple[int, int]:
    """First and one-past-last line of the block under `lines[header]` (children are indented
    more than `indent`; blank and comment lines belong to the block when more lines follow)."""
    last_content = header
    for i in range(header + 1, len(lines)):
        raw = lines[i].rstrip("\r\n")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(raw) - len(raw.lstrip()) <= indent:
            break
        last_content = i
    end = last_content + 1
    return header + 1, end


def _set_one(text: str, path: tuple[str, ...], value: Any) -> str:
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    new_value = _yaml_value(value)
    lo, hi = 0, len(lines)
    indent = -1
    for depth, name in enumerate(path):
        want_indent = indent + 2 if depth else 0
        leaf = depth == len(path) - 1
        found = -1
        for i in range(lo, hi):
            raw = lines[i].rstrip("\r\n")
            if raw.strip().startswith("#") or not raw.strip():
                continue
            cur_indent = len(raw) - len(raw.lstrip())
            if cur_indent != want_indent:
                continue
            if raw.lstrip().startswith(f"{name}:"):
                found = i
                break
        if found < 0:
            pad = " " * want_indent
            if leaf:
                insert_at = hi
                while insert_at > lo and not lines[insert_at - 1].strip():
                    insert_at -= 1
                if insert_at > 0 and not lines[insert_at - 1].endswith(("\n", "\r")):
                    lines[insert_at - 1] += nl
                lines.insert(insert_at, f"{pad}{name}: {new_value}{nl}")
            else:
                chain = []
                for extra, part in enumerate(path[depth:-1]):
                    chain.append(f"{' ' * (want_indent + 2 * extra)}{part}:{nl}")
                chain.append(f"{' ' * (want_indent + 2 * len(path[depth:-1]))}{path[-1]}: ")
                chain[-1] += f"{new_value}{nl}"
                insert_at = hi
                if insert_at > 0 and not lines[insert_at - 1].endswith(("\n", "\r")):
                    lines[insert_at - 1] += nl
                if depth == 0 and insert_at > 0 and lines[insert_at - 1].strip():
                    chain.insert(0, nl)
                lines[insert_at:insert_at] = chain
            return "".join(lines)
        raw = lines[found].rstrip("\r\n")
        if leaf:
            m = _VALUE.match(raw)
            if m is None:
                raise _LayoutError(
                    f"{'.'.join(path)} is written in a layout this page cannot edit safely"
                )
            ending = lines[found][len(raw) :]
            lines[found] = f"{m['head']}{new_value}{m['tail']}{ending}"
            return "".join(lines)
        if not _SECTION.match(raw):
            raise _LayoutError(f"{name} is written in a layout this page cannot edit safely")
        lo, hi = _block(lines, found, want_indent)
        indent = want_indent
    raise _LayoutError("empty path")  # pragma: no cover


def apply_changes(text: str, changes: dict[str, Any]) -> str:
    """`text` with every change applied, comments kept; verified by parsing the result."""
    new_text = text
    for key, value in changes.items():
        new_text = _set_one(new_text, tuple(key.split(".")), value)
    try:
        parsed = yaml.safe_load(new_text) or {}
    except yaml.YAMLError as exc:
        raise _LayoutError("the edited file would not be valid YAML") from exc
    for key, value in changes.items():
        node: Any = parsed
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node != value or type(node) is not type(value):
            raise _LayoutError(f"{key} could not be written safely")
    return new_text


# ---------------------------------------------------------------------------- saving


@dataclass
class SaveResult:
    changed: list[dict[str, Any]] = field(default_factory=list)
    backup: str | None = None
    errors: dict[str, str] = field(default_factory=dict)  # field key -> message
    general: str | None = None  # a problem that belongs to no single field

    @property
    def ok(self) -> bool:
        return not self.errors and not self.general


def _replace_with_retry(src: Path, dst: Path) -> None:
    """os.replace, retried briefly: on Windows a virus scan or a reader can hold the file."""
    last: OSError | None = None
    for _ in range(10):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(0.2)
    assert last is not None
    raise last


def _prune(folder: Path) -> None:
    kept = sorted(folder.glob("config-*.yaml"))
    for old in kept[:-KEEP_BACKUPS]:
        with contextlib.suppress(OSError):
            old.unlink()


def _plan(submitted: dict[str, str], cfg: AppConfig) -> tuple[dict[str, Any], dict[str, str]]:
    """Typed changes (only fields whose value differs from the running config) and errors."""
    changes: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, setting in _BY_KEY.items():
        if key not in submitted:
            continue
        value, err = _parse(setting, submitted[key])
        if err:
            errors[key] = err
        elif value != _get(cfg, key):
            changes[key] = value
    return changes, errors


def _blame(exc: ValidationError) -> dict[str, str]:
    """Field-level messages from a model validation failure, where the error names a field."""
    out: dict[str, str] = {}
    for err in exc.errors():
        key = ".".join(str(p) for p in err.get("loc", ()))
        if key in _BY_KEY:
            out[key] = str(err.get("msg", "")).removeprefix("Value error, ")
    return out


def save(
    submitted: dict[str, str],
    *,
    version: str,
    actor: str = "operator",
    path: Path | None = None,
) -> SaveResult:
    """Validate and write the submitted settings. Never raises for bad input; see SaveResult."""
    path = path or config_path()
    result = SaveResult()
    lock = FileLock(str(path) + ".settings.lock")
    try:
        lock.acquire(timeout=LOCK_WAIT_SECONDS)
    except Timeout:
        result.general = "Another save is in progress. Wait a moment and try again."
        return result
    try:
        return _save_locked(submitted, version, actor, path, result)
    except OSError as exc:
        # A read-only file, a full disk or a locked folder: say so, change nothing.
        result.general = (
            "The settings file could not be written "
            f"({exc.strerror or exc.__class__.__name__}). Nothing was changed."
        )
        result.changed = []
        return result
    finally:
        lock.release()


def _save_locked(
    submitted: dict[str, str], version: str, actor: str, path: Path, result: SaveResult
) -> SaveResult:
    """The save itself; the caller holds the file lock and turns an OSError into a message."""
    if version != file_version(path):
        result.general = (
            "The settings file was changed by someone else after you opened this page. "
            "Your changes were not saved. The page now shows the current values."
        )
        return result
    try:
        cfg = config_module.load_config_file(path, remember=False)
    except Exception as exc:
        result.general = (
            "The settings file on disk is not valid, so nothing can be changed here until "
            f"IT fixes it or restores a copy from config_backups ({_plain(exc)})."
        )
        return result
    changes, result.errors = _plan(submitted, cfg)
    if result.errors:
        return result
    if not changes:
        return result

    old_text = path.read_bytes().decode("utf-8")
    try:
        new_text = apply_changes(old_text, changes)
    except _LayoutError as exc:
        result.general = f"{exc}. Ask IT to change it in config.yaml."
        return result

    candidate = path.with_name(f".{path.name}.candidate")
    candidate.write_text(new_text, encoding="utf-8", newline="")
    try:
        try:
            new_cfg = config_module.load_config_file(candidate, remember=False)
        except ValidationError as exc:
            blamed = _blame(exc)
            if blamed:
                result.errors = blamed
            else:
                result.general = _plain(exc)
            return result
        except Exception as exc:
            result.general = f"The new settings are not valid: {_plain(exc)}"
            return result
        for key, value in changes.items():
            # A validator may quietly adjust a value (it raises a lock timeout that is too
            # short). The page must not report a value the service is not running on.
            if _get(new_cfg, key) != value:
                result.errors[key] = (
                    f"Must be at least {_get(new_cfg, key)} with the other settings as they are."
                )
        if result.errors:
            return result

        backups = path.parent / BACKUP_FOLDER_NAME
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        backup = backups / f"config-{stamp}.yaml"
        shutil.copy2(path, backup)
        result.backup = backup.name
        _replace_with_retry(candidate, path)
    finally:
        with contextlib.suppress(OSError):
            candidate.unlink()

    try:
        saved = config_module.load_config_file(path, remember=True)
        for key, value in changes.items():
            if _get(saved, key) != value:
                raise ValueError(f"{key} did not read back as saved")
    except Exception as exc:
        shutil.copy2(backup, candidate)
        _replace_with_retry(candidate, path)
        result.general = (
            "The saved file could not be read back, so the previous settings were "
            f"restored ({_plain(exc)}). Nothing was changed."
        )
        return result

    _prune(backups)
    result.changed = [
        {
            "key": key,
            "label": _BY_KEY[key].label,
            "old": _get(cfg, key),
            "new": value,
            "apply": _BY_KEY[key].apply,
            "apply_text": APPLY_TEXT[_BY_KEY[key].apply],
        }
        for key, value in changes.items()
    ]
    _audit(saved, result, actor)
    return result


def _audit(cfg: AppConfig, result: SaveResult, actor: str) -> None:
    """One audit row per save. A failure here never undoes the (already verified) save."""
    import logging

    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models.models import AuditAction, AuditLog

    try:
        detail = {
            "changes": {c["key"]: {"old": c["old"], "new": c["new"]} for c in result.changed},
            "backup": result.backup,
        }
        with Session(get_engine(cfg)) as s:
            s.add(
                AuditLog(
                    po_set_id=None,
                    action=AuditAction.settings_changed,
                    detail=json.dumps(detail, default=str),
                    source=actor,
                )
            )
            s.commit()
    except Exception:
        logging.getLogger(__name__).warning("Could not write the settings audit row", exc_info=True)
