"""arq 任务队列（v1.41）：长 LLM 操作后台化——刷新/断网/重启都不再断链。

背景（用户实测痛点）：主笔排一批（~40s）、审稿会（6 次 LLM 调用 ~40s）、质量回环（~2min）
都是同步 HTTP 挂着等——刷新页面连接断，协程被取消，LLM 调到一半白花钱，结果丢弃。

方案：任务进 Redis 队列，**arq worker 独立进程执行**；进度逐阶段写 Redis，
前端凭 job_id 轮询取进度与结果。连接与任务彻底解耦。

约定：
  - Redis db1（与 lvco 项目的 db0 隔离）；key 前缀 novel_ink:job:{id}
  - 任务状态机：queued → running → done / failed（存 status/stages/result/error）
  - worker 启动：arq app.services.tasks_worker.WorkerSettings（dev-up.sh 追加）
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import redis.asyncio as aredis

from app.config import settings

logger = logging.getLogger(__name__)

_REDIS_URL = settings.redis_url          # redis://127.0.0.1:6379/1
_PREFIX = "novel_ink:job:"
_TTL = 60 * 60 * 24                      # 结果保留 24h


def _key(job_id: str) -> str:
    return _PREFIX + job_id


async def _client() -> aredis.Redis:
    return aredis.from_url(_REDIS_URL, decode_responses=True)


async def job_status(job_id: str) -> dict | None:
    """查任务状态（前端轮询用）。返回 {status, stages, result?, error?}。"""
    r = await _client()
    try:
        raw = await r.get(_key(job_id))
        return json.loads(raw) if raw else None
    finally:
        await r.aclose()


async def _set(job_id: str, **fields: Any) -> None:
    r = await _client()
    try:
        raw = await r.get(_key(job_id))
        state = json.loads(raw) if raw else {}
        state.update(fields)
        await r.set(_key(job_id), json.dumps(state, ensure_ascii=False), ex=_TTL)
    finally:
        await r.aclose()


async def enqueue(job_kind: str, payload: dict) -> dict:
    """入队：arq enqueue_job（写 arq:queue，worker 才能领到）+ 任务状态写 Redis。"""
    from arq import create_pool

    from app.services.tasks_worker import WorkerSettings

    job_id = f"job-{uuid.uuid4().hex[:10]}"
    # 1) 任务状态（前端轮询用）
    r = await _client()
    try:
        await r.set(_key(job_id), json.dumps({
            "job_id": job_id, "kind": job_kind, "status": "queued",
            "stages": [], "payload": payload, "created_at": int(time.time() * 1000),
        }, ensure_ascii=False), ex=_TTL)
    finally:
        await r.aclose()
    # 2) 真正入 arq 队列（worker 领取执行）——函数名映射到 worker functions
    fn_map = {"plan_batch": "run_plan_batch",
              "review_meeting": "run_review_meeting",
              "quality_loop": "run_quality_loop",
              "bookkeeping": "run_bookkeeping"}
    arq_fn = fn_map.get(job_kind)
    if not arq_fn:
        raise ValueError(f"未知任务类型: {job_kind}")
    pool = await create_pool(WorkerSettings.redis_settings)
    try:
        await pool.enqueue_job(arq_fn, job_id=job_id, **payload)
    finally:
        await pool.aclose()
    return {"job_id": job_id, "kind": job_kind, "status": "queued"}


async def _guard(job_id: str, coro):
    """任务守卫（v1.42）：异常必须写进 Redis 状态，否则前端永远轮询到 running。

    事故背景：审稿会任务因 scene_id 为空抛 SceneNotFoundError——arq 自己记了失败，
    但 novel_ink:job:* 一直停在 running（stages 空白）→ 前端死等。
    """
    try:
        return await coro
    except Exception as e:  # noqa: BLE001
        logger.exception("[job] %s 执行失败: %s", job_id, e)
        await _set(job_id, status="failed", error=f"{type(e).__name__}: {e}"[:300])
        raise


# ---------------------------------------------------------------- worker 端任务函数
async def _stage(job_id: str, text: str, detail: str = "") -> None:
    await _set(job_id, running_since=int(time.time() * 1000))
    r = await _client()
    try:
        raw = await r.get(_key(job_id))
        state = json.loads(raw) if raw else {"stages": []}
        state.setdefault("stages", []).append({"text": text, "detail": detail, "ts": int(time.time() * 1000)})
        state["status"] = "running"
        await r.set(_key(job_id), json.dumps(state, ensure_ascii=False), ex=_TTL)
    finally:
        await r.aclose()


async def run_plan_batch(ctx: dict, *, job_id: str = "", book_id: str, direction: str = "",
                         batch_size: int = 3, batch_from: int = 0,
                         inspiration_ids: list[str] | None = None) -> dict:
    """后台：主笔排一批骨架预览。结果（plan+batch）写 Redis，前端确认后照常 commit。"""
    jid = job_id or "job-unknown"
    return await _guard(jid, _do_plan_batch(jid, book_id, direction, batch_size, batch_from, inspiration_ids))


async def _do_plan_batch(jid: str, book_id: str, direction: str, batch_size: int,
                         batch_from: int, inspiration_ids: list[str] | None) -> dict:
    await _stage(jid, "正在排本批骨架（自动承接已落库章节）…")
    from app.db.repo import Repo
    from app.services.service import SimulationService

    svc = SimulationService(Repo())
    plan = await svc.plan_book(book_id, direction, inspiration_ids,
                              batch_size=batch_size, batch_from=batch_from)
    await _stage(jid, f"本批 {len((plan.get('plan') or {}).get('chapters') or [])} 章已排好，等作者确认落库")
    await _set(jid, status="done", result=plan)
    return plan


async def run_review_meeting(ctx: dict, *, job_id: str = "", scene_id: str, text: str = "") -> dict:
    """后台：D 批审稿会（体检 ∥ 质检 → 润色按需 → 评审 → 裁决）。"""
    jid = job_id or "job-unknown"
    return await _guard(jid, _do_review_meeting(jid, scene_id, text))


async def _do_review_meeting(jid: str, scene_id: str, text: str) -> dict:
    await _stage(jid, "审稿会开始：体检 ∥ 质检 → 润色(按需) → 评审")
    from app.db.repo import Repo
    from app.services.service import SimulationService

    svc = SimulationService(Repo())
    out = await svc.prose_review_meeting(scene_id, text)
    for s in out.get("steps") or []:
        extra = ""
        if s.get("total") is not None:
            extra = f"（{s.get('total')} 分）"
        elif s.get("issues") is not None:
            extra = f"（问题 {s.get('issues')} 条 · 口吻 {s.get('voice') or 0} 处）"
        await _stage(jid, f"{s.get('step')} 完成{extra}")
    await _stage(jid, f"裁决：{out.get('verdict', '')}")
    await _set(jid, status="done", result=out)
    return out


async def run_quality_loop(ctx: dict, *, job_id: str = "", scene_id: str, text: str = "") -> dict:
    """后台：A2 质量回环（自评 → 定点/全文重写 → 三闸复检）。"""
    jid = job_id or "job-unknown"
    return await _guard(jid, _do_quality_loop(jid, scene_id, text))


async def _do_quality_loop(jid: str, scene_id: str, text: str) -> dict:
    await _stage(jid, "质量回环开始：自评 → 按需重写 → 复检")
    from app.db.repo import Repo
    from app.services.service import SimulationService

    svc = SimulationService(Repo())
    out = await svc.prose_quality_loop(scene_id, text)
    await _stage(jid, f"回环结束：{'已采纳改写' if out.get('accepted') else '未采纳（评分达标或未改动）'}")
    await _set(jid, status="done", result=out)
    return out


async def run_bookkeeping(ctx: dict, *, job_id: str = "", scene_id: str, text: str) -> dict:
    """后台：保存正文后的自动记账（原 _bookkeep_after_save 挪进队列，获得重试保障）。"""
    jid = job_id or "job-unknown"
    return await _guard(jid, _do_bookkeeping(jid, scene_id, text))


async def _do_bookkeeping(jid: str, scene_id: str, text: str) -> dict:
    from app.db.repo import Repo
    from app.services.service import SimulationService

    svc = SimulationService(Repo())
    await _stage(jid, "记账：分析正文对伏笔/信念/世界状态的影响…")
    await svc._bookkeep_after_save(scene_id, text)
    await _stage(jid, "记账完成")
    await _set(jid, status="done", result={"scene_id": scene_id})
    return {"scene_id": scene_id}

class WorkerSettings:
    """arq worker 配置：arq app.services.tasks_worker.WorkerSettings"""
    functions = [run_plan_batch, run_review_meeting, run_quality_loop, run_bookkeeping]
    redis_settings = None   # 在类体下方赋值（arq RedisSettings）
    max_jobs = 4
    job_timeout = 600      # LLM 长任务放宽


def _build_worker():
    """构造 arq 的 redis_settings（延迟到 import 时一次性执行）。"""
    from arq.connections import RedisSettings

    from app.config import settings as s

    # redis://[:pass@]host:port/db
    url = s.redis_url
    parsed = url.replace("redis://", "")
    db = 0
    if "/" in parsed:
        parsed, db_s = parsed.rsplit("/", 1)
        db = int(db_s or 0)
    password = ""
    if "@" in parsed:
        creds, parsed = parsed.rsplit("@", 1)
        password = creds.split(":")[-1] if ":" in creds else ""
    host, _, port = parsed.partition(":")
    return RedisSettings(host=host or "127.0.0.1", port=int(port or 6379),
                         password=password or None, database=db)


WorkerSettings.redis_settings = _build_worker()