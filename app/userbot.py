"""Пункт 2 ТЗ: входящие в обычный Telegram-аккаунт становятся лидами.

Telethon работает от имени живого аккаунта, поэтому нужны api_id/api_hash
с my.telegram.org и один раз — вход по номеру (scripts/userbot_login.py).
Без этих ключей модуль не стартует, остальная CRM работает как обычно.

Логика простая: один собеседник = один лид. Первое сообщение заводит лид
с источником telegram_dm, последующие дописываются в его запрос.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telethon import TelegramClient
    from telethon.events import NewMessage

from app.config import BASE_DIR, settings
from app.crud import append_to_request, create_lead, get_lead_by_chat
from app.db import SessionLocal
from app.models import LeadSource

log = logging.getLogger("crm.userbot")

SESSION_PATH = BASE_DIR / "userbot.session"


def build_client() -> TelegramClient:
    """Создаёт Telethon-клиент. Импорт внутри, чтобы не тянуть его без нужды."""
    from telethon import TelegramClient

    return TelegramClient(str(SESSION_PATH), settings.api_id, settings.api_hash)


async def run() -> None:
    if not settings.userbot_enabled:
        log.info("юзербот выключен: нет TG_API_ID/TG_API_HASH")
        return

    from telethon import events

    client = build_client()
    # Не client.start(): он при отсутствии сессии спросит номер и код через input(),
    # а фоновый процесс сервера некому об этом спросить — он просто зависнет.
    await client.connect()
    if not await client.is_user_authorized():
        log.warning("нет авторизованной сессии, сначала запустите scripts/userbot_login.py")
        await client.disconnect()
        return
    me = await client.get_me()
    log.info("юзербот запущен как @%s", getattr(me, "username", None) or getattr(me, "id", "?"))

    @client.on(events.NewMessage(incoming=True))
    async def _on_message(event: NewMessage.Event) -> None:
        if not event.is_private or event.out:
            return
        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return
        chat_id = int(event.chat_id)
        text = (event.raw_text or "").strip()
        if not text:
            return
        display = " ".join(
            part for part in (getattr(sender, "first_name", ""), getattr(sender, "last_name", "")) if part
        ).strip()
        username = getattr(sender, "username", None)
        async with SessionLocal() as session:
            lead = await get_lead_by_chat(session, chat_id)
            if lead is None:
                await create_lead(
                    session,
                    name=display or (f"@{username}" if username else f"id{chat_id}"),
                    contact=f"@{username}" if username else f"tg://user?id={chat_id}",
                    request=text,
                    source=LeadSource.TELEGRAM_DM,
                    tg_chat_id=chat_id,
                )
                log.info("лид из лички создан, чат %s", chat_id)
            else:
                await append_to_request(session, lead, text)

    await client.run_until_disconnected()
