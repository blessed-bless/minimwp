"""Разовый вход обычного Telegram-аккаунта для пункта 2 ТЗ.

Запуск:  .venv\\Scripts\\python.exe scripts\\userbot_login.py
Спросит номер и код, создаст userbot.session рядом с проектом.
После этого основной процесс подхватит юзербот сам.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.userbot import SESSION_PATH, build_client  # noqa: E402


async def main() -> int:
    if not settings.userbot_enabled:
        print("Нет TG_API_ID/TG_API_HASH в .env — получите их на my.telegram.org")
        return 1
    client = build_client()
    await client.start()
    me = await client.get_me()
    print(f"Вошли как @{getattr(me, 'username', me.id)}; сессия: {SESSION_PATH}")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
