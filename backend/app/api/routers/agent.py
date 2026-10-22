"""Agent 层路由：责编对话（计划式工具调用）+ 段落批注 CRUD + 工具清单。

设计依据：docs/正文协作副驾与Agent协作架构.md §7/§8。
按钮与对话走**同一套工具**（ToolRegistry/ToolExecutor），因此审计一致、闸门一致。
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_service
from app.services.agents.editor import editor_chat, editor_chat_stream
from app.services.agents.registry import list_tools
from app.services.service import SimulationService

router = APIRouter(prefix="/api/v1", tags=["agent"])


class EditorChatIn(BaseModel):
    message: str = ""
    text: str = ""
    who: str = "author"


class RewriteIn(BaseModel):
    """按建议改稿（v1.69）：候选不落库，作者采纳才写正文。"""
    instruction: str = ""
    scope: str = "chapter"            # chapter | para | scene
    para_index: int = 0
    scene_id: str = ""
    text: str = ""


class ApplyNoteIn(BaseModel):
    """采纳候选（v1.69）：必须 confirm；based_on 用于"候选过期"校验。"""
    confirm: bool = False
    based_on: str = ""


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


class ToolRunIn(BaseModel):
    """按钮直调（SSE 版 · v1.43）：工具名 + 参数 + 是否已确认。"""
    tool: str = Field(min_length=1)
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


@router.post("/scenes/{scene_id}/agent/chat/stream")
async def agent_chat_stream(scene_id: str, body: EditorChatIn, svc: SimulationService = Depends(get_service)):
    """责编对话（SSE 流式 · v1.39）：逐阶段上报"我在干什么"。

    event 序列：percept → plan → tool_start/tool_done（逐个）→ reply → done。
    前端把每个阶段渲染成过程卡，作者不再对着"（责编在想…）"黑等。
    """
    import json as _json

    async def gen():
        try:
            async for ev in editor_chat_stream(svc, scene_id, body.message, body.text, body.who or "author"):
                yield f"event: {ev['event']}\ndata: {_json.dumps(ev['data'], ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001  流中异常也要让前端看到，不能静默断流
            yield f"event: error\ndata: {_json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/scenes/{scene_id}/agent/run/stream")
async def agent_run_stream(scene_id: str, body: ToolRunIn, svc: SimulationService = Depends(get_service)):
    """**按钮**直调工具（SSE · v1.43）：复杂按钮的执行过程也变成可见的阶段流。

    与 /agent/chat/stream 同一事件词汇（tool_start / tick / tool_done / done），
    前端因此能用同一张过程卡渲染"按钮"与"对话"两条路径——作者点「体检」也能看到
    它在读什么、跑了多久、查出几条、明细是什么，而不是只等一句"完成"。
    """
    import json as _json

    from app.services.agents.editor import tool_run_stream

    async def gen():
        try:
            async for ev in tool_run_stream(svc, scene_id, body.tool, body.args,
                                            who="author", confirm=body.confirm):
                yield f"event: {ev['event']}\ndata: {_json.dumps(ev['data'], ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001  流中异常也要让前端看到，不能静默断流
            yield f"event: error\ndata: {_json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/books/{book_id}/rehearsal-plan")
async def book_rehearsal_plan(book_id: str, svc: SimulationService = Depends(get_service)):
    """C 批：彩排建议（主笔视角的"重头戏判定"，0-token）+ 每场已排演回合数。"""
    return await svc.book_rehearsal_plan(book_id)
# ---------------------------------------------------------------- 章级副驾（v1.65 · 两个页面合并）
# 合并后「章」是主线单位：责编在章级干活（读整章 + 前情 + 记忆五段 + 场清单），
# 场级路由保留给拆了场的精修路径（老链接不失效，但页面上不再有第二个写作入口）。
@router.post("/chapters/{chapter_id}/agent/chat")
async def chapter_agent_chat(chapter_id: str, body: EditorChatIn,
                             svc: SimulationService = Depends(get_service)):
    """责编对话（章级）：感知整章 → 计划 → 同一套工具链执行 → 汇报。"""
    return await editor_chat(svc, "", body.message, body.text, body.who or "author",
                             chapter_id=chapter_id)


@router.post("/chapters/{chapter_id}/agent/chat/stream")
async def chapter_agent_chat_stream(chapter_id: str, body: EditorChatIn,
                                    svc: SimulationService = Depends(get_service)):
    """责编对话（章级 · SSE）：事件词汇与场级完全一致，前端复用同一张过程卡。"""
    import json as _json

    async def gen():
        try:
            async for ev in editor_chat_stream(svc, "", body.message, body.text,
                                               body.who or "author", chapter_id=chapter_id):
                data = _json.dumps(ev["data"], ensure_ascii=False)
                yield "event: " + ev["event"] + chr(10) + "data: " + data + chr(10) + chr(10)
        except Exception as e:  # noqa: BLE001  流中异常也要让前端看到，不能静默断流
            err = _json.dumps({"error": str(e)}, ensure_ascii=False)
            yield "event: error" + chr(10) + "data: " + err + chr(10) + chr(10)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chapters/{chapter_id}/agent/run/stream")
async def chapter_agent_run_stream(chapter_id: str, body: ToolRunIn,
                                   svc: SimulationService = Depends(get_service)):
    """章级按钮直调工具（SSE）：与对话路径同一事件词汇（tool_start/tick/tool_done/done）。"""
    import json as _json

    from app.services.agents.editor import tool_run_stream

    async def gen():
        try:
            async for ev in tool_run_stream(svc, "", body.tool, body.args,
                                            who="author", confirm=body.confirm,
                                            chapter_id=chapter_id):
                data = _json.dumps(ev["data"], ensure_ascii=False)
                yield "event: " + ev["event"] + chr(10) + "data: " + data + chr(10) + chr(10)
        except Exception as e:  # noqa: BLE001
            err = _json.dumps({"error": str(e)}, ensure_ascii=False)
            yield "event: error" + chr(10) + "data: " + err + chr(10) + chr(10)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/chapters/{chapter_id}/agent/notes")
async def chapter_agent_notes(chapter_id: str, limit: int = 100,
                              svc: SimulationService = Depends(get_service)):
    """章级审校审计记录（体检/质检/润色/AI 味/工具调用留痕），ts 倒序。"""
    rows = await svc.repo.list_chapter_notes(chapter_id, limit=limit)
    return {"chapter_id": chapter_id, "notes": rows, "count": len(rows)}


class MergeScenesIn(BaseModel):
    scene_id: str = ""
    confirm: bool = False


@router.post("/chapters/{chapter_id}/rewrite")
async def chapter_rewrite(chapter_id: str, body: RewriteIn,
                          svc: SimulationService = Depends(get_service)):
    """按一条审校建议产候选（v1.69 · 只出候选不落库）。"""
    return await svc.chapter_rewrite(
        chapter_id=chapter_id, instruction=body.instruction, scope=body.scope,
        para_index=body.para_index, scene_id=body.scene_id, text=body.text)


@router.post("/prose-notes/{note_id}/apply")
async def apply_prose_note(note_id: str, body: ApplyNoteIn,
                           svc: SimulationService = Depends(get_service)):
    """采纳候选：把 note.after 写进正文并自动存版本（v1.69 · 需 confirm）。"""
    if not body.confirm:
        raise HTTPException(status_code=409,
                            detail="采纳会改写正文（需 confirm=true）；覆盖前的旧正文会自动存版本")
    return await svc.apply_prose_note(note_id, confirm=True, based_on=body.based_on)
@router.post("/chapters/{chapter_id}/audit")
async def chapter_audit(chapter_id: str, svc: SimulationService = Depends(get_service)):
    """骨架遵循度审计（v1.67 · 只读）：0-token 判定 + LLM 语义判定 → 偏离项 + 建议，不改正文。"""
    return await svc.chapter_prose_audit(chapter_id)


@router.post("/chapters/{chapter_id}/merge-scenes")
async def chapter_merge_scenes(chapter_id: str, body: MergeScenesIn,
                               svc: SimulationService = Depends(get_service)):
    """把已推演的场正文并入章正文（破坏性：需显式 confirm）。

    语义（v1.65 定的真源律）：chapters.prose_md 是唯一真源；
    scenes.final_prose 只是「可选的来源快照」，并入后不再独立承担正式保存。
    """
    if not body.confirm:
        raise HTTPException(status_code=409,
                            detail="并入会改写章正文（需 confirm=true）；覆盖前的旧正文已自动存版本")
    return await svc.merge_scene_into_chapter(chapter_id, body.scene_id)



@router.get("/books/{book_id}/material-usage")
async def book_material_usage(book_id: str, svc: SimulationService = Depends(get_service)):
    """C 批：素材使用率（已采纳素材 vs 正文命中）——启发式文本命中法。"""
    return await svc.book_material_usage(book_id)


@router.get("/books/{book_id}/world-states")
async def book_world_states(
    book_id: str,
    kind: str | None = None,
    svc: SimulationService = Depends(get_service),
):
    """S1 世界状态账本只读浏览（设定页）：可按 kind 过滤；edited 标记区分作者手改。"""
    rows = await svc.repo.list_world_states(book_id, kind=kind)
    return {"book_id": book_id, "states": rows, "count": len(rows)}


class WorldStateIn(BaseModel):
    kind: str = Field(min_length=1)     # character/item/time/term/numeric
    name: str = Field(min_length=1)
    value: str = Field(min_length=1)
    key: str = ""
    attr: str = ""
    scene_no: int = 0


@router.post("/books/{book_id}/world-states", status_code=201)
async def create_world_state(book_id: str, body: WorldStateIn,
                             svc: SimulationService = Depends(get_service)):
    """作者手写世界状态（edited=True，质检不再覆盖）。同 (kind,key) 已有行则更新。"""
    return await svc.create_world_state(book_id, body.model_dump())


class WorldStatePatch(BaseModel):
    """PUT 用的**增量**模型（B30）：只传要改的字段；未传/传 null 一律保持原值。"""
    kind: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    value: str | None = Field(default=None, min_length=1)
    key: str | None = None
    attr: str | None = None
    scene_no: int | None = None


@router.put("/world-states/{ws_id}")
async def update_world_state(ws_id: str, body: WorldStatePatch,
                             svc: SimulationService = Depends(get_service)):
    """作者修改世界状态（增量：只传要改的字段；落库置 edited=True）。"""
    return await svc.update_world_state(ws_id, body.model_dump(exclude_unset=True))


@router.delete("/world-states/{ws_id}", status_code=204)
async def delete_world_state(ws_id: str, svc: SimulationService = Depends(get_service)):
    await svc.delete_world_state(ws_id)


@router.get("/books/{book_id}/constraints")
async def book_constraints(book_id: str, svc: SimulationService = Depends(get_service)):
    """书级约束表（B 批）：主笔维护、**全员读**。0-token 派生（不新建表），缺失项走 dropped 留痕。"""
    from app.services.agents.constraints import build_constraints

    return await build_constraints(svc.repo, book_id)


@router.get("/agent/specs")
async def agent_specs():
    """能力声明 + 写权限矩阵（**v1.30 已启用**：走工具链的行 enforced=True 并注明强制位置）。"""
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
