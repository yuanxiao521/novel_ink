"""阶段④ 正文协作工作区测试（内存态 + 无 LLM 确定性回退）。

覆盖：四角色调用（draft/review/polish/verify）在无 LLM 下走回退 + 审计记录生命周期
（pending → approve 记账 / reject 不写库）+ 正文保存幂等。
"""
from __future__ import annotations

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

from app.db.repo import Repo
from app.errors import InvalidActionError
from app.services.service import SimulationService


@pytest_asyncio.fixture
async def svc():
    repo = Repo(use_db=False)
    service = SimulationService(repo)
    # 装配 book/chapter/scene（正文协作依赖场景与反查 chapter）
    await repo.save_book({"id": "book-p", "title": "测试书", "genre": "玄幻"})
    await repo.save_chapter({"id": "ch-1", "book_id": "book-p", "title": "第一章", "order_no": 1})
    await repo.save_scene({
        "id": "sc-1", "chapter_id": "ch-1", "title": "破庙夜谈",
        "stage_desc": "破庙，供桌上一盏残灯", "goal": "试探妹妹身份",
        "content_desc": "主角在破庙避雨，妹妹蒙面现身。", "final_prose": "",
    })
    return service


async def test_draft_no_llm_returns_empty_and_writer_note(svc):
    """无 LLM → 写手返回空串（前端提示），并留 writer 记录（approved）。"""
    out = await svc.prose_draft("sc-1")
    assert out["text"] == ""
    notes = await svc.list_prose_notes("sc-1")
    assert len(notes) == 1
    assert notes[0]["kind"] == "writer"
    assert notes[0]["status"] == "approved"


async def test_review_no_llm_empty_report_and_pending_note(svc):
    """体检无 LLM → 空问题报告 + editor note（pending）。"""
    out = await svc.prose_review("sc-1", "测试正文，一段内容。")
    assert out["report"]["issues"] == []
    assert out["note_status"] == "pending"
    notes = await svc.list_prose_notes("sc-1")
    assert notes[0]["kind"] == "editor"
    assert notes[0]["status"] == "pending"
    assert notes[0]["before"] == "测试正文，一段内容。"


async def test_polish_no_llm_returns_original(svc):
    """润色无 LLM → after=原文 + polisher note（pending）。"""
    text = "他推开门，夜风扑面。"
    out = await svc.prose_polish("sc-1", text)
    assert out["after"] == text
    assert "未润色" in out["summary"]
    notes = await svc.list_prose_notes("sc-1")
    assert notes[0]["kind"] == "polisher"
    assert notes[0]["after"] == text


async def test_verify_no_llm_empty_opinion(svc):
    """质检无 LLM → 空意见 + verifier note（pending）。"""
    out = await svc.prose_verify("sc-1", "正文……")
    assert out["opinion"]["foreshadow_updates"] == []
    assert out["note_status"] == "pending"


async def test_approve_verifier_note_then_reject_new(svc):
    """审阅生命周期：approve verifier → 置 approved（记账前置）；重复操作拒绝；新 note reject → rejected。"""
    await svc.prose_verify("sc-1", "正文……")
    notes = await svc.list_prose_notes("sc-1")
    vid = notes[0]["id"]
    r = await svc.review_prose_note(vid, approve=True)
    assert r["status"] == "approved"
    assert r["bookkeeping"] is False  # 无 LLM → 空意见，无账本可记（仅置状态）

    with pytest.raises(InvalidActionError):
        await svc.review_prose_note(vid, approve=True)  # 已审阅 → 拒绝

    await svc.prose_review("sc-1", "另一段正文")
    notes = await svc.list_prose_notes("sc-1")
    eid = next(n["id"] for n in notes if n["kind"] == "editor")
    r2 = await svc.review_prose_note(eid, approve=False)
    assert r2["status"] == "rejected"
    assert r2["bookkeeping"] is False  # 非 verifier 不记账


async def test_save_prose_idempotent(svc):
    """保存正文幂等：同内容重复保存 → unchanged=True 且不重复记账（不加新 note）。"""
    first = await svc.save_scene_prose("sc-1", "定稿正文。")
    assert first["unchanged"] is False
    second = await svc.save_scene_prose("sc-1", "定稿正文。")
    assert second["unchanged"] is True
    notes_before = len(await svc.list_prose_notes("sc-1"))
    await svc.save_scene_prose("sc-1", "定稿正文。")
    assert len(await svc.list_prose_notes("sc-1")) == notes_before  # 幂等：不新增记录


