"""Точка входа: FastAPI + бот и юзербот в одном процессе."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app import userbot
from app.bot import LeadBot
from app.config import settings
from app.db import dispose_db, init_db
from app.web import render_http_error, render_validation_error, router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx пишет в лог полный URL запроса, а в нём лежит токен бота — приглушаем.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("crm")


async def _supervise(name: str, coro_factory: Callable[[], Coroutine[None, None, None]]) -> None:
    """Перезапускает фоновую задачу, если она упала не по отмене."""
    while True:
        try:
            await coro_factory()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s упал, перезапуск через 5 с", name)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    tasks: list[asyncio.Task[None]] = []
    if settings.bot_enabled:
        bot = LeadBot(settings.bot_token)
        tasks.append(asyncio.create_task(_supervise("бот", bot.run), name="bot"))
    else:
        log.warning("BOT_TOKEN не задан — бот не запущен")
    if settings.userbot_enabled:
        tasks.append(asyncio.create_task(_supervise("юзербот", userbot.run), name="userbot"))
    else:
        log.info("юзербот выключен: нет TG_API_ID/TG_API_HASH")
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await dispose_db()


app = FastAPI(title="Мини-CRM для заявок", lifespan=lifespan)
app.add_exception_handler(HTTPException, render_http_error)
app.add_exception_handler(RequestValidationError, render_validation_error)
app.include_router(router)
