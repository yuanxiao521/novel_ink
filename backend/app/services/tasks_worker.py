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

import asyncio
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

# ---- 队列活性（v1.45 · B51 的代码层补完）------------------------------------
# B51：worker 被 Redis 断连搞死时**没有任何东西会重启它**，队列从此静默停摆，
# 前端要等轮询 5 分钟超时才报错，而且会留下永远 running 的僵尸任务。
# 这里补两件事：①worker 心跳（让 /health 能说「没人消费」）②启动清扫遗留僵尸。
HEARTBEAT_KEY = "novel_ink:worker:heartbeat"
_HB_TTL = 90            # 心跳键存活秒数：worker 挂掉后最多 90s 内被 /health 发现
_HB_EVERY = 30          # 刷新间隔
STUCK_SEC = 600         # running 超过这么久 = 疑似僵尸（前端轮询上限 300s，留余量）


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


# ---------------------------------------------------------------- 队列活性（v1.45）
async def _write_heartbeat() -> None:
    r = await _client()
    try:
        await r.set(HEARTBEAT_KEY, json.dumps({"at": int(time.time() * 1000)}), ex=_HB_TTL)
    finally:
        await r.aclose()


async def _heartbeat_loop() -> None:
    """worker 活着就每 30s 刷一次心跳；被取消（进程退出）时**删键**，
    让 /health 立刻能说「没有消费者」，而不是等 TTL 慢慢过期。"""
    try:
        while True:
            await _write_heartbeat()
            await asyncio.sleep(_HB_EVERY)
    except asyncio.CancelledError:
        try:
            r = await _client()
            try:
                await r.delete(HEARTBEAT_KEY)
            finally:
                await r.aclose()
        except Exception:  # noqa: BLE001  退出路径不能因为清理失败再抛
            pass
        raise


def select_orphans(states: list[dict], now_ms: int, threshold_ms: int = STUCK_SEC * 1000) -> list[str]:
    """**纯函数**：挑出「上一条进程留下的 running 僵尸」。

    只挑 running_since（无则 created_at）超过阈值的——避免误杀另一个并行 worker 的活任务。
    """
    out: list[str] = []
    for st in states or []:
        if str(st.get("status")) != "running":
            continue
        since = int(st.get("running_since") or st.get("created_at") or 0)
        jid = str(st.get("job_id") or "")
        if jid and since and now_ms - since >= threshold_ms:
            out.append(jid)
    return out


async def sweep_orphans(threshold_ms: int = STUCK_SEC * 1000) -> int:
    """worker 启动时清扫上个进程留下的僵尸任务 → 返回清扫条数。"""
    r = await _client()
    try:
        states: list[dict] = []
        for k in await r.keys(_PREFIX + "*"):
            raw = await r.get(k)
            try:
                st = json.loads(raw) if raw else {}
            except Exception:  # noqa: BLE001  坏 JSON 跳过，不影响清扫
                continue
            if isinstance(st, dict):
                states.append(st)
        ids = select_orphans(states, int(time.time() * 1000), threshold_ms)
        for jid in ids:
            await _set(jid, status="failed",
                       error="worker 重启：该任务在上一次进程中断时未完成（已自动标记 failed）")
        return len(ids)
    finally:
        await r.aclose()


async def queue_health() -> dict:
    """队列活性（/health.queue）：前端据此提示「后台队列没有消费者」。

    返回 status ∈ ok | no-worker | stuck；alive=False 时排批/审稿会/质量回环会一直排队，
    必须让作者看得见（T9：静默降级必须反馈）。
    """
    r = await _client()
    try:
        now = int(time.time() * 1000)
        raw = await r.get(HEARTBEAT_KEY)
        hb = json.loads(raw) if raw else None
        age = int((now - int(hb["at"])) / 1000) if isinstance(hb, dict) and hb.get("at") else None
        alive = age is not None and age <= _HB_TTL
        queued = int(await r.llen("arq:queue") or 0)
        in_progress = len(await r.keys("arq:in-progress:*"))
        stuck = 0
        for k in await r.keys(_PREFIX + "*"):
            try:
                s = json.loads(await r.get(k) or "{}")
            except Exception:  # noqa: BLE001
                continue
            if isinstance(s, dict) and str(s.get("status")) == "running":
                since = int(s.get("running_since") or s.get("created_at") or 0)
                if since and now - since >= STUCK_SEC * 1000:
                    stuck += 1
        status = "ok" if (alive and stuck == 0) else ("no-worker" if not alive else "stuck")
        return {"alive": alive, "status": status, "heartbeat_age": age,
                "heartbeat_ttl": _HB_TTL, "queued": queued,
                "in_progress": in_progress, "stuck": stuck}
    finally:
        await r.aclose()


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
    health_check_interval = 60   # arq 自己的健康键也刷新勤一点（默认 3600 太钝）

    @staticmethod
    async def on_startup(ctx: dict) -> None:
        """v1.45：启动即 ①清扫上个进程留下的僵尸任务 ②起心跳。"""
        try:
            swept = await sweep_orphans()
            if swept:
                logger.warning("[worker] 启动清扫：%d 个遗留 running 任务已标记 failed", swept)
        except Exception:  # noqa: BLE001  清扫失败不能挡住 worker 启动
            logger.warning("[worker] 启动清扫失败（忽略，继续起）", exc_info=True)
        ctx["heartbeat"] = asyncio.ensure_future(_heartbeat_loop())

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        task = ctx.get("heartbeat")
        if task is not None:
            task.cancel()
            try:
                await task
            except Exception:  # noqa: BLE001  关机路径不抛
                pass


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