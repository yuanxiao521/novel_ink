"""章级直写（v1.46 · 直写链路）：分段规划 / prompt 装配 / 承接 / 落库。

为什么值得单测：产品方向 v2 的核心结构变化是「章是最小可写单位，场是可选细分」。
这条链路一旦退化（比如又写死 150-220 字、又不喂章级字段），表现是「写不出一章」——
不报错，只能靠断言看住。
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.db.repo import Repo
from app.services.engine.prose import (DEFAULT_WORD_TARGET, MAX_SEGS, chapter_prompt,
                                      chapter_segment_goals)
from app.services.service import SimulationService


# ---------------------------------------------------------------- 分段规划（0-token）
def test_segment_count_follows_word_target():
    """段数由字数目标决定；没设目标用默认；上限 MAX_SEGS 防跑飞。"""
    assert len(chapter_segment_goals(2500)) == 4        # ceil(2500/800)
    assert len(chapter_segment_goals(1600)) == 2
    assert len(chapter_segment_goals(0)) == -(-DEFAULT_WORD_TARGET // 800)
    assert len(chapter_segment_goals(999999)) == MAX_SEGS
    assert len(chapter_segment_goals(500)) == 1


def test_goal_shape_is_open_push_close():
    """形状固定：开篇 → 推进×(n-2) → 收束（收束段必须留钩子）。"""
    goals = chapter_segment_goals(3200)      # 4 段
    assert len(goals) == 4
    assert goals[0].startswith("开篇")
    assert all(g.startswith("推进") for g in goals[1:-1])
    assert goals[-1].startswith("收束") and "钩子" in goals[-1]
    one = chapter_segment_goals(800)
    assert len(one) == 1 and "钩子" in one[0]


# ---------------------------------------------------------------- prompt 装配
def _chapter(**kw):
    base = {"id": "ch-1", "title": "第一章 雨夜", "tone": "suspense",
            "tension_curve": "raise", "word_target": 1600, "summary": "主角追查失踪案"}
    base.update(kw)
    return base


def test_chapter_prompt_carries_chapter_fields():
    """章级字段必须进 prompt（此前 tone/word_target/summary 落库了却从不进写手 —— F2）。"""
    p = chapter_prompt(chapter=_chapter(), seg_no=2, seg_total=4,
                       seg_goal="推进：制造冲突", seg_target=800)
    assert "第一章 雨夜" in p
    assert "suspense" in p
    assert "1600" in p
    assert "主角追查失踪案" in p
    assert "第 2 段" in p and "4 段" in p
    assert "推进：制造冲突" in p
    assert "不重复已写内容" in p


def test_chapter_prompt_gap_discipline_and_tail_only():
    """摘要未填时要求不要发明；已写正文只喂尾部 600 字（省上下文）。"""
    long_written = "甲" * 2000
    p = chapter_prompt(chapter=_chapter(summary=""), written=long_written,
                       seg_no=2, seg_total=3, seg_goal="推进", seg_target=800)
    assert "不要发明" in p
    assert "甲" * 700 not in p          # 全文不该进 prompt
    assert "甲" * 500 in p              # 尾部要进


# ---------------------------------------------------------------- 服务层：承接与落库
@pytest_asyncio.fixture
async def svc():
    repo = Repo(use_db=False)
    service = SimulationService(repo)
    await repo.save_book({"id": "book-w", "title": "直写书", "genre": "悬疑"})
    await repo.save_chapter({"id": "ch-w", "book_id": "book-w", "title": "第一章 零里雨夜",
                             "order_no": 1, "tone": "suspense", "tension_curve": "raise",
                             "word_target": 1600, "summary": "车站醒来，追查断轨"})
    return service


@pytest.mark.asyncio
async def test_chapter_context_without_scenes(svc):
    """没有场景也能装配上下文（这是「无场景怎么写」的底线）。"""
    ctx = await svc._chapter_context("ch-w")
    assert ctx["word_target"] == 1600
    assert "本章未拆场" in ctx["scenes_text"]
    assert ctx["existing"] == ""


@pytest.mark.asyncio
async def test_chapter_prose_roundtrip(svc):
    """保存 → 读回：prose_md 是权威存储，字数走 text_metrics。"""
    body = "第一段。" + chr(10) + chr(10) + "第二段。"
    r = await svc.save_chapter_prose("ch-w", body)
    assert r["saved"] is True and r["words"] == 8
    p = await svc.get_chapter_prose("ch-w")
    assert p["prose_md"] == body
    assert p["prose"] == body          # 章正文优先于场景聚合
    assert p["words"] == 8 and p["word_count"] == 8
    assert p["word_target"] == 1600 and p["tone"] == "suspense"


@pytest.mark.asyncio
async def test_chapter_draft_stream_segments_and_carryover(svc, monkeypatch):
    """分段写满：2 段 → 2 次生成；第 2 段 prompt 必须带上第 1 段已写内容（承接）。"""
    import app.services.service as service_mod

    class _Fake:
        available = True

        def __init__(self):
            self.prompts = []

        async def chat_stream(self, prompt, temperature=0.8, model=None):
            self.prompts.append(prompt)
            for ch in ["雨", "落", "下", "来"]:
                yield ch

    fake = _Fake()
    monkeypatch.setattr(service_mod, "llm_client", fake)
    got = []
    async for tok in svc.chapter_draft_stream("ch-w"):
        got.append(tok)
    assert len(fake.prompts) == 2, "1600 字目标应分 2 段"
    assert "第 1 段" in fake.prompts[0]
    assert "第 2 段" in fake.prompts[1]
    assert "雨落下来" in fake.prompts[1], "第 2 段必须能看见第 1 段（承接）"
    text = "".join(got)
    assert text.count("雨落下来") == 2
    assert chr(10) + chr(10) in text, "段间应有空行（前端直接拼接即成段落）"
