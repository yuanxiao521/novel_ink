"""Api 层 · simulation 路由：启动 / 恢复 / step / 状态 / 介入 / SSE 流式 / 四层目录。

全链路 async：数据经 async Repo（SQLAlchemy 2.0 ORM）走 DB，DB 未就绪时内存态兜底。
数据库会话经 `Depends` 注入（见 `app.api.deps`），路由不自行握手。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import get_service
from app.config import settings
from app.schemas import chief, models
from app.services.service import SimulationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["simulation"])

# SSE 节流：回合间步进节奏
_STREAM_INTERVAL = 0.25


def _sse(event: str, data: dict) -> str:
    """把一条事件序列化成 SSE 帧（event: 事件名\n data: JSON\n\n）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sim_stream(
    sim_id: str,
    svc: SimulationService,
    turns: Optional[int],
) -> AsyncGenerator[str, None]:
    """长连接生成器：逐回合、逐事件流式推送 SSE。

    每回合由 svc.stream_step()（graph.astream custom 模式）驱动——节点每完成一步
    （导演决策 / 角色思考 / 角色行动 / 成文 token）立即产出一条事件，本生成器即时
    转发为 SSE 帧；回合结束落库，继续下一回合，直至 收束/举手/超上限。
    """
    sim = await svc.get_state(sim_id)
    done = 0
    try:
        while True:
            # 终止条件：结束 / 收敛 / 举手待批 / 超回合上限 / 事件暂停 / （可选）固定回合数
            if (
                sim.ended
                or sim.director.converged
                or sim.director.raise_request.pending
                or sim.paused
                or sim.world.turn >= settings.max_turns
            ):
                break
            if turns is not None and done >= turns:
                break

            turn = sim.world.turn + 1
            yield _sse("turn_start", {"turn": turn})

            # 立即让角色"感知"先亮（导演 LLM 计划可能耗时几十秒，
            # 感知是即时的，先推给前端产生"开始动了"的实时感，避免空白等待）
            if sim.action_order:
                for char_id in sim.action_order:
                    yield _sse("character_perceive", {"turn": turn, "char_id": char_id})
            else:
                for char_id in sim.scratch.get("thoughts", {}):
                    yield _sse("character_perceive", {"turn": turn, "char_id": char_id})

            # 事件级流式步进：即到即转发（导演/角色思考/行动/成文 token…）
            async for event in svc.stream_step(sim_id):
                if not isinstance(event, dict) or "kind" not in event:
                    continue
                if event["kind"] == "character_perceive":
                    continue  # 已在回合开始时统一推过
                event["turn"] = event.get("turn", turn)
                yield _sse(event["kind"], event)

            # 回合结束状态刷新（stream_step 已落库）
            sim = await svc.get_state(sim_id)
            yield _sse("turn_end", {"turn": sim.world.turn})
            done += 1
            await asyncio.sleep(_STREAM_INTERVAL)

        # —— 场景收束：长程汇报 + 换场（S3）——
        next_scene: Optional[dict] = None
        if sim.director.converged:
            try:
                report = await svc.analyze_scene_close(sim_id)
                yield _sse("director_close", report)
            except Exception as e:  # noqa: BLE001 —— 汇报失败不阻塞收束
                logger.warning("[sim-stream] 收束汇报失败：%s", e)
            next_scene = await svc.next_scene_seed(sim_id)

        yield _sse("done", {
            "turn": sim.world.turn,
            "ended": bool(sim.ended),
            "converged": bool(sim.director.converged),
            "raise_pending": bool(sim.director.raise_request.pending),
            "paused": bool(sim.paused),
            "next_scene": next_scene,
        })
    except asyncio.CancelledError:
        # 客户端断连 → 终止流，不做清理（每回合已落库）
        raise


# ------------------------------------------------------------------ 四层目录
@router.get("/books")
async def list_books(svc: SimulationService = Depends(get_service)):
    """书籍列表（含章数/状态）。"""
    return await svc.list_books()


@router.get("/books/{book_id}/chapters")
async def list_chapters(book_id: str, svc: SimulationService = Depends(get_service)):
    return await svc.list_chapters(book_id)


@router.get("/books/{book_id}/tree")
async def book_tree(book_id: str, svc: SimulationService = Depends(get_service)):
    """聚合树：书 → 章 → 场景（含角色），一次拉全防客户端 N+1。"""
    tree = await svc.get_book_tree(book_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="未找到书籍")
    return tree


@router.get("/chapters/{chapter_id}/scenes")
async def list_scenes(chapter_id: str, svc: SimulationService = Depends(get_service)):
    return await svc.list_scenes(chapter_id)


