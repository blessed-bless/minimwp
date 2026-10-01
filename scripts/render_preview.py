"""Сохраняет отрендеренные страницы в preview_*.html для глазной проверки."""

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
    with httpx.Client(base_url=BASE, auth=auth, timeout=20) as client:
        for path, out in (("/leads", "preview_leads.html"), ("/leads/1", "preview_lead.html")):
            response = client.get(path)
            (ROOT / out).write_text(response.text, encoding="utf-8")
            print(f"{path} -> {response.status_code}, {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
