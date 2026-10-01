"""HTTP-слой CRM: список лидов, карточка, ручное добавление, теги."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path as FilePath
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Query, Request, status
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app import crud
from app.config import settings
from app.db import get_session
from app.models import STATUS_LABELS, LeadSource, LeadStatus

templates = Jinja2Templates(directory=str(FilePath(__file__).parent / "templates"))

# SQLite хранит id как 64-битное целое: всё, что больше, — заведомо не наш лид.
MAX_DB_INT = 9223372036854775807
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def local_dt(value: datetime, fmt: str = "%d.%m %H:%M") -> str:
    """В базе время в UTC, показываем в часовом поясе сервера."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime(fmt)


templates.env.filters["local_dt"] = local_dt

security = HTTPBasic()

SessionDep = Annotated[AsyncSession, Depends(get_session)]
LeadIdDep = Annotated[int, Path(ge=1, le=MAX_DB_INT)]
TagIdDep = Annotated[int, Path(ge=1, le=MAX_DB_INT)]


def require_same_origin(request: Request) -> None:
    """Защита изменяющих запросов от отправки формы с чужого сайта.

    Basic Auth подтверждает, кто пользователь, но не то, что действие он затеял сам:
    браузер приложит учётные данные и к форме с постороннего домена. Поэтому для
    POST сверяем Origin (или Referer) с адресом CRM.

    Если источника нет вовсе — пропускаем: так ходят curl и скрипты проверки,
    а браузер в межсайтовом POST Origin проставляет всегда, и такой запрос
    отсечётся по несовпадению.
    """
    if request.method in SAFE_METHODS:
        return
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return
    host = request.headers.get("host", "")
    if urlsplit(source).netloc != host:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Источник запроса не совпадает с адресом CRM",
        )


router = APIRouter(dependencies=[Depends(require_same_origin)])


async def render_http_error(request: Request, exc: StarletteHTTPException) -> Response:
    accept = request.headers.get("accept", "")
    if "text/html" not in accept or request.url.path == "/healthz" or request.url.path.startswith("/api"):
        return await http_exception_handler(request, exc)
    messages = {
        401: "Для входа в CRM нужны логин и пароль.",
        403: "Запрос пришёл со стороннего адреса и отклонён.",
        404: "Страница или лид не найдены.",
        422: "Проверьте поля формы и параметры поиска. Имя и контакт — до 128 символов, статус — из списка.",
    }
    return templates.TemplateResponse(
        request, "error.html",
        {"code": exc.status_code, "message": messages.get(
            exc.status_code, exc.detail if exc.status_code == 400 else "Не удалось выполнить запрос."
        )},
        status_code=exc.status_code, headers=exc.headers,
    )


async def render_validation_error(request: Request, exc: RequestValidationError) -> Response:
    if "text/html" not in request.headers.get("accept", "") or request.url.path.startswith("/api"):
        return await request_validation_exception_handler(request, exc)
    return await render_http_error(request, StarletteHTTPException(status_code=422))


def require_auth(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> str:
    user_ok = secrets.compare_digest(credentials.username, settings.crm_user)
    password_ok = secrets.compare_digest(credentials.password, settings.crm_password)
    if not (user_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


AuthDep = Annotated[str, Depends(require_auth)]


def _back_to_lead(lead_id: int) -> RedirectResponse:
    return RedirectResponse(f"/leads/{lead_id}", status_code=status.HTTP_303_SEE_OTHER)


async def _require_lead(session: AsyncSession, lead_id: int) -> crud.Lead:
    lead = await crud.get_lead(session, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Лид не найден")
    return lead


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/", include_in_schema=False)
async def index(_: AuthDep) -> RedirectResponse:
    return RedirectResponse("/leads", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/leads", response_class=HTMLResponse)
async def leads_page(
    request: Request,
    session: SessionDep,
    _: AuthDep,
    tag: Annotated[str | None, Query(max_length=64)] = None,
    q: Annotated[str, Query(max_length=256)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
) -> HTMLResponse:
    total = await crud.count_leads(session, tag=tag, q=q)
    pages = max(1, (total + crud.PAGE_SIZE - 1) // crud.PAGE_SIZE)
    page = min(page, pages)
    leads = await crud.list_leads(session, tag=tag, q=q, page=page)
    tags = await crud.list_tags(session)
    return templates.TemplateResponse(
        request,
        "leads.html",
        {"leads": leads, "tags": tags, "active_tag": crud.normalize_tag(tag) if tag else None,
         "q": q, "page": page, "total": total, "pages": pages},
    )


@router.post("/leads")
async def create_lead_manual(
    session: SessionDep,
    _: AuthDep,
    name: Annotated[str, Form(max_length=128)],
    contact: Annotated[str, Form(max_length=128)] = "",
    request_text: Annotated[str, Form()] = "",
    tags: Annotated[str, Form(max_length=512)] = "",
) -> RedirectResponse:
    if not name.strip():
        raise HTTPException(status_code=400, detail="Имя обязательно")
    lead = await crud.create_lead(
        session,
        name=name,
        contact=contact,
        request=request_text,
        source=LeadSource.MANUAL,
        tags=crud.parse_tags(tags),
    )
    return _back_to_lead(lead.id)


@router.get("/leads/{lead_id}", response_class=HTMLResponse)
async def lead_page(
    request: Request, lead_id: LeadIdDep, session: SessionDep, _: AuthDep
) -> HTMLResponse:
    lead = await _require_lead(session, lead_id)
    return templates.TemplateResponse(
        request,
        "lead.html",
        {"lead": lead, "statuses": [(s.value, STATUS_LABELS[s]) for s in LeadStatus]},
    )


@router.post("/leads/{lead_id}")
async def edit_lead(
    lead_id: LeadIdDep,
    session: SessionDep,
    _: AuthDep,
    name: Annotated[str, Form(max_length=128)],
    contact: Annotated[str, Form(max_length=128)] = "",
    request_text: Annotated[str, Form()] = "",
    lead_status: Annotated[LeadStatus, Form()] = LeadStatus.NEW,
) -> RedirectResponse:
    lead = await _require_lead(session, lead_id)
    if not name.strip():
        raise HTTPException(status_code=400, detail="Имя обязательно")
    await crud.update_lead(
        session, lead, name=name, contact=contact, request=request_text, status=lead_status
    )
    return _back_to_lead(lead_id)


@router.post("/leads/{lead_id}/tags")
async def add_lead_tag(
    lead_id: LeadIdDep, session: SessionDep, _: AuthDep,
    tag: Annotated[str, Form(max_length=64)],
) -> RedirectResponse:
    lead = await _require_lead(session, lead_id)
    if not crud.normalize_tag(tag):
        raise HTTPException(status_code=400, detail="Введите название тега")
    await crud.add_tag(session, lead, tag)
    return _back_to_lead(lead_id)


@router.post("/leads/{lead_id}/tags/{tag_id}/delete")
async def delete_lead_tag(
    lead_id: LeadIdDep, tag_id: TagIdDep, session: SessionDep, _: AuthDep
) -> RedirectResponse:
    lead = await _require_lead(session, lead_id)
    await crud.remove_tag(session, lead, tag_id)
    return _back_to_lead(lead_id)


@router.post("/leads/{lead_id}/delete", status_code=204)
async def delete_lead(lead_id: LeadIdDep, session: SessionDep, _: AuthDep) -> Response:
    lead = await _require_lead(session, lead_id)
    await crud.delete_lead(session, lead)
    return Response(status_code=204)
