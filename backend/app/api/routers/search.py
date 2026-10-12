"""Api 层 · search 路由：跨书搜索（书名 / 章节 / 场景 / 角色 / 伏笔）。

缘起（B38 后续）：概览复盘页头部有个「跨书搜索角色、伏笔、章节…」输入框，
但既没有 handler、后端也没有接口——敲进去什么都没有（用户实测反馈）。
本路由只做**字面包含**匹配，不做分词/语义（那是素材库 RAG 的活）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_service
from app.services.service import SimulationService

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.get("/search")
async def search_everything(q: str = Query("", max_length=80),
                            limit: int = Query(8, ge=1, le=30),
                            svc: SimulationService = Depends(get_service)):
    """跨书搜索：返回带 path 的统一命中列表（前端直接跳）。"""
    return await svc.search_all(q, limit)
