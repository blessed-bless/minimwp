"""Модель данных CRM: лид и теги (many-to-many)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import Column, DateTime, ForeignKey, String, Table, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LeadSource(StrEnum):
    TELEGRAM_BOT = "telegram_bot"
    TELEGRAM_DM = "telegram_dm"
    MANUAL = "manual"


class LeadStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


SOURCE_LABELS = {
    LeadSource.TELEGRAM_BOT: "Бот",
    LeadSource.TELEGRAM_DM: "Личка Telegram",
    LeadSource.MANUAL: "Вручную",
}

STATUS_LABELS = {
    LeadStatus.NEW: "Новый",
    LeadStatus.IN_PROGRESS: "В работе",
    LeadStatus.CLOSED: "Закрыт",
}

lead_tags = Table(
    "lead_tags",
    Base.metadata,
    Column("lead_id", ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(48), unique=True, index=True)

    leads: Mapped[list["Lead"]] = relationship(secondary=lead_tags, back_populates="tags")

    def __repr__(self) -> str:
        return f"<Tag {self.name}>"


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    contact: Mapped[str] = mapped_column(String(128), default="")
    request: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default=LeadSource.MANUAL.value)
    status: Mapped[str] = mapped_column(String(32), default=LeadStatus.NEW.value, index=True)
    tg_chat_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tags: Mapped[list[Tag]] = relationship(
        secondary=lead_tags, back_populates="leads", lazy="selectin", order_by=Tag.name
    )

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(LeadSource(self.source), self.source)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(LeadStatus(self.status), self.status)

    def __repr__(self) -> str:
        return f"<Lead {self.id} {self.name!r}>"
