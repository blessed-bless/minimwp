"""Тесты CRM: ручное создание лида, теги, фильтрация по тегу, лид из бота."""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["CRM_USER"] = "admin"
os.environ["CRM_PASSWORD"] = "test-pass"
os.environ["BOT_TOKEN"] = ""
os.environ["TG_API_ID"] = ""
os.environ["TG_API_HASH"] = ""

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.crud import add_tag, create_lead, list_leads  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base, Lead, LeadSource, Tag  # noqa: E402
from app.main import app  # noqa: E402

AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:test-pass").decode()}


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.mark.asyncio
async def test_manual_lead_appears_in_list(client: AsyncClient) -> None:
    response = await client.post(
        "/leads",
        data={"name": "Иван", "contact": "+79990000000", "request_text": "Нужен лендинг"},
        headers=AUTH,
    )
    assert response.status_code == 303

    page = await client.get("/leads", headers=AUTH)
    assert page.status_code == 200
    assert "Иван" in page.text
    assert "Нужен лендинг" in page.text


@pytest.mark.asyncio
async def test_auth_required(client: AsyncClient) -> None:
    response = await client.get("/leads")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_tag_add_and_filter(client: AsyncClient) -> None:
    created = await client.post("/leads", data={"name": "Пётр"}, headers=AUTH)
    lead_url = created.headers["location"]

    tagged = await client.post(f"{lead_url}/tags", data={"tag": "  #Срочно "}, headers=AUTH)
    assert tagged.status_code == 303

    await client.post("/leads", data={"name": "Без тега"}, headers=AUTH)

    filtered = await client.get("/leads", params={"tag": "срочно"}, headers=AUTH)
    assert "Пётр" in filtered.text
    assert "Без тега" not in filtered.text


@pytest.mark.asyncio
async def test_tag_is_normalized_and_not_duplicated(client: AsyncClient) -> None:
    async with SessionLocal() as session:
        lead = await create_lead(session, name="Анна", source=LeadSource.TELEGRAM_BOT)
        await add_tag(session, lead, "Сайт")
        await add_tag(session, lead, "#сайт")
        assert [tag.name for tag in lead.tags] == ["сайт"]


@pytest.mark.asyncio
async def test_bot_lead_is_listed_by_tag(client: AsyncClient) -> None:
    async with SessionLocal() as session:
        lead = await create_lead(
            session,
            name="Из бота",
            contact="@someone",
            request="Хочу бота",
            source=LeadSource.TELEGRAM_BOT,
            tg_chat_id=12345,
        )
        await add_tag(session, lead, "бот")
        found = await list_leads(session, tag="бот")
        assert [item.id for item in found] == [lead.id]
        assert found[0].source == LeadSource.TELEGRAM_BOT.value


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["in_progress", "closed"])
async def test_edit_fields_and_status(client: AsyncClient, status: str) -> None:
    created = await client.post("/leads", data={"name": "До правки"}, headers=AUTH)
    url = created.headers["location"]
    response = await client.post(url, headers=AUTH, data={
        "name": "После правки", "contact": "new@example.test",
        "request_text": "Новый запрос", "lead_status": status,
    })
    assert response.status_code == 303
    async with SessionLocal() as session:
        lead = await session.get(Lead, int(url.rsplit("/", 1)[1]))
        assert lead is not None
        assert (lead.name, lead.contact, lead.request, lead.status) == (
            "После правки", "new@example.test", "Новый запрос", status,
        )


@pytest.mark.asyncio
async def test_remove_tag(client: AsyncClient) -> None:
    created = await client.post("/leads", data={"name": "С тегом", "tags": "сайт"}, headers=AUTH)
    url = created.headers["location"]
    async with SessionLocal() as session:
        tag_id = await session.scalar(select(Tag.id))
    response = await client.post(f"{url}/tags/{tag_id}/delete", headers=AUTH)
    assert response.status_code == 303
    assert "С тегом" not in (await client.get("/leads", params={"tag": "сайт"}, headers=AUTH)).text
    async with SessionLocal() as session:
        lead = await session.get(Lead, int(url.rsplit("/", 1)[1]))
        assert lead is not None and not lead.tags