@router.get("/scenes/{scene_id}")
async def scene_detail(scene_id: str, svc: SimulationService = Depends(get_service)):
    scene = await svc.get_scene(scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="未找到场景")
    scene["characters"] = await svc.get_scene_characters(scene_id)
    return scene


# ------------------------------------------------------------------ 四层写接口（S1）
class BookBody(models.BaseModel):
    title: str = ""
    genre: str = ""
    status: str = "planned"
    cover_init: str = "墨"
    synopsis: str = ""
    worldview_json: str = "{}"


class ChapterBody(models.BaseModel):
    title: str = ""
    summary: str = ""
    order_no: int = 0
    tone: str = ""
    tension_curve: str = ""
    word_target: int = 0


class SceneBody(models.BaseModel):
    title: str = ""
    scenario_def: str = "betrayal_night"
    initial_facts_json: str = "[]"
    plan_cfg_json: str = "{}"
    cursor_pos: int = 0
    stage_desc: str = ""


class CharacterBody(models.BaseModel):
    name: str = ""
    spec_json: str = "{}"


@router.post("/books", status_code=status.HTTP_201_CREATED)
async def create_book(body: BookBody, svc: SimulationService = Depends(get_service)):
    return await svc.create_book(body.model_dump(exclude_none=True))


@router.put("/books/{book_id}")
async def update_book(book_id: str, body: BookBody, svc: SimulationService = Depends(get_service)):
    data = body.model_dump(exclude_none=True)
    return await svc.update_book(book_id, data)


