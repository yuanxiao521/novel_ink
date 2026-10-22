"""主笔对话直连骨架审计（v1.68）：作者问「看看第 3 章写得对不对」就出报告。

为什么单独一批：prose.audit 是主笔的「只读眼睛」，但如果只能靠模型自己选工具，
会踩三个坑 —— ①模型不知道内部 chapter_id；②LLM 挂掉时作者一无所获；③行为不可测。
所以对话层做**意图识别 + 快速路径**：命中就直调工具（工具内部仍跑完整审计）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from app.services.agents.editor import (  # noqa: E402
    _audit_reply_text, _chapters_block, _cn_to_int, detect_audit_request,
)


# ---------------------------------------------------------------- 意图识别
def test_detect_audit_request_digits():
    assert detect_audit_request("看看第 3 章写得对不对") == {"chapter_no": 3}
    assert detect_audit_request("第12章有没有偏离骨架") == {"chapter_no": 12}


def test_detect_audit_request_chinese_numerals():
    assert _cn_to_int("一") == 1
    assert _cn_to_int("十") == 10
    assert _cn_to_int("十二") == 12
    assert _cn_to_int("二十一") == 21
    assert detect_audit_request("第十二章合不合骨架") == {"chapter_no": 12}


def test_detect_audit_request_negative_cases():
    assert detect_audit_request("帮我排骨架") is None           # 没提章
    assert detect_audit_request("第 3 章") is None             # 提了章但没说要审
    assert detect_audit_request("看看这一章写得对不对") is None  # 没给章号（留给模型）
    assert detect_audit_request("") is None


# ---------------------------------------------------------------- 提示词素材
def test_chapters_block_maps_number_to_no():
    block = _chapters_block([
        {"order_no": 1, "title": "雨夜", "word_target": 3000, "prose_md": "一二三", "mode": "draft"},
        {"order_no": 2, "title": "街", "word_target": 0, "prose_md": "", "mode": "emerge"},
    ])
    assert "第 1 章《雨夜》" in block and "chapter_no=3" in block
    assert "已写 3 字 / 目标 3000" in block and "目标 —" in block


def test_audit_reply_text_shapes():
    ok = _audit_reply_text({"deviation": {"score": 66, "verdict": "基本完成"},
                            "checks": [{"detail": "字数超量"}],
                            "rendered": "结论：基本完成\n- [low] 字数超量"})
    assert "遵循度 66" in ok and "字数超量" in ok and "一个字没改" in ok
    err = _audit_reply_text({"error": "这本书没有第 9 章"})
    assert "没找到" in err and "第 9 章" in err


# ---------------------------------------------------------------- 快速路径（不需要 LLM）
async def _book_with_two_chapters():
    from app.db.repo import Repo

    repo = Repo(use_db=False)
    await repo.save_book({"id": "b-c", "title": "主笔审计", "genre": "测试",
                          "status": "planned", "cover_init": "审", "synopsis": ""})
    await repo.save_chapter({"id": "c-1", "book_id": "b-c", "title": "第 1 章 雨夜",
                             "summary": "林澈拿到路引。", "order_no": 1, "tone": "suspense",
                             "word_target": 1000})
    await repo.save_chapter_prose("c-1", "林澈在雨里捡到一张路引。")
    await repo.save_chapter({"id": "c-2", "book_id": "b-c", "title": "第 2 章 街",
                             "summary": "街道重排。", "order_no": 2, "tone": "action",
                             "word_target": 2000})
    return repo


@pytest.mark.asyncio
async def test_chief_chat_answers_chapter_question_without_llm(_disable_llm):
    """LLM 不可用时也必须能回答「看看第 1 章写得对不对」（0-token 结论）。"""
    from app.services.agents.editor import chief_chat_with_tools
    from app.services.service import SimulationService

    repo = await _book_with_two_chapters()
    svc = SimulationService(repo)
    out = await chief_chat_with_tools(svc, "b-c", "看看第 1 章写得对不对")
    assert out["fast_path"] == "prose.audit"
    assert out["executed"] and out["executed"][0]["tool"] == "prose.audit"
    assert "遵循度" in out["reply"] or "骨架" in out["reply"]
    # 只读：正文没被改
    assert (await repo.get_chapter("c-1"))["prose_md"] == "林澈在雨里捡到一张路引。"
    # v1.70：LLM 不可用 → 不落 note（留痕只发生在真正跑了模型的审计里），避免噪声
    assert await repo.list_chapter_notes("c-1") == []


@pytest.mark.asyncio
async def test_chief_chat_reports_missing_chapter_gracefully(_disable_llm):
    from app.services.agents.editor import chief_chat_with_tools
    from app.services.service import SimulationService

    repo = await _book_with_two_chapters()
    svc = SimulationService(repo)
    out = await chief_chat_with_tools(svc, "b-c", "看看第 9 章写得对不对")
    assert "没有第 9 章" in out["reply"]
    assert out["executed"] is not None       # 工具确实跑了，只是给了错误码


@pytest.mark.asyncio
async def test_audit_tool_accepts_chapter_no():
    """工具层：chapter_no + book_id 就能定位到章（模型不必知道内部 id）。"""
    from app.services.agents.executor import call_tool
    from app.services.service import SimulationService

    repo = await _book_with_two_chapters()
    svc = SimulationService(repo)
    res = await call_tool(svc, "chief", "prose.audit",
                          {"book_id": "b-c", "chapter_no": 2}, book_id="b-c")
    assert res.ok
    assert res.data["chapter_no"] == 2
    assert res.data["goal"]["chapter"] == "街道重排。"


@pytest.mark.asyncio
async def test_audit_tool_model_path_via_plan(monkeypatch):
    """模型路径也要留给主笔：给一个会调 prose.audit 的计划，事件要跑通。"""
    import app.services.agents.editor as editor_mod
    from app.services.agents.editor import chief_chat_with_tools
    from app.services.service import SimulationService

    repo = await _book_with_two_chapters()
    svc = SimulationService(repo)

    class _PlanLLM:
        available = True

        # 注意：validate_and_retry 走的是 call_cheap（见 engine/schema_retry.py）
        async def call_cheap(self, prompt, json_schema=None):
            return {"reply": "我去审一下第 1 章。",
                    "actions": [{"tool": "prose.audit", "args": {"chapter_no": 1},
                                 "why": "作者问第 1 章写得对不对"}]}

        async def call_strong(self, prompt, json_schema=None):
            return None

    monkeypatch.setattr(editor_mod, "llm_client", _PlanLLM())
    # 用一个不带章号的问法走模型路径（带章号的会被快速路径接走）
    out = await chief_chat_with_tools(svc, "b-c", "我的骨架还稳吗？")
    tools_run = [e["tool"] for e in out.get("executed") or []]
    assert "prose.audit" in tools_run, out