@pytest.mark.asyncio
@pytest.mark.parametrize("path,code", [("/leads/999", 404), ("/missing", 404), ("/leads", 401)])
async def test_html_and_json_errors(client: AsyncClient, path: str, code: int) -> None:
    headers = AUTH if code == 404 else {}
    html = await client.get(path, headers={**headers, "Accept": "text/html"})
    assert html.status_code == code
    assert html.headers["content-type"].startswith("text/html")
    assert f"Ошибка {code}" in html.text and "Мини-CRM" in html.text
    json = await client.get(path, headers={**headers, "Accept": "application/json"})
    assert json.status_code == code and "detail" in json.json()
    if code == 401:
        assert html.headers["www-authenticate"] == "Basic"


@pytest.mark.asyncio
async def test_api_errors_remain_json(client: AsyncClient) -> None:
    response = await client.get("/api/missing", headers={"Accept": "text/html"})
    assert response.status_code == 404 and "detail" in response.json()
    health = await client.get("/healthz", headers={"Accept": "text/html"})
    assert health.json() == {"status": "ok"}
    from app.web import render_http_error
    from starlette.exceptions import HTTPException
    from starlette.requests import Request
    request = Request({"type": "http", "path": "/healthz", "headers": [(b"accept", b"text/html")]})
    error = await render_http_error(request, HTTPException(401))
    assert error.media_type == "application/json"


@pytest.mark.asyncio
async def test_manual_creation_commits_once(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    commits = 0
    original = AsyncSession.commit

    async def counted(session: AsyncSession) -> None:
        nonlocal commits
        commits += 1
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", counted)
    response = await client.post("/leads", headers=AUTH, data={"name": "Атомарный", "tags": "#Сайт, сайт, срочно, #"})
    assert response.status_code == 303 and commits == 1
    async with SessionLocal() as session:
        lead = await session.scalar(select(Lead))
        assert lead is not None
        assert sorted(tag.name for tag in lead.tags) == ["сайт", "срочно"]


@pytest.mark.asyncio
async def test_manual_creation_rolls_back_on_tag_error(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import crud
    original = crud.get_or_create_tag

    async def fail_second(session: AsyncSession, name: str) -> Tag:
        if name == "ошибка":
            raise RuntimeError("Проверочная ошибка тега")
        return await original(session, name)

    monkeypatch.setattr(crud, "get_or_create_tag", fail_second)
    with pytest.raises(RuntimeError, match="Проверочная ошибка"):
        await client.post("/leads", headers=AUTH, data={"name": "Не сохранится", "tags": "первый, ошибка"})
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Lead)) == 0
        assert await session.scalar(select(func.count()).select_from(Tag)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "contact", "request"])
async def test_search_with_tag(client: AsyncClient, field: str) -> None:
    async with SessionLocal() as session:
        values = {"name": "Нужный", "contact": "", "request": ""}
        values[field] = "FindMe"
        target = await create_lead(session, **values, tags=["сайт"])
        target_id = target.id
        await create_lead(session, name="FindMe без тега")
        await create_lead(session, name="Другой", tags=["сайт"])
    response = await client.get("/leads", params={"q": "findme", "tag": "#Сайт"}, headers=AUTH)
    assert response.status_code == 200 and f'href="/leads/{target_id}"' in response.text
    assert "FindMe без тега" not in response.text and "Другой" not in response.text


@pytest.mark.asyncio
async def test_search_literal_wildcards(client: AsyncClient) -> None:
    async with SessionLocal() as session:
        await create_lead(session, name="100%_готово")
        await create_lead(session, name="100 прочее")
    response = await client.get("/leads", params={"q": "%_"}, headers=AUTH)
    assert "100%_готово" in response.text and "100 прочее" not in response.text


@pytest.mark.asyncio
async def test_pagination_preserves_filters(client: AsyncClient) -> None:
    async with SessionLocal() as session:
        for index in range(51):
            await create_lead(session, name=f"Клиент {index:02}", request="search", tags=["сайт"])
        await create_lead(session, name="Без тега", request="search")
        await create_lead(session, name="Без поиска", tags=["сайт"])
    first = await client.get("/leads", params={"q": "search", "tag": "сайт"}, headers=AUTH)
    second = await client.get("/leads", params={"q": "search", "tag": "сайт", "page": 2}, headers=AUTH)
    assert first.text.count('class="lead"') == 50
    assert second.text.count('class="lead"') == 1
    assert "Клиент 00" not in first.text and "Клиент 00" in second.text
    assert "Страница 1 из 2" in first.text and "page=2&amp;q=search&amp;tag=" in first.text
    assert "Без тега" not in first.text and "Без поиска" not in first.text
    assert (await client.get("/leads", params={"page": 0}, headers=AUTH)).status_code == 422




