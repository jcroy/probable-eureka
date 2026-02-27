"""Configuration loading and validation for webcollector."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class LLMConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_tokens: int = 2048
    temperature: float = 0.0

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


class DomainOverride(BaseModel):
    rate_limit_rps: float | None = None
    adapter: str | None = None
    rendering_mode: str | None = None  # adaptive | playwright_only | http_only


class CrawlConfig(BaseModel):
    max_depth: int = 3
    max_pages: int = 1000
    max_tasks_per_minute: int = 60
    default_rate_limit_rps: float = 1.0
    download_timeout_seconds: int = 120
    max_retries: int = 3
    max_concurrency: int = 10
    min_concurrency: int = 1
    respect_robots_txt: bool = True
    user_agent: str = "webcollector/0.1 (+https://github.com/yourorg/webcollector)"
    domain_overrides: dict[str, DomainOverride] = Field(default_factory=dict)

    @field_validator("respect_robots_txt")
    @classmethod
    def robots_txt_must_be_true(cls, v: bool) -> bool:
        if not v:
            raise ValueError("respect_robots_txt cannot be set to false")
        return v


class BrowserConfig(BaseModel):
    playwright_pool_size: int = 3
    rendering_mode: str = "adaptive"  # adaptive | playwright_only | http_only
    block_resources: list[str] = Field(default_factory=lambda: ["image", "font", "media"])


class ExtractionConfig(BaseModel):
    pdf_provider: str = "mistral"  # mistral | local
    mistral_api_key_env: str = "MISTRAL_API_KEY"
    mistral_model: str = "mistral-ocr-latest"
    local_ocr_enabled: bool = False
    local_ocr_languages: list[str] = Field(default_factory=lambda: ["eng"])
    llm_metadata: bool = False
    max_text_length: int = 500_000

    @property
    def mistral_api_key(self) -> str | None:
        return os.environ.get(self.mistral_api_key_env)


class StorageConfig(BaseModel):
    backend: str = "sqlite"  # sqlite | postgres
    db_path: str = "./data/webcollector.db"
    file_store_path: str = "./data"


class OutputConfig(BaseModel):
    default_format: str = "jsonl"  # jsonl | csv | sqlite_dump | filesystem


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"  # json | text
    file: str | None = "./logs/webcollector.log"


class WebCollectorConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


CONFIG_FILE_NAMES = ["webcollector.yaml", "webcollector.yml"]
GLOBAL_CONFIG_DIR = Path.home() / ".webcollector"


def find_config_file(start_dir: Path | None = None) -> Path | None:
    """Search for config file in start_dir, then home dir."""
    search_dirs = []
    if start_dir:
        search_dirs.append(Path(start_dir))
    search_dirs.append(Path.cwd())
    search_dirs.append(GLOBAL_CONFIG_DIR)

    for directory in search_dirs:
        for name in CONFIG_FILE_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def load_config(
    config_path: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> WebCollectorConfig:
    """Load config from YAML file, with optional overrides.

    Priority: overrides > config file > defaults.
    """
    data: dict[str, Any] = {}

    # Load from file
    path = config_path or find_config_file()
    if path and path.is_file():
        with open(path) as f:
            file_data = yaml.safe_load(f)
            if isinstance(file_data, dict):
                data = file_data

    # Apply overrides
    if overrides:
        _deep_merge(data, overrides)

    return WebCollectorConfig(**data)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, modifying base in place."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
