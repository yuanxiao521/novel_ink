"""Api 层 · 依赖注入：把 async Repo / Service 交给 FastAPI 管理。

数据访问统一走 `app.db.repo.Repo`（SQLAlchemy 2.0 异步 ORM），
DB 未就绪时由 Repo 内部降级内存态兜底（无需显式会话注入）。
"""
from __future__ import annotations

from fastapi import Depends

from app.db.repo import Repo


async def get_repo() -> Repo:
    """实例化 async Repo（每次请求一个，内部共享引擎连接池）。"""
    return Repo()


def get_service(repo: Repo = Depends(get_repo)) -> "SimulationService":
    from app.services.service import SimulationService

    return SimulationService(repo)