@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["name", "contact", "request", "confirm"])
async def test_bot_cancel_command(step: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.bot import CANCELLED, Draft, LeadBot
    from unittest.mock import AsyncMock
    bot = LeadBot("test-token")
    bot._drafts[123] = Draft(step=step)
    send = AsyncMock()
    monkeypatch.setattr(bot, "_send", send)
    async with AsyncClient() as http:
        await bot._handle(http, {"message": {"chat": {"id": 123, "type": "private"}, "text": "/cancel"}})
        send.assert_awaited_once_with(http, 123, CANCELLED)
    assert not bot._drafts


@pytest.mark.asyncio
async def test_userbot_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib
    from dataclasses import replace
    from app.config import settings
    from app import userbot
    importlib.reload(userbot)
    monkeypatch.setattr(userbot, "settings", replace(settings, api_id=None, api_hash=None))

    def forbidden() -> None:
        pytest.fail("Telethon не должен запускаться без ключей")

    monkeypatch.setattr(userbot, "build_client", forbidden)
    await userbot.run()


@pytest.mark.asyncio
async def test_smoke_empty_database_and_cleanup(client: AsyncClient) -> None:
    from scripts.smoke_check import check
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", auth=("admin", "test-pass")) as http:
        await check(http)
        await check(http)
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Lead)) == 0
        assert await session.scalar(select(func.count()).select_from(Tag)) == 0


@pytest.mark.asyncio
async def test_delete_preserves_shared_tag_and_requires_auth(client: AsyncClient) -> None:
    async with SessionLocal() as session:
        first = await create_lead(session, name="Первый", tags=["общий", "свой"])
        first_id = first.id
        second = await create_lead(session, name="Второй", tags=["общий"])
        second_id = second.id
    assert (await client.post(f"/leads/{first_id}/delete")).status_code == 401
    assert (await client.post(f"/leads/{first_id}/delete", headers=AUTH)).status_code == 204
    async with SessionLocal() as session:
        second = await session.get(Lead, second_id)
        assert second is not None and [tag.name for tag in second.tags] == ["общий"]
        assert list(await session.scalars(select(Tag.name))) == ["общий"]


@pytest.mark.asyncio
async def test_smoke_cleans_up_after_failure(client: AsyncClient) -> None:
    from scripts.smoke_check import check
    from httpx import MockTransport, Request, Response
    transport = ASGITransport(app=app)

    async def fail_tags(request: Request) -> Response:
        if request.method == "POST" and request.url.path.endswith("/tags"):
            return Response(500)
        return await transport.handle_async_request(request)

    async with AsyncClient(transport=MockTransport(fail_tags), base_url="http://test", auth=("admin", "test-pass")) as http:
        with pytest.raises(AssertionError, match="Тег не добавлен"):
            await check(http)
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Lead)) == 0
        assert await session.scalar(select(func.count()).select_from(Tag)) == 0


@pytest.mark.asyncio
async def test_bot_api_shapes_and_safe_errors() -> None:
    from app.bot import LeadBot
    from httpx import MockTransport, Request, Response
    responses = {
        "getMe": {"ok": True, "result": {"username": "test_bot"}},
        "getUpdates": {"ok": True, "result": [{"update_id": 7}]},
    }

    async def respond(request: Request) -> Response:
        return Response(200, json=responses[request.url.path.rsplit("/", 1)[1]])

    bot = LeadBot("test-token")
    async with AsyncClient(transport=MockTransport(respond)) as http:
        assert (await bot._get_me(http))["username"] == "test_bot"
        assert await bot._get_updates(http) == [{"update_id": 7}]
        responses["getMe"]["result"] = []
        with pytest.raises(RuntimeError, match="Некорректный ответ getMe"):
            await bot._get_me(http)
        responses["getUpdates"]["result"] = ["не объект"]
        with pytest.raises(RuntimeError, match="Некорректное обновление"):
            await bot._get_updates(http)

    async def failure(request: Request) -> Response:
        return Response(401)

    async with AsyncClient(transport=MockTransport(failure)) as http:
        with pytest.raises(RuntimeError) as error:
            await bot._get_me(http)
        assert "test-token" not in str(error.value) and error.value.__suppress_context__


