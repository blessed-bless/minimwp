"""Telegram-бот, собирающий заявку в три шага и кладущий её в CRM.

Работает на Bot API через long polling (httpx), без api_id/api_hash:
боту нужен только токен от @BotFather. Telethon используется отдельно,
в userbot.py, для подключения обычного аккаунта (пункт 2 ТЗ).

Важное правило в этом модуле: состояние диалога меняется только ПОСЛЕ того,
как ответ ушёл пользователю. Иначе сбой отправки сдвигает шаг молча, человек
повторяет ответ, не увидев вопроса, и повтор попадает не в то поле.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal, TypeAlias

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.crud import create_lead
from app.db import SessionLocal
from app.models import LeadSource

log = logging.getLogger("crm.bot")
JSONValue: TypeAlias = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
Step: TypeAlias = Literal["name", "contact", "request", "confirm"]

API_ROOT = "https://api.telegram.org/bot{token}/{method}"
YES = {"да", "ага", "yes", "y", "ок", "ok", "+", "отправляй"}
NO = {"нет", "no", "n", "отмена"}
# Сколько последних подтверждений помним, чтобы не создать лид дважды при сбое отправки.
COMPLETED_LIMIT = 500

ASK_NAME = "Привет! Оставьте заявку — задам три коротких вопроса.\n\nКак вас зовут?"
ASK_CONTACT = "Как с вами связаться? Телефон, email или @username."
ASK_REQUEST = "Коротко опишите задачу."
ASK_CONFIRM = "Отправить заявку? Напишите «да» или «нет». Для отмены — /cancel."
CONFIRM = (
    "Проверьте заявку:\n\nИмя: {name}\nКонтакт: {contact}\nЗапрос: {request}\n\n"
    "Отправляем? Напишите «да» или «нет»."
)
DONE = "Заявка №{lead_id} принята. Менеджер свяжется с вами."
CANCELLED = "Заявка отменена. Напишите /start, чтобы начать заново."
FALLBACK = "Напишите /start, чтобы оставить заявку."
SAVE_FAILED = (
    "Пока не удалось сохранить заявку. Данные сохранены в диалоге — "
    "попробуйте ещё раз написать «да»."
)

QUESTIONS: dict[Step, str] = {
    "name": ASK_NAME,
    "contact": ASK_CONTACT,
    "request": ASK_REQUEST,
    "confirm": ASK_CONFIRM,
}


@dataclass
class Draft:
    """Черновик заявки. Живёт в памяти процесса — перезапуск обнуляет диалоги."""

    step: Step = "name"
    name: str = ""
    contact: str = ""
    request: str = ""


def phone_from_contact(message: dict[str, JSONValue]) -> str:
    """Телефон из нативной карточки контакта Telegram — ответ на вопрос о связи."""
    contact = message.get("contact")
    if not isinstance(contact, dict):
        return ""
    phone = contact.get("phone_number")
    if isinstance(phone, str):
        return phone.strip()
    if isinstance(phone, int) and not isinstance(phone, bool):
        return str(phone)
    return ""


class LeadBot:
    def __init__(self, token: str, poll_timeout: int = 30) -> None:
        self._token = token
        self._poll_timeout = poll_timeout
        self._drafts: dict[int, Draft] = {}
        self._completed: dict[int, str] = {}
        self._offset = 0

    async def run(self) -> None:
        timeout = httpx.Timeout(self._poll_timeout + 10)
        async with httpx.AsyncClient(timeout=timeout) as client:
            me = await self._get_me(client)
            log.info("бот запущен как @%s", me.get("username"))
            while True:
                try:
                    updates = await self._get_updates(client)
                except (httpx.HTTPError, RuntimeError) as exc:
                    log.warning("getUpdates не прошёл: %s", type(exc).__name__)
                    await asyncio.sleep(3)
                    continue
                for update in updates:
                    update_id = update.get("update_id")
                    if not isinstance(update_id, int) or isinstance(update_id, bool):
                        continue
                    self._offset = update_id + 1
                    try:
                        await self._handle(client, update)
                    except Exception:
                        # Один сломанный апдейт не должен ронять поллинг. Шаг при этом
                        # не сдвинут, поэтому повтор ответа пользователем отработает верно.
                        log.exception("ошибка на апдейте %s", update_id)

    async def _call(self, client: httpx.AsyncClient, method: str, **payload: JSONValue) -> JSONValue:
        url = API_ROOT.format(token=self._token, method=method)
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError:
            raise RuntimeError(f"Ошибка HTTP при вызове Telegram API: {method}") from None
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(f"Некорректный JSON от Telegram API: {method}") from None
        if not isinstance(data, dict) or not data.get("ok") or "result" not in data:
            raise RuntimeError(f"Telegram API вернул ошибку на {method}")
        return data["result"]

    async def _get_me(self, client: httpx.AsyncClient) -> dict[str, JSONValue]:
        result = await self._call(client, "getMe")
        if not isinstance(result, dict):
            raise RuntimeError("Некорректный ответ getMe")
        return result

    async def _get_updates(self, client: httpx.AsyncClient) -> list[dict[str, JSONValue]]:
        result = await self._call(
            client, "getUpdates", offset=self._offset, timeout=self._poll_timeout
        )
        if not isinstance(result, list):
            raise RuntimeError("Некорректный ответ getUpdates")
        updates: list[dict[str, JSONValue]] = []
        for item in result:
            if not isinstance(item, dict):
                raise RuntimeError("Некорректное обновление getUpdates")
            updates.append(item)
        return updates

    async def _send(self, client: httpx.AsyncClient, chat_id: int, text: str) -> None:
        await self._call(client, "sendMessage", chat_id=chat_id, text=text)

    def _remember_completed(self, chat_id: int, reply: str) -> None:
        if len(self._completed) >= COMPLETED_LIMIT:
            self._completed.pop(next(iter(self._completed)))
        self._completed[chat_id] = reply

    async def _handle(self, client: httpx.AsyncClient, update: dict[str, JSONValue]) -> None:
        # Редактирование старого сообщения не является ответом на следующий вопрос.
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict):
            return
        chat_id = chat.get("id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return
        if chat.get("type") != "private":
            # Заявки принимаем только в личке: в группе черновик был бы общим
            # на всех участников, и ответы разных людей склеились бы в одну заявку.
            log.debug("сообщение из чата типа %s пропущено", chat.get("type"))
            return

        raw_text = message.get("text")
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        draft = self._drafts.get(chat_id)

        if not text and draft is not None and draft.step == "contact":
            text = phone_from_contact(message)

        if text.startswith("/start"):
            await self._send(client, chat_id, ASK_NAME)
            self._drafts[chat_id] = Draft()
            self._completed.pop(chat_id, None)
            return
        if text.startswith("/cancel"):
            await self._send(client, chat_id, CANCELLED)
            self._drafts.pop(chat_id, None)
            self._completed.pop(chat_id, None)
            return
        if draft is None:
            # Повтор после сбоя отправки подтверждения: отдаём тот же ответ,
            # а не создаём вторую заявку.
            await self._send(client, chat_id, self._completed.get(chat_id) or FALLBACK)
            return
        if not text:
            await self._send(client, chat_id, QUESTIONS[draft.step])
            return
        await self._step(client, draft, chat_id, text)

    async def _step(
        self, client: httpx.AsyncClient, draft: Draft, chat_id: int, text: str
    ) -> None:
        if draft.step == "name":
            await self._send(client, chat_id, ASK_CONTACT)
            draft.name = text[:128]
            draft.step = "contact"
            return
        if draft.step == "contact":
            await self._send(client, chat_id, ASK_REQUEST)
            draft.contact = text[:128]
            draft.step = "request"
            return
        if draft.step == "request":
            request = text[:2000]
            await self._send(
                client,
                chat_id,
                CONFIRM.format(name=draft.name, contact=draft.contact, request=request),
            )
            draft.request = request
            draft.step = "confirm"
            return
        await self._confirm(client, draft, chat_id, text)

    async def _confirm(
        self, client: httpx.AsyncClient, draft: Draft, chat_id: int, text: str
    ) -> None:
        answer = text.strip().lower()
        if answer in NO:
            await self._send(client, chat_id, CANCELLED)
            self._drafts.pop(chat_id, None)
            return
        if answer not in YES:
            await self._send(client, chat_id, ASK_CONFIRM)
            return
        try:
            async with SessionLocal() as session:
                lead = await create_lead(
                    session,
                    name=draft.name,
                    contact=draft.contact,
                    request=draft.request,
                    source=LeadSource.TELEGRAM_BOT,
                    tg_chat_id=chat_id,
                )
        except SQLAlchemyError:
            log.warning("не удалось сохранить заявку из бота")
            await self._send(client, chat_id, SAVE_FAILED)
            return
        reply = DONE.format(lead_id=lead.id)
        # Запоминаем ответ ДО отправки: если sendMessage упадёт, повторное
        # сообщение пользователя получит тот же номер заявки, а не новый лид.
        self._drafts.pop(chat_id, None)
        self._remember_completed(chat_id, reply)
        log.info("лид #%s создан из бота", lead.id)
        await self._send(client, chat_id, reply)
