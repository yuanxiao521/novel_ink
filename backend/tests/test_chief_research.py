"""主笔自主调研（v1.51 · 0-token 逻辑部分）测试。

为什么值得测：调研决定主笔"看什么"，而它的输出会直接进排骨架 prompt。
写坏的两个方向都很难发现——① 收了脏查询（空串/重复/超长）→ prompt 噪音；
② 查不到就假装没查（不记录空命中）→ 作者以为它核过了。这里把两边都钉住。
"""
from __future__ import annotations

import pytest

from app.services.engine.chief_research import (
    MAX_QUERIES, fallback_queries, normalize_queries, render_research,
)


# ---------------------------------------------------------------- 查询清洗
def test_normalize_dedupes_and_caps():
    raw = {"queries": [{"q": "玉佩", "why": "伏笔"},
                       {"q": "玉佩", "why": "重复"},
                       {"q": "  ", "why": "空"},
                       {"q": "宁秋鹜", "why": "断档"},
                       {"q": "a1"}, {"q": "a2"}, {"q": "a3"}, {"q": "a4"},
                       {"q": "a5"}, {"q": "a6"}]}
    out = normalize_queries(raw)
    assert [x["q"] for x in out][:2] == ["玉佩", "宁秋鹜"]
    assert len(out) <= MAX_QUERIES
    assert all(x["q"] for x in out)


def test_normalize_survives_bad_shapes():
    assert normalize_queries(None) == []
    assert normalize_queries({"queries": "not-a-list"}) == []
    assert normalize_queries({"queries": ["str-item", {"q": "ok"}]}) == [{"q": "ok", "why": ""}]


def test_normalize_truncates_long_fields():
    out = normalize_queries({"queries": [{"q": "长" * 99, "why": "长" * 99}]})
    assert len(out[0]["q"]) <= 40 and len(out[0]["why"]) <= 24


# ---------------------------------------------------------------- 确定性兜底
def test_fallback_uses_overdue_characters_and_recent_chapters():
    brief = {
        "foreshadows": {"overdue": [{"text": "玉佩上的缺口应在前两章收掉"}]},
        "characters": {"entries": [{"name": "林澈", "worst_gap": 3}]},
        "chapters": [{"no": 1, "title": "第一章 零里雨夜", "status": "written"},
                     {"no": 2, "title": "第二章 界墙", "status": "planned"}],
    }
    qs = fallback_queries(brief)
    text = [q["q"] for q in qs]
    assert any("玉佩" in t for t in text)
    assert "林澈" in text
    assert any("零里雨夜" in t for t in text)
    # 没写过的章不该被当成"要承接"的素材
    assert not any("界墙" in t for t in text)


def test_fallback_empty_brief_is_empty():
    assert fallback_queries({}) == []


# ---------------------------------------------------------------- 笔记渲染
def test_render_includes_hits_and_misses():
    notes = [
        {"q": "玉佩", "why": "伏笔逾期", "hits": [{"kind": "chapter", "title": "第 3 章",
                                                 "text": "玉佩上的缺口"}]},
        {"q": "谁也不知道的词", "why": "角色断档", "hits": []},
    ]
    text = render_research(notes)
    assert "【主笔调研笔记" in text
    assert "查「玉佩」" in text and "第 3 章：玉佩上的缺口" in text
    # 查了没中必须写出来（防"假装查过"）
    assert "记忆里没有命中" in text


def test_render_respects_budget_and_empty():
    notes = [{"q": f"q{i}", "why": "", "hits": [{"kind": "chapter", "title": "t",
                                                "text": "很长的命中内容" * 30}]}
             for i in range(10)]
    text = render_research(notes, budget=300)
    assert len(text) <= 300
    assert render_research([]) == ""


# ---------------------------------------------------------------- 与 LLM 的边界
@pytest.mark.asyncio
async def test_plan_queries_returns_empty_without_llm():
    """LLM 不可用时返回 []（调用方用 fallback 兜底），不抛异常。"""
    from app.services.engine.chief_research import plan_queries

    class _Off:
        available = False

    assert await plan_queries(_Off(), "brief", "方向", 2) == []


@pytest.mark.asyncio
async def test_plan_queries_parses_fake_llm():
    """模型返回合法 JSON → 清洗成查询列表（不依赖真 LLM）。"""
    from app.services.engine import chief_research as cr

    class _Fake:
        available = True

    async def fake_retry(llm, prompt, schema, validator):
        assert validator({"queries": [{"q": "玉佩"}]}) is None
        return {"queries": [{"q": "玉佩", "why": "伏笔"}, {"q": "玉佩", "why": "重复"}]}

    original = cr.validate_and_retry
    cr.validate_and_retry = fake_retry  # type: ignore[assignment]
    try:
        out = await cr.plan_queries(_Fake(), "brief", "方向", 3)
    finally:
        cr.validate_and_retry = original  # type: ignore[assignment]
    assert out == [{"q": "玉佩", "why": "伏笔"}]