@pytest.mark.asyncio
async def test_tag_forms_have_valid_ancestors(client: AsyncClient) -> None:
    from html.parser import HTMLParser

    class FormsParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.stack: list[str] = []
            self.forms = 0

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "form":
                assert not {"p", "span", "form"}.intersection(self.stack)
                self.forms += 1
            if tag not in {"meta", "input", "br", "link", "hr", "img"}:
                self.stack.append(tag)

        def handle_endtag(self, tag: str) -> None:
            assert self.stack.pop() == tag

    created = await client.post("/leads", data={"name": "HTML", "tags": "сайт"}, headers=AUTH)
    response = await client.get(created.headers["location"], headers=AUTH)
    parser = FormsParser()
    parser.feed(response.text)
    assert parser.forms == 3 and not parser.stack


def test_settings_repr_hides_credentials() -> None:
    from app.config import Settings
    settings = Settings("test-token", None, "test-hash", "admin", "test-secret", "sqlite://private")
    assert all(value not in repr(settings) for value in ["test-token", "test-hash", "test-secret", "sqlite://private"])


@pytest.mark.asyncio
async def test_bot_to_crm_tag_scenario(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.bot import LeadBot
    from unittest.mock import AsyncMock
    bot = LeadBot("test-token")
    send = AsyncMock()
    monkeypatch.setattr(bot, "_send", send)
    async with AsyncClient() as http:
        for text in ["/start", "Заявка из диалога", "@customer", "Нужен сайт", "да"]:
            await bot._handle(http, {"message": {"chat": {"id": 321, "type": "private"}, "text": text}})
        # Повторное подтверждение не создаёт вторую заявку.
        await bot._handle(http, {"message": {"chat": {"id": 321, "type": "private"}, "text": "да"}})
    async with SessionLocal() as session:
        leads = list(await session.scalars(select(Lead)))
        assert len(leads) == 1
        lead = leads[0]
        assert (lead.name, lead.contact, lead.request, lead.source) == (
            "Заявка из диалога", "@customer", "Нужен сайт", "telegram_bot",
        )
        url = f"/leads/{lead.id}"
    tagged = await client.post(f"{url}/tags", data={"tag": "#Срочно"}, headers=AUTH)
    assert tagged.status_code == 303
    card = await client.get(url, headers=AUTH)
    filtered = await client.get("/leads", params={"tag": "срочно"}, headers=AUTH)
    assert "срочно" in card.text and "Заявка из диалога" in filtered.text




@pytest.mark.asyncio
async def test_bot_ignores_edited_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.bot import Draft, LeadBot
    from unittest.mock import AsyncMock
    bot = LeadBot("test-token")
    draft = Draft(step="contact", name="Анна")
    bot._drafts[123] = draft
    send = AsyncMock()
    monkeypatch.setattr(bot, "_send", send)
    async with AsyncClient() as http:
        await bot._handle(http, {"edited_message": {"chat": {"id": 123, "type": "private"}, "text": "Анна исправлено"}})
    assert draft.step == "contact" and draft.contact == ""
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_invalid_json_is_retryable() -> None:
    from app.bot import LeadBot
    from httpx import MockTransport, Request, Response

    async def response(request: Request) -> Response:
        return Response(200, text="Временный сбой")

    async with AsyncClient(transport=MockTransport(response)) as http:
        with pytest.raises(RuntimeError, match="Некорректный JSON"):
            await LeadBot("test-token")._get_updates(http)


@pytest.mark.asyncio
@pytest.mark.parametrize("data,code", [
    ({"name": " "}, 400),
    ({"name": "Я" * 129}, 422),
    ({"name": "Анна", "contact": "x" * 129}, 422),
])
async def test_invalid_manual_form(client: AsyncClient, data: dict[str, str], code: int) -> None:
    response = await client.post("/leads", data=data, headers={**AUTH, "Accept": "text/html"})
    assert response.status_code == code and "Ошибка" in response.text
    assert response.headers["content-type"].startswith("text/html")
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Lead)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("data,code", [
    ({"name": " "}, 400),
    ({"name": "Новое имя", "lead_status": "unknown"}, 422),
])
async def test_invalid_edit_does_not_change_lead(client: AsyncClient, data: dict[str, str], code: int) -> None:
    created = await client.post("/leads", data={"name": "До правки"}, headers=AUTH)
    url = created.headers["location"]
    response = await client.post(url, data=data, headers=AUTH)
    assert response.status_code == code and "detail" in response.json()
    async with SessionLocal() as session:
        lead = await session.get(Lead, int(url.rsplit("/", 1)[1]))
        assert lead is not None and lead.name == "До правки" and lead.status == "new"


