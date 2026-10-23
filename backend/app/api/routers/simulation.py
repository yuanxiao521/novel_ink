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
from app.services.agents.editor import chief_chat_with_tools
from app.services.service import SimulationService, _archive_is_empty

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


@router.get("/books/{book_id}/brief")
async def book_brief(book_id: str, svc: SimulationService = Depends(get_service)):
    """全局脉络 + 骨架体检（v1.50 · 0-token，零 LLM）。

    主笔"掌管全局"看的就是这一页：每章写了没/多少字/走哪种生产方式(mode)，
    伏笔回收率与逾期项，角色出场断档，骨架缺陷清单（不是模型猜的，是规则查的）。
    """
    return await svc.get_book_brief(book_id)


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
    # v1.55 场级必然事件（hard 不可取消 / soft 只作倾向）；None = 不改
    beats: list[dict] | None = None


class ScenePatchIn(models.BaseModel):
    """批量替换单场景载荷（PUT /chapters/{id}/scenes）：id 为空=新建，保留 scenario_def/facts。"""
    id: str = ""
    title: str = ""
    stage_desc: str = ""
    goal: str = ""
    content_desc: str = ""
    # v1.55 场级必然事件（None = 不改；给了就整体替换该场 beats）
    beats: list[dict] | None = None


class SceneBatchIn(models.BaseModel):
    """场景级全量替换载荷：传入该章完整场景数组（服务端 diff 增删改）。"""
    scenes: list[ScenePatchIn] = []


class CharacterBody(models.BaseModel):
    name: str = ""
    spec_json: str = "{}"
    # v1.53 角色分层（主角/关键配角/反派/次要/背景）；空 = 不改（exclude_unset 语义）
    role_tier: str = ""


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


class ProductionIn(models.BaseModel):
    """章级制作单（v1.50）：mode + must_rehearse/beats/hook/word_split。"""
    mode: str = ""
    production: dict = {}


@router.get("/chapters/{chapter_id}/revisions")
async def list_chapter_revisions(chapter_id: str,
                                  svc: SimulationService = Depends(get_service)):
    """稿本历史（version 倒序，不含正文全文）。"""
    return await svc.list_chapter_revisions(chapter_id)


@router.get("/chapters/{chapter_id}/revisions/{version}")
async def get_chapter_revision(chapter_id: str, version: int,
                               svc: SimulationService = Depends(get_service)):
    """取某一版的完整正文。"""
    return await svc.get_chapter_revision(chapter_id, version)


class RevisionRestoreIn(models.BaseModel):
    """恢复到某一版（v1.61）：会追加一条 restore 版本，历史不断。"""
    confirm: bool = False


@router.post("/chapters/{chapter_id}/revisions/{version}/restore")
async def restore_chapter_revision(chapter_id: str, version: int,
                                   body: RevisionRestoreIn,
                                   svc: SimulationService = Depends(get_service)):
    """恢复正文到某一版（覆盖当前版；恢复动作本身也留版本）。"""
    if not body.confirm:
        raise HTTPException(status_code=409,
                            detail="恢复会覆盖当前正文，需要显式 confirm=true")
    return await svc.restore_chapter_revision(chapter_id, version)


@router.get("/chapters/{chapter_id}/prior")
async def chapter_prior(chapter_id: str, svc: SimulationService = Depends(get_service)):
    """本章的前情素材（v1.58）：全书 / 本卷 / 最近两章 + 冲突清单（写作页「前情」面板）。"""
    return await svc.chapter_prior(chapter_id)


class DigestGenIn(models.BaseModel):
    """前情压缩入参：scope=volume 时 volume_no>0 只重算该卷。"""
    scope: str = "chapter"
    volume_no: int = 0


@router.post("/chapters/{chapter_id}/digest/generate")
async def chapter_digest_generate(chapter_id: str,
                                  svc: SimulationService = Depends(get_service)):
    """重算本章前情概要（后台队列）。"""
    from app.services import tasks_worker as tw

    return await tw.enqueue("chapter_digest", {"chapter_id": chapter_id})


