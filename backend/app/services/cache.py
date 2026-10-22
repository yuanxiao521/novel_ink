"""两级缓存 + single-flight（v1.69 · 方案② 的缓存底座）。

设计要求（作者拍板，逐条落地）：
  1. **不引入新组件**，复用已有 Redis（db1，前缀 novel_ink:）；
  2. **L1 进程内**只放"极小极热、丢了无所谓"的映射（章清单 / 章号→chapter_id / 当前章），
     TTL 30–60s + 容量上限，**不放 LLM 结果**（进程重启/多进程会不一致）；
  3. **L2 Redis** 放审校结果与候选：审校 24h、候选 10min，键里带 body_hash（hash-as-version）；
  4. **single-flight**：同一 (chapter, body, plan/instruction) 并发只算一次（SET NX PX 30s，
     后来者等结果，不打多次 LLM）；
  5. **降级**：Redis 任何异常 → 直算（只 warning，绝不把审计/候选打挂）。

键约定（与设计稿一致）：
  audit   novel_ink:cache:audit:{chapter_id}:{body_hash}:{plan_hash}
  rewrite novel_ink:cache:rewrite:{chapter_id}:{body_hash}:{sha1(instruction|scope)[:12]}
  lock    上述键 + ":lock"
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Awaitable, Callable

import redis.asyncio as aredis

from app.config import settings

logger = logging.getLogger(__name__)

_PREFIX = "novel_ink:cache:"
AUDIT_TTL = 60 * 60 * 24          # 审校结果：24h 兜底（body_hash/plan_hash 变了键自然失效）
REWRITE_TTL = 60 * 10             # 候选：10 分钟（作者改一改就该重算，别拿旧候选覆盖新正文）
LOCK_PX_MS = 30_000               # single-flight 互斥 30s（一次 LLM 审计最长约 30s）
WAIT_TIMEOUT = 45.0               # 后来者最多等 45s，超时直算（宁可多算一次也不无限等）
WAIT_STEP = 0.25


# ---------------------------------------------------------------- hash-as-version
def sha256_text(text: str) -> str:
    """body_hash = sha256(全文)。**不用长度+首尾 hash**：撞了会让候选覆盖错正文。"""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def plan_hash(material: dict | str) -> str:
    """审校"评测计划"指纹：章目标/张力曲线/卷目标/0-token 检查项等，任一变化就要重算。"""
    raw = material if isinstance(material, str) else json.dumps(material, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def audit_key(chapter_id: str, body_hash: str, plan_hash_value: str) -> str:
    return f"{_PREFIX}audit:{chapter_id}:{body_hash}:{plan_hash_value}"


def rewrite_key(chapter_id: str, body_hash: str, instruction: str, scope: str) -> str:
    stem = hashlib.sha1(f"{instruction or ''}|{scope or ''}".encode("utf-8")).hexdigest()[:12]
    return f"{_PREFIX}rewrite:{chapter_id}:{body_hash}:{stem}"


def _ttl_for(key: str) -> int:
    return REWRITE_TTL if ":rewrite:" in key else AUDIT_TTL


# ---------------------------------------------------------------- L1：进程内小缓存
class TinyTTLCache:
    """极小极热映射（章清单/章号→id/当前章）。丢了大不了重查，**不放 LLM 结果**。"""

    def __init__(self, *, maxsize: int = 256, ttl: float = 45.0) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expire_at, value = item
        if expire_at < time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        if len(self._data) >= self._maxsize:
            # 简单淘汰：先清过期，再丢最早插入的一条（容量本来就小，O(n) 无所谓）
            now = time.monotonic()
            for k in [k for k, (exp, _) in self._data.items() if exp < now]:
                self._data.pop(k, None)
            if len(self._data) >= self._maxsize:
                self._data.pop(next(iter(self._data)), None)
        self._data[key] = (time.monotonic() + (ttl if ttl is not None else self._ttl), value)

    def clear(self) -> None:
        self._data.clear()

    @property
    def size(self) -> int:
        return len(self._data)


L1 = TinyTTLCache(maxsize=256, ttl=45.0)      # 设计：TTL 30–60s、容量上限


def l1_get(ns: str, key: str) -> Any | None:
    return L1.get(ns + ":" + str(key))


def l1_set(ns: str, key: str, value: Any, ttl: float | None = None) -> None:
    L1.set(ns + ":" + str(key), value, ttl)


def l1_drop(ns: str, key: str) -> None:
    """显式失效（增删改章后立刻生效，不必等 45s TTL）。"""
    L1._data.pop(ns + ":" + str(key), None)   # 内部小结构，直接删即可


# ---------------------------------------------------------------- L2：Redis
# 进程内复用**一条**客户端：v1.69 的写法是"每次操作 from_url 再 aclose"，
# 而 single-flight 的等待方每 250ms 轮询一次 → 并发下就是连接风暴（实测 10 并发可到 40 连接/秒）。
# 现在按事件循环缓存（redis.asyncio 客户端绑 loop，测试里换 loop 要重建）。
_CLIENT: tuple[object, aredis.Redis] | None = None


async def _client() -> aredis.Redis:
    global _CLIENT
    loop = asyncio.get_running_loop()
    if _CLIENT is not None and _CLIENT[0] is loop:
        return _CLIENT[1]
    if _CLIENT is not None:
        try:
            await _CLIENT[1].aclose()
        except Exception:  # noqa: BLE001
            pass
    client = aredis.from_url(settings.redis_url, decode_responses=True)
    _CLIENT = (loop, client)
    return client


def reset_client() -> None:
    """测试/重启用：丢掉共享客户端（不关连接，避免跨 loop 报错）。"""
    global _CLIENT
    _CLIENT = None


async def get_json(key: str) -> Any | None:
    """读缓存；Redis 不可用 → None（当未命中处理，绝不抛）。"""
    try:
        client = await _client()
        raw = await client.get(key)
        return json.loads(raw) if raw else None
    except Exception as e:  # noqa: BLE001  缓存故障不能影响业务
        logger.warning("[cache] 读失败（降级为直算）：%s", e)
        return None


async def set_json(key: str, value: Any, ttl: int | None = None) -> bool:
    try:
        client = await _client()
        await client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl or _ttl_for(key))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[cache] 写失败（不影响本次结果）：%s", e)
        return False


async def delete(key: str) -> None:
    try:
        client = await _client()
        await client.delete(key)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- single-flight
LOCK_OK = "ok"        # 抢到锁，由我计算
LOCK_BUSY = "busy"    # 别人在算，我等结果
LOCK_DOWN = "down"    # Redis 不可用 → 立刻直算（不傻等）

# 释放锁必须**只删自己的**：锁 30s 过期后若已被别人拿到，无条件 DEL 会把别人的锁删掉
# （v1.69 就是无条件 delete）。这里用 Lua 做 compare-and-del（GET==token 才 DEL，原子）。
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
)


def new_token_or_none() -> str:
    import uuid

    return uuid.uuid4().hex


async def acquire_lock(key: str, px_ms: int = LOCK_PX_MS) -> tuple[str, str]:
    """SET NX PX。返回 (state, token)：state 见 LOCK_OK / LOCK_BUSY / LOCK_DOWN。"""
    token = new_token_or_none()
    try:
        client = await _client()
        ok = await client.set(key + ":lock", token, nx=True, px=px_ms)
        return (LOCK_OK, token) if ok else (LOCK_BUSY, "")
    except Exception as e:  # noqa: BLE001
        logger.warning("[cache] 加锁失败（缓存不可用，降级直算）：%s", e)
        return LOCK_DOWN, ""


async def release_lock(key: str, token: str = "") -> None:
    """只释放自己的锁（token 不符 = 锁已过期且被别人接手 → 什么都不做）。"""
    if not token:
        return
    try:
        client = await _client()
        await client.eval(_RELEASE_LUA, 1, key + ":lock", token)
    except Exception as e:  # noqa: BLE001  Redis 不可用就当锁会自己过期（PX）
        logger.warning("[cache] 释放锁失败（等待过期）：%s", e)


async def single_flight(
    key: str,
    factory: Callable[[], Awaitable[Any]],
    *,
    lock_px: int = LOCK_PX_MS,
    wait_timeout: float = WAIT_TIMEOUT,
) -> tuple[Any, bool]:
    """同 key 并发只算一次。返回 (value, computed_by_me)。

    流程：查缓存 → 抢锁 → 算并写缓存 → 放锁；没抢到的轮询缓存（最多 wait_timeout，
    超时则直算一次，避免无限等待）。缓存不可用时退化成"每次直算"。
    """
    cached = await get_json(key)
    if cached is not None:
        return cached, False
    state, token = await acquire_lock(key, lock_px)
    if state == LOCK_DOWN:
        # 缓存不可用：直算，绝不在这里等 45s（作者点一下要立刻有结果）
        return await factory(), True
    if state == LOCK_OK:
        try:
            value = await factory()
            await set_json(key, value, _ttl_for(key))
            return value, True
        finally:
            await release_lock(key, token)
    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(WAIT_STEP)
        cached = await get_json(key)
        if cached is not None:
            return cached, False
    value = await factory()
    return value, True