@pytest.mark.asyncio
async def test_empty_tag_reports_error(client: AsyncClient) -> None:
    created = await client.post("/leads", data={"name": "Анна"}, headers=AUTH)
    response = await client.post(
        created.headers["location"] + "/tags", data={"tag": "#"},
        headers={**AUTH, "Accept": "text/html"},
    )
    assert response.status_code == 400 and "Введите название тега" in response.text
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Tag)) == 0


@pytest.mark.asyncio
async def test_page_out_of_range_and_no_search_results(client: AsyncClient) -> None:
    await client.post("/leads", data={"name": "Единственный"}, headers=AUTH)
    response = await client.get("/leads", params={"page": 10**25}, headers=AUTH)
    assert response.status_code == 200 and "Единственный" in response.text
    empty = await client.get("/leads", params={"q": "Не найден"}, headers=AUTH)
    assert "По вашему запросу лидов не найдено" in empty.text
    assert "Сбросить поиск и фильтр" in empty.text


@pytest.mark.asyncio
async def test_polling_recovers_after_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from app.bot import Draft, LeadBot
    from httpx import MockTransport, Request, Response
    from unittest.mock import AsyncMock
    from app import bot as module
    polls = 0

    async def respond(request: Request) -> Response:
        nonlocal polls
        if request.url.path.endswith("getMe"):
            return Response(200, json={"ok": True, "result": {"username": "test_bot"}})
        polls += 1
        if polls == 1:
            return Response(200, text="Временный сбой")
        if polls == 2:
            return Response(200, json={"ok": True, "result": [{"update_id": 8}]})
        raise asyncio.CancelledError

    http = AsyncClient(transport=MockTransport(respond))
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: http)
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    bot = LeadBot("test-token")
    draft = Draft(step="contact", name="Анна")
    bot._drafts[123] = draft
    handle = AsyncMock()
    monkeypatch.setattr(bot, "_handle", handle)
    with pytest.raises(asyncio.CancelledError):
        await bot.run()
    sleep.assert_awaited_once_with(3)
    handle.assert_awaited_once_with(http, {"update_id": 8})
    assert bot._offset == 9 and bot._drafts[123] is draft


@pytest.mark.asyncio
async def test_userbot_chat_lookup_does_not_mix_sources(client: AsyncClient) -> None:
    from app.crud import get_lead_by_chat
    async with SessionLocal() as session:
        await create_lead(session, name="Из бота", source=LeadSource.TELEGRAM_BOT, tg_chat_id=123)
        assert await get_lead_by_chat(session, 123) is None
        dm = await create_lead(session, name="Из лички", source=LeadSource.TELEGRAM_DM, tg_chat_id=123)
        await create_lead(session, name="Ещё заявка боту", source=LeadSource.TELEGRAM_BOT, tg_chat_id=123)
        found = await get_lead_by_chat(session, 123)
        assert found is not None and found.id == dm.id


