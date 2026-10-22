"""字段级记忆索引（v1.62）测试：章 digest → 事实/知晓/钩子，卷概要，伏笔推进痕迹。

为什么要有这一批：v1.49 的索引是「一章一块」（摘要 + 正文尾），检索只能整块搬，
预算全花在无关叙述上；而 v1.58 已经产出了结构化 digest（事件/状态迁移/知晓/钩子），
却没进索引 —— 写手侧读 digest、检索侧读老摘要，两套口径。

覆盖：
  1. digest 三块的文本装配（空字段不产垃圾、坏 JSON 不抛）
  2. build_rows 的 kind 集合（有 digest / 没 digest 两种章都要能索引）
  3. 场素材必须真的进索引（DB 模式的 list_chapters_by_book 不带 scenes —— v1.62 修）
  4. 卷级素材：chapter_no 取卷末章号、stale 不索引
  5. 前情去重：同章只留信息量最高的一种
  6. 章号时间闸：新增章级 kind 必须在可信白名单里（否则后文泄漏）
  7. kind 权重与渲染（知晓段单列、排在前情之后）
  8. 真实库：digest → 重建 → 按 kind 命中；未来章的章事实不得进入前情检索
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
from app.services.rag import indexer  # noqa: E402
from app.services.rag import recall as rag_recall  # noqa: E402
from app.services.rag.fusion import apply_kind_weight  # noqa: E402
from app.services.rag.kinds import CHAPTER_SCOPED_KINDS  # noqa: E402


def _digest(**kw) -> dict:
    base = {
        "one_line": "林澈在零里车站拿到了路引",
        "events": [{"who": "林澈", "did": "捡起路引", "where": "零里车站",
                    "consequence": "路引到了他手里"}],
        "state_changes": [{"target": "路引", "from": "遗在地上", "to": "在林澈手里"}],
        "knowledge": [{"who": "林澈", "learned": "路引背面有涂改", "how": "顺手翻看"}],
        "facts_established": ["路引只认背面涂改过的名"],
        "foreshadow_touched": [{"text": "路引背面的涂改", "action": "bury"}],
        "hook": "雨里有人在找他",
        "open_questions": ["涂改是谁的手笔"],
        "new_entities": [{"name": "零里站", "kind": "place", "note": "废弃车站"}],
        "summary_md": "人读版摘要",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- 1. 文本装配
def test_fact_text_has_events_states_and_entities():
    text = indexer._digest_fact_text(_digest())
    for piece in ("林澈在零里车站拿到了路引", "事件：", "捡起路引", "零里车站",
                  "状态：路引 遗在地上→在林澈手里", "新确立：路引只认背面涂改过的名",
                  "新出现：零里站（place）：废弃车站"):
        assert piece in text, (piece, text)


def test_knowledge_and_hook_text():
    know = indexer._digest_knowledge_text(_digest())
    assert "林澈知道了「路引背面有涂改」" in know and "顺手翻看" in know
    hook = indexer._digest_hook_text(_digest())
    assert "章末钩子：雨里有人在找他" in hook and "未解问题：涂改是谁的手笔" in hook


def test_empty_digest_fields_produce_nothing():
    empty = {"one_line": "", "events": [], "state_changes": [],
             "knowledge": [], "hook": "", "open_questions": [], "facts_established": []}
    assert indexer._digest_fact_text(empty) == ""
    assert indexer._digest_knowledge_text(empty) == ""
    assert indexer._digest_hook_text(empty) == ""


def test_broken_digest_json_is_ignored_not_raised():
    assert indexer._digest_obj({"digest_json": "{not json"}) == {}
    assert indexer._digest_obj({"digest_json": None}) == {}
    assert indexer._digest_obj({"digest_json": json.dumps({"one_line": "x"})}) == {"one_line": "x"}
    # 空 digest → 三块都不产出（调用方保留老 chapter 块，功能不退化）
    rows: list[dict] = []
    assert indexer._push_chapter_digest(rows, "b1", {"id": "c1"}, 1, "第 1 章") is False
    assert rows == []


# ---------------------------------------------------------------- 2. build_rows 的 kind 集合
async def _mem_book_with_digest(book_id: str = "b-dg") -> object:
    from app.db.repo import Repo

    repo = Repo(use_db=False)
    await repo.save_book({"id": book_id, "title": "字段级索引", "genre": "测试",
                          "status": "planned", "cover_init": "字", "synopsis": ""})
    await repo.save_chapter({"id": "c-dg-1", "book_id": book_id, "title": "第 1 章 零里",
                             "summary": "章摘要兜底", "order_no": 1, "tone": "suspense",
                             "word_target": 3000, "volume_id": "vol-b-dg-1",
                             "digest_json": json.dumps(_digest(), ensure_ascii=False)})
    # 没有 digest 的第 2 章：只该有 chapter 一块（升级不要求先跑压缩）
    await repo.save_chapter({"id": "c-dg-2", "book_id": book_id, "title": "第 2 章 雨",
                             "summary": "第二章摘要", "order_no": 2, "tone": "suspense",
                             "word_target": 3000, "digest_json": "{}"})
    await repo.save_book_volume({"id": "vol-b-dg-1", "book_id": book_id, "no": 1,
                                 "title": "零里", "range_from": 1, "range_to": 2,
                                 "arc_goal": "找到路引来路", "climax": "雨夜对峙"})
    await repo.save_book_digest({"id": "vd-b-dg-1", "book_id": book_id, "level": "volume",
                                 "volume_id": "vol-b-dg-1", "range_from": 1, "range_to": 2,
                                 "digest_json": json.dumps({
                                     "arc": "林澈从失忆到摸到路引的线",
                                     "characters": [{"name": "林澈", "where": "零里站",
                                                     "condition": "轻伤",
                                                     "knows": ["路引背面的涂改"],
                                                     "does_not_know": ["自己的身世"],
                                                     "holds": ["路引"], "stance": "想弄清来路"}],
                                     "rules": ["路引只认背面涂改过的名"],
                                     "turning_points": ["第 1 章 拿到路引"],
                                     "ending_state": "手里有路引，被盯上",
                                 }, ensure_ascii=False)})
    await repo.save_scene({"id": "s-dg-1", "chapter_id": "c-dg-1",
                           "title": "站台", "cursor_pos": 0,
                           "stage_desc": "废弃车站，雨", "goal": "拿到路引",
                           "content_desc": "林澈在长椅下摸到路引", "scene_summary": "拿了东西"})
    return repo


@pytest.mark.asyncio
async def test_build_rows_emits_field_level_kinds():
    repo = await _mem_book_with_digest()
    rows = await indexer.build_rows(repo, "b-dg")
    kinds = {r["kind"] for r in rows}
    for kind in ("chapter", indexer.DIGEST_FACT_KIND, indexer.DIGEST_KNOWLEDGE_KIND,
                 indexer.DIGEST_HOOK_KIND, indexer.FORESHADOW_TRACE_KIND,
                 indexer.VOLUME_KIND, "scene"):
        assert kind in kinds, (kind, kinds)
    # 第 1 章有 digest → 三块都在；第 2 章没有 → 只有 chapter 一块
    def _of(kind: str) -> list[dict]:
        return [r for r in rows if r["kind"] == kind]
    assert len(_of(indexer.DIGEST_FACT_KIND)) == 1
    assert _of(indexer.DIGEST_FACT_KIND)[0]["chapter_no"] == 1
    assert _of(indexer.DIGEST_FACT_KIND)[0]["title"].endswith("·事实")
    assert [r["chapter_no"] for r in _of("chapter")] == [1, 2]
    # meta 要带得动前端展示（卷号 + 段名）
    meta = json.loads(_of(indexer.DIGEST_KNOWLEDGE_KIND)[0]["meta_json"])
    assert meta["section"] == "knowledge" and meta["volume_no"] == 1


@pytest.mark.asyncio
async def test_build_rows_indexes_scenes_without_inline_scenes():
    """v1.62 回归：DB 模式的章 dict 不带 scenes，场素材曾整体漏索引（实测 5 场 → 0 块）。"""
    repo = await _mem_book_with_digest()
    rows = await indexer.build_rows(repo, "b-dg")
    scene_rows = [r for r in rows if r["kind"] == "scene"]
    assert scene_rows, "场素材没进索引（回归：list_scenes_by_chapter 没被调用）"
    assert "废弃车站" in scene_rows[0]["text"]


@pytest.mark.asyncio
async def test_volume_row_uses_last_chapter_no_and_skips_stale():
    repo = await _mem_book_with_digest()
    rows = await indexer.build_rows(repo, "b-dg")
    vol_rows = [r for r in rows if r["kind"] == indexer.VOLUME_KIND]
    assert len(vol_rows) == 1
    # 卷概要取卷末章号：卷没写完就不该进当前章的前情（否则提前泄漏后文状态）
    assert vol_rows[0]["chapter_no"] == 2
    assert "人物当前状态：林澈在零里站" in vol_rows[0]["text"]
    assert "不知道自己的身世" in vol_rows[0]["text"]
    assert "卷末状态：手里有路引，被盯上" in vol_rows[0]["text"]
    assert vol_rows[0]["title"].startswith("第 1 卷")
    # stale（边界变过）的卷概要不索引：宁缺勿错
    await repo.save_book_digest({"id": "vd-b-dg-1", "book_id": "b-dg", "level": "volume",
                                 "volume_id": "vol-b-dg-1", "range_from": 1, "range_to": 2,
                                 "stale": True, "digest_json": json.dumps({"arc": "旧弧线"})})
    rows2 = await indexer.build_rows(repo, "b-dg")
    assert not [r for r in rows2 if r["kind"] == indexer.VOLUME_KIND]


# ---------------------------------------------------------------- 5. 前情去重
def test_dedupe_prev_keeps_best_block_per_chapter():
    hits = [
        {"kind": "chapter", "chapter_no": 3, "text": "摘要"},
        {"kind": "chapter_fact", "chapter_no": 3, "text": "事实"},
        {"kind": "chapter_hook", "chapter_no": 3, "text": "钩子"},
        {"kind": "chapter_fact", "chapter_no": 2, "text": "事实2"},
        {"kind": "volume", "chapter_no": 2, "text": "卷概要"},   # 卷和章各自一格
    ]
    got = rag_recall._dedupe_prev(hits, limit=6)
    assert [g["text"] for g in got] == ["事实", "事实2", "卷概要"]
    # 顺序保持检索顺序（近的/相关的靠前由检索决定，去重不重排）
    assert rag_recall._dedupe_prev(hits, limit=2) == got[:2]


def test_dedupe_prev_empty():
    assert rag_recall._dedupe_prev([], 6) == []


# ---------------------------------------------------------------- 6. 章号时间闸
def test_new_chapter_kinds_are_time_scoped():
    """新增章级 kind 如果不在白名单里，后文事实会被当成"前情"喂给写手（硬伤）。"""
    for kind in (indexer.DIGEST_FACT_KIND, indexer.DIGEST_KNOWLEDGE_KIND,
                 indexer.DIGEST_HOOK_KIND, indexer.VOLUME_KIND):
        assert kind in CHAPTER_SCOPED_KINDS, kind
    for kind in ("character", "world", "foreshadow", "belief", "memory", "scene"):
        assert kind not in CHAPTER_SCOPED_KINDS, kind


def test_chapter_scope_is_none_without_limit():
    from app.services.rag.store import _chapter_scope

    assert _chapter_scope(None) is None
    assert _chapter_scope(5) is not None


# ---------------------------------------------------------------- 7. 权重与渲染
def test_kind_weight_prefers_knowledge_over_hook():
    weighted = apply_kind_weight({"a": 0.01, "b": 0.01, "c": 0.01},
                                 {"a": "chapter_knowledge", "b": "chapter_hook",
                                  "c": "scene"})
    assert weighted["a"] > weighted["b"] > weighted["c"]


def test_render_puts_knowledge_after_prev():
    bundle = {"prev": [{"title": "第 1 章·事实", "text": "林澈拿到路引"}],
              "knowledge": [{"title": "第 1 章·知晓", "text": "林澈知道了路引有涂改"}],
              "characters": [], "world": [], "foreshadows": [],
              "mode": "keyword", "from_search": 2, "query": "q", "counts": {}, "chars": 0}
    text = rag_recall.render(bundle)
    assert text.index("【记忆检索 · 前情提要】") < text.index("【记忆检索 · 知晓范围】")
    assert "知道了路引有涂改" in text


def test_render_hides_empty_knowledge_section():
    bundle = {"prev": [{"title": "第 1 章", "text": "x"}], "knowledge": [],
              "characters": [], "world": [], "foreshadows": [],
              "mode": "keyword", "from_search": 1, "query": "q", "counts": {}, "chars": 0}
    assert "知晓范围" not in rag_recall.render(bundle)


# ---------------------------------------------------------------- 8. 真实库
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
async def test_digest_index_and_future_chapter_does_not_leak():
    """真实库：digest 拆块 → 重建 → 按 kind 命中；**后一章的章事实不能进前情**。"""
    from uuid import uuid4

    if not await _db_ready():
        pytest.skip("DB 不可用，跳过真实库测试")

    from app.db.repo import Repo
    from app.services.rag import store

    repo = Repo()
    bid = "b-dg-" + uuid4().hex[:8]
    c1, c2 = bid + "-c1", bid + "-c2"
    await repo.save_book({"id": bid, "title": "字段级真实库", "genre": "测试",
                          "status": "planned", "cover_init": "字", "synopsis": ""})
    try:
        await repo.save_chapter({"id": c1, "book_id": bid, "title": "第 1 章 零里",
                                 "summary": "第一章摘要", "order_no": 1, "tone": "suspense",
                                 "word_target": 3000,
                                 "digest_json": json.dumps(_digest(), ensure_ascii=False)})
        await repo.save_chapter({"id": c2, "book_id": bid, "title": "第 2 章 雨",
                                 "summary": "第二章摘要", "order_no": 2, "tone": "suspense",
                                 "word_target": 3000,
                                 "digest_json": json.dumps(_digest(
                                     one_line="陆照把路引夺走了",
                                     events=[{"who": "陆照", "did": "夺走路引",
                                              "where": "巷口", "consequence": "路引易主"}],
                                     state_changes=[{"target": "路引", "from": "在林澈手里",
                                                     "to": "在陆照手里"}],
                                     knowledge=[{"who": "陆照", "learned": "林澈在查涂改",
                                                 "how": "跟踪"}],
                                 ), ensure_ascii=False)})
        await indexer.reindex_book(repo, bid)
        stats = await store.stats(bid)
        assert stats["by_kind"].get(indexer.DIGEST_FACT_KIND) == 2, stats["by_kind"]
        assert stats["by_kind"].get(indexer.DIGEST_KNOWLEDGE_KIND) == 2, stats["by_kind"]

        # 写第 2 章时：前情只该看到第 1 章；第 2 章自己的事实必须被时间闸挡住
        prev = await store.search(bid, "路引", kinds=[indexer.DIGEST_FACT_KIND],
                                  limit=10, chapter_no_lt=2)
        assert prev, "第 1 章的章事实应命中"
        assert all(h["chapter_no"] < 2 for h in prev), [h["chapter_no"] for h in prev]
        assert any("林澈在零里车站" in h["text"] for h in prev)
    finally:
        await store.delete_book(bid)
        await repo.delete_book(bid)
