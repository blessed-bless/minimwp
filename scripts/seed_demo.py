"""Готовит базу к демонстрации: один лид руками и теги на существующих."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def main() -> int:
    auth = (settings.crm_user, settings.crm_password)
    with httpx.Client(base_url=BASE, auth=auth, timeout=20, follow_redirects=False) as client:
        created = client.post(
            "/leads",
            data={
                "name": "Мария Ковалёва",
                "contact": "maria@agency-demo.ru",
                "request_text": "Нужен ребрендинг и новый сайт к декабрю",
                "tags": "сайт, дизайн",
            },
        )
        print(f"ручной лид: {created.status_code} {created.headers.get('location')}")
        for lead_id, tag in ((1, "срочно"), (4, "контекстная реклама"), (7, "срочно")):
            response = client.post(f"/leads/{lead_id}/tags", data={"tag": tag})
            print(f"тег «{tag}» на лид {lead_id}: {response.status_code}")
        for tag in ("срочно", "сайт"):
            page = client.get("/leads", params={"tag": tag})
            print(f"фильтр «{tag}»: карточек {page.text.count('class=\"lead\"')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