async def test_list_notes_sorted(svc):
    """审计记录列表：新记录在前（kind 顺序 writer→editor）。"""
    await svc.prose_review("sc-1", "……")
    notes = await svc.list_prose_notes("sc-1")
    assert notes[0]["kind"] == "editor"  # 新在前
    assert notes[0]["ts"] >= notes[-1]["ts"]


# ---------------------------------------------------------------- 阶段⑤ 记账 Agent
async def test_approve_verifier_writes_foreshadow_and_belief(svc):
    """approve verifier note → 读 payload_json 明细落账本：伏笔推进 + 信念新增（书级角色名→id）。"""
    await svc.repo.save_character({"id": "c-hero", "book_id": "book-p", "name": "主角", "spec_json": "{}"})
    await svc.repo.save_foreshadow({
        "id": "f-1", "book_id": "book-p", "text": "玉佩里的上古剑魂沉睡", "status": "buried",
    })
    opinion = {
        "foreshadow_updates": [{"text": "上古剑魂", "status": "in_progress", "reason": "正文中剑魂低语觉醒"}],
        "belief_deltas": [{"char": "主角", "text": "他终于确信剑魂残念在指引自己", "channel": "inferred"}],
        "causal": [], "risks": [],
    }
    await svc.repo.save_prose_note({
        "id": "note-v1", "scene_id": "sc-1", "kind": "verifier", "status": "pending",
        "suggestion": "概", "before": "", "after": "", "created_by": "verifier",
        "payload_json": __import__("json").dumps(opinion, ensure_ascii=False), "ts": 1,
    })
    r = await svc.review_prose_note("note-v1", approve=True)
    assert r["status"] == "approved"
    assert r["bookkeeping"] == 2  # 实际写入条数：1 伏笔推进 + 1 信念新增

    # 伏笔被推进
    fs = await svc.repo.list_foreshadows("book-p")
    assert fs[0]["status"] == "in_progress"
    assert "PROSE_VERIFY" in fs[0]["notes"]
    # 信念新增
    bs = await svc.repo.list_beliefs("book-p")
    assert any(b["char_id"] == "c-hero" and b["channel"] == "inferred" for b in bs)


async def test_bookkeeping_skips_unknown(svc):
    """记账写入容错：账本无匹配伏笔 / 角色名无对应 id → 该条跳过，不污染账本。"""
    await svc.repo.save_character({"id": "c-2", "book_id": "book-p", "name": "妹妹", "spec_json": "{}"})
    opinion = {
        "foreshadow_updates": [{"text": "不存在的伏笔", "status": "closed", "reason": "x"}],
        "belief_deltas": [{"char": "不存在角色", "text": "无主认知", "channel": "perceived"}],
        "causal": [], "risks": [],
    }
    written = await svc._apply_verify_bookkeeping("book-p", opinion)
    assert written == 0
    assert await svc.repo.list_foreshadows("book-p") == []
    assert await svc.repo.list_beliefs("book-p") == []


async def test_save_unchanged_no_extra_bookkeeping_note(svc):
    """幂等保存：unchanged 不触发自动记账（不新增审计记录）。"""
    await svc.save_scene_prose("sc-1", "定稿正文。")
    await svc.save_scene_prose("sc-1", "定稿正文。")
    notes = await svc.list_prose_notes("sc-1")
    assert len(notes) == 0  # 无 LLM 时自动记账静默跳过，也无书keep note


async def test_bookkeep_after_save_no_llm_returns_silently(svc):
    """无 LLM（FakeLLM）：_bookkeep_after_save 直接返回，不产生 note、不写账本。"""
    await svc.save_scene_prose("sc-1", "新正文内容。")
    await svc._bookkeep_after_save("sc-1", "新正文内容。")  # available=False → early return
    assert await svc.list_prose_notes("sc-1") == []
    assert await svc.repo.list_foreshadows("book-p") == []
    assert await svc.repo.list_beliefs("book-p") == []