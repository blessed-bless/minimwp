"""App settings loaded once from the environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    bot_token: str = Field(default="", repr=False)
    api_id: int | None = Field(default=None, validation_alias="TG_API_ID")
    api_hash: str | None = Field(default=None, repr=False, validation_alias="TG_API_HASH")
    crm_user: str = "admin"
    crm_password: str = Field(default="change-me", repr=False)
    database_url: str = Field(
        default="",
        repr=False,
        validation_alias="DATABASE_URL",
    )

    @field_validator("bot_token", "crm_user", "crm_password", "database_url", mode="before")
    @classmethod
    def _strip_str(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("api_id", mode="before")
    @classmethod
    def _empty_api_id_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("api_hash", mode="before")
    @classmethod
    def _empty_hash_to_none(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @property
    def bot_enabled(self) -> bool:
        return bool(self.bot_token)

    @property
    def userbot_enabled(self) -> bool:
        """Userbot (TZ item 2) needs api_id/api_hash from my.telegram.org."""
        return bool(self.api_id and self.api_hash)


@lru_cache
def get_settings() -> Settings:
    default_db = f"sqlite+aiosqlite:///{(BASE_DIR / 'crm.db').as_posix()}"
    settings = Settings()
    if not settings.database_url:
        return settings.model_copy(update={"database_url": default_db})
    return settings


settings = get_settings()
