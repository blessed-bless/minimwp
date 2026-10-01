"""Настройки приложения: читаются из .env один раз при старте."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    api_id: int | None
    api_hash: str | None = field(repr=False)
    crm_user: str
    crm_password: str = field(repr=False)
    database_url: str = field(repr=False)

    @property
    def bot_enabled(self) -> bool:
        return bool(self.bot_token)

    @property
    def userbot_enabled(self) -> bool:
        """Пункт 2 ТЗ работает только при наличии api_id/api_hash с my.telegram.org."""
        return bool(self.api_id and self.api_hash)


def _int_or_none(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def get_settings() -> Settings:
    default_db = f"sqlite+aiosqlite:///{(BASE_DIR / 'crm.db').as_posix()}"
    return Settings(
        bot_token=(os.getenv("BOT_TOKEN") or "").strip(),
        api_id=_int_or_none(os.getenv("TG_API_ID")),
        api_hash=(os.getenv("TG_API_HASH") or "").strip() or None,
        crm_user=(os.getenv("CRM_USER") or "admin").strip(),
        crm_password=(os.getenv("CRM_PASSWORD") or "change-me").strip(),
        database_url=(os.getenv("DATABASE_URL") or "").strip() or default_db,
    )


settings = get_settings()
