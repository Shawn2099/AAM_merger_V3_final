"""Core config — SPEC §13. All env-specific values via config.yaml (pydantic-settings + YAML), secrets via .env/env var only."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    model: str = "openai/gpt-4o"
    request_timeout_seconds: int = 60
    api_key_env_var: str = "OPENROUTER_API_KEY"


class ExtractionConfig(BaseModel):
    max_retries: int = Field(ge=1, le=5, default=3)
    retry_backoff_seconds: list[int] = Field(default=[2, 5, 15])


class MatchingConfig(BaseModel):
    fuzzy_description_threshold: int = Field(ge=0, le=100, default=85)


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
        # allow example fallback in dev
        alt = Path("config.example.yaml")
        if alt.exists():
            cfg_path = alt
        else:
            raise FileNotFoundError(f"Config not found: {cfg_path}")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cfg = AppConfig.model_validate(data)
    # secrets never in YAML — resolve via env
    api_key = os.getenv(cfg.vlm.api_key_env_var)
    if not api_key:
        # not fatal at import time, but warn for extraction
        pass
    return cfg
