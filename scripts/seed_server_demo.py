"""Наполняет базу на сервере парой демонстрационных лидов.

Запуск на сервере:
    sudo -u minicrm /opt/minicrm/.venv/bin/python /opt/minicrm/scripts/seed_server_demo.py

Настройки читает из /etc/minicrm.env, поэтому пароль CRM нигде не всплывает.
Повторный запуск ничего не дублирует.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SERVER_ENV = Path("/etc/minicrm.env")
if SERVER_ENV.exists():
    load_dotenv(SERVER_ENV)

from sqlalchemy import select  # noqa: E402

from app.crud import add_tag, create_lead  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Lead, LeadSource  # noqa: E402

DEMO = [
    {
        "name": "Мария Ковалёва",
        "contact": "maria@agency-demo.ru",
        "request": "Нужен ребрендинг и новый сайт к декабрю",
        "tags": ["сайт", "дизайн"],
    },
    {
        "name": "Сергей Литвинов",
        "contact": "+7 999 000-11-22",
        "request": "Настроить контекстную рекламу, бюджет обсудим",
        "tags": ["контекстная реклама", "срочно"],
    },
]


async def main() -> int:
    await init_db()
    async with SessionLocal() as session:
        for item in DEMO:
            exists = await session.scalar(select(Lead).where(Lead.name == item["name"]))
            if exists is not None:
                print(f"уже есть: {item['name']}")
                continue
            lead = await create_lead(
                session,
                name=item["name"],
                contact=item["contact"],
                request=item["request"],
                source=LeadSource.MANUAL,
            )
            for tag in item["tags"]:
                await add_tag(session, lead, tag)
            print(f"создан лид {lead.id}: {lead.name}")
        total = len(list(await session.scalars(select(Lead))))
        print(f"всего лидов в базе: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
