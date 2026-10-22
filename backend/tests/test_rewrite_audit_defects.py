"""方案② 审计回归（v1.70）：把 7 个疑点变成"应当通过"的用例。

先红后绿：这些用例描述的**都是正确行为**；在 v1.69 的代码上是红的（证明缺陷真实存在），
修完后转绿并长期守卫。
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


# ---------------------------------------------------------------- 公共夹具
class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.locks: dict[str, str] = {}

    async def get_json(self, key):
        return self.store.get(key)

    async def set_json(self, key, value, ttl=None):
        self.store[key] = value
        return True

    async def acquire_lock(self, key, px_ms=30000):
        from app.services.cache import LOCK_BUSY, LOCK_OK, new_token_or_none
        token = new_token_or_none()
        if key in self.locks:
            return LOCK_BUSY, ""
        self.locks[key] = token or ""
        return LOCK_OK, self.locks[key]

    async def release_lock(self, key, token=""):
        if self.locks.get(key) == token:
            self.locks.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    f = FakeRedis()
    monkeypatch.setattr(cache_mod, "get_json", f.get_json)
    monkeypatch.setattr(cache_mod, "set_json", f.set_json)
    monkeypatch.setattr(cache_mod, "acquire_lock", f.acquire_lock)
    monkeypatch.setattr(cache_mod, "release_lock", f.release_lock)
    return f


class LLM:
    """记录 prompt；按指令返回片段/整章/极短文本，用于验范围与护栏。"""

    def __init__(self) -> None:
        self.available = True
        self.prompts: list[str] = []
        self.audit_calls = 0
        self.rewrite_calls = 0
        self.after_mode = "echo_tail"      # echo_tail | fixed | short

    async def call_cheap(self, prompt, json_schema=None):
        self.prompts.append(prompt)
        if "【要执行的建议】" in prompt:
            self.rewrite_calls += 1
            if self.after_mode == "short":
                return {"after": "太短了。", "summary": "缩了", "kept": []}
            if self.after_mode == "fixed":
                return {"after": "改写后的这一段。", "summary": "改了一段", "kept": []}
            # 模拟"模型把看到的东西原样返回"（scope 传错时就会把整章塞回来）
            body = prompt.split("【整章正文】")[-1] if "【整章正文】" in prompt else prompt
            return {"after": body.strip()[-400:], "summary": "原样返回", "kept": []}
        self.audit_calls += 1
        return {"verdict": "基本完成", "score": 70,
                "items": [{"kind": "goal_missed", "detail": "没写到界墙", "suggest": "补一段",
                           "severity": "mid", "scope_hint": "para"}],
                "keep": ["腔调"]}

    async def call_strong(self, prompt, json_schema=None):
        return None


async def _repo():
    from app.db.repo import Repo

    repo = Repo(use_db=False)
    await repo.save_book({"id": "b-a", "title": "审计回归", "genre": "测试",
                          "status": "planned", "cover_init": "审", "synopsis": ""})
    await repo.save_chapter({"id": "c-a", "book_id": "b-a", "title": "第 1 章",
                             "summary": "林澈拿到路引。", "order_no": 1, "tone": "suspense",
                             "word_target": 1000, "tension_curve": "压→扬"})
    await repo.save_chapter_prose("c-a", "第一段：林澈捡到路引。\n\n第二段：他抬头看界墙。\n\n第三段：钟声响了。")
    return repo


# ---------------------------------------------------------------- 疑点 1：scope=para 是否把整章塞进某段
@pytest.mark.asyncio
async def test_para_scope_only_sends_and_replaces_that_paragraph(monkeypatch, fake_redis):
    import app.services.service as svc_mod
    from app.services.service import SimulationService

    repo = await _repo()
    svc = SimulationService(repo)
    llm = LLM()
    monkeypatch.setattr(svc_mod, "llm_client", llm)

    out = await svc.chapter_rewrite("c-a", instruction="压缩第二段", scope="para", para_index=2)
    assert not out.get("error"), out
    # ① 进 prompt 的只有这一段，不是整章
    assert "第二段：他抬头看界墙。" in llm.prompts[-1]
    assert "第一段：林澈捡到路引。" not in llm.prompts[-1]
    # ② 其它段落逐字保留（不膨胀、不串段）
    assert "第一段：林澈捡到路引。" in out["after"]
    assert "第三段：钟声响了。" in out["after"]
    assert out["after"].count("第一段") == 1


# ---------------------------------------------------------------- 疑点 3：超长章整章重写会不会被截断
@pytest.mark.asyncio
async def test_chapter_scope_refuses_too_long_body(monkeypatch, fake_redis):
    import app.services.service as svc_mod
    from app.services.service import SimulationService

    repo = await _repo()
    await repo.save_chapter_prose("c-a", "长" * 9000 + "。")
    svc = SimulationService(repo)
    monkeypatch.setattr(svc_mod, "llm_client", LLM())

    out = await svc.chapter_rewrite("c-a", instruction="整体压缩", scope="chapter")
    assert out.get("error"), "超长章（>8000 字）整章改写必须拒绝，不能悄悄只喂尾部"
    assert (await repo.get_chapter("c-a"))["prose_md"].startswith("长" * 10)


@pytest.mark.asyncio
async def test_chapter_scope_rejects_suspiciously_short_candidate(monkeypatch, fake_redis):
    import app.services.service as svc_mod
    from app.services.service import SimulationService

    repo = await _repo()
    long_body = ("这一章的开头写林澈在雨夜醒来，手里只有一张被涂改过的路引。" * 9)
    await repo.save_chapter_prose("c-a", long_body)
    svc = SimulationService(repo)
    llm = LLM()
    llm.after_mode = "short"          # 模型只回了一句 → 整章采纳等于砍掉正文
    monkeypatch.setattr(svc_mod, "llm_client", llm)

    out = await svc.chapter_rewrite("c-a", instruction="整体压缩", scope="chapter")
    assert out.get("error"), "候选比原文短 40% 以上必须拒绝（防截断/砍稿）"
    assert (await repo.get_chapter("c-a"))["prose_md"] == long_body


# ---------------------------------------------------------------- 疑点 4：锁过期后误删别人的锁
@pytest.mark.asyncio
async def test_release_lock_only_removes_own_token(monkeypatch):
    class StubRedis:
        def __init__(self) -> None:
            self.data: dict[str, str] = {}

        async def set(self, key, val, nx=False, px=None):
            if nx and key in self.data:
                return None
            self.data[key] = val
            return True

        async def eval(self, script, numkeys, key, token):
            if self.data.get(key) == token:
                del self.data[key]
                return 1
            return 0

        async def get(self, key):
            return self.data.get(key)

        async def aclose(self):
            return None

    stub = StubRedis()
    monkeypatch.setattr(cache_mod, "_client", lambda *a, **kw: _const(stub))
    state, token = await cache_mod.acquire_lock("k")
    assert state == cache_mod.LOCK_OK and token
    # 模拟"我的锁过期，别人拿到了锁"
    stub.data["k:lock"] = "someone-else"
    await cache_mod.release_lock("k", token)
    assert stub.data.get("k:lock") == "someone-else", "不能删掉别人的锁"


async def _const(v):
    return v


# ---------------------------------------------------------------- 疑点 5：每次操作新建连接（连接风暴）
@pytest.mark.asyncio
async def test_redis_client_is_reused(monkeypatch):
    created: list[int] = []

    class Stub:
        async def get(self, key):
            return None

        async def set(self, *a, **kw):
            return True

        async def aclose(self):
            return None

    def _factory(*a, **kw):
        created.append(1)
        return Stub()

    monkeypatch.setattr(cache_mod.aredis, "from_url", _factory)
    cache_mod.reset_client()
    await cache_mod.get_json("a")
    await cache_mod.set_json("b", {"x": 1})
    await cache_mod.get_json("c")
    assert len(created) == 1, f"每次操作都新建连接（{len(created)} 次）"


# ---------------------------------------------------------------- 疑点 6：命中缓存仍重复落 note
@pytest.mark.asyncio
async def test_cache_hit_does_not_duplicate_audit_note(monkeypatch, fake_redis):
    import app.services.service as svc_mod
    from app.services.service import SimulationService

    repo = await _repo()
    svc = SimulationService(repo)
    monkeypatch.setattr(svc_mod, "llm_client", LLM())

    first = await svc.chapter_prose_audit("c-a")
    second = await svc.chapter_prose_audit("c-a")
    notes = await repo.list_chapter_notes("c-a")
    assert len(notes) == 1, f"命中缓存又写了一条 note（{len(notes)} 条）"
    assert first.get("note_id") and first["note_id"] == second.get("note_id")


@pytest.mark.asyncio
async def test_cache_hit_does_not_duplicate_rewrite_note(monkeypatch, fake_redis):
    import app.services.service as svc_mod
    from app.services.service import SimulationService

    repo = await _repo()
    svc = SimulationService(repo)
    monkeypatch.setattr(svc_mod, "llm_client", LLM())

    a = await svc.chapter_rewrite("c-a", instruction="压缩第二段", scope="para", para_index=2)
    b = await svc.chapter_rewrite("c-a", instruction="压缩第二段", scope="para", para_index=2)
    notes = [n for n in await repo.list_chapter_notes("c-a") if n["kind"] == "polisher"]
    assert len(notes) == 1, f"同一候选命中缓存却落了两条 note（{len(notes)} 条）"
    assert a.get("note_id") == b.get("note_id")


# ---------------------------------------------------------------- 疑点 7：并发 apply 会不会都过
@pytest.mark.asyncio
async def test_concurrent_apply_writes_once(monkeypatch, fake_redis):
    import app.services.service as svc_mod
    from app.services.service import SimulationService

    repo = await _repo()
    svc = SimulationService(repo)
    monkeypatch.setattr(svc_mod, "llm_client", LLM())
    cand = await svc.chapter_rewrite("c-a", instruction="压缩第二段", scope="para", para_index=2)
    note_id = cand["note_id"]

    saves = 0
    orig = svc.save_chapter_prose

    async def spy(cid, text):
        nonlocal saves
        saves += 1
        await asyncio.sleep(0.05)      # 放大并发窗口
        return await orig(cid, text)

    monkeypatch.setattr(svc, "save_chapter_prose", spy)
    r1, r2 = await asyncio.gather(
        svc.apply_prose_note(note_id, confirm=True, based_on=cand["based_on"]),
        svc.apply_prose_note(note_id, confirm=True, based_on=cand["based_on"]),
        return_exceptions=True,
    )
    ok = [r for r in (r1, r2) if isinstance(r, dict) and not r.get("already_applied")]
    assert saves == 1, f"并发 apply 写了 {saves} 次（应只写一次）"
    assert len(ok) <= 1


# ---------------------------------------------------------------- 疑点 2：scope_hint 与 L1 缓存是否落地
def test_audit_items_carry_scope_hint():
    from app.services.engine.chapter_audit import AUDIT_SCHEMA, normalize_audit

    assert "scope_hint" in json.dumps(AUDIT_SCHEMA, ensure_ascii=False), "审计 schema 缺 scope_hint"
    out = normalize_audit({"verdict": "v", "items": [{"detail": "d", "scope_hint": "para"}]})
    assert out["items"][0]["scope_hint"] == "para"
    fallback = normalize_audit({"verdict": "v", "items": [{"detail": "d", "scope_hint": "??"}]})
    assert fallback["items"][0]["scope_hint"] == "chapter"


@pytest.mark.asyncio
async def test_resolve_chapter_uses_l1_cache(monkeypatch):
    from app.db.repo import Repo
    from app.services.service import SimulationService

    repo = await _repo()
    svc = SimulationService(repo)
    calls = 0
    orig = repo.list_chapters_by_book

    async def spy(book_id):
        nonlocal calls
        calls += 1
        return await orig(book_id)

    monkeypatch.setattr(repo, "list_chapters_by_book", spy)
    cache_mod.L1.clear()
    first, _ = await svc._resolve_chapter_ref(chapter_no=1, book_id="b-a")
    second, _ = await svc._resolve_chapter_ref(chapter_no=1, book_id="b-a")
    assert first and second
    assert calls == 1, f"章清单没走 L1 缓存（查了 {calls} 次库）"
