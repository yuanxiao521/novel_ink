"""记忆检索 / RAG（v1.49）测试：纯函数 + 真实库。

覆盖：
  1. 切块（句读边界、不丢内容、块长上限）
  2. RRF 融合（双通道命中最高分、kind 权重、稳定排序）
  3. 召回渲染（分段标题、预算裁剪）
  4. 兜底直取（没建索引时前情/未回收伏笔照样有）
  5. 角色卡摊平（技术字段不得进 prompt）
  6. 工具注册与闸门（memory.search 只读、主笔/责编可调、角色不可调）
  7. 真实库：建书建章 → 重建索引 → 关键词检索命中 → 清理
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

from app.config import settings  # noqa: E402
from app.services.rag import recall as rag_recall  # noqa: E402
from app.services.rag.chunker import split_text  # noqa: E402
from app.services.rag.fusion import apply_kind_weight, rrf_fuse, top  # noqa: E402
from app.services.rag.indexer import _char_text, _flatten  # noqa: E402


# ---------------------------------------------------------------- 1. 切块
def test_split_text_empty():
    assert split_text("") == []
    assert split_text("   ") == []


def test_split_text_single_short():
    assert split_text("雪落进来。") == ["雪落进来。"]


def test_split_text_long_keeps_all_sentences():
    text = "".join("这是第%d句测试文本，用来验证切块逻辑。" % i for i in range(40))
    chunks = split_text(text, size=120, overlap=10)
    assert len(chunks) > 2
    assert all(len(c) <= 120 + 10 for c in chunks), [len(c) for c in chunks]
    for i in (0, 17, 39):
        assert any("这是第%d句" % i in c for c in chunks), i


def test_split_text_no_punctuation_still_cuts():
    text = "无标点长文本" * 60
    chunks = split_text(text, size=100, overlap=0)
    assert len(chunks) > 2
    assert all(len(c) <= 100 for c in chunks)


# ---------------------------------------------------------------- 2. RRF 融合
def test_rrf_prefers_chunk_hit_by_both_channels():
    fused = rrf_fuse({"vector": ["a", "b"], "keyword": ["b", "c"]})
    # b 两路都中 → 分最高；a、c 各中一路，按名次 a > c
    assert fused["b"] > fused["a"] > fused["c"]


def test_rrf_is_order_insensitive_for_channels():
    one = rrf_fuse({"vector": ["a", "b"], "keyword": ["b", "c"]})
    two = rrf_fuse({"keyword": ["b", "c"], "vector": ["a", "b"]})
    assert set(one) == set(two)
    assert abs(one["b"] - two["b"]) < 1e-12


def test_kind_weight_prefers_characters_over_scenes():
    fused = {"x": 0.01, "y": 0.01}
    weighted = apply_kind_weight(fused, {"x": "character", "y": "scene"})
    assert weighted["x"] > weighted["y"]
    assert weighted["x"] == pytest.approx(0.01 * 1.25)


def test_top_is_stable_on_ties():
    scores = {"b": 0.5, "a": 0.5, "c": 0.9}
    assert top(["a", "b", "c"], scores, 2) == ["c", "a"]


# ---------------------------------------------------------------- 3. 召回渲染
def _bundle(**kw):
    base = {"prev": [], "characters": [], "world": [], "foreshadows": [],
            "mode": "keyword", "from_search": 4, "query": "q", "counts": {}, "chars": 0}
    base.update(kw)
    return base


def test_render_sections_and_order():
    text = rag_recall.render(_bundle(
        prev=[{"title": "第 1 章 雨夜", "text": "林澈捡到一张路引。"}],
        characters=[{"title": "林澈", "text": "summary：不记得自己是谁。"}],
        world=[{"title": "路引", "text": "item 路引：只认背面涂改过的名。"}],
        foreshadows=[{"title": "伏笔（buried）", "text": "路引背面有一道涂改。"}],
    ))
    heads = ["【记忆检索 · 前情提要】", "【记忆检索 · 相关角色】",
             "【记忆检索 · 世界观 / 世界状态】", "【记忆检索 · 未回收伏笔】"]
    positions = [text.find(h) for h in heads]
    assert all(p >= 0 for p in positions), text
    assert positions == sorted(positions)
    assert "林澈" in text and "路引" in text


def test_render_respects_budget():
    long_text = "很长的素材内容。" * 400
    bundle = _bundle(world=[{"title": "世界%d" % i, "text": long_text} for i in range(20)])
    out = rag_recall.render(bundle)
    assert len(out) <= rag_recall.BUDGET["world"] + 40   # 标题行 + 首行


def test_render_empty_bundle_is_empty():
    assert rag_recall.render(_bundle()) == ""


# ---------------------------------------------------------------- 4. 兜底直取
def test_fallback_prev_takes_recent_chapters_before_current():
    chapters = [
        {"id": "c1", "order_no": 1, "title": "一", "summary": "第一章摘要", "prose_md": ""},
        {"id": "c2", "order_no": 2, "title": "二", "summary": "第二章摘要", "prose_md": ""},
        {"id": "c3", "order_no": 3, "title": "三", "summary": "", "prose_md": "第三章正文" * 50},
        {"id": "c4", "order_no": 4, "title": "四", "summary": "", "prose_md": ""},
    ]
    got = rag_recall._fallback_prev(chapters, cur_no=4, limit=3)
    assert [g["chapter_no"] for g in got] == [3, 2, 1]
    assert "第三章正文" in got[0]["text"]


def test_fallback_foreshadows_skips_closed():
    items = [{"id": "f1", "status": "buried", "text": "A"},
             {"id": "f2", "status": "closed", "text": "B"},
             {"id": "f3", "status": "in_progress", "text": "C"}]
    got = rag_recall._fallback_foreshadows(items)
    assert [g["text"] for g in got] == ["A", "C"]


# ---------------------------------------------------------------- 5. 角色卡摊平
def test_char_text_drops_technical_fields():
    spec = {"id": "char-abc", "book_id": "b1", "summary": "在雨里醒来。",
            "dynamic_goals": json.dumps([{"id": "g-1", "text": "弄清涂改是谁的手笔",
                                          "weight": 0.5}], ensure_ascii=False)}
    text = _char_text({"name": "林澈", "spec_json": json.dumps(spec, ensure_ascii=False)})
    assert "char-abc" not in text
    assert "b1" not in text
    assert "在雨里醒来" in text
    assert "弄清涂改是谁的手笔" in text
    assert "{" not in text and "}" not in text


def test_flatten_handles_plain_and_broken_json():
    assert _flatten("普通文本") == "普通文本"
    assert _flatten("[not json") == "[not json"
    assert _flatten(["甲", "乙"]) == "甲、乙"
    assert _flatten(None) == ""


# ---------------------------------------------------------------- 6. 工具注册与闸门
def test_memory_search_tool_registered_and_gated():
    from app.services.agents.registry import get_tool
    from app.services.agents.spec import can_use

    decl = get_tool("memory.search")
    assert decl is not None
    assert decl.side_effect == "read"      # 只读：不改正文、不落库
    assert decl.writes == ""
    assert can_use("chief", "memory.search")
    assert can_use("editor", "memory.search")
    assert not can_use("character", "memory.search")   # 角色只能经黑板表达


@pytest.mark.asyncio
async def test_memory_search_tool_runs_in_memory_mode():
    """无 DB（内存态）时工具也要能跑通：返回空命中而不是报错。"""
    from app.db.repo import Repo
    from app.services.agents.executor import call_tool
    from app.services.service import SimulationService

    svc = SimulationService(Repo(use_db=False))
    res = await call_tool(svc, "editor", "memory.search",
                          {"book_id": "b-none", "query": "路引"})
    assert res.ok
    assert res.data["count"] == 0
    assert res.data["hits"] == []


# ---------------------------------------------------------------- 7. 真实库往返
async def _db_ready() -> bool:
    if not settings.persist:
        return False
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(settings.dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async def _probe():
            async with eng.connect() as conn:
                await conn.execute(_text("SELECT 1"))

        await asyncio.wait_for(_probe(), timeout=5)
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        await eng.dispose()


@pytest.mark.skipif(not settings.persist, reason="persist=False，走内存态")
@pytest.mark.asyncio
async def test_reindex_and_keyword_search_roundtrip():
    """真实库：建书建章 → 重建索引 → 关键词命中 → 清理（不留脏书）。"""
    from uuid import uuid4

    if not await _db_ready():
        pytest.skip("DB 不可用，跳过真实库测试")

    from app.db.repo import Repo
    from app.services.rag import indexer, store

    repo = Repo()
    bid = "b-rag-" + uuid4().hex[:8]
    cid = "c-rag-" + uuid4().hex[:8]
    await repo.save_book({"id": bid, "title": "RAG 测试书", "genre": "测试",
                          "status": "planned", "cover_init": "测", "synopsis": ""})
    try:
        await repo.save_chapter({"id": cid, "book_id": bid, "title": "第 1 章 零里",
                                 "summary": "林澈在零里车站醒来，手里只有一张涂改过的路引。",
                                 "order_no": 1, "tone": "suspense", "word_target": 3000})
        out = await indexer.reindex_book(repo, bid)
        assert out["chunks"] >= 1
        st = await store.stats(bid)
        assert st["chunks"] >= 1 and st["by_kind"].get("chapter", 0) >= 1
        hits = await store.search(bid, "路引", limit=3)
        assert hits, "关键词检索应命中「路引」"
        assert hits[0]["kind"] == "chapter"
        assert "路引" in hits[0]["text"]
        # 重建幂等：id 稳定、条数不变
        again = await indexer.reindex_book(repo, bid)
        assert again["chunks"] == out["chunks"]
    finally:
        await store.delete_book(bid)
        await repo.delete_book(bid)

# ---------------------------------------------------------------- 8. 索引保鲜（v1.49）
@pytest.mark.asyncio
async def test_save_chapter_prose_queues_reindex(monkeypatch):
    """保存正文后必须自动入队重建记忆索引。

    否则「前情提要」永远停在旧版本：索引里那一章的摘要/正文尾部不会自己更新，
    写到第 3 章时检索到的还是第 1 章保存时的样子。
    """
    import app.services.tasks_worker as tw
    from app.db.repo import Repo
    from app.services.service import SimulationService

    calls: list[tuple[str, dict]] = []

    async def fake_enqueue(kind: str, payload: dict) -> dict:
        calls.append((kind, payload))
        return {"job_id": "job-fake", "kind": kind, "status": "queued"}

    monkeypatch.setattr(tw, "enqueue", fake_enqueue)

    repo = Repo(use_db=False)
    await repo.save_book({"id": "b-mem-1", "title": "记忆测试书", "genre": "测试",
                          "status": "planned", "cover_init": "记", "synopsis": ""})
    await repo.save_chapter({"id": "c-mem-1", "book_id": "b-mem-1", "title": "第 1 章",
                             "summary": "摘要", "order_no": 1, "tone": "action",
                             "word_target": 2000})
    svc = SimulationService(repo)

    body = "这是正文。" * 20
    out = await svc.save_chapter_prose("c-mem-1", body)
    assert out["reindex"] == "queued"
    kinds = [k for k, _ in calls]
    assert "chapter_bookkeeping" in kinds, kinds
    assert "book_reindex" in kinds, kinds
    assert any(p.get("book_id") == "b-mem-1" for k, p in calls if k == "book_reindex")

    # 同内容重复保存：两者都不该再入队（天然幂等，不白烧 LLM/算力）
    calls.clear()
    again = await svc.save_chapter_prose("c-mem-1", body)
    assert again["unchanged"] is True
    assert again["reindex"] == "skip"
    assert calls == []

# ---------------------------------------------------------------- 9. 真库落库（v1.49.1 · B57）
@pytest.mark.skipif(not settings.persist, reason="persist=False，走内存态")
@pytest.mark.asyncio
async def test_chapter_prose_actually_persists_to_db():
    """B57 回归：章正文保存必须真的写进 DB，而不是静默降级到内存态。

    事故：`_upsert` 把 `get_chapter()` 返回的 `updated_at`（isoformat 字符串）写回 timestamptz 列
    → asyncpg DataError → 被 except 吞成"降级内存态" → UI 提示已保存、刷新后正文回到旧版本。
    所以这里**换一个新的 Repo 实例复读**（绕开内存态），才验得出真落库。
    """
    from uuid import uuid4

    if not await _db_ready():
        pytest.skip("DB 不可用，跳过真实库测试")

    from app.db.repo import Repo

    repo = Repo()
    bid = "b-b57-" + uuid4().hex[:8]
    cid = "c-b57-" + uuid4().hex[:8]
    await repo.save_book({"id": bid, "title": "B57 测试书", "genre": "测试",
                          "status": "planned", "cover_init": "测", "synopsis": ""})
    try:
        await repo.save_chapter({"id": cid, "book_id": bid, "title": "第 1 章 落库",
                                 "summary": "摘要", "order_no": 1, "tone": "action",
                                 "word_target": 2000})
        body = "林澈把破陶罐放在灯下，罐底刻着一行小字。" * 4
        assert await repo.save_chapter_prose(cid, body) is True
        fresh = await Repo().get_chapter(cid)   # 新实例 = 只可能从 DB 读到
        assert (fresh or {}).get("prose_md") == body, "章正文没有真正落库（B57 复发）"
    finally:
        await repo.delete_book(bid)


