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


class TaskIn(BaseModel):
    kind: str = "rehearsal"
    goal: str = ""
    to_agent: str = "stage_manager"
    from_agent: str = "author"


class TaskPatch(BaseModel):
    status: str
    note: str = ""


class ToolCallIn(BaseModel):
    args: dict = {}
    confirm: bool = False


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


@router.get("/books/{book_id}/rehearsal-plan")
async def book_rehearsal_plan(book_id: str, svc: SimulationService = Depends(get_service)):
    """C 批：彩排建议（主笔视角的"重头戏判定"，0-token）+ 每场已排演回合数。"""
    return await svc.book_rehearsal_plan(book_id)


@router.get("/books/{book_id}/material-usage")
async def book_material_usage(book_id: str, svc: SimulationService = Depends(get_service)):
    """C 批：素材使用率（已采纳素材 vs 正文命中）——启发式文本命中法。"""
    return await svc.book_material_usage(book_id)


@router.get("/books/{book_id}/constraints")
async def book_constraints(book_id: str, svc: SimulationService = Depends(get_service)):
    """书级约束表（B 批）：主笔维护、**全员读**。0-token 派生（不新建表），缺失项走 dropped 留痕。"""
    from app.services.agents.constraints import build_constraints

    return await build_constraints(svc.repo, book_id)


@router.get("/agent/specs")
async def agent_specs():
    """能力声明 + 写权限矩阵（**草案**，尚未强制）：让"谁能读/写什么"从口头约定变成可见数据。"""
    from app.services.agents.permissions import matrix
    from app.services.agents.spec import SPECS

    return {
        "specs": [
            {
                "id": s.id, "name": s.name, "scope": s.scope, "persona": s.persona,
                "reads": list(s.reads), "writes": list(s.writes), "tools": list(s.tools),
                "model_tier": s.model_tier, "emits": list(s.emits),
                "can_initiate_tasks": s.can_initiate_tasks, "needs_confirm": list(s.needs_confirm),
            }
            for s in SPECS.values()
        ],
        "write_matrix": matrix(),
    }


@router.get("/scenes/{scene_id}/agent/tasks")
async def list_tasks(scene_id: str, status: str = "", svc: SimulationService = Depends(get_service)):
    """本场任务单（请求彩排 / 曝光 / 裁决 / 重写）。"""
    return await svc.agent_task_list(scene_id=scene_id, status=status)


@router.post("/scenes/{scene_id}/agent/tasks", status_code=201)
async def create_task(scene_id: str, body: TaskIn, svc: SimulationService = Depends(get_service)):
    """发起任务。**角色 agent 会被拒**（409）：发起者边界不靠约定，靠代码。"""
    from app.services.agents.tasks import TaskDenied

    try:
        return await svc.agent_task_create(
            scene_id, goal=body.goal, kind=body.kind, to_agent=body.to_agent, from_agent=body.from_agent
        )
    except TaskDenied as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.patch("/agent/tasks/{task_id}")
async def patch_task(task_id: str, body: TaskPatch, svc: SimulationService = Depends(get_service)):
    """推进任务状态（submitted→working→input-required→completed/failed）。"""
    from app.services.agents.tasks import TaskDenied

    try:
        return await svc.agent_task_transition(task_id, body.status, note=body.note)
    except TaskDenied as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/agent/tasks/{task_id}/messages")
async def task_messages(task_id: str, svc: SimulationService = Depends(get_service)):
    """任务消息（request / response / notify）——只传请求与裁决，不传状态。"""
    return await svc.agent_task_messages(task_id)


@router.post("/scenes/{scene_id}/tools/{tool_name}")
async def call_tool_endpoint(
    scene_id: str, tool_name: str, body: ToolCallIn, svc: SimulationService = Depends(get_service)
):
    """作者直调工具（= 界面按钮）。**与责编对话走同一条工具链**：同一份声明、同一道闸门、同一套审计。

    409 = 被拒（未知工具 / 越权 / 缺参 / 破坏性操作未确认）——前端据此弹确认后带 confirm=true 重试。
    """
    from app.services.agents.executor import ToolDenied, call_tool

    try:
        res = await call_tool(svc, "", tool_name, body.args, who="author", confirm=body.confirm, scene_id=scene_id)
    except ToolDenied as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return res.to_dict()


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