@pytest.mark.asyncio
async def test_userbot_incoming_messages_without_network(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from collections.abc import Awaitable, Callable
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app import userbot

    EventHandler = Callable[[SimpleNamespace], Awaitable[None]]

    def message(text: str, *, private: bool = True, outgoing: bool = False, bot: bool = False) -> SimpleNamespace:
        sender = SimpleNamespace(first_name="Анна", last_name="Тестовая", username="test_user", bot=bot)
        return SimpleNamespace(
            is_private=private, out=outgoing, chat_id=777, raw_text=text,
            get_sender=AsyncMock(return_value=sender),
        )

    class TestClient:
        handler: EventHandler

        connected = False
        authorized = True

        async def connect(self) -> None:
            type(self).connected = True

        async def is_user_authorized(self) -> bool:
            return type(self).authorized

        async def disconnect(self) -> None:
            type(self).connected = False

        async def get_me(self) -> SimpleNamespace:
            return SimpleNamespace(username="test_manager", id=1)

        def on(self, event: object) -> Callable[[EventHandler], EventHandler]:
            def register(handler: EventHandler) -> EventHandler:
                self.handler = handler
                return handler
            return register

        async def run_until_disconnected(self) -> None:
            await self.handler(message("Первый запрос"))
            await self.handler(message("Уточнение задачи"))
            await self.handler(message("Групповой чат", private=False))
            await self.handler(message("Исходящее", outgoing=True))
            await self.handler(message("От бота", bot=True))
            await self.handler(message("   "))

    async with SessionLocal() as session:
        existing = await create_lead(
            session, name="Отдельная заявка", request="Из бота",
            source=LeadSource.TELEGRAM_BOT, tg_chat_id=777,
        )
        existing_id = existing.id
    monkeypatch.setattr(userbot, "settings", replace(userbot.settings, api_id=1, api_hash="test-hash"))
    monkeypatch.setattr(userbot, "build_client", TestClient)
    await userbot.run()
    async with SessionLocal() as session:
        leads = list(await session.scalars(select(Lead).order_by(Lead.id)))
        assert len(leads) == 2
        assert leads[0].id == existing_id and leads[0].request == "Из бота"
        assert (leads[1].name, leads[1].contact, leads[1].request, leads[1].source) == (
            "Анна Тестовая", "@test_user", "Первый запрос\nУточнение задачи", "telegram_dm",
        )


# --- Диалог бота: состояние меняется только после успешной отправки ---


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmation", ["да", "нет"])
async def test_bot_dialog_without_network(
    client: AsyncClient, confirmation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from app.bot import ASK_CONTACT, ASK_REQUEST, CANCELLED, DONE, Draft, LeadBot

    bot = LeadBot("test-token")
    send = AsyncMock()
    monkeypatch.setattr(bot, "_send", send)
    draft = Draft()
    bot._drafts[123] = draft
    async with AsyncClient() as http:
        await bot._step(http, draft, 123, "Анна")
        assert send.await_args.args[2] == ASK_CONTACT
        assert draft.step == "contact" and draft.name == "Анна"
        await bot._step(http, draft, 123, "@anna")
        assert send.await_args.args[2] == ASK_REQUEST
        assert draft.step == "request" and draft.contact == "@anna"
        await bot._step(http, draft, 123, "Нужен сайт")
        preview = send.await_args.args[2]
        assert draft.step == "confirm" and draft.request == "Нужен сайт"
        assert all(value in preview for value in ["Анна", "@anna", "Нужен сайт"])
        await bot._step(http, draft, 123, confirmation)
        reply = send.await_args.args[2]
    assert 123 not in bot._drafts
    async with SessionLocal() as session:
        lead = await session.scalar(select(Lead))
        if confirmation == "да":
            assert lead is not None and reply == DONE.format(lead_id=lead.id)
            assert (lead.name, lead.contact, lead.request, lead.tg_chat_id, lead.source) == (
                "Анна", "@anna", "Нужен сайт", 123, LeadSource.TELEGRAM_BOT.value,
            )
        else:
            assert lead is None and reply == CANCELLED


@pytest.mark.asyncio
async def test_bot_keeps_draft_until_saved(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from sqlalchemy.exc import OperationalError

    from app import bot as module

    bot = module.LeadBot("test-token")
    send = AsyncMock()
    monkeypatch.setattr(bot, "_send", send)
    draft = module.Draft(step="confirm", name="Анна", contact="@anna", request="Сайт")
    bot._drafts[123] = draft
    async with AsyncClient() as http:
        await bot._confirm(http, draft, 123, "не уверен")
        assert send.await_args.args[2] == module.ASK_CONFIRM
        assert bot._drafts[123] is draft
        original = module.create_lead
        monkeypatch.setattr(
            module, "create_lead", AsyncMock(side_effect=OperationalError("", {}, Exception()))
        )
        await bot._confirm(http, draft, 123, "да")
        assert send.await_args.args[2] == module.SAVE_FAILED
        assert bot._drafts[123] is draft
        monkeypatch.setattr(module, "create_lead", original)
        await bot._confirm(http, draft, 123, "да")
        assert "принята" in send.await_args.args[2]
    assert 123 not in bot._drafts
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Lead)) == 1


@pytest.mark.asyncio
async def test_failed_send_does_not_advance_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """R1: вопрос не дошёл — шаг не сдвинут, повтор ответа попадает в то же поле."""
    from unittest.mock import AsyncMock

    from app.bot import Draft, LeadBot

    bot = LeadBot("test-token")
    draft = Draft()
    bot._drafts[123] = draft
    message = {"message": {"chat": {"id": 123, "type": "private"}, "text": "Анна"}}
    monkeypatch.setattr(bot, "_send", AsyncMock(side_effect=RuntimeError("сеть недоступна")))
    async with AsyncClient() as http:
        with pytest.raises(RuntimeError):
            await bot._handle(http, message)
    assert draft.step == "name" and draft.name == ""

    monkeypatch.setattr(bot, "_send", AsyncMock())
    async with AsyncClient() as http:
        await bot._handle(http, message)
    assert draft.step == "contact" and draft.name == "Анна"


@pytest.mark.asyncio
async def test_bot_ignores_group_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """R7: в группе черновик был бы общим на всех участников."""
    from unittest.mock import AsyncMock

    from app.bot import LeadBot

    bot = LeadBot("test-token")
    send = AsyncMock()
    monkeypatch.setattr(bot, "_send", send)
    async with AsyncClient() as http:
        for text in ("/start", "Анна"):
            await bot._handle(
                http, {"message": {"chat": {"id": -100, "type": "supergroup"}, "text": text}}
            )
    assert not bot._drafts
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_accepts_native_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    """R8: карточка контакта — естественный ответ на вопрос «как связаться»."""
    from unittest.mock import AsyncMock

    from app.bot import ASK_REQUEST, Draft, LeadBot

    bot = LeadBot("test-token")
    send = AsyncMock()
    monkeypatch.setattr(bot, "_send", send)
    bot._drafts[123] = Draft(step="contact", name="Анна")
    async with AsyncClient() as http:
        await bot._handle(
            http,
            {"message": {"chat": {"id": 123, "type": "private"},
                         "contact": {"phone_number": "+79990001122"}}},
        )
    assert send.await_args.args[2] == ASK_REQUEST
    assert bot._drafts[123].contact == "+79990001122"
    assert bot._drafts[123].step == "request"


@pytest.mark.asyncio
async def test_bot_repeats_question_on_unsupported_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from app.bot import ASK_REQUEST, Draft, LeadBot

    bot = LeadBot("test-token")
    send = AsyncMock()
    monkeypatch.setattr(bot, "_send", send)
    bot._drafts[123] = Draft(step="request", name="Анна", contact="@anna")
    async with AsyncClient() as http:
        await bot._handle(http, {"message": {"chat": {"id": 123, "type": "private"}}})
    assert send.await_args.args[2] == ASK_REQUEST
    assert bot._drafts[123].step == "request"


# --- Данные: поиск, теги, параллельная запись ---


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["ирина", "ИРИНА", "Соколова", "соколОВА", "ирина соколова"])
async def test_search_is_case_insensitive_for_cyrillic(client: AsyncClient, query: str) -> None:
    """R6: встроенный lower() в SQLite не знает кириллицу — поэтому свой."""
    await client.post("/leads", data={"name": "Ирина Соколова"}, headers=AUTH)
    await client.post("/leads", data={"name": "Пётр Иванов"}, headers=AUTH)
    found = await client.get("/leads", params={"q": query}, headers=AUTH)
    assert "Ирина Соколова" in found.text
    assert "Пётр Иванов" not in found.text


