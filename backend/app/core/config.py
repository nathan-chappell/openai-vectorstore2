from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, cast
from urllib.parse import urlparse

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class AppSettings(BaseSettings):
    """Runtime settings for the app, ChatKit, MCP, storage, and OpenAI integrations."""

    openai_api_key: SecretStr = Field(init=False)
    app_signing_secret: SecretStr = Field(init=False)
    clerk_secret_key: SecretStr | None = None
    clerk_issuer_url: AnyHttpUrl | None = None

    app_base_url: AnyHttpUrl = cast(AnyHttpUrl, "http://localhost:8000")
    app_name: str = "openai-vectorstore2"
    database_url: str = "sqlite+aiosqlite:///./.local/openai-vectorstore2.db"
    database_schema_mode: Literal["create_all", "migrations"] = "migrations"
    static_dir: str = "frontend/dist"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    allow_local_dev_auth: bool = True

    clerk_active_metadata_key: str = "active"
    clerk_role_metadata_key: str = "role"
    clerk_clock_skew_ms: int = 5_000
    clerk_authorized_parties: Annotated[list[str], NoDecode] = Field(default_factory=list)
    mcp_required_scopes: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["openid", "email", "profile"])

    storage_backend: Literal["local", "s3"] = "local"
    local_storage_dir: str = ".local/storage"
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_region: str = "auto"
    s3_url_style: Literal["path", "virtual"] = "path"
    storage_upload_url_ttl_seconds: int = 900
    storage_download_url_ttl_seconds: int = 900

    openai_agent_model: str = "gpt-5.5"
    openai_fast_model: str = "gpt-5.4-mini"
    openai_image_generation_model: str = "gpt-image-2"
    openai_speech_model: str = "gpt-4o-mini-tts"
    openai_transcription_model: str = "gpt-4o-transcribe-diarize"
    openai_poll_interval_ms: int = 1_000
    openai_default_voice: str = "alloy"
    openai_context_compact_threshold: int | None = 80_000

    semantic_split_pdf_batch_pages: int = 25
    semantic_split_text_batch_lines: int = 2_000
    semantic_chunk_max_search_results: int = 12
    research_import_max_depth: int = 2
    research_import_max_candidates_per_source: int = 8
    research_import_max_pending_candidates: int = 40
    research_import_fetch_timeout_seconds: float = 15.0
    research_import_max_fetch_bytes: int = 12_000_000
    research_import_max_text_chars: int = 120_000
    research_import_user_agent: str = "openai-vectorstore2-research-importer/0.1"
    task_runner_max_concurrency: int = 1
    mcp_client_session_timeout_seconds: float = 60.0

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_file_path: str | None = ".local/logs/openai-vectorstore2.log"
    log_file_max_bytes: int = 5_000_000
    log_file_backup_count: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("cors_origins", "clerk_authorized_parties", "mcp_required_scopes", mode="before")
    @classmethod
    def _parse_string_list(cls, raw_value: object) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, list):
            return [str(item).strip() for item in raw_value if str(item).strip()]
        if isinstance(raw_value, str):
            return [part.strip() for part in raw_value.split(",") if part.strip()]
        raise TypeError("Expected a comma-separated string or list.")

    @field_validator(
        "clerk_secret_key",
        "clerk_issuer_url",
        "s3_endpoint",
        "s3_bucket",
        "s3_access_key_id",
        "s3_secret_access_key",
        "log_file_path",
        "openai_context_compact_threshold",
        mode="before",
    )
    @classmethod
    def _empty_optional_string_as_none(cls, raw_value: object) -> object:
        if isinstance(raw_value, str) and not raw_value.strip():
            return None
        return raw_value

    @property
    def normalized_app_base_url(self) -> str:
        return str(self.app_base_url).rstrip("/")

    @property
    def normalized_static_dir(self) -> str:
        candidate = Path(self.static_dir).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        return str(candidate)

    @property
    def normalized_local_storage_dir(self) -> Path:
        candidate = Path(self.local_storage_dir).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        return candidate

    @property
    def normalized_log_file_path(self) -> Path | None:
        if self.log_file_path is None:
            return None
        normalized_value = self.log_file_path.strip()
        if not normalized_value:
            return None
        candidate = Path(normalized_value).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        return candidate

    @property
    def normalized_database_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self.database_url

    @property
    def sync_database_url(self) -> str:
        database_url = self.normalized_database_url
        if database_url.startswith("postgresql+asyncpg://"):
            return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
        if database_url.startswith("sqlite+aiosqlite://"):
            return database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
        return database_url

    @property
    def effective_clerk_domain(self) -> str | None:
        if self.clerk_issuer_url is None:
            return None
        parsed = urlparse(str(self.clerk_issuer_url))
        return parsed.netloc or None


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()
