"""Проверка CRM по HTTP на собственных данных с очисткой после проверки."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402


async def check(client: httpx.AsyncClient) -> None:
    suffix = uuid4().hex
    names = [f"Проверка А {suffix}", f"Проверка Б {suffix}"]
    tag = f"проверка-{suffix}"
    created: list[str] = []
    try:
        page = await client.get("/leads")
        assert page.status_code == 200, "Список лидов недоступен"
        for name in names:
            response = await client.post(
                "/leads", data={"name": name, "contact": "smoke@example.test", "request_text": suffix}
            )
            location = response.headers.get("location", "")
            assert response.status_code == 303 and re.fullmatch(r"/leads/\d+", location), "Лид не создан"
            created.append(location)
        tagged = await client.post(f"{created[0]}/tags", data={"tag": tag})
        assert tagged.status_code == 303, "Тег не добавлен"
        card = await client.get(created[0])
        assert card.status_code == 200 and tag in card.text, "Тег не виден в карточке"
        filtered = await client.get("/leads", params={"tag": tag, "q": suffix})
        assert filtered.status_code == 200 and names[0] in filtered.text and names[1] not in filtered.text, "Фильтр неверен"
        anon = await client.get("/leads", auth=None)
        assert anon.status_code == 401, "Нет защиты авторизацией"
    finally:
        failed = False
        for location in reversed(created):
            try:
                response = await client.post(f"{location}/delete")
                failed = failed or response.status_code != 204
            except httpx.HTTPError:
                failed = True
        if failed:
            raise RuntimeError("Не удалось удалить проверочные лиды")


async def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    try:
        async with httpx.AsyncClient(
            base_url=base, auth=(settings.crm_user, settings.crm_password),
            follow_redirects=False, timeout=20,
        ) as client:
            await check(client)
    except (httpx.HTTPError, AssertionError, RuntimeError) as exc:
        print(f"Проверка не пройдена: {type(exc).__name__}")
        return 1
    print("Проверены создание, карточка, тег, поиск, фильтр и авторизация; данные удалены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
