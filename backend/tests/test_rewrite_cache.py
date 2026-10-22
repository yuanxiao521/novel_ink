"""方案②（建议→候选→采纳→版本）+ 两级缓存（v1.69）测试。

覆盖作者拍板的缓存规格：
  1. L1 进程内：小容量 + TTL；丢了无所谓（只放映射，不放 LLM 结果）；
  2. L2 Redis：audit 键 = audit:{cid}:{body_hash}:{plan_hash}，rewrite 键含 sha1(instruction|scope)；
     body_hash = sha256(全文)（不是长度+首尾）；
  3. single-flight：同一 key 并发只算一次；
  4. 正文变更 → 键变 → 必然失效；
  5. Redis 不可用 → 降级直算、不报错。

另验 ② 的安全底线：候选不落库、采纳需 confirm、候选过期拒绝覆盖、采纳必留版本。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from app.services import cache as cache_mod  # noqa: E402
from app.services.engine.chapter_rewrite import (  # noqa: E402
    normalize_rewrite, splice_scope, validate_rewrite,
)


# ---------------------------------------------------------------- 1. 键与 hash
def test_body_hash_is_sha256_of_full_text():
    h1 = cache_mod.sha256_text("第一段。\n\n第二段。")
    assert len(h1) == 64 and h1 != cache_mod.sha256_text("第一段。\n\n第二段！")
    # 长度+首尾相同但中间不同 → 必须不同 hash（这就是不用"长度+首尾"的原因）
    a = "A" * 50 + "中" + "B" * 50
    b = "A" * 50 + "改" + "B" * 50
    assert len(a) == len(b) and cache_mod.sha256_text(a) != cache_mod.sha256_text(b)


def test_key_shapes():
    ak = cache_mod.audit_key("c1", "bh", "ph")
    assert ak == "novel_ink:cache:audit:c1:bh:ph"
    rk = cache_mod.rewrite_key("c1", "bh", "合并查验", "chapter")
    rk2 = cache_mod.rewrite_key("c1", "bh", "补一段", "chapter")
    rk3 = cache_mod.rewrite_key("c1", "bh", "合并查验", "para")
    assert rk.startswith("novel_ink:cache:rewrite:c1:bh:")
    assert rk != rk2 and rk != rk3          # instruction / scope 都进键
    assert cache_mod._ttl_for(rk) == cache_mod.REWRITE_TTL
    assert cache_mod._ttl_for(ak) == cache_mod.AUDIT_TTL


# ---------------------------------------------------------------- 2. L1 小缓存
def test_l1_ttl_and_capacity():
    c = cache_mod.TinyTTLCache(maxsize=2, ttl=999)
    c.set("a", 1)
    c.set("b", 2)
    assert c.get("a") == 1
    c.set("c", 3)                            # 超容量 → 淘汰最早
    assert c.get("a") is None and c.get("c") == 3
    assert c.size == 2
    expired = cache_mod.TinyTTLCache(maxsize=4, ttl=-1)   # 立刻过期
    expired.set("x", 1)
    assert expired.get("x") is None


# ---------------------------------------------------------------- 3. 假 Redis（内存版）
class FakeRedis:
    """把 get_json/set_json/acquire_lock/release_lock 换成内存实现（等价于 Redis 行为）。"""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.locks: set[str] = set()
        self.gets = 0
        self.sets = 0

    async def get_json(self, key):
        self.gets += 1
        return self.store.get(key)

    async def set_json(self, key, value, ttl=None):
        self.sets += 1
        self.store[key] = value
        return True

    async def acquire_lock(self, key, px_ms=30000):
        if key in self.locks:
            return False
        self.locks.add(key)
        return True

    async def release_lock(self, key):
        self.locks.discard(key)


@pytest.fixture
def fake_redis(monkeypatch):
    f = FakeRedis()
    monkeypatch.setattr(cache_mod, "get_json", f.get_json)
    monkeypatch.setattr(cache_mod, "set_json", f.set_json)
    monkeypatch.setattr(cache_mod, "acquire_lock", f.acquire_lock)
    monkeypatch.setattr(cache_mod, "release_lock", f.release_lock)
    return f


class CountingLLM:
    """按 prompt 内容分别回审计 / 改稿 JSON，并计数（验缓存与 single-flight）。"""

    def __init__(self) -> None:
        self.available = True
        self.calls = 0
        self.audit_calls = 0
        self.rewrite_calls = 0
        self.delay = 0.0
        self.rewrite_after: str | None = None

    async def call_cheap(self, prompt, json_schema=None):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if "【要执行的建议】" in prompt:
            self.rewrite_calls += 1
            after = self.rewrite_after
            if after is None:
                after = "改写后的这一段。"
            return {"after": after, "summary": "压掉了重复查验", "kept": ["对白"]}
        self.audit_calls += 1
        return {"verdict": "基本完成章目标", "score": 72,
                "items": [{"kind": "goal_missed", "detail": "没写到界墙", "suggest": "补一段",
                           "severity": "mid"}],
                "keep": ["腔调"]}

    async def call_strong(self, prompt, json_schema=None):
        return None


async def _repo_book():
    from app.db.repo import Repo

    repo = Repo(use_db=False)
    await repo.save_book({"id": "b-rw", "title": "改稿", "genre": "测试",
                          "status": "planned", "cover_init": "改", "synopsis": ""})
    await repo.save_chapter({"id": "c-rw", "book_id": "b-rw", "title": "第 1 章 雨夜",
                             "summary": "林澈拿到路引。", "order_no": 1, "tone": "suspense",
                             "word_target": 1000, "tension_curve": "压→扬"})
    await repo.save_chapter_prose("c-rw", "第一段：林澈捡到路引。\n\n第二段：他抬头看界墙。\n\n第三段：钟声响了。")
    return repo


# ---------------------------------------------------------------- 4. 审校缓存
@pytest.mark.asyncio
async def test_audit_cache_hit_and_invalidation(monkeypatch, fake_redis):
    import app.services.service as svc_mod
    from app.services.service import SimulationService

    repo = await _repo_book()
    svc = SimulationService(repo)
    llm = CountingLLM()
    monkeypatch.setattr(svc_mod, "llm_client", llm)

    first = await svc.chapter_prose_audit("c-rw")
    assert first["cache"]["hit"] is False and llm.audit_calls == 1
    second = await svc.chapter_prose_audit("c-rw")
    assert second["cache"]["hit"] is True and llm.audit_calls == 1        # 命中，不再打 LLM
    assert second["deviation"]["score"] == 72

    # 正文变了 → body_hash 变 → 键变 → 必然未命中
    await repo.save_chapter_prose("c-rw", "第一段：林澈捡到路引（改）。\n\n第二段：界墙。")
    third = await svc.chapter_prose_audit("c-rw")
    assert third["cache"]["hit"] is False and llm.audit_calls == 2


@pytest.mark.asyncio
async def test_audit_single_flight_runs_llm_once(monkeypatch, fake_redis):
    import app.services.service as svc_mod
    from app.services.service import SimulationService

    repo = await _repo_book()
    svc = SimulationService(repo)
    llm = CountingLLM()
    llm.delay = 0.3
    monkeypatch.setattr(svc_mod, "llm_client", llm)

    results = await asyncio.gather(*(svc.chapter_prose_audit("c-rw") for _ in range(3)))
    assert llm.audit_calls == 1, llm.audit_calls          # 并发只算一次
    assert [r["deviation"]["score"] for r in results] == [72, 72, 72]


@pytest.mark.asyncio
async def test_audit_degrades_when_redis_down(monkeypatch):
    import app.services.service as svc_mod
    from app.services.service import SimulationService

    repo = await _repo_book()
    svc = SimulationService(repo)
    llm = CountingLLM()
    monkeypatch.setattr(svc_mod, "llm_client", llm)

    async def _boom(*a, **kw):
        raise OSError("redis down")

    monkeypatch.setattr(cache_mod, "_client", _boom)      # 真实降级路径：连不上 Redis
    out = await svc.chapter_prose_audit("c-rw")
    assert out["deviation"]["score"] == 72               # 仍然给出结果
    assert out["cache"]["hit"] is False
    # 缓存挂了不能重复计算成灾：这里只要求"不报错 + 有结果"
    assert llm.audit_calls >= 1


# ---------------------------------------------------------------- 5. 候选与范围
def test_splice_scope_para_keeps_other_paragraphs():
    body = "甲段。\n\n乙段。\n\n丙段。"
    new_body, diff = splice_scope(body, "乙段（改）。", scope="para", para_index=2)
    assert new_body == "甲段。\n\n乙段（改）。\n\n丙段。"
    assert diff["scope"] == "para" and diff["para_index"] == 2
    assert diff["before_chars"] == len("乙段。")


def test_splice_scope_errors():
    with pytest.raises(ValueError):
        splice_scope("甲\n\n乙", "x", scope="para", para_index=9)
    with pytest.raises(ValueError):
        splice_scope("甲", "x", scope="scene", scene_prose="")
    with pytest.raises(ValueError):
        splice_scope("甲", "x", scope="weird")


def test_validate_and_normalize_rewrite():
    assert validate_rewrite({"after": "x", "summary": "y"}) is None
    assert validate_rewrite({"after": "", "summary": "y"}) is not None
    out = normalize_rewrite({"after": "  新正文 ", "summary": " 改了 ", "kept": ["对白", ""]})
    assert out["after"] == "新正文" and out["summary"] == "改了" and out["kept"] == ["对白"]


@pytest.mark.asyncio
async def test_rewrite_candidate_cached_by_instruction(monkeypatch, fake_redis):
    import app.services.service as svc_mod
    from app.services.service import SimulationService

    repo = await _repo_book()
    svc = SimulationService(repo)
    llm = CountingLLM()
    monkeypatch.setattr(svc_mod, "llm_client", llm)

    out1 = await svc.chapter_rewrite("c-rw", instruction="合并两次查验", scope="chapter")
    assert out1["cache"]["hit"] is False and llm.rewrite_calls == 1
    assert out1["note_id"] and out1["after"] != (await repo.get_chapter("c-rw"))["prose_md"]   # 候选未落库

    out2 = await svc.chapter_rewrite("c-rw", instruction="合并两次查验", scope="chapter")
    assert out2["cache"]["hit"] is True and llm.rewrite_calls == 1

    out3 = await svc.chapter_rewrite("c-rw", instruction="补一句界墙描写", scope="chapter")
    assert out3["cache"]["hit"] is False and llm.rewrite_calls == 2     # instruction 进键 → 不同候选


# ---------------------------------------------------------------- 6. 采纳（安全边界）
@pytest.mark.asyncio
async def test_apply_requires_confirm_and_keeps_version(monkeypatch, fake_redis):
    import app.services.service as svc_mod
    from app.errors import InvalidActionError
    from app.services.service import SimulationService

    repo = await _repo_book()
    svc = SimulationService(repo)
    llm = CountingLLM()
    monkeypatch.setattr(svc_mod, "llm_client", llm)

    cand = await svc.chapter_rewrite("c-rw", instruction="合并查验", scope="chapter")
    note_id = cand["note_id"]
    with pytest.raises(InvalidActionError):
        await svc.apply_prose_note(note_id, confirm=False)

    applied = await svc.apply_prose_note(note_id, confirm=True, based_on=cand["based_on"])
    assert applied["already_applied"] is False and applied["target"] == "chapter"
    assert (await repo.get_chapter("c-rw"))["prose_md"] == cand["after"]
    revs = await repo.list_chapter_revisions("c-rw")
    assert revs, "采纳覆盖了正文却没留版本"
    note = await repo.get_prose_note(note_id)
    assert note["status"] == "approved"
    assert json.loads(note["payload_json"])["applied"]["target"] == "chapter"
    # 重复采纳：幂等返回，不重复写库
    again = await svc.apply_prose_note(note_id, confirm=True)
    assert again["already_applied"] is True


@pytest.mark.asyncio
async def test_apply_rejects_stale_candidate(monkeypatch, fake_redis):
    import app.services.service as svc_mod
    from app.errors import InvalidActionError
    from app.services.service import SimulationService

    repo = await _repo_book()
    svc = SimulationService(repo)
    llm = CountingLLM()
    monkeypatch.setattr(svc_mod, "llm_client", llm)

    cand = await svc.chapter_rewrite("c-rw", instruction="合并查验", scope="chapter")
    await repo.save_chapter_prose("c-rw", "作者又改了一版正文。")      # 候选过期
    with pytest.raises(InvalidActionError):
        await svc.apply_prose_note(cand["note_id"], confirm=True, based_on=cand["based_on"])
    assert (await repo.get_chapter("c-rw"))["prose_md"] == "作者又改了一版正文。"   # 没被覆盖


def test_rewrite_tool_registered_for_editor_and_chief():
    from app.services.agents.registry import get_tool
    from app.services.agents.spec import can_use

    decl = get_tool("chapter.rewrite")
    assert decl is not None and decl.group == "chapter"
    assert decl.side_effect == "write"      # 只出候选
    assert decl.needs_confirm is False      # 候选不需要确认；采纳才需要（apply 接口）
    assert can_use("editor", "chapter.rewrite")
    assert can_use("chief", "chapter.rewrite")
