"""Асинхронный движок и фабрика сессий SQLAlchemy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql.elements import ColumnElement

from app.config import settings
from app.models import Base

engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

IS_SQLITE = engine.dialect.name == "sqlite"


def _py_lower(value: Any) -> Any:
    return value.lower() if isinstance(value, str) else value


if IS_SQLITE:
    # Встроенный lower() в SQLite умеет только латиницу: lower('Ирина') вернёт 'Ирина'.
    # Регистрируем питоновский lower() как SQL-функцию, иначе поиск по-русски
    # зависит от регистра. На Postgres это не нужно — там ILIKE знает юникод.
    @event.listens_for(engine.sync_engine, "connect")
    def _register_pylower(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.create_function("pylower", 1, _py_lower, deterministic=True)


def lower_expr(column: ColumnElement[str]) -> ColumnElement[str]:
    """Регистронезависимое приведение, работающее и с кириллицей."""
    return func.pylower(column) if IS_SQLITE else func.lower(column)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: сессия на один запрос."""
    async with SessionLocal() as session:
        yield session
