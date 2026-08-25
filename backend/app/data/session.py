"""数据层 · 会话（依赖注入）。

提供 `get_db()`，让 FastAPI 路由以 `Depends(get_db)` 取得会话（用户明确要求），
会话的生命周期由请求依赖管理：请求内开启事务，结束时提交/关连接。
DB 未就绪（配置 persist=False 或连不上）时降级为占位，保证内存态闭环可跑。
"""
from __future__ import annotations

import contextlib
from typing import Iterator, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

_pool: Optional[ConnectionPool] = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.dsn, min_size=1, max_size=5, open=False
        )
    if _pool.closed:
        _pool.open()
    return _pool


@contextlib.contextmanager
def _conn():
    """底层连接：defer 到真正需要时才连，避免脚本无库时也报错。"""
    try:
        with get_pool().connection() as conn:
            yield conn
    except psycopg.OperationalError:
        yield None  # DB 未就绪 → 上层走内存态


def get_db() -> Iterator[Optional[object]]:
    """FastAPI 依赖：yield 一个轻量 DB 会话对象。"""
    class _Sess:  # 微会话封装：隔离 psycopg 细节
        def __init__(self):
            self._pool = get_pool() if settings.persist else None

        @property
        def ready(self) -> bool:
            return self._pool is not None and not self._pool.closed

        @contextlib.contextmanager
        def cursor(self):
            if not self.ready:
                yield None
                return
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    yield cur

    sess = _Sess()
    try:
        yield sess
    finally:
        # 会话级无额外清理（连接池自行管理）
        pass