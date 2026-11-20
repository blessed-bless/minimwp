"""Lead operations shared by web CRM, bot, and userbot."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Select, case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import lower_expr
from app.models import Lead, LeadSource, LeadStatus, Tag

PAGE_SIZE = 50


def normalize_tag(raw: str) -> str:
    """Tag is a filter key, so fold case and strip extra spaces.

    Пробел внутри названия сохраняется: «контекстная реклама» — один тег.
    """
    return " ".join(raw.strip().lstrip("#").split()).lower()[:48]


def parse_tags(raw: str) -> list[str]:
    """Comma-separated tags field: split only on commas, as the UI promises."""
    return [part for part in (chunk.strip() for chunk in raw.split(",")) if part]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtered_leads(tag: str | None, q: str) -> Select[tuple[Lead]]:
    stmt = select(Lead)
    if tag:
        stmt = stmt.where(Lead.tags.any(Tag.name == normalize_tag(tag)))
    needle = q.strip().lower()
    if needle:
        pattern = f"%{_escape_like(needle)}%"
        stmt = stmt.where(
            or_(
                lower_expr(Lead.name).like(pattern, escape="\\"),
                lower_expr(Lead.contact).like(pattern, escape="\\"),
                lower_expr(Lead.request).like(pattern, escape="\\"),
            )
        )
    return stmt


async def list_leads(
    session: AsyncSession,
    tag: str | None = None,
    q: str = "",
    page: int = 1,
) -> list[Lead]:
    stmt = _filtered_leads(tag, q).order_by(Lead.created_at.desc(), Lead.id.desc())
    stmt = stmt.limit(PAGE_SIZE).offset((max(page, 1) - 1) * PAGE_SIZE)
    result = await session.scalars(stmt)
    return list(result.unique())


async def count_leads(session: AsyncSession, tag: str | None = None, q: str = "") -> int:
    stmt = select(func.count()).select_from(_filtered_leads(tag, q).subquery())
    return await session.scalar(stmt) or 0


async def get_lead(session: AsyncSession, lead_id: int) -> Lead | None:
    return await session.get(Lead, lead_id)


async def get_lead_by_chat(session: AsyncSession, chat_id: int) -> Lead | None:
    stmt = (
        select(Lead)
        .where(Lead.tg_chat_id == chat_id, Lead.source == LeadSource.TELEGRAM_DM.value)
        .order_by(Lead.id.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


async def create_lead(
    session: AsyncSession,
    *,
    name: str,
    contact: str = "",
    request: str = "",
    source: LeadSource = LeadSource.MANUAL,
    tg_chat_id: int | None = None,
    tags: Iterable[str] = (),
) -> Lead:
    lead = Lead(
        name=name.strip()[:128] or "Без имени",
        contact=contact.strip()[:128],
        request=request.strip(),
        source=LeadSource(source).value,
        status=LeadStatus.NEW.value,
        tg_chat_id=tg_chat_id,
        tags=[],
    )
    session.add(lead)
    for raw in tags:
        await _attach_tag(session, lead, raw)
    await session.commit()
    # expire_on_commit=False: fields stay readable after a successful commit.
    return lead


async def update_lead(
    session: AsyncSession,
    lead: Lead,
    *,
    name: str | None = None,
    contact: str | None = None,
    request: str | None = None,
    status: str | None = None,
) -> Lead:
    if name is not None:
        lead.name = name.strip()[:128] or lead.name
    if contact is not None:
        lead.contact = contact.strip()[:128]
    if request is not None:
        lead.request = request.strip()
    if status is not None and status in {s.value for s in LeadStatus}:
        lead.status = status
    await session.commit()
    await session.refresh(lead)
    return lead


async def append_to_request(session: AsyncSession, lead: Lead, text: str) -> Lead:
    """Append a message to the lead request — used by the userbot.

    Склейка делается одним UPDATE на стороне базы, а не «прочитали в Python,
    собрали строку, записали»: два сообщения из одного чата могут обрабатываться
    параллельно, и при чтении в память одно дополнение затирало бы другое.
    """
    addition = text.strip()
    if not addition:
        return lead
    merged = case(
        (func.coalesce(Lead.request, "") == "", addition),
        else_=Lead.request + "\n" + addition,
    )
    await session.execute(update(Lead).where(Lead.id == lead.id).values(request=merged))
    await session.commit()
    await session.refresh(lead)
    return lead


async def get_or_create_tag(session: AsyncSession, name: str) -> Tag:
    """Create a tag or return the existing one, surviving a two-session race."""
    normalized = normalize_tag(name)
    tag = await session.scalar(select(Tag).where(Tag.name == normalized))
    if tag is not None:
        return tag
    try:
        # SAVEPOINT: on unique conflict roll back only the tag insert, not the lead tx.
        async with session.begin_nested():
            tag = Tag(name=normalized)
            session.add(tag)
            await session.flush()
    except IntegrityError:
        tag = await session.scalar(select(Tag).where(Tag.name == normalized))
        if tag is None:
            raise
    return tag


async def _attach_tag(session: AsyncSession, lead: Lead, name: str) -> None:
    normalized = normalize_tag(name)
    if not normalized:
        return
    tag = await get_or_create_tag(session, normalized)
    if tag.name not in {t.name for t in lead.tags}:
        lead.tags.append(tag)


async def add_tag(session: AsyncSession, lead: Lead, name: str) -> Lead:
    await _attach_tag(session, lead, name)
    await session.commit()
    await session.refresh(lead)
    return lead


async def remove_tag(session: AsyncSession, lead: Lead, tag_id: int) -> Lead:
    lead.tags = [t for t in lead.tags if t.id != tag_id]
    await session.commit()
    await session.refresh(lead)
    return lead


async def list_tags(session: AsyncSession) -> list[Tag]:
    result = await session.scalars(select(Tag).order_by(Tag.name))
    return list(result)


async def delete_lead(session: AsyncSession, lead: Lead) -> None:
    tag_ids = [tag.id for tag in lead.tags]
    await session.delete(lead)
    await session.flush()
    if tag_ids:
        await session.execute(delete(Tag).where(Tag.id.in_(tag_ids), ~Tag.leads.any()))
    await session.commit()
