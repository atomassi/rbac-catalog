"""Application configuration via Pydantic Settings."""

from __future__ import annotations

import logging
import os
from typing import Annotated, Final

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from rbaccatalog.core.singleton import ThreadSafeSingleton

logger = logging.getLogger(__name__)

_VALID_ENVIRONMENTS: Final = frozenset({"production", "staging", "ppe"})
_VALID_AI_ENGINES: Final = frozenset(
    {"tfidf", "llm", "rag", "hybrid", "semantic", "crossencoder", "hyde"}
)


def is_running_in_pytest() -> bool:
    """Check if running inside pytest."""
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Field-to-env-var mapping is automatic: ``db_connection_string`` reads
    ``DB_CONNECTION_STRING``. Fields whose env name doesn't match the
    snake-case attribute use ``Field(alias="EXPLICIT_ENV_NAME")``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
        # Treat empty env vars as unset (matches the prior loader).
        env_ignore_empty=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Skip the dotenv source under pytest so a stray local ``.env``
        # can't leak into tests.
        if is_running_in_pytest():
            return (init_settings, env_settings, file_secret_settings)
        return (init_settings, env_settings, dotenv_settings, file_secret_settings)

    # --- Scan / worker ------------------------------------------------------
    roles_poll_interval_seconds: int = Field(default=7200, gt=0)
    operations_poll_interval_seconds: int = Field(default=86400, gt=0)
    role_scan_enabled: bool = True
    operations_scan_enabled: bool = True
    # Env name doesn't match field name.
    run_roles_scan_on_startup: bool = Field(default=True, alias="RUN_SCAN_ON_STARTUP")
    run_operations_scan_on_startup: bool = True

    # --- Database -----------------------------------------------------------
    db_connection_string: str = "sqlite+aiosqlite:///./rbaccatalog.db"
    use_managed_identity: bool = False
    msi_db_host: str = ""
    msi_db_port: int = 5432
    msi_db_name: str = ""
    msi_db_user: str = ""

    # --- Ollama -------------------------------------------------------------
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen-rbac-v5"

    # --- Misc ---------------------------------------------------------------
    log_level: str = "INFO"
    db_rebuild_interval_seconds: int = Field(default=21600, gt=0)
    enable_embeddings_in_tests: bool = Field(
        default=False, alias="AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS"
    )
    app_insights_connection_string: str = Field(
        default="", alias="APPLICATIONINSIGHTS_CONNECTION_STRING"
    )
    environment_name: str = Field(default="local", alias="APP_ENVIRONMENT_NAME")
    mcp_server_enabled: bool = True
    # Decommission notice toggle. Defaults to off; flip on per-environment
    # via the ``DECOMMISSION_BANNER_ENABLED`` env var (typically as an App
    # Service / App Insights application setting). See
    # ``rbaccatalog/web/constants.py`` for the message + version constants.
    decommission_banner_enabled: bool = False

    # ``NoDecode`` keeps pydantic-settings from JSON-parsing the env value
    # before our ``_split_engines`` validator gets to do CSV/SCSV parsing.
    enabled_ai_engines: Annotated[list[str], NoDecode] = Field(
        default=["crossencoder", "semantic", "llm", "rag", "hyde", "tfidf"],
        description="AI recommendation engines to expose in the UI.",
    )

    # --- Validators ---------------------------------------------------------
    @field_validator("environment_name", mode="after")
    @classmethod
    def _normalize_environment(cls, v: str) -> str:
        v = v.lower().strip()
        return v if v in _VALID_ENVIRONMENTS else "local"

    @field_validator("log_level", mode="after")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _drop_app_insights_when_local(self) -> Settings:
        # App Insights is only used in deployed envs — drop it when local
        # so tests/local runs don't accidentally ship telemetry. Done as a
        # model-level validator so that `environment_name` is guaranteed to
        # be already validated/normalized by the time we read it.
        if self.environment_name not in _VALID_ENVIRONMENTS:
            self.app_insights_connection_string = ""
        return self

    @field_validator("enabled_ai_engines", mode="before")
    @classmethod
    def _split_engines(cls, v: object) -> object:
        # Accept "tfidf,semantic" or "tfidf;semantic" from env, or a list in code.
        if isinstance(v, str):
            return [e.strip() for e in v.replace(";", ",").split(",") if e.strip()]
        return v

    @field_validator("enabled_ai_engines", mode="after")
    @classmethod
    def _validate_engines(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        valid: list[str] = []
        invalid: list[str] = []
        for raw in v:
            e = raw.strip().lower()
            if not e or e in seen:
                continue
            seen.add(e)
            (valid if e in _VALID_AI_ENGINES else invalid).append(e)
        if invalid:
            logger.warning("Ignoring unknown AI engine(s): %s", ", ".join(invalid))
        return valid or ["tfidf"]

    @property
    def is_deployed(self) -> bool:
        """Check if running in any deployed environment (not local)."""
        return self.environment_name in _VALID_ENVIRONMENTS

    @classmethod
    def reset(cls) -> None:
        _settings.reset()

    @classmethod
    def get(cls) -> Settings:
        return _settings.get()


_settings: ThreadSafeSingleton[Settings] = ThreadSafeSingleton(factory=Settings)