@pytest.mark.asyncio
async def test_tag_with_space_is_one_tag_in_both_forms(client: AsyncClient) -> None:
    """R2: «теги через запятую» — значит разделяет запятая, а не пробел."""
    await client.post(
        "/leads",
        data={"name": "Через список", "tags": "контекстная реклама, сайт"},
        headers=AUTH,
    )
    card = await client.post("/leads", data={"name": "Через карточку"}, headers=AUTH)
    await client.post(
        f"{card.headers['location']}/tags", data={"tag": "Контекстная  Реклама"}, headers=AUTH
    )
    async with SessionLocal() as session:
        names = sorted(tag.name for tag in await session.scalars(select(Tag)))
    assert names == ["контекстная реклама", "сайт"]

    filtered = await client.get("/leads", params={"tag": "контекстная реклама"}, headers=AUTH)
    assert "Через список" in filtered.text and "Через карточку" in filtered.text


@pytest.mark.asyncio
async def test_tag_creation_survives_concurrent_insert(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3: другая сессия успела вставить тот же тег между нашим SELECT и INSERT."""
    async with SessionLocal() as setup:
        setup.add(Tag(name="гонка"))
        await setup.commit()

    async with SessionLocal() as session:
        lead = await create_lead(session, name="Гонщик")
        real_scalar = session.scalar
        seen = {"calls": 0}

        async def blind_first_select(*args: object, **kwargs: object) -> object:
            seen["calls"] += 1
            if seen["calls"] == 1:
                return None
            return await real_scalar(*args, **kwargs)

        monkeypatch.setattr(session, "scalar", blind_first_select)
        await add_tag(session, lead, "гонка")
        monkeypatch.undo()
        assert [tag.name for tag in lead.tags] == ["гонка"]

    async with SessionLocal() as check:
        total = await check.scalar(
            select(func.count()).select_from(Tag).where(Tag.name == "гонка")
        )
    assert total == 1


@pytest.mark.asyncio
async def test_userbot_appends_do_not_overwrite(client: AsyncClient) -> None:
    """R9: два сообщения из одного чата обрабатываются параллельно."""
    from app.crud import append_to_request, get_lead

    async with SessionLocal() as session:
        lead = await create_lead(
            session, name="Клиент", request="Первое",
            source=LeadSource.TELEGRAM_DM, tg_chat_id=777,
        )
        lead_id = lead.id

    async with SessionLocal() as first, SessionLocal() as second:
        # Обе сессии прочитали одно и то же состояние запроса.
        lead_one = await get_lead(first, lead_id)
        lead_two = await get_lead(second, lead_id)
        assert lead_one is not None and lead_two is not None
        assert lead_one.request == lead_two.request == "Первое"
        await append_to_request(first, lead_one, "Второе")
        await append_to_request(second, lead_two, "Третье")

    async with SessionLocal() as check:
        stored = await get_lead(check, lead_id)
    assert stored is not None
    assert stored.request.splitlines() == ["Первое", "Второе", "Третье"]


@pytest.mark.asyncio
async def test_append_to_empty_request_has_no_leading_newline(client: AsyncClient) -> None:
    from app.crud import append_to_request

    async with SessionLocal() as session:
        lead = await create_lead(session, name="Молчун", source=LeadSource.TELEGRAM_DM)
        await append_to_request(session, lead, "Первое слово")
        assert lead.request == "Первое слово"


# --- Веб: источник запроса и границы идентификаторов ---


@pytest.mark.asyncio
async def test_cross_origin_post_is_rejected(client: AsyncClient) -> None:
    """R4: Basic Auth подтверждает пользователя, но не его намерение."""
    created = await client.post("/leads", data={"name": "Жертва"}, headers=AUTH)
    url = created.headers["location"]

    foreign = await client.post(
        url,
        data={"name": "Подменено", "lead_status": "new"},
        headers={**AUTH, "Origin": "https://foreign.example"},
    )
    assert foreign.status_code == 403

    same = await client.post(
        url,
        data={"name": "Свой домен", "lead_status": "new"},
        headers={**AUTH, "Origin": "http://test"},
    )
    assert same.status_code == 303

    card = await client.get(url, headers=AUTH)
    assert "Свой домен" in card.text and "Подменено" not in card.text


@pytest.mark.asyncio
async def test_cross_origin_referer_is_rejected(client: AsyncClient) -> None:
    created = await client.post("/leads", data={"name": "Жертва"}, headers=AUTH)
    url = created.headers["location"]
    foreign = await client.post(
        f"{url}/tags",
        data={"tag": "чужой"},
        headers={**AUTH, "Referer": "https://foreign.example/page"},
    )
    assert foreign.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lead_id", "code"),
    [("99999999999999999999999999", 422), ("0", 422), ("-5", 422), ("424242", 404)],
)
async def test_lead_id_bounds(client: AsyncClient, lead_id: str, code: int) -> None:
    """R5: id вне диапазона INTEGER в SQLite давал 500."""
    assert (await client.get(f"/leads/{lead_id}", headers=AUTH)).status_code == code


@pytest.mark.asyncio
async def test_userbot_stops_without_authorized_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Фоновый процесс не должен уходить в интерактивный ввод номера и кода."""
    from dataclasses import replace
    from types import SimpleNamespace

    from app import userbot

    calls: list[str] = []

    class UnauthorizedClient:
        async def connect(self) -> None:
            calls.append("connect")

        async def is_user_authorized(self) -> bool:
            return False

        async def disconnect(self) -> None:
            calls.append("disconnect")

        async def start(self) -> None:  # pragma: no cover - не должен вызываться
            pytest.fail("start() спрашивает номер и код через input()")

        async def get_me(self) -> SimpleNamespace:  # pragma: no cover
            pytest.fail("до get_me дойти не должны")

        async def run_until_disconnected(self) -> None:  # pragma: no cover
            pytest.fail("без сессии слушать нечего")

    monkeypatch.setattr(
        userbot, "settings", replace(userbot.settings, api_id=1, api_hash="test-hash")
    )
    monkeypatch.setattr(userbot, "build_client", UnauthorizedClient)
    await userbot.run()
    assert calls == ["connect", "disconnect"]