@router.delete("/books/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(book_id: str, svc: SimulationService = Depends(get_service)):
    await svc.delete_book(book_id)


@router.post("/books/{book_id}/chapters", status_code=status.HTTP_201_CREATED)
async def create_chapter(book_id: str, body: ChapterBody, svc: SimulationService = Depends(get_service)):
    return await svc.create_chapter(book_id, body.model_dump(exclude_none=True))


@router.put("/chapters/{chapter_id}")
async def update_chapter(chapter_id: str, body: ChapterBody, svc: SimulationService = Depends(get_service)):
    return await svc.update_chapter(chapter_id, body.model_dump(exclude_none=True))


@router.delete("/chapters/{chapter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chapter(chapter_id: str, svc: SimulationService = Depends(get_service)):
    await svc.delete_chapter(chapter_id)


@router.post("/chapters/{chapter_id}/scenes", status_code=status.HTTP_201_CREATED)
async def create_scene(chapter_id: str, body: SceneBody, svc: SimulationService = Depends(get_service)):
    return await svc.create_scene(chapter_id, body.model_dump(exclude_none=True))


@router.put("/scenes/{scene_id}")
async def update_scene(scene_id: str, body: SceneBody, svc: SimulationService = Depends(get_service)):
    return await svc.update_scene(scene_id, body.model_dump(exclude_none=True))


@router.delete("/scenes/{scene_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scene(scene_id: str, svc: SimulationService = Depends(get_service)):
    await svc.delete_scene(scene_id)


@router.post("/scenes/{scene_id}/characters", status_code=status.HTTP_201_CREATED)
async def create_character(scene_id: str, body: CharacterBody, svc: SimulationService = Depends(get_service)):
    return await svc.create_character(scene_id, body.model_dump(exclude_none=True))


@router.put("/characters/{char_id}")
async def update_character(char_id: str, body: CharacterBody, svc: SimulationService = Depends(get_service)):
    return await svc.update_character(char_id, body.model_dump(exclude_none=True))


@router.delete("/characters/{char_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(char_id: str, svc: SimulationService = Depends(get_service)):
    await svc.delete_character(char_id)


# ------------------------------------------------------------------ 主笔规划（S2）
class PlanBody(models.BaseModel):
    direction: str = ""      # 一句话方向
    plan: dict = {}           # commit 时携带要落库的骨架
    inspiration_ids: list[str] = []   # 已采纳灵感卡 id（P1-1 灵感→骨架落地）


@router.post("/books/{book_id}/plan")
async def plan_book(book_id: str, body: PlanBody, svc: SimulationService = Depends(get_service)):
    """主笔产出书籍骨架预览（不落库）。LLM 不可用 → 503。"""
    try:
        return await svc.plan_book(book_id, body.direction, body.inspiration_ids or None)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/books/{book_id}/plan/commit")
async def commit_book_plan(book_id: str, body: PlanBody, svc: SimulationService = Depends(get_service)):
    """确认骨架 → 落库 chapters/scenes/worldview/rules/foreshadows。"""
    return await svc.commit_book_plan(book_id, body.plan or {})


@router.get("/books/{book_id}/foreshadows")
async def list_foreshadows(book_id: str, svc: SimulationService = Depends(get_service)):
    """该书的伏笔账本（三态：buried/in_progress/closed）。"""
    return await svc.repo.list_foreshadows(book_id)


# ------------------------------------------------------------------ 灵感池（P0-1）
@router.get("/books/{book_id}/inspirations")
async def list_inspirations(book_id: str, svc: SimulationService = Depends(get_service)):
    """该书灵感卡列表（主笔生成 + 作者自建，含采纳状态）。"""
    return await svc.list_inspirations(book_id)


@router.post("/books/{book_id}/inspirations", status_code=status.HTTP_201_CREATED)
async def create_inspiration(book_id: str, body: chief.InspirationIn,
                             svc: SimulationService = Depends(get_service)):
    """作者自建灵感卡。"""
    return await svc.create_inspiration(book_id, body.model_dump())


@router.post("/books/{book_id}/inspirations/generate")
async def generate_inspirations(book_id: str, body: chief.GenerateIn,
                                svc: SimulationService = Depends(get_service)):
    """主笔生成灵感卡（LLM，无模型时回退模板卡）。"""
    cards = await svc.generate_inspirations(book_id, body.direction)
    return {"book_id": book_id, "cards": cards}


@router.patch("/inspirations/{card_id}")
async def set_inspiration_adopted(card_id: str, body: chief.AdoptBody,
                                  svc: SimulationService = Depends(get_service)):
    """采纳⇄取消灵感卡。"""
    await svc.set_inspiration_adopted(card_id, body.adopted)
    return {"id": card_id, "adopted": body.adopted}


@router.delete("/inspirations/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inspiration(card_id: str, svc: SimulationService = Depends(get_service)):
    await svc.delete_inspiration(card_id)


# ------------------------------------------------------------------ 主笔共创对话（P0-2）
@router.post("/books/{book_id}/chief/chat")
async def chief_chat(book_id: str, body: chief.ChiefChatIn,
                     svc: SimulationService = Depends(get_service)):
    """主笔共创对话：SSE 流式（event: token / data: {delta}，结尾 done）。"""
    messages = [m.model_dump() for m in body.messages]

    async def gen():
        try:
            async for token in svc.chief_chat_stream(book_id, messages):
                yield _sse("token", {"delta": token})
            yield _sse("done", {"ok": True})
        except asyncio.CancelledError:
            raise

    return StreamingResponse(gen(), media_type="text/event-stream")


# ------------------------------------------------------------------ sim 生命周期
@router.post("/sims", status_code=status.HTTP_201_CREATED)
async def start(
    body: dict | None = None,
    svc: SimulationService = Depends(get_service),
):
    """启动/恢复叙事。body: {book_id?, chapter_id?, scene_id, resume?: bool}"""
    body = body or {}
    scene_id = body.get("scene_id") or body.get("scenario") or "scene-betrayal-night"
    result = await svc.start(
        book_id=body.get("book_id", ""),
        chapter_id=body.get("chapter_id", ""),
        scene_id=scene_id,
        resume=bool(body.get("resume", True)),
    )
    result["scene_id"] = scene_id
    return result


@router.post("/sims/{sim_id}/step")
async def step(sim_id: str, n: int = 1, svc: SimulationService = Depends(get_service)):
    """推进 n 个回合，返回最新黑板状态（扁平化，与 /state 格式一致）。"""
    sim = await svc.step(sim_id, n)
    return await _flatten_state(sim, sim_id)


@router.get("/sims/{sim_id}/history")
async def history(sim_id: str, svc: SimulationService = Depends(get_service)):
    """回合归档列表（时间线回退查看）。"""
    sim = await svc.get_state(sim_id)
    return {
        "sim_id": sim_id,
        "turn": sim.world.turn,
        "archives": [a.model_dump() for a in sim.turn_archives],
    }


@router.post("/sims/{sim_id}/rewind")
async def rewind(sim_id: str, turn: int, svc: SimulationService = Depends(get_service)):
    """回退到指定回合：截断事件与归档，恢复该回合力/张力（可从此重演）。"""
    sim = await svc.rewind(sim_id, turn)
    return await _flatten_state(sim, sim_id)


@router.post("/sims/{sim_id}/pause")
async def pause(sim_id: str, svc: SimulationService = Depends(get_service)):
    """事件暂停：当前回合跑完即停（回合边界），resume 后从下一回合继续。"""
    sim = await svc.pause(sim_id)
    return await _flatten_state(sim, sim_id)


@router.post("/sims/{sim_id}/resume")
async def resume(sim_id: str, svc: SimulationService = Depends(get_service)):
    """恢复推演（清除暂停标记）。"""
    sim = await svc.resume(sim_id)
    return await _flatten_state(sim, sim_id)


@router.get("/sims/{sim_id}/state")
async def get_state(sim_id: str, svc: SimulationService = Depends(get_service)):
    """查看当前黑板状态（不含敏感中间量）。"""
    sim = await svc.get_state(sim_id)
    return await _flatten_state(sim, sim_id)


async def _flatten_state(sim: models.SimulationState, sim_id: str) -> dict:
    return {
        "sim_id": sim_id,
        "book_id": sim.book_id,
        "chapter_id": sim.chapter_id,
        "scene_id": sim.scene_id,
        "turn": sim.world.turn,
        "tension": sim.director.tension,
        "tension_trend": sim.director.tension_trend,
        "converged": sim.director.converged,
        "ending": sim.director.ending_selected,
        "raise_pending": sim.director.raise_request.pending,
        "raise_reason": sim.director.raise_request.reason,
        "paused": sim.paused,
        "characters": list(sim.characters.keys()),
        "beliefs": {cid: [b.model_dump() for b in bl] for cid, bl in sim.beliefs.items()},
        "guard": sim.guard.model_dump(),
        "world": sim.world.model_dump(),
        "last_main_actor": sim.last_main_actor,
    }


@router.get("/sims/{sim_id}/stream")
async def stream(
    sim_id: str,
    turns: Optional[int] = None,
    svc: SimulationService = Depends(get_service),
):
    """SSE 长连接：逐步推进回合并按 感知→思考→决策→事件→记忆→成文 分段增量推送。

    供前端导演台 EventSource 消费。连接建立前先校验 sim 存在；随后交给异步
    生成器_循环推进。`turns` 可选：限制推进的回合数（默认跑到 ended/converged/举手）。
    """
    await svc.get_state(sim_id)  # 连接前 404 校验（SimNotFoundError → 全局 handler）

    # SSE 转发：中途异常（LLM 抖动等）被引擎兜底成事件，这里保证连接总有收尾帧
    async def _safe_stream():
        async for frame in _sim_stream(sim_id, svc, turns):
            yield frame

    return StreamingResponse(
        _safe_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# 作者介入：举手批复 + 导演工具
class InterveneBody(models.BaseModel):
    action: str                      # accept / reject / inject_event / adjust_weight / expose
    payload: dict = {}               # 视 action 而异


@router.post("/sims/{sim_id}/intervene")
async def intervene(sim_id: str, body: InterveneBody, svc: SimulationService = Depends(get_service)):
    sim = await svc.intervene(sim_id, body.action, body.payload)
    return {"sim_id": sim_id, "action": body.action, "ok": True, "turn": sim.world.turn}


# 作者↔导演 共创对话（逐 token 流式）
class ChatBody(models.BaseModel):
    message: str                      # 作者本轮想跟导演讨论的内容


@router.post("/sims/{sim_id}/director/chat")
async def director_chat(sim_id: str, body: ChatBody, svc: SimulationService = Depends(get_service)):
    """SSE：作者消息 → 导演以全局视角回应，逐 token 推送。

    事件流：director_chat_delta（逐 token）→ director_chat_done（全文）
    异常时发 director_chat_error（LLM 未接入/中断），连接随后正常关闭。
    """
    # 连接前校验 sim 存在 + 导演可用性快速失败（LLM 未配置直接 503，不进流）
    await svc.get_state(sim_id)
    if not settings.openai_api_key:
        return JSONResponse(
            status_code=503,
            content={"code": "DirectorChatUnavailable", "detail": "导演暂离场：模型未接入，无法与你共创", "status_code": 503},
        )

    async def _chat_stream():
        full: list[str] = []
        try:
            async for tok in svc.director_chat(sim_id, body.message):
                full.append(tok)
                yield _sse("director_chat_delta", {"delta": tok})
            yield _sse("director_chat_done", {"text": "".join(full)})
        except Exception as e:  # noqa: BLE001 —— 流内异常转 error 事件（兜底，不让连接悬空）
            detail = str(getattr(e, "detail", None) or e)
            logger.exception("[director-chat] 流内出错: %s", e)
            yield _sse("director_chat_error", {"message": detail})

    return StreamingResponse(
        _chat_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )