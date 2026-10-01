"""Проверка заявленных аудитом проблем на живом стенде (только чтение + свой лид)."""

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
    with httpx.Client(base_url=BASE, auth=auth, timeout=30, follow_redirects=False) as c:
        created = c.post(
            "/leads",
            data={"name": "Ирина Проверкина", "contact": "+70000000000",
                  "request_text": "Кириллица в поиске", "tags": "очень срочно"},
        )
        lead_url = created.headers.get("location", "")
        print(f"создан лид: {created.status_code} {lead_url}")

        up = c.get("/leads", params={"q": "Ирина"}).text
        low = c.get("/leads", params={"q": "ирина"}).text
        lat = c.get("/leads", params={"q": "irina"}).text
        print(f"поиск 'Ирина': {'Ирина Проверкина' in up}")
        print(f"поиск 'ирина' (строчная): {'Ирина Проверкина' in low}")
        print(f"поиск латиницей 'irina': {'Ирина Проверкина' in lat}")

        card = c.get(lead_url).text if lead_url else ""
        print(f"тег 'очень срочно' в карточке целиком: {'очень срочно' in card}")
        print(f"тег распался на два ('очень' и 'срочно'): "
              f"{('>очень<' in card) and ('>срочно<' in card)}")

        big = c.get("/leads/99999999999999999999")
        print(f"огромный id -> {big.status_code}")
        missing = c.get("/leads/424242")
        print(f"несуществующий id -> {missing.status_code}")

        if lead_url:
            lead_id = lead_url.rstrip("/").split("/")[-1]
            page = c.get(lead_url).text
            import re
            for tag_id in re.findall(rf"/leads/{lead_id}/tags/(\d+)/delete", page):
                c.post(f"/leads/{lead_id}/tags/{tag_id}/delete")
            print(f"теги убраны у лида {lead_id}; удалить лид вручную: {lead_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
