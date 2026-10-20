"""SAWHost - Database (SQLite on /data or PostgreSQL via DATABASE_URL)"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from app.config import get_cfg


_engine: AsyncEngine | None = None
_factory: async_sessionmaker | None = None


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine: return _engine
    cfg = get_cfg()
    if cfg.is_sqlite:
        from sqlalchemy import event as ev
        _engine = create_async_engine(cfg.async_dsn, connect_args={"check_same_thread":False}, echo=False)
        @ev.listens_for(_engine.sync_engine, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            for p in ("PRAGMA foreign_keys=ON","PRAGMA journal_mode=WAL","PRAGMA synchronous=NORMAL"):
                cur.execute(p)
            cur.close()
    else:
        _engine = create_async_engine(cfg.async_dsn, pool_size=2, max_overflow=8, pool_pre_ping=True, echo=False)
    return _engine


def _get_factory() -> async_sessionmaker:
    global _factory
    if not _factory:
        _factory = async_sessionmaker(bind=_get_engine(), expire_on_commit=False, autoflush=False)
    return _factory


@asynccontextmanager
async def db() -> AsyncIterator[AsyncSession]:
    """استخدام: async with db() as s: s.add(obj); await s.commit()"""
    async with _get_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    import pathlib
    from app.models import Base  # noqa
    cfg = get_cfg()
    if cfg.is_sqlite:
        pathlib.Path(cfg.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    async with _get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
    if cfg.is_sqlite:   # قاعدة البيانات فيها توكنات وجلسات: مالكها فقط (يمنع قراءتها من عمليات المواقع)
        import os
        for suffix in ("", "-wal", "-shm"):
            try: os.chmod(cfg.sqlite_path + suffix, 0o600)
            except OSError: pass


def _add_missing_columns(sync_conn) -> None:
    """ترحيل خفيف: create_all لا يضيف أعمدة لجداول موجودة، فنضيف الناقص (ADD COLUMN) بأمان."""
    from sqlalchemy import inspect, text
    from app.models import Base
    insp = inspect(sync_conn)
    existing = set(insp.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            ddl = col.type.compile(dialect=sync_conn.dialect)
            sync_conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))


async def close_db() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
