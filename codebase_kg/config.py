"""Environment-backed settings.

Secrets use :class:`pydantic.SecretStr` so accidental ``repr`` output does not
reveal them. The OpenAI key is read from ``OPENAI_API_KEY`` or
``CKG_OPENAI_API_KEY``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the CLI, API, Neo4j, and OpenAI clients."""

    model_config = SettingsConfigDict(
        env_prefix="CKG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("please-change-me")
    neo4j_database: str = "neo4j"

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CKG_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = 1536
    openai_chat_model: str = "gpt-4o-mini"

    allowed_roots: str = ""
    log_level: str = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    vector_min_score: float = 0.20

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def _blank_api_key(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @field_validator("openai_embedding_dimensions")
    @classmethod
    def _dimensions(cls, value: int) -> int:
        if not 1 <= value <= 4096:
            raise ValueError("embedding dimensions must be between 1 and 4096")
        return value

    def parsed_allowed_roots(self) -> list[Path]:
        """Return configured API index roots as resolved paths.

        An empty configuration means the API refuses every index request until
        ``CKG_ALLOWED_ROOTS`` is set. The CLI does not consult this list.
        """

        roots: list[Path] = []
        for part in self.allowed_roots.split(","):
            item = part.strip()
            if item:
                roots.append(Path(item).expanduser().resolve())
        return roots

    def openai_key_value(self) -> str | None:
        """Return the OpenAI key, or ``None`` when Q&A is not configured."""

        if self.openai_api_key is None:
            return None
        value = self.openai_api_key.get_secret_value().strip()
        return value or None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        """Prefer explicit constructor arguments over the environment."""

        return init_settings, env_settings, dotenv_settings, file_secret_settings

    def secret_values(self) -> list[str]:
        """Return secret strings that must be stripped from logs and errors."""

        values = [self.neo4j_password.get_secret_value()]
        key = self.openai_key_value()
        if key:
            values.append(key)
        return [item for item in values if item]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached process settings."""

    return Settings()
