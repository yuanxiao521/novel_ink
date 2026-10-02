"""Agent 层路由：责编对话（计划式工具调用）+ 段落批注 CRUD + 工具清单。

设计依据：docs/正文协作副驾与Agent协作架构.md §7/§8。
按钮与对话走**同一套工具**（ToolRegistry/ToolExecutor），因此审计一致、闸门一致。
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_service
from app.services.agents.editor import editor_chat
from app.services.agents.registry import list_tools
from app.services.service import SimulationService

router = APIRouter(prefix="/api/v1", tags=["agent"])


class EditorChatIn(BaseModel):
    message: str = ""
    text: str = ""
    who: str = "author"


class AnnotationIn(BaseModel):
    note: str = Field(min_length=1)
    para_index: int = 0
    quote: str = ""


class AnnotationPatch(BaseModel):
    status: str | None = None
    note: str | None = None
    handled_by_note_id: str | None = None


@router.get("/agent/tools")
async def agent_tools():
    """工具清单（含副作用等级）——前端按此分组按钮、决定是否弹确认。"""
    return {"tools": list_tools()}


@router.post("/scenes/{scene_id}/agent/chat")
async def agent_chat(scene_id: str, body: EditorChatIn, svc: SimulationService = Depends(get_service)):
    """责编对话：感知 → 计划 → 执行（同一工具链）→ 汇报。"""
    return await editor_chat(svc, scene_id, body.message, body.text, body.who or "author")


@router.get("/scenes/{scene_id}/annotations")
async def list_annotations(scene_id: str, status: str = "", svc: SimulationService = Depends(get_service)):
    """段落批注（status 为空 = 全部；open = 待处理）。"""
    return await svc.repo.list_annotations(scene_id, status=status)


@router.post("/scenes/{scene_id}/annotations", status_code=201)
async def create_annotation(scene_id: str, body: AnnotationIn, svc: SimulationService = Depends(get_service)):
    ann = {
        "id": f"ann-{uuid.uuid4().hex[:10]}",
        "scene_id": scene_id,
        "para_index": int(body.para_index or 0),
        "quote": body.quote or "",
        "note": body.note,
        "status": "open",
        "created_by": "author",
        "handled_by_note_id": "",
        "ts": int(time.time() * 1000),
    }
    await svc.repo.save_annotation(ann)
    return ann


@router.patch("/annotations/{annotation_id}")
async def patch_annotation(annotation_id: str, body: AnnotationPatch, svc: SimulationService = Depends(get_service)):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if patch.get("status") == "handled":
        patch["handled_at"] = datetime.now()
    out = await svc.repo.update_annotation(annotation_id, patch)
    if out is None:
        raise HTTPException(status_code=404, detail="批注不存在")
    return out


@router.delete("/annotations/{annotation_id}", status_code=204)
async def delete_annotation(annotation_id: str, svc: SimulationService = Depends(get_service)):
    await svc.repo.delete_annotation(annotation_id)
    return None
