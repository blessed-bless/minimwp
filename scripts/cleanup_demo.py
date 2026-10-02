"""Удаляет лидов, оставшихся от проверок, и показывает, что осталось в базе."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

BASE = "http://127.0.0.1:8000"
JUNK_IDS = [int(arg) for arg in sys.argv[1:]] or [2, 3, 5]


def main() -> int:
    auth = (settings.crm_user, settings.crm_password)
    with httpx.Client(base_url=BASE, auth=auth, timeout=20) as client:
        for lead_id in JUNK_IDS:
            response = client.post(f"/leads/{lead_id}/delete")
            print(f"лид {lead_id}: {response.status_code}")
        page = client.get("/leads")
        print(f"осталось карточек в списке: {page.text.count('class=\"lead\"')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
