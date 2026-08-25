"""Api 层 · 依赖注入：把 Repository / Service 交给 FastAPI 管理。

数据会话来自 `app.data.session.get_db()`（`Depends` 注入，用户明确要求）。
```
"""
from __future__ import annotations

from fastapi import Depends

from app.data.repo import Repo
from app.data.session import get_db


def get_repo(db=Depends(get_db)) -> Repo:
    """数据会话依赖注入 → 实例化 Repo。"""
    return Repo(db)


def get_service(repo: Repo = Depends(get_repo)) -> "SimulationService":
    from app.services.service import SimulationService

    return SimulationService(repo)