@router.post("/books/{book_id}/digest/generate")
async def book_digest_generate(book_id: str, body: DigestGenIn,
                               svc: SimulationService = Depends(get_service)):
    """重算卷级（scope=volume，volume_no=0 表示全部）或全书级（scope=book）前情概要。"""
    from app.services import tasks_worker as tw

    scope = (body.scope or "volume").strip().lower()
    if scope == "book":
        return await tw.enqueue("book_digest", {"book_id": book_id})
    return await tw.enqueue("volume_digest",
                            {"book_id": book_id, "volume_no": int(body.volume_no or 0)})


class VolumeCfgIn(models.BaseModel):
    """卷配置微调（v1.58）：volume_size 或显式 volumes 边界。"""
    volume_size: int | None = None
    volumes: list[dict] | None = None


@router.get("/books/{book_id}/volumes")
async def list_book_volumes(book_id: str, svc: SimulationService = Depends(get_service)):
    """分卷列表（v1.59）：真源是 book_volumes 表；附每卷章数与是否存在章归属。"""
    vols = await svc.list_book_volumes(book_id)
    return {"book_id": book_id, "volumes": vols}


@router.put("/books/{book_id}/volumes")
async def set_volume_config(book_id: str, body: VolumeCfgIn,
                            svc: SimulationService = Depends(get_service)):
    """微调卷边界（默认 8 章一卷；主笔/作者可改）。"""
    return await svc.set_volume_config(book_id, volume_size=body.volume_size,
                                       volumes=body.volumes)


@router.get("/chapters/{chapter_id}/production-plan")
async def chapter_production_plan(chapter_id: str,
                                  svc: SimulationService = Depends(get_service)):
    """本章生产路径裁决（v1.52 · 0-token）：draft 直写 / emerge 先推演 / hybrid 关键场先演，

    以及"还差几场推演、下一步该去哪"——写作页据此引导，作者不用记路由。
    """
    return await svc.chapter_production_plan(chapter_id)


@router.put("/chapters/{chapter_id}/production")
async def set_chapter_production(chapter_id: str, body: ProductionIn,
                                 svc: SimulationService = Depends(get_service)):
    """写章级制作单（主笔裁决 / 作者覆盖）。边界：只改结构，不碰正文。"""
    return await svc.set_chapter_mode(chapter_id, body.mode, body.production or None)


@router.delete("/chapters/{chapter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chapter(chapter_id: str, svc: SimulationService = Depends(get_service)):
    await svc.delete_chapter(chapter_id)


@router.post("/chapters/{chapter_id}/scenes", status_code=status.HTTP_201_CREATED)
async def create_scene(chapter_id: str, body: SceneBody, svc: SimulationService = Depends(get_service)):
    return await svc.create_scene(chapter_id, body.model_dump(exclude_none=True))


@router.put("/scenes/{scene_id}")
async def update_scene(scene_id: str, body: SceneBody, svc: SimulationService = Depends(get_service)):
    # B58（v1.55）：**必须 exclude_unset**。此前用 exclude_none，SceneBody 的默认值（title=""、
    # stage_desc="" …）会被当成"要写入的空值" → 只想改 beats 的一次 PUT 把场景标题/舞台/目标全清空。
    return await svc.update_scene(scene_id, body.model_dump(exclude_unset=True))


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


@router.post("/scenes/{scene_id}/prose/draft/stream")
async def prose_draft_stream(scene_id: str, svc: SimulationService = Depends(get_service)):
    """写手**流式**（v1.44 · SSE）：逐 token 下发，前端边收边写进中间正文区。

    event 序列：token（{delta}）→ done（{text, chars}）；异常走 error（不静默断流）。
    收尾的 writer 审计由 service 层负责（含**流式中断**时已生成的部分稿）。
    """
    async def gen():
        acc: list[str] = []
        try:
            async for tok in svc.prose_draft_stream(scene_id):
                acc.append(tok)
                yield _sse("token", {"delta": tok})
            text = "".join(acc).strip()
            yield _sse("done", {"text": text, "chars": len(text)})
        except Exception as e:  # noqa: BLE001  流中异常也要让前端看到
            yield _sse("error", {"error": f"{type(e).__name__}: {e}"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- 章级直写（v1.46 · 直写链路）
# 产品方向 v2：**章是最小可写单位，场是可选的细分**。
# 原来正文只存在 scenes.final_prose → 没有场景就写不了小说（Studio 直接空态死路），
# 而一场只有 150-220 字（prompt 写死）→ 全书 286 字、写不出一章。


@router.put("/chapters/{chapter_id}/prose")
async def put_chapter_prose(chapter_id: str, body: ProseTextIn,
                            svc: SimulationService = Depends(get_service)):
    """保存章正文（唯一真源；字数用 text_metrics 现算，不落列）。"""
    return await svc.save_chapter_prose(chapter_id, body.text)


@router.post("/chapters/{chapter_id}/prose/draft/stream")
async def chapter_prose_draft_stream(chapter_id: str,
                                     svc: SimulationService = Depends(get_service)):
    """**章级直写**（SSE）：按字数目标分段写满一章，逐 token 下发。

    与 /scenes/{id}/prose/draft/stream 的区别：那是「写一段（150-220 字）」，
    这是「写完一章」（按 word_target ≈4000 字，分 3-5 段，段间自动承接）。
    event：token（{delta}）→ done（{text, chars}）；异常走 error（不静默断流）。
    """
    chapter = await svc.repo.get_chapter(chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail=f"未找到章节: {chapter_id}")

    async def gen():
        acc: list[str] = []
        try:
            async for tok in svc.chapter_draft_stream(chapter_id):
                acc.append(tok)
                yield _sse("token", {"delta": tok})
            text = "".join(acc).strip()
            yield _sse("done", {"text": text, "chars": len(text)})
        except Exception as e:  # noqa: BLE001  流中异常要让前端看到
            yield _sse("error", {"error": f"{type(e).__name__}: {e}"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chapters/{chapter_id}/bookkeeping")
async def chapter_bookkeeping(chapter_id: str, body: ProseTextIn,
                              svc: SimulationService = Depends(get_service)):
    """章级记账 + 复检（手动重跑入口）：章正文对照账本 → 写伏笔/信念/世界状态。

    正常流程由保存（PUT /chapters/{id}/prose）**自动入队**触发，这里是重试/排查用。
    """
    return await svc.chapter_bookkeeping(chapter_id, body.text)


# ---------------------------------------------------------------- P1：设定补齐 + 章级巡检（v1.48）
class SettingsGenIn(models.BaseModel):
    """补设定入参：force=True 连已填字段一起覆盖（默认只补空）。"""
    force: bool = False


@router.post("/chapters/{chapter_id}/settings/generate")
async def generate_chapter_settings(chapter_id: str, body: SettingsGenIn,
                                   svc: SimulationService = Depends(get_service)):
    """补齐章摘要 + 各场设定（舞台 / 本场目标 / 内容描述）——**只补空字段，不覆盖作者手写**。

    动机：真实书场景设定填充率 0/3，写手只能自由发挥，体检随后必打回。
    同步返回（单次 LLM，约 10-20s）+ 填写摘要，前端据此如实提示"补了几项"。
    """
    return await svc.ensure_chapter_settings(chapter_id, force=body.force)


@router.post("/chapters/{chapter_id}/inspect")
async def chapter_inspect(chapter_id: str, svc: SimulationService = Depends(get_service)):
    """**章级巡检**（v1.48 · P1）：体检 ∥ 质检 → 评审 → 裁决 → 记账，**后台队列执行**。

    60-90s 的长任务 → 入队（11ms 返回 job_id），进度与结果走 GET /jobs/{job_id}。
    """
    chapter = await svc.repo.get_chapter(chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail=f"未找到章节: {chapter_id}")
    from app.services import tasks_worker as tw

    return await tw.enqueue("chapter_inspect", {"chapter_id": chapter_id})


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


@router.post("/books/{book_id}/characters/tiers/infer")
async def infer_character_tiers(book_id: str, apply: bool = False,
                                svc: SimulationService = Depends(get_service)):
    """让主笔给存量角色卡分档（主角/关键配角/反派/次要/背景）。

    apply=false（默认）只出预览；apply=true 才落库。v1.53：只有前三档会进涌现回合。
    """
    return await svc.infer_book_role_tiers(book_id, apply=apply)


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


@router.get("/books/{book_id}/collation")
async def book_collation(book_id: str, svc: SimulationService = Depends(get_service)):
    """逐章校对：每章场景清单（字数/定稿状态/预览）+ 全书统计（成书收口 C）。"""
    return await svc.get_collation(book_id)


@router.get("/books/{book_id}/health-report")
async def book_health_report(book_id: str, svc: SimulationService = Depends(get_service)):
    """整书体检：结构诊断 + 口音漂移 + 时间线回放（0-token，零 LLM）。"""
    return await svc.get_health_report(book_id)


@router.get("/books/{book_id}/global-view")
async def book_global_view(book_id: str, svc: SimulationService = Depends(get_service)):
    """S3 全局结构与张力视图（0-token 派生）：章级张力曲线 + 结构诊断 + 伏笔网络 + 评分。"""
    return await svc.book_global_view(book_id)


# ------------------------------------------------------------------ 主笔规划（S2）
class PlanBody(models.BaseModel):
    direction: str = ""      # 一句话方向
    plan: dict = {}           # commit 时携带要落库的骨架
    inspiration_ids: list[str] = []   # 已采纳灵感卡 id（P1-1 灵感→骨架落地）
    # A4 分批交付：batch_size>0 → 只排 batch_from 起的 batch_size 章（默认 = 已落库章数+1）
    batch_size: int = 0
    batch_from: int = 0
    # B33：mode=append（默认）只追加不删旧章；mode=replace 全量替换，已有正文需 allow_overwrite
    mode: str = "append"
    allow_overwrite: bool = False


@router.post("/books/{book_id}/plan")
async def plan_book(book_id: str, body: PlanBody, svc: SimulationService = Depends(get_service)):
    """主笔产出书籍骨架预览（不落库）。LLM 不可用 → 503。

    A4 分批交付：传 batch_size=N → 只排本批 N 章（承接已落库章节），返回 batch 元信息。
    """
    try:
        return await svc.plan_book(book_id, body.direction, body.inspiration_ids or None,
                                  batch_size=body.batch_size, batch_from=body.batch_from)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/books/{book_id}/plan/commit")
async def commit_book_plan(book_id: str, body: PlanBody, svc: SimulationService = Depends(get_service)):
    """确认骨架 → 落库 chapters/scenes/worldview/rules/foreshadows。

    mode=append（默认）分批追加，不碰已有章节与正文；mode=replace 全量替换
    （已有正文时须 allow_overwrite=true，否则 409）。
    """
    return await svc.commit_book_plan(book_id, body.plan or {},
                                      mode=body.mode, allow_overwrite=body.allow_overwrite)


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


# ------------------------------------------------------------------ 记忆检索（RAG · v1.49）
@router.get("/books/{book_id}/memory/stats")
async def book_memory_stats(book_id: str, svc: SimulationService = Depends(get_service)):
    """记忆索引统计：块数 / 各 kind 分布 / 向量条数 / 向量通道是否可用。"""
    return await svc.book_memory_stats(book_id)


@router.get("/books/{book_id}/memory/search")
async def book_memory_search(book_id: str, q: str, kinds: str = "", top_k: int = 8,
                             svc: SimulationService = Depends(get_service)):
    """记忆检索（作者/前端调试用）：q 必填；kinds 逗号分隔（chapter,character,world,...）。"""
    ks = [k.strip() for k in kinds.replace("，", ",").split(",") if k.strip()] or None
    return await svc.memory_search(book_id, q, ks, top_k)


@router.post("/books/{book_id}/memory/reindex")
async def book_memory_reindex(book_id: str,
                              svc: SimulationService = Depends(get_service)):
    """重建本书记忆索引（后台任务）：立刻返回 job_id，进度查 GET /jobs/{job_id}。"""
    from app.services import tasks_worker as tw

    return await tw.enqueue("book_reindex", {"book_id": book_id})


@router.get("/chapters/{chapter_id}/recall")
async def chapter_recall(chapter_id: str, svc: SimulationService = Depends(get_service)):
    """本章将注入写手 prompt 的记忆素材（写作页「记忆」面板数据源）。"""
    out = await svc.chapter_recall(chapter_id)
    return out


# ------------------------------------------------------------------ 主笔共创对话（P0-2）
@router.post("/books/{book_id}/chief/tools")
async def chief_tools_chat(book_id: str, body: chief.ChiefChatIn,
                           svc: SimulationService = Depends(get_service)):
    """主笔的计划式工具对话（v1.40）：对话 → 计划 → ToolExecutor 执行 → 汇报。

    与责编 /agent/chat 同一模式；最后一条 user 消息作为作者要求。
    """
    last_user = ""
    for m in body.messages:
        if m.role == "user" and m.content:
            last_user = str(m.content)
    return await chief_chat_with_tools(svc, book_id, last_user)


# ---------------------------------------------------------------- 任务队列（v1.41）
class AsyncJobIn(models.BaseModel):
    """后台任务入参（按 kind 取所需字段）。"""
    direction: str = ""
    batch_size: int = 3
    batch_from: int = 0
    inspiration_ids: list[str] = []
    text: str = ""
    scene_id: str = ""      # v1.42：审稿会/质量回环是场景级任务，必须带（此前空 → SceneNotFoundError）


@router.post("/books/{book_id}/jobs/{kind}")
async def enqueue_book_job(book_id: str, kind: str, body: AsyncJobIn,
                           svc: SimulationService = Depends(get_service)):
    """长 LLM 操作后台化：立即返回 job_id（连接与任务解耦，刷新不断链）。

    kind ∈ plan_batch | review_meeting | quality_loop
    进度与结果：GET /jobs/{job_id}
    """
    from app.services import tasks_worker as tw

    kinds = {
        "plan_batch": {
            "book_id": book_id, "direction": body.direction,
            "batch_size": max(0, body.batch_size), "batch_from": body.batch_from,
            "inspiration_ids": body.inspiration_ids,
        },
        "review_meeting": {"scene_id": body.scene_id, "text": body.text},
        "quality_loop": {"scene_id": body.scene_id, "text": body.text},
    }
    if kind not in kinds:
        raise HTTPException(status_code=400, detail=f"未知任务类型：{kind}（可选 {sorted(kinds)}）")
    if kind in ("review_meeting", "quality_loop") and not body.scene_id:
        raise HTTPException(status_code=400, detail=f"{kind} 是场景级任务，必须提供 scene_id")
    payload = {k: v for k, v in kinds[kind].items() if v is not None}
    job = await tw.enqueue(kind, payload)
    return job


@router.get("/jobs/{job_id}")
async def job_status(job_id: str):
    """查后台任务进度/结果（前端轮询）。"""
    from app.services import tasks_worker as tw

    st = await tw.job_status(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail=f"任务不存在或已过期: {job_id}")
    return st


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
    """回合归档列表（时间线回退查看）。

    v1.70：过滤掉**空回合**（无角色行动、无成文）——这些是"导演提示-only"的空壳归档
    （LLM 挂掉期间推演的老数据，实测某 sim 19 回合全空），时间线上不该显示。
    """
    sim = await svc.get_state(sim_id)
    archives = [a.model_dump() for a in sim.turn_archives]
    # v1.72：口径统一走 service._archive_is_empty（与 prune 端点/归档清理同一判据）
    valid = [a for a, raw in zip(archives, sim.turn_archives) if not _archive_is_empty(raw)]
    return {
        "sim_id": sim_id,
        "turn": sim.world.turn,
        "archives": valid,
        "total_archived": len(archives),
        "filtered_empty": len(archives) - len(valid),
    }


@router.post("/sims/{sim_id}/prune-archives")
async def prune_archives(sim_id: str, svc: SimulationService = Depends(get_service)):
    """清理空壳回合归档（v1.72 物理清理，幂等）。

    v1.70 只在 /history 展示层过滤空壳，DB 里一直留着（实测某 sim 19 条空壳 →
    total_archived 恒为 20）。此端点把**存量的**空壳从 state_json 里真正删掉；
    新的 step/rewind 已自动清理，所以正常使用不需要手动点。
    """
    return await svc.prune_archives(sim_id)


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