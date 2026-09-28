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
    unclassified_folder: Path
    database_path: Path
    log_folder: Path


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class VLMConfig(BaseModel):
    provider: str = "openrouter"
    model: str = "openai/gpt-6-luna"
    request_timeout_seconds: int = 60
    api_key_env_var: str = "OPENROUTER_API_KEY"


class ExtractionConfig(BaseModel):
    max_retries: int = Field(ge=1, le=5, default=3)
    retry_backoff_seconds: list[int] = Field(default=[2, 5, 15])


class MatchingConfig(BaseModel):
    fuzzy_description_threshold: int = Field(ge=0, le=100, default=85)
    # v20.5 3-step guards (DECISIONS_LOG §10). sanity: an exact line_no hit whose
    # description scores below this is a wrong-index row, not a real match.
    sanity_description_threshold: int = Field(ge=0, le=100, default=40)
    # fuzzy winner must beat the runner-up by this margin, else ambiguous.
    fuzzy_margin: int = Field(ge=0, le=100, default=5)
    # v20.5 Step 3: unique normalized-SKU rescue for reworded descriptions.
    enable_sku_rescue: bool = True
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
    # Default keeps current behavior: SI→DN→PO→COMBINED→SHIPPING→CUSTOMS.
    legal_order: list[str] = Field(default=["SI", "DN", "PO", "COMBINED", "SHIPPING", "CUSTOMS"])

    @field_validator("legal_order", mode="after")
    @classmethod
    def validate_legal_order(cls, v: list[str]) -> list[str]:
        known = {
            "PO",
            "DN",
            "SI",
            "COMBINED",
            "CUSTOMS",
            "SHIPPING",
            "COMMERCIAL_INVOICE",
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


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_file_size_mb: int = 10
    backup_count: int = 5


class BackupConfig(BaseModel):
    enabled: bool = True
    folder: Path = Path("./data/backup")
    interval_hours: int = 24


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
    logging: LoggingConfig = LoggingConfig()
    backup: BackupConfig = BackupConfig()

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
            "unclassified_folder",
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
