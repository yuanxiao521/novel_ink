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

# ---------------------------------------------------------------- S2 前置：角色卡注入（B16 回归）
class _PromptCaptureLLM:
    """记录 prompt 的假 LLM：返回满足 DRAFT_VALIDATOR 的正文（≥20 字）。"""

    def __init__(self):
        self.available = True
        self.calls: list[str] = []

    async def call_cheap(self, prompt, json_schema=None):
        self.calls.append(prompt)
        return "破庙夜谈" * 6

    async def call_strong(self, prompt, json_schema=None):
        return await self.call_cheap(prompt, json_schema)


async def test_draft_injects_character_card_fields():
    """写手 prompt 必须带角色卡的真实字段（summary/traits/voice/bottom_lines），
    而不是旧字段名 personality/tone/bottom_line 造成的「无详细设定」空块（B16 回归）。"""
    import json

    from app.services.engine.prose import draft_prose

    spec = {
        "id": "c-1", "name": "林尘", "summary": "落魄剑修",
        "traits": ["隐忍", "护短"], "voice": "话少，短句，常以「嗯」应人",
        "core_beliefs": ["剑不欺人"], "bottom_lines": ["不伤妇孺"],
    }
    llm = _PromptCaptureLLM()
    await draft_prose(
        llm, {"title": "破庙夜谈"}, "", characters=[
            {"name": "林尘", "spec_json": json.dumps(spec, ensure_ascii=False)},
        ],
    )
    prompt = llm.calls[0]
    assert "落魄剑修" in prompt          # summary
    assert "隐忍" in prompt               # traits
    assert "话少，短句" in prompt         # voice（口吻锚点）
    assert "不伤妇孺" in prompt           # bottom_lines
    assert "（无详细设定）" not in prompt  # 旧字段名错位症状


async def test_draft_marks_blank_card_explicitly():
    """空卡（spec_json={}）→ 明确标注未填卡，不静默假装有设定。"""
    from app.services.engine.prose import draft_prose

    llm = _PromptCaptureLLM()
    await draft_prose(llm, {"title": "破庙夜谈"}, "", characters=[{"name": "路人", "spec_json": "{}"}])
    assert "未填角色卡" in llm.calls[0]


# ---------------------------------------------------------------- S2：口吻纪律 + 体检回环
class _FakeReportLLM(_PromptCaptureLLM):
    """返回固定体检报告，并记录 prompt。"""

    def __init__(self, report):
        super().__init__()
        self._report = report

    async def call_cheap(self, prompt, json_schema=None):
        self.calls.append(prompt)
        return self._report


async def test_draft_prompt_carries_voice_discipline():
    """写手 prompt 必须含「口吻纪律」硬要求（反同质/称呼一致），不能退回口号式要求。"""
    import json

    from app.services.engine.prose import draft_prose

    llm = _PromptCaptureLLM()
    spec = {"name": "林尘", "voice": "话少，短句"}
    await draft_prose(llm, {"title": "破庙夜谈"}, "",
                      characters=[{"name": "林尘", "spec_json": json.dumps(spec, ensure_ascii=False)}])
    prompt = llm.calls[0]
    assert "口吻纪律" in prompt
    assert "可区分" in prompt          # 反同质
    assert "互称" in prompt            # 称呼一致


async def test_review_injects_cards_and_drops_ungrounded_findings():
    """体检：①角色卡逐字进 prompt；②口吻检点只保留"有角色名 + 有正文原句"的条目。"""
    import json

    from app.services.engine.prose import review_prose

    text = "“夹层里。”陈默说。李文却笑了笑：“我帮你查了这么久，你才肯拿出来？”"
    report = {
        "issues": [],
        "voice_findings": [
            {"char": "陈默", "evidence": "“夹层里。”陈默说。", "issue": "话太少", "suggestion": "补一句"},
            {"char": "陈默", "evidence": "（正文里根本没这句）", "issue": "幻觉", "suggestion": "x"},
            {"char": "张三", "evidence": "“夹层里。”陈默说。", "issue": "伪角色", "suggestion": "x"},
            {"char": "李文", "evidence": "", "issue": "无证据", "suggestion": "x"},
        ],
        "overall": "整体尚可",
    }
    llm = _FakeReportLLM(report)
    chars = [{"name": "陈默", "spec_json": json.dumps(
        {"name": "陈默", "voice": "话不多，句句见血", "traits": ["敏锐"]}, ensure_ascii=False)}]
    out = await review_prose(llm, {"title": "书房夜谈"}, text, characters=chars)
    assert "话不多，句句见血" in llm.calls[0]   # 角色卡进体检 prompt
    assert "voice_findings" in llm.calls[0]
    assert [v["char"] for v in out["voice_findings"]] == ["陈默"]  # 仅留可核对的一条
    assert out["voice_findings"][0]["evidence"] == "“夹层里。”陈默说。"


async def test_review_note_payload_keeps_voice_findings(svc):
    """服务层：体检 note 的 payload_json 落 voice_findings（前端可审阅）。"""
    import json

    await svc.repo.save_character({"id": "c-v", "book_id": "book-p", "name": "主角", "spec_json": "{}"})
    out = await svc.prose_review("sc-1", "测试正文。")
    assert out["report"]["voice_findings"] == []          # 无 LLM → 空数组（键存在）
    notes = await svc.list_prose_notes("sc-1")
    payload = json.loads(notes[0].get("payload_json") or "{}")
    assert payload.get("voice_findings") == []
