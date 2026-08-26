"""SQLAlchemy 2.0 异步引擎与会话工厂。

数据访问统一走这里：dsn 用 `postgresql+asyncpg://`（密码经 quote 编码）。
配置读取自根目录 .env（见 config.py 的 env_file）。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker

from app.config import settings

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_pool_closed = False


def get_async_engine() -> AsyncEngine:
    """全局单例 async engine（懒创建）。"""
    global _engine
    if _engine is None:
        dsn = settings.dsn.replace("postgresql://", "postgresql+asyncpg://") if settings.persist else None
        if dsn is None:
            raise RuntimeError("persist=False 时不应请求数据库引擎")
        _engine = create_async_engine(dsn, pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """全局单例 async sessionmaker（懒创建）。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_async_engine(), expire_on_commit=False)
    return _session_factory


async def close_engine() -> None:
    """应用关闭时释放连接池（挂到 FastAPI lifespan）。"""
    global _engine, _session_factory, _pool_closed
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    _session_factory = None
    _pool_closed = True


def engine_configured() -> bool:
    """settings.persist=True 且连接可配置时返回 True（用于内存态判定）。"""
    return settings.persist


if __name__ == "__main__":  # pragma: no cover
    async def _ping() -> None:
        eng = get_async_engine()
        async with eng.connect() as conn:
            print("DB 连接 OK:", await conn.exec_driver_sql("SELECT 1"))

    asyncio.run(_ping())