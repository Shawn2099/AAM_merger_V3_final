"""Core config — SPEC §13. All env-specific values via config.yaml (pydantic-settings + YAML), secrets via .env/env var only."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from app.core.limits import MAX_MANUAL_MERGE_FILES, MAX_MANUAL_MERGE_PAGES, MAX_UPLOAD_BYTES

logger = logging.getLogger(__name__)


class _StrictConfigModel(BaseModel):
    """Base for every config section: an unknown key is an error (audit P2-16).

    `extra="ignore"` meant a typo (`stability_pol_count`, `schedule_timezon`)
    ran the application on the default while the operator believed the file
    had set the value. That is the opposite of SPEC §13.1 fail-fast.
    """

    model_config = ConfigDict(extra="forbid")


class PathsConfig(_StrictConfigModel):
    input_folder: Path
    output_folder: Path
    quarantine_folder: Path
    stored_documents_folder: Path
    database_path: Path
    log_folder: Path
    # Layer-1 split parking for multi-doc PDFs (PLAN Step 4). Parents live here
    # after being cut; never re-scanned (only `input_folder` is scanned).
    combined_folder: Path = Path("./data/combined")
    # Delete is an ARCHIVE action. The exported set (PDF copies, JSON manifests,
    # ARCHIVE.txt) is written here. Optional so existing configs and the
    # many hand-written test configs keep validating; omitted means
    # `<config dir>/data/development/archived_sets`.
    archive_folder: Path = Path("./data/development/archived_sets")
    # `unclassified_folder` was removed 2026-09-29. Unclassified documents
    # are identified by `documents.doc_type == UNKNOWN` and surfaced through
    # the web UI, so no folder was ever written to it.


class ServerConfig(_StrictConfigModel):
    host: str = "0.0.0.0"
    port: int = 8000
    # Hosts accepted in the HTTP `Host` header (audit P1-10). `["*"]` — the
    # default — accepts any host, which is what a LAN deployment behind a
    # changing IP needs. List the real names (`aam.local`, `10.0.0.5`) when the
    # server has a stable address: that closes DNS-rebinding reads.
    allowed_hosts: list[str] = ["*"]


class VLMConfig(_StrictConfigModel):
    # `provider` was removed 2026-09-29: OpenRouter was the only supported
    # provider and nothing read the key, so it could only ever be misleading.
    model: str = "openai/gpt-6-luna"
    # ge=1: a zero/negative timeout makes every extraction fail immediately
    # (audit P2-20).
    request_timeout_seconds: int = Field(default=60, ge=1)
    api_key_env_var: str = "OPENROUTER_API_KEY"
    # Page rendering and reasoning for the validated "150 DPI images + effort high"
    # recipe (prompt_improvement/eval). `extraction._vlm_settings` re-checks them.
    raster_dpi: int = Field(default=150, ge=72, le=300)
    reasoning_effort: Literal["minimal", "low", "medium", "high"] = "high"
    max_pages: int = Field(default=30, ge=1)


class ExtractionConfig(_StrictConfigModel):
    # The number of extraction attempts a document gets before it is
    # permanently failed. `app.services.extraction._attempt_cap` reads this
    # value directly as the cap, so it is *attempts*, not retries-after-the-
    # first-try (audit P2-25). Prefect also uses it as its retry count; the
    # following pass that finds `attempt_count >= cap` writes the terminal
    # `failed` state, which is how a capped document becomes actionable
    # instead of looking like a wait. Values above 5 are refused because each
    # attempt is a paid VLM call on a 2-core host.
    max_retries: int = Field(ge=1, le=5, default=3)
    retry_backoff_seconds: list[int] = Field(default=[2, 5, 15])
    # A document claimed for extraction (pending -> processing) that is still
    # `processing` after this many seconds belonged to a worker that died.
    # It is released back to `pending` and read again on the next pass.
    # Must exceed the worst case of one attempt:
    # vlm.request_timeout_seconds * (max_retries + 1), plus the retry backoff.
    # Enforced by `AppConfig.validate_attempt_budget` below.
    stale_processing_seconds: int = Field(default=900, ge=60)

    # A combined PDF (PO + DN + SI in one file) is quarantined unsplit by default:
    # one operator-visible document, nothing guessed. True re-enables the Layer-1
    # splitter (`services/splitting.py`), which cuts it into child PDFs.
    split_combined: bool = False


class LineNumberFallbackConfig(_StrictConfigModel):
    """Vendor line-number rules tried ONLY when exact matching leaves DN/SI keys unmatched.

    Each switch enables one rule (see `services.matching.resolve_line_mapping`).
    All four off means behaviour identical to exact-only matching.
    """

    step10: bool = True  # PO 10,20,30 vs DN/SI 1,2,3        (rule x10)
    step10_reverse: bool = True  # PO 1,2,3 vs DN/SI 10,20,30        (rule div10)
    release_suffix: bool = True  # PO 1-1,2-1 vs DN/SI 1,2           (rule add_rel)
    release_suffix_reverse: bool = True  # PO 1,2 vs DN/SI 1-1,2-1           (rule strip_rel)

    def rules(self) -> frozenset[str]:
        pairs = (
            (self.step10, "x10"),
            (self.step10_reverse, "div10"),
            (self.release_suffix, "add_rel"),
            (self.release_suffix_reverse, "strip_rel"),
        )
        return frozenset(rule for on, rule in pairs if on)


class MatchingConfig(_StrictConfigModel):
    # `fuzzy_description_threshold` is read by `group_by_line_no` as its
    # description-similarity threshold, used solely for rows that carry no
    # usable line number. `line_number_fallback` holds the per-rule switches
    # for the vendor line-number mapping (x10 / -1 suffix).
    #
    # Removed 2026-09-29 as dead config: `sanity_description_threshold`,
    # `fuzzy_margin` and `enable_sku_rescue` all belonged to the retired v20.5
    # matcher and were read by nothing. They were worse than inert — an
    # `enable_sku_rescue: true` next to a matcher that has no SKU rescue
    # invites someone to "fix" a feature that no longer exists.
    fuzzy_description_threshold: int = Field(ge=0, le=100, default=85)
    locale: str = "en_IN"
    line_number_fallback: LineNumberFallbackConfig = Field(default_factory=LineNumberFallbackConfig)

    @field_validator("locale", mode="after")
    @classmethod
    def validate_locale(cls, v: str) -> str:
        try:
            from babel import Locale

            Locale.parse(v)
        except Exception as e:
            raise ValueError(f"Unsupported babel locale: {v!r}") from e
        return v


class MergeConfig(_StrictConfigModel):
    # Final packet order, editable via config (DECISIONS_LOG §8).
    # Default keeps current behavior: SI→DN→PO→SHIPPING→CUSTOMS.
    legal_order: list[str] = Field(default=["SI", "DN", "PO", "SHIPPING", "CUSTOMS"])

    @field_validator("legal_order", mode="after")
    @classmethod
    def validate_legal_order(cls, v: list[str]) -> list[str]:
        known = {
            "PO",
            "DN",
            "SI",
            "CUSTOMS",
            "SHIPPING",
            "UNKNOWN",
        }
        if not v:
            raise ValueError("merge.legal_order must not be empty")
        unknown = [t for t in v if t not in known]
        if unknown:
            raise ValueError(f"Unknown doc types in merge.legal_order: {unknown}")
        if len(set(v)) != len(v):
            raise ValueError(f"Duplicate doc types in merge.legal_order: {v}")
        return v


class IngestionConfig(_StrictConfigModel):
    # ge=1: a poll count of 0 raised IndexError and a negative interval called
    # sleep(-1) at ingestion time (audit P2-20). Fail at startup instead.
    stability_poll_interval_seconds: int = Field(default=2, ge=1)
    stability_poll_count: int = Field(default=2, ge=1)


class PrefectConfig(_StrictConfigModel):
    work_pool_name: str = "aam-merger-process-pool"
    max_concurrent_extraction_tasks: int = Field(default=3, ge=1, le=10)
    # IANA zone for the midnight sync (e.g. "Asia/Kolkata"). The deploy script
    # falls back to Asia/Kolkata when this is unset (the office zone), NOT to
    # Prefect's UTC default — an unset key must not run at 05:30 local.
    schedule_timezone: str | None = None

    @field_validator("schedule_timezone", mode="after")
    @classmethod
    def validate_schedule_timezone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(v)
        except Exception as e:
            raise ValueError(f"Unknown timezone for prefect.schedule_timezone: {v!r}") from e
        return v


class ScheduleConfig(_StrictConfigModel):
    """When the pipeline runs on its own (D5).

    The poller is a lightweight thread inside the web service: every
    `poll_interval_seconds` it looks for input files it has not seen (a folder
    listing, no API calls) and starts a sync when there are any. A sync that is
    already running is never interrupted or skipped: one more pass is queued and
    starts when it ends (one pass takes every waiting file, oldest first).
    """

    poll_enabled: bool = False
    poll_interval_seconds: int = Field(default=60, ge=15, le=3600)
    #: Keep the midnight sweep (re-reconcile all open sets, retry customs appends).
    #: Read by `scripts/deploy_prefect.py`; re-run it after changing this.
    nightly_enabled: bool = True
    #: A set started by the poller merges only when its NEWEST file is at least this
    #: old, so documents of one PO that arrive minutes apart are not merged early.
    #: 0 turns it off. Manual Scan, Merge Now and the nightly run never wait.
    merge_settle_minutes: int = Field(default=5, ge=0, le=240)


class ConcurrencyConfig(_StrictConfigModel):
    #: A per-PO lock older than this is treated as abandoned and cleared by the next dashboard
    #: poll. Must exceed one document's worst-case extraction (see AppConfig.validate_attempt_budget).
    po_set_lock_timeout_seconds: int = Field(default=900, ge=60, le=86400)


class ManualMergeConfig(_StrictConfigModel):
    """Limits and staging for the standalone Merge PDFs tool (FR-14.11-14.13).

    The browser reads `max_files`/`max_total_mb` from the page, so changing
    these values changes the UI copy and the server checks together (restart
    required: the request-body guard is built at app startup).
    """

    max_files: int = Field(default=MAX_MANUAL_MERGE_FILES, ge=1, le=100)
    max_total_mb: int = Field(default=MAX_UPLOAD_BYTES // (1024 * 1024), ge=1, le=1024)
    max_pages: int = Field(default=MAX_MANUAL_MERGE_PAGES, ge=1, le=100_000)
    #: Where uploads are staged. `None` means `<database folder>/manual_merge_tmp`.
    #: Must not be one of the pipeline data folders; the app refuses to start
    #: if it is (the sweep must never see a real document).
    temp_folder: Path | None = None
    #: Delete our own abandoned `mm-*.pdf` staging files older than this.
    sweep_after_hours: int = Field(default=6, ge=1, le=720)
    sweep_on_startup: bool = True

    @property
    def max_total_bytes(self) -> int:
        return self.max_total_mb * 1024 * 1024

    @property
    def max_request_bytes(self) -> int:
        """Whole multipart body: the files plus ~2 MB of boundaries/fields."""
        return self.max_total_bytes + (2 * 1024 * 1024)

    @field_validator("temp_folder", mode="after")
    @classmethod
    def _temp_folder_must_be_a_directory(cls, value: Path | None) -> Path | None:
        if value is not None and value.exists() and not value.is_dir():
            raise ValueError(f"manual_merge.temp_folder is not a directory: {value}")
        return value


class ReconciliationConfig(_StrictConfigModel):
    # When true, a PO Set carrying more than one PO document is held pending
    # for a human. It is not summed and not auto-merged: the engine cannot tell
    # a page fragment from a re-issue.
    single_po_document: bool = True


class LoggingConfig(_StrictConfigModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_file_size_mb: int = 10
    backup_count: int = 5


class AppConfig(BaseSettings):
    paths: PathsConfig
    server: ServerConfig = ServerConfig()
    vlm: VLMConfig = VLMConfig()
    extraction: ExtractionConfig = ExtractionConfig()
    matching: MatchingConfig = MatchingConfig()
    merge: MergeConfig = MergeConfig()
    ingestion: IngestionConfig = IngestionConfig()
    prefect: PrefectConfig = PrefectConfig()
    concurrency: ConcurrencyConfig = ConcurrencyConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    manual_merge: ManualMergeConfig = ManualMergeConfig()
    reconciliation: ReconciliationConfig = ReconciliationConfig()
    logging: LoggingConfig = LoggingConfig()

    # Audit P2-16: an unknown *top-level* key (e.g. `pathz:`) is an error too.
    # The YAML file is the only settings source — see
    # `settings_customise_sources` — so `extra` never sees `.env` secrets.
    model_config = SettingsConfigDict(extra="forbid")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Read config from the validated YAML dict only (SPEC §13.1).

        With `extra="forbid"`, letting the env/dotenv sources contribute
        would make every secret in `.env` (`OPENROUTER_API_KEY`) look like a
        typo and refuse startup. Secrets are read with `os.getenv` by
        `load_env` / the VLM client; they are not config fields.
        """
        return (init_settings,)

    @model_validator(mode="after")
    def validate_attempt_budget(self) -> AppConfig:
        """A live extraction must not be reclaimed as stale (audit P2-20).

        `stale_processing_seconds` releases a `processing` document back to
        `pending`. If the worst-case single attempt can outlive it, the
        recovery sweep reclaims a document that is still being read and the
        VLM is called twice for the same PDF (double line items).
        """
        attempts = self.extraction.max_retries + 1
        worst_case_seconds = self.vlm.request_timeout_seconds * attempts + sum(
            self.extraction.retry_backoff_seconds
        )
        if self.extraction.stale_processing_seconds <= worst_case_seconds:
            raise ValueError(
                "extraction.stale_processing_seconds "
                f"({self.extraction.stale_processing_seconds}s) must exceed the "
                f"worst-case extraction ({worst_case_seconds}s = "
                "vlm.request_timeout_seconds * (extraction.max_retries + 1) + "
                "sum(extraction.retry_backoff_seconds)); otherwise a live "
                "extraction is reclaimed as stale and the PDF is read twice"
            )
        if self.concurrency.po_set_lock_timeout_seconds <= worst_case_seconds:
            # An older config (the default used to be 300 s) must keep working after an
            # upgrade, so the value is raised instead of refusing to start. A lock cleared
            # mid-action would let a second operator change the same set.
            safe = worst_case_seconds + 60
            logging.getLogger(__name__).warning(
                "concurrency.po_set_lock_timeout_seconds (%ss) is shorter than the worst-case "
                "extraction of one document (%ss); using %ss. Set it to at least that in "
                "config.yaml.",
                self.concurrency.po_set_lock_timeout_seconds,
                worst_case_seconds,
                safe,
            )
            self.concurrency.po_set_lock_timeout_seconds = safe
        return self

    @field_validator("paths", mode="after")
    @classmethod
    def validate_paths(cls, v: PathsConfig) -> PathsConfig:
        import logging

        logger = logging.getLogger(__name__)
        for field_name in [
            "input_folder",
            "output_folder",
            "quarantine_folder",
            "stored_documents_folder",
            "combined_folder",
            "archive_folder",
            "log_folder",
        ]:
            p = getattr(v, field_name, None)
            if isinstance(p, Path) and not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                logger.info("Initialized required path: %s", p)
        if v.database_path and isinstance(v.database_path, Path):
            v.database_path.parent.mkdir(parents=True, exist_ok=True)
        return v

    @model_validator(mode="after")
    def validate_data_folders_are_not_nested_in_input(self) -> AppConfig:
        """The input folder is scanned recursively and cleared after a merge.

        A stored, output, quarantine, combined, archive or log folder inside it (or the input
        folder inside one of them) would put the system's own durable copies in the scan.
        """
        inp = Path(self.paths.input_folder).resolve()
        for label, raw in (
            ("output_folder", self.paths.output_folder),
            ("quarantine_folder", self.paths.quarantine_folder),
            ("stored_documents_folder", self.paths.stored_documents_folder),
            ("combined_folder", self.paths.combined_folder),
            ("archive_folder", self.paths.archive_folder),
            ("log_folder", self.paths.log_folder),
        ):
            other = Path(raw).resolve()
            if other == inp or inp in other.parents or other in inp.parents:
                raise ValueError(
                    f"paths.{label} ({other}) must be separate from paths.input_folder ({inp}): "
                    "the input folder is scanned and cleared, so nothing else may live inside it"
                )
        return self

    @model_validator(mode="after")
    def validate_manual_merge_temp_folder(self) -> AppConfig:
        """The sweep must never be able to see a pipeline document.

        The sweep only deletes `mm-*.pdf` files, but a misconfigured folder
        that is (or contains) a real data folder is still a configuration the
        app should refuse rather than trust.
        """
        folder = self.manual_merge.temp_folder
        if folder is None:
            return self
        target = Path(folder).resolve()
        for raw in (
            self.paths.input_folder,
            self.paths.output_folder,
            self.paths.quarantine_folder,
            self.paths.stored_documents_folder,
            self.paths.combined_folder,
            self.paths.archive_folder,
            self.paths.log_folder,
        ):
            other = Path(raw).resolve()
            if target == other or other in target.parents or target in other.parents:
                raise ValueError(
                    "manual_merge.temp_folder "
                    f"({target}) must be separate from the pipeline folder {other}"
                )
        return self


def load_env(cfg_path: Path | None = None) -> None:
    """Load the `.env` file(s) so `os.getenv` sees the secrets in them.

    Secrets live in `.env` or in the process environment — never in YAML
    (SPEC §13.1). The service account sets the variable in the environment;
    a developer sets it in `.env` beside the config.

    `override=False`: an environment variable that is already set wins, so a
    deployment's own environment is never overwritten by a file that happens
    to sit next to the config.

    The module is imported as `dotenv` and called as `dotenv.load_dotenv(...)`
    rather than `from dotenv import load_dotenv`, so that a test which
    replaces `dotenv.load_dotenv` (the suite must never see the developer's
    real key) still replaces the call this function makes.
    """
    try:
        import dotenv
    except ImportError:  # pragma: no cover - dotenv is a dependency
        return
    candidates = []
    if cfg_path is not None:
        candidates.append(Path(cfg_path).parent / ".env")
    candidates.append(Path(__file__).resolve().parents[3] / ".env")
    candidates.append(Path(".env"))
    for candidate in candidates:
        try:
            if candidate.exists():
                dotenv.load_dotenv(candidate, override=False)
        except Exception:  # pragma: no cover - a broken .env must not stop boot
            logger.warning("Could not read %s", candidate, exc_info=True)


_CONFIG_CACHE: dict[tuple[str, int, int, int, str], AppConfig] = {}


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load YAML config + .env. SPEC §13.1 — validated at startup, fail fast.

    The dashboard polls every 2 s and each route used to re-read the YAML and
    re-run six `mkdir`s (audit P2-24). Parsed configs are cached by the config
    file's stat signature and the API-key presence; callers get a deep copy so
    a test or a route that adjusts a path cannot poison the cache.

    `paths.combined_folder` is optional: when the YAML omits it, the default
    is `<config dir>/data/combined`. Every relative `paths:` value is resolved
    against **the config file's directory**, not the process working directory.
    The Windows service starts in `C:\\Windows\\System32`, so a CWD-relative
    `./data/output` parked real output in a system folder (audit P2-16).
    """
    cfg_path = Path(path or os.getenv("AAM_CONFIG_PATH", "config.yaml"))
    if not cfg_path.exists():
        # SPEC §13.1: refuse to start — never silently fall back to the
        # committed example (dev paths/credentials would run in prod).
        raise FileNotFoundError(
            f"Config not found: {cfg_path} — copy config.example.yaml to "
            f"{cfg_path} and adjust paths for this host"
        )
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    try:
        stat = cfg_path.stat()
        env_path = cfg_path.resolve().parent / ".env"
        env_stat = env_path.stat() if env_path.exists() else None
        cache_key = (
            str(cfg_path.resolve()),
            stat.st_mtime_ns,
            stat.st_size,
            env_stat.st_mtime_ns if env_stat else 0,
            os.getenv("OPENROUTER_API_KEY") or "",
        )
    except OSError:
        cache_key = None
    if cache_key is not None and cache_key in _CONFIG_CACHE:
        return _CONFIG_CACHE[cache_key].model_copy(deep=True)
    paths_raw = data.get("paths")
    base_dir = cfg_path.resolve().parent
    if isinstance(paths_raw, dict):
        # Relative paths are relative to the CONFIG FILE, not the process
        # working directory. Under NSSM that cwd is C:\Windows\System32, so a
        # `./data/output` written in YAML would otherwise create output in a
        # system folder. Absolute paths (the production C:\AAM form) are left
        # exactly as written.
        for key, value in list(paths_raw.items()):
            if isinstance(value, str) and value and not Path(value).is_absolute():
                paths_raw[key] = str((base_dir / value).resolve())
        if "combined_folder" not in paths_raw:
            paths_raw["combined_folder"] = str(base_dir / "data" / "combined")
        if "archive_folder" not in paths_raw:
            paths_raw["archive_folder"] = str(base_dir / "data" / "development" / "archived_sets")

    mm_raw = data.get("manual_merge")
    if isinstance(mm_raw, dict):
        temp = mm_raw.get("temp_folder")
        if isinstance(temp, str):
            if temp.strip():
                if not Path(temp).is_absolute():
                    mm_raw["temp_folder"] = str((base_dir / temp).resolve())
            else:
                mm_raw["temp_folder"] = None
    cfg = AppConfig.model_validate(data)
    # Secrets are in .env or in the environment; load the file so the check
    # below sees the same key the VLM client will use. Without this the app
    # announced "extraction will fail closed" on every single config load
    # while extraction was in fact working — a false statement to the
    # operator, printed once per file per sync.
    load_env(cfg_path)
    api_key = os.getenv(cfg.vlm.api_key_env_var)
    if not api_key:
        logger.warning(
            "%s not set — VLM extraction will fail closed; set it in .env or env var",
            cfg.vlm.api_key_env_var,
        )
    if cache_key is not None:
        _CONFIG_CACHE.clear()
        _CONFIG_CACHE[cache_key] = cfg
    return cfg.model_copy(deep=True)
