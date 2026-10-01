"""Api 层 · simulation 路由：启动 / 恢复 / step / 状态 / 介入 / SSE 流式 / 四层目录。

全链路 async：数据经 async Repo（SQLAlchemy 2.0 ORM）走 DB，DB 未就绪时内存态兜底。
数据库会话经 `Depends` 注入（见 `app.api.deps`），路由不自行握手。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional
from urllib.parse import quote as _urlquote

from fastapi import APIRouter, Depends, HTTPException, status

from fastapi.responses import JSONResponse, Response, StreamingResponse

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


@router.get("/books/{book_id}/export")
async def book_export(book_id: str, svc: SimulationService = Depends(get_service)):
    """全书导出 markdown（仅含已定稿场景），浏览器直接下载 .md 文件。"""
    md = await svc.export_book_md(book_id)
    filename = f"{book_id}.md"
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{_urlquote(filename)}",
        },
    )


@router.get("/chapters/{chapter_id}")
async def chapter_detail(chapter_id: str, svc: SimulationService = Depends(get_service)):
    """章节详情（含 book_id，供导演台 scene→chapter→book 反查书树）。"""
    ch = await svc.get_chapter(chapter_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="未找到章节")
    return ch


@router.get("/chapters/{chapter_id}/prose")
async def chapter_prose(chapter_id: str, svc: SimulationService = Depends(get_service)):
    """章节定稿正文：该章全部场景 final_prose 聚合（阅读台按章渲染）。"""
    p = await svc.get_chapter_prose(chapter_id)
    if p is None:
        raise HTTPException(status_code=404, detail="未找到章节")
    return p


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


@router.get("/scenes/{scene_id}/prose")
async def scene_prose(scene_id: str, svc: SimulationService = Depends(get_service)):
    """单场景定稿正文（含字数与 finalized 标记）。"""
    p = await svc.get_scene_prose(scene_id)
    if p is None:
        raise HTTPException(status_code=404, detail="未找到场景")
    return p


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
    goal: str = ""
    content_desc: str = ""


class ScenePatchIn(models.BaseModel):
    """批量替换单场景载荷（PUT /chapters/{id}/scenes）：id 为空=新建，保留 scenario_def/facts。"""
    id: str = ""
    title: str = ""
    stage_desc: str = ""
    goal: str = ""
    content_desc: str = ""


class SceneBatchIn(models.BaseModel):
    """场景级全量替换载荷：传入该章完整场景数组（服务端 diff 增删改）。"""
    scenes: list[ScenePatchIn] = []


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


@router.put("/chapters/{chapter_id}/scenes")
async def replace_chapter_scenes(chapter_id: str, body: SceneBatchIn,
                                 svc: SimulationService = Depends(get_service)):
    """场景级全量替换（主笔升级 · 部分修改）：传该章完整场景数组，服务端 diff 增删改。"""
    scenes = [s.model_dump() for s in body.scenes]
    return await svc.replace_chapter_scenes(chapter_id, scenes)


class ProseTextIn(models.BaseModel):
    """正文协作载荷：当前正文（体检/润色/质检基于编辑区内容）。"""
    text: str = ""


class ProseReviewBody(models.BaseModel):
    """作者审阅：approve 时可选携带 verifier 记账明细（后续接入账本）。"""
    detail: dict = {}


# ------------------------------------------------------------------ 正文协作工作区（阶段④）
@router.post("/scenes/{scene_id}/prose/draft")
async def prose_draft(scene_id: str, svc: SimulationService = Depends(get_service)):
    """写手：正文初稿生成（无 LLM → text 为空串）。"""
    return await svc.prose_draft(scene_id)


@router.post("/scenes/{scene_id}/prose/review")
async def prose_review(scene_id: str, body: ProseTextIn,
                       svc: SimulationService = Depends(get_service)):
    """体检员：体检报告 → editor note（pending）。"""
    return await svc.prose_review(scene_id, body.text)


@router.post("/scenes/{scene_id}/prose/polish")
async def prose_polish(scene_id: str, body: ProseTextIn,
                       svc: SimulationService = Depends(get_service)):
    """润色师：润色稿 + 摘要 → polisher note（pending，after 供 [应用]）。"""
    return await svc.prose_polish(scene_id, body.text)


@router.post("/scenes/{scene_id}/prose/verify")
async def prose_verify(scene_id: str, body: ProseTextIn,
                       svc: SimulationService = Depends(get_service)):
    """质检员：伏笔/信念/因果质检 → verifier note（pending，确认后才记账）。"""
    return await svc.prose_verify(scene_id, body.text)


@router.post("/scenes/{scene_id}/prose/ai-tone")
async def prose_ai_tone(scene_id: str, body: ProseTextIn,
                        svc: SimulationService = Depends(get_service)):
    """A3 反 AI 味扫描（0-token）：规则清单 → editor note（明细存 payload_json.ai_tone）。"""
    return await svc.prose_ai_tone(scene_id, body.text)


@router.post("/scenes/{scene_id}/prose/spot-fix")
async def prose_spot_fix(scene_id: str, body: ProseTextIn,
                         svc: SimulationService = Depends(get_service)):
    """A3 定点修复：只改白名单规则命中句；过双闸才采纳并落 polisher note（after 供 [应用]）。"""
    return await svc.prose_spot_fix(scene_id, body.text)


@router.get("/scenes/{scene_id}/prose/notes")
async def list_prose_notes(scene_id: str, svc: SimulationService = Depends(get_service)):
    """审计记录列表（可追溯）。"""
    return await svc.list_prose_notes(scene_id)


@router.post("/prose-notes/{note_id}/approve")
async def approve_prose_note(note_id: str, svc: SimulationService = Depends(get_service)):
    """作者批准：verifier note → 先记账再置 approved。"""
    return await svc.review_prose_note(note_id, approve=True)


@router.post("/prose-notes/{note_id}/reject")
async def reject_prose_note(note_id: str, svc: SimulationService = Depends(get_service)):
    """作者驳回：仅置 rejected，不写库。"""
    return await svc.review_prose_note(note_id, approve=False)


@router.put("/scenes/{scene_id}/prose")
async def save_scene_prose(scene_id: str, body: ProseTextIn,
                           svc: SimulationService = Depends(get_service)):
    """作者保存正文 → final_prose 幂等落库。"""
    return await svc.save_scene_prose(scene_id, body.text)


class CharacterGenIn(models.BaseModel):
    direction: str = ""      # 可选：一句话方向（留空按书名/已有信息推导）


@router.post("/books/{book_id}/characters/generate")
async def generate_book_characters(book_id: str, body: CharacterGenIn,
                                   svc: SimulationService = Depends(get_service)):
    """主笔生成角色卡（并增）：空 cast 时一键补齐，作者可再编辑。"""
    return await svc.generate_book_characters(book_id, body.direction)


@router.get("/books/{book_id}/characters")
async def list_book_characters(book_id: str, svc: SimulationService = Depends(get_service)):
    """书级角色库（全书共享，一次编辑到处生效）。"""
    return await svc.get_book_characters(book_id)


@router.post("/books/{book_id}/characters", status_code=status.HTTP_201_CREATED)
async def create_book_character(book_id: str, body: CharacterBody, svc: SimulationService = Depends(get_service)):
    """书级新建角色卡（不挂具体场景）。"""
    return await svc.create_character(book_id, body.model_dump(exclude_none=True))


@router.post("/scenes/{scene_id}/characters", status_code=status.HTTP_201_CREATED)
async def create_scene_character(scene_id: str, body: CharacterBody, svc: SimulationService = Depends(get_service)):
    """场景特设角色（群演/NPC，仅该场景登场）。"""
    scene = await svc.get_scene(scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="场景不存在")
    ch = await svc.get_chapter(scene["chapter_id"])
    book_id = ch["book_id"] if ch else ""
    return await svc.create_character(book_id, body.model_dump(exclude_none=True), scene_id=scene_id)


@router.put("/characters/{char_id}")
async def update_character(char_id: str, body: CharacterBody, svc: SimulationService = Depends(get_service)):
    # exclude_unset：PUT {name} 不带默认 "{}" 的 spec_json，避免误触④层校验
    return await svc.update_character(char_id, body.model_dump(exclude_unset=True))


@router.delete("/characters/{char_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(char_id: str, svc: SimulationService = Depends(get_service)):
    await svc.delete_character(char_id)


# ------------------------------------------------------------------ 信念账本（书级 CRUD）
class BeliefBody(models.BaseModel):
    char_id: str = ""
    fact_id: str = ""
    source_event_id: str = ""
    channel: str = "perceived"       # perceived/told/inferred
    text: str = ""
    confidence: float = 0.5


@router.get("/books/{book_id}/beliefs")
async def list_beliefs(book_id: str, char_id: Optional[str] = None,
                       channel: Optional[str] = None,
                       svc: SimulationService = Depends(get_service)):
    """书级信念账本（推演自动沉淀 + 作者手改）。支持 char_id / channel 过滤。"""
    return await svc.list_beliefs(book_id, char_id, channel)


@router.post("/books/{book_id}/beliefs", status_code=status.HTTP_201_CREATED)
async def create_belief(book_id: str, body: BeliefBody, svc: SimulationService = Depends(get_service)):
    """作者手写信念（edited=True，需指定 char_id 与非空 text）。"""
    return await svc.create_belief(book_id, body.model_dump())


@router.put("/beliefs/{belief_id}")
async def update_belief(belief_id: str, body: BeliefBody, svc: SimulationService = Depends(get_service)):
    """作者修改信念（只传要改的字段；落库置 edited=True）。"""
    return await svc.update_belief(belief_id, body.model_dump(exclude_unset=True))


@router.delete("/beliefs/{belief_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_belief(belief_id: str, svc: SimulationService = Depends(get_service)):
    await svc.delete_belief(belief_id)


@router.get("/books/{book_id}/dashboard")
async def get_dashboard(book_id: str, svc: SimulationService = Depends(get_service)):
    """Overview 概览聚合：KPI / 章节时间线 / 待办 / 金句 / 近 4 周热力。"""
    return await svc.get_dashboard(book_id)


@router.post("/scenes/{scene_id}/prose/quality-loop")
async def prose_quality_loop(scene_id: str, body: ProseTextIn,
                             svc: SimulationService = Depends(get_service)):
    """A2 质量回环：自评（带证据）→ 达标不动 / 不达标重写一次 → 三闸复检；采纳落 polisher note。"""
    return await svc.prose_quality_loop(scene_id, body.text)


@router.get("/scenes/{scene_id}/script")
async def scene_script(scene_id: str, thoughts: int = 1, tension: int = 0,
                       svc: SimulationService = Depends(get_service)):
    """S4 涌现产物：单场景剧本/对话记录（Markdown 剧本体 + JSON 结构化）。

    thoughts=1 含各角色思考（内心独白/动机），tension=1 标注回合张力。
    """
    return await svc.scene_script(scene_id, with_thoughts=bool(thoughts),
                                  with_tension=bool(tension))


class EmergenceAdoptIn(models.BaseModel):
    turns: list[int] = []      # 空 = 采纳全部高光回合


@router.post("/scenes/{scene_id}/emergence-hits")
async def adopt_emergence_hits(scene_id: str, body: EmergenceAdoptIn,
                               svc: SimulationService = Depends(get_service)):
    """S4 step2：把确定性高光采纳为灵感卡（source=emergence，零新表）。"""
    return await svc.adopt_emergence_hits(scene_id, body.turns)


@router.get("/books/{book_id}/script")
async def book_script(book_id: str, thoughts: int = 1, tension: int = 0,
                      svc: SimulationService = Depends(get_service)):
    """S4 涌现产物：全书剧本（按章/场景拼接，仅含有推演回合的场景）。"""
    return await svc.book_script(book_id, with_thoughts=bool(thoughts),
                                 with_tension=bool(tension))


@router.get("/books/{book_id}/global-view")
async def book_global_view(book_id: str, svc: SimulationService = Depends(get_service)):
    """S3 全局结构与张力视图（0-token 派生）：章级张力曲线 + 结构诊断 + 伏笔网络 + 评分。"""
    return await svc.book_global_view(book_id)


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


class InspirationUpdateIn(models.BaseModel):
    """灵感卡编辑（PUT）：字段全可选，只更新显式传入的。"""
    title: str = ""
    desc: str = ""
    icon: str = ""
    type: str = ""


class CastBody(models.BaseModel):
    """选角（PUT /sims/{id}/cast）：上场角色 id 列表（允许空=清场）。"""
    character_ids: list[str] = []


@router.put("/inspirations/{card_id}")
async def update_inspiration(card_id: str, body: InspirationUpdateIn,
                             svc: SimulationService = Depends(get_service)):
    """编辑灵感卡（title/desc/type/icon；不动 adopted）。"""
    return await svc.update_inspiration(card_id, body.model_dump(exclude_unset=True))


@router.put("/sims/{sim_id}/cast")
async def set_sim_cast(sim_id: str, body: CastBody, svc: SimulationService = Depends(get_service)):
    """配置上场角色：以该书角色库重建 sim 角色（cast 不重启局面）。"""
    return await svc.set_sim_cast(sim_id, body.character_ids)


# ------------------------------------------------------------------ 主笔书级记忆（记忆域）
@router.get("/books/{book_id}/memories")
async def list_memories(book_id: str, topic: Optional[str] = None, limit: int = 50,
                        svc: SimulationService = Depends(get_service)):
    """书级记忆列表（topic 过滤 + 最近 limit 条，ts 倒序）。"""
    return await svc.list_memories(book_id, topic, limit)


@router.post("/books/{book_id}/memories")
async def create_memory(book_id: str, body: chief.MemoryIn,
                        svc: SimulationService = Depends(get_service)):
    """作者/主笔写入一条书级记忆（参与主笔感知）。"""
    return await svc.create_memory(book_id, body.model_dump())


@router.put("/memories/{memory_id}")
async def update_memory(memory_id: str, body: chief.MemoryUpdateIn,
                        svc: SimulationService = Depends(get_service)):
    """编辑记忆（只更新显式传入字段）。"""
    return await svc.update_memory(memory_id, body.model_dump(exclude_unset=True))


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: str, svc: SimulationService = Depends(get_service)):
    await svc.delete_memory(memory_id)


# ------------------------------------------------------------------ 主笔共创对话（P0-2）
@router.get("/books/{book_id}/chief/chat_history")
async def get_chat_history(book_id: str,
                           svc: SimulationService = Depends(get_service)):
    """获取某书的主笔共创对话历史（ts 正序）。"""
    return await svc.list_chat_histories(book_id)


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


@router.post("/sims/{sim_id}/finalize")
async def finalize(sim_id: str, svc: SimulationService = Depends(get_service)):
    """作者手动定稿：聚合该 sim 回合归档的成文 → 写入 scenes.final_prose（幂等覆盖）。"""
    return await svc.finalize_scene_prose(sim_id)


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
        "empty_world": bool(sim.scratch.get("empty_world")),
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


@router.get("/sims/{sim_id}/inject-palette")
async def inject_palette(sim_id: str, svc: SimulationService = Depends(get_service)):
    """介入工具的下拉数据源：facts（可曝光）+ 角色动态目标（可调权）。"""
    return await svc.get_inject_palette(sim_id)


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