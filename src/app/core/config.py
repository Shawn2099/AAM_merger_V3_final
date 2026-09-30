"""Core config — SPEC §13. All env-specific values via config.yaml (pydantic-settings + YAML), secrets via .env/env var only."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class PathsConfig(BaseModel):
    input_folder: Path
    output_folder: Path
    quarantine_folder: Path
    stored_documents_folder: Path
    database_path: Path
    log_folder: Path
    # Layer-1 split parking for multi-doc PDFs (PLAN Step 4). Parents live here
    # after being cut; never re-scanned (only `input_folder` is scanned).
    combined_folder: Path = Path("./data/combined")
    # `unclassified_folder` was removed 2026-09-29. Unclassified documents
    # are identified by `documents.doc_type == UNKNOWN` and surfaced through
    # the web UI, so no folder was ever written to it.


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class VLMConfig(BaseModel):
    # `provider` was removed 2026-09-29: OpenRouter was the only supported
    # provider and nothing read the key, so it could only ever be misleading.
    model: str = "openai/gpt-6-luna"
    request_timeout_seconds: int = 60
    api_key_env_var: str = "OPENROUTER_API_KEY"


class ExtractionConfig(BaseModel):
    max_retries: int = Field(ge=1, le=5, default=3)
    retry_backoff_seconds: list[int] = Field(default=[2, 5, 15])


class MatchingConfig(BaseModel):
    # The only matching knob that is read. `group_by_line_no` takes this as
    # its description-similarity threshold, used solely for rows that carry
    # no usable line number.
    #
    # Removed 2026-09-29 as dead config: `sanity_description_threshold`,
    # `fuzzy_margin` and `enable_sku_rescue` all belonged to the retired v20.5
    # matcher and were read by nothing. They were worse than inert — an
    # `enable_sku_rescue: true` next to a matcher that has no SKU rescue
    # invites someone to "fix" a feature that no longer exists.
    fuzzy_description_threshold: int = Field(ge=0, le=100, default=85)
    locale: str = "en_IN"

    @field_validator("locale", mode="after")
    @classmethod
    def validate_locale(cls, v: str) -> str:
        try:
            from babel import Locale

            Locale.parse(v)
        except Exception as e:
            raise ValueError(f"Unsupported babel locale: {v!r}") from e
        return v


class MergeConfig(BaseModel):
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


class IngestionConfig(BaseModel):
    stability_poll_interval_seconds: int = 2
    stability_poll_count: int = 2


class PrefectConfig(BaseModel):
    work_pool_name: str = "aam-merger-process-pool"
    max_concurrent_extraction_tasks: int = Field(default=3, ge=1, le=10)


class ConcurrencyConfig(BaseModel):
    po_set_lock_timeout_seconds: int = 300


class ReconciliationConfig(BaseModel):
    # When true, a PO Set carrying more than one PO document is quarantined
    # instead of summing them. A PO Set is expected to hold exactly one PO; a
    # second one usually means a re-issue or a transcription of the same PO
    # number, and summing two POs into one baseline silently doubles every
    # quantity, which then never reconciles.
    single_po_document: bool = True


class LoggingConfig(BaseModel):
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
    reconciliation: ReconciliationConfig = ReconciliationConfig()
    logging: LoggingConfig = LoggingConfig()

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
            "log_folder",
        ]:
            p = getattr(v, field_name, None)
            if isinstance(p, Path) and not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                logger.info("Initialized required path: %s", p)
        if v.database_path and isinstance(v.database_path, Path):
            v.database_path.parent.mkdir(parents=True, exist_ok=True)
        return v


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load YAML config + .env. SPEC §13.1 — validated at startup, fail fast."""
    cfg_path = Path(path or os.getenv("AAM_CONFIG_PATH", "config.yaml"))
    if not cfg_path.exists():
        # SPEC §13.1: refuse to start — never silently fall back to the
        # committed example (dev paths/credentials would run in prod).
        raise FileNotFoundError(
            f"Config not found: {cfg_path} — copy config.example.yaml to "
            f"{cfg_path} and adjust paths for this host"
        )
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cfg = AppConfig.model_validate(data)
    # secrets never in YAML — resolve via env
    api_key = os.getenv(cfg.vlm.api_key_env_var)
    if not api_key:
        logger.warning(
            "%s not set — VLM extraction will fail closed; set it in .env or env var",
            cfg.vlm.api_key_env_var,
        )
    return cfg
