"""D 批回归：书级约束注入写手 prompt + 审稿会编排 + 评审身份与边界。

设计依据：docs/正文协作副驾与Agent协作架构.md §11（D 批）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- 约束注入写手


async def test_draft_prompt_injects_book_constraints():
    """书级约束只有真正进 prompt 才算"全员读"生效（B 批留下的最后一环）。"""
    from app.services.engine.prose import draft_prose

    captured: dict = {}

    class _LLM:
        available = True

        async def call_cheap(self, prompt, json_schema=None):
            captured["prompt"] = prompt
            return "雨夜书房，烛火被风压弯。陈默把那张残页按在桌面，指节泛白，他终于找到了那半句剑诀。"

    text = await draft_prose(_LLM(), {"title": "雨夜书房"}, "（无）", constraints="硬规则：禁止出现超自然力量")
    assert text.startswith("雨夜书房")
    assert "书级约束" in captured["prompt"]
    assert "禁止出现非超自然力量" not in captured["prompt"]      # 拼写守卫（防手滑）
    assert "禁止出现超自然力量" in captured["prompt"]

    await draft_prose(_LLM(), {"title": "雨夜书房"}, "（无）")
    assert "书级约束" in captured["prompt"]      # 占位仍在
    assert "（无）" in captured["prompt"]         # 未提供时显式标注，不留空


# ---------------------------------------------------------------- 裁决（沿用 A2 阈值）


async def test_verdict_rules_follow_a2_thresholds():
    from app.services.agents.review_meeting import verdict_of

    assert "质量回环" in verdict_of(72, {"coherence": 75}, 0, 0)                 # 总分 < 78
    assert "质量回环" in verdict_of(88, {"coherence": 75, "pacing": 66}, 0, 0)   # 维度 < 70
    assert "润色" in verdict_of(90, {"coherence": 90}, 1, 0)                    # 高危问题
    assert "人工确认" in verdict_of(90, {"coherence": 90}, 0, 2)                # 风险提示
    assert "可直接保存" in verdict_of(92, {"coherence": 92}, 0, 0)
    assert "未评分" in verdict_of(None, {}, 0, 0)


# ---------------------------------------------------------------- 审稿会编排


class _MeetingSvc:
    def __init__(self, issues=None, score=None):
        self.calls: list[str] = []
        self.notes: list[dict] = []
        self.repo = self
        self._issues = issues if issues is not None else [
            {"severity": "high", "text": "节奏拖", "suggestion": "删一句"},
            {"severity": "low", "text": "用词平", "suggestion": ""},
        ]
        self._score = score or {"total": 76, "scores": {"coherence": 80, "pacing": 68}, "weak_points": []}
        self._risks = ["时间线可疑"]

    async def _scene_and_book(self, scene_id: str):
        return {"id": scene_id, "final_prose": "原始正文"}, "book-1"

    async def prose_review(self, scene_id: str, text: str):
        self.calls.append("review")
        return {"report": {"overall": "还行", "issues": self._issues}}

    async def prose_polish(self, scene_id: str, text: str):
        self.calls.append("polish")
        return {"after": "打磨后的正文", "summary": "删一句"}

    async def prose_verify(self, scene_id: str, text: str):
        self.calls.append("verify")
        # 默认有一处风险提示；需要"干净场景"的用例把它置空（见 _CleanSvc）
        return {"opinion": {"risks": self._risks, "foreshadow_updates": [], "belief_deltas": []}}

    async def prose_quality_score(self, scene_id: str, text: str = ""):
        self.calls.append("score")
        return self._score

    async def save_prose_note(self, data: dict):
        self.notes.append(data)


async def test_review_meeting_orchestrates_and_audits():
    """体检 → 润色（有问题才做）→ 质检 → 评审，一次跑完 + 落 reviewer 审计。"""
    from app.services.agents.review_meeting import run_meeting

    svc = _MeetingSvc()
    out = await run_meeting(svc, "scene-1", "原始正文")
    assert svc.calls == ["review", "polish", "verify", "score"]      # 一次编排，顺序固定
    assert [s["step"] for s in out["steps"]] == ["体检", "润色", "质检", "评审"]
    assert out["candidate"] == "打磨后的正文"
    assert "质量回环" in out["verdict"]                               # 76 < 78
    assert out["note_id"].startswith("note-")
    note = svc.notes[0]
    assert note["kind"] == "reviewer" and note["created_by"] == "reviewer"
    assert "审稿会" in note["suggestion"] and "裁决" in note["suggestion"]


async def test_review_meeting_skips_polish_when_clean():
    from app.services.agents.review_meeting import run_meeting

    svc = _MeetingSvc(issues=[], score={"total": 92, "scores": {"coherence": 92}, "weak_points": []})
    svc._risks = []          # 干净场景：无问题、无风险
    out = await run_meeting(svc, "scene-1", "原始正文")
    assert svc.calls == ["review", "verify", "score"]                # 没问题就不白跑润色
    assert out["candidate"] == "" and "可直接保存" in out["verdict"]


async def test_review_meeting_handles_empty_prose():
    from app.services.agents.review_meeting import run_meeting

    class _Empty:
        async def _scene_and_book(self, scene_id: str):
            return {"id": scene_id, "final_prose": ""}, "book-1"

    out = await run_meeting(_Empty(), "scene-1", "")
    assert out["steps"] == [] and "正文为空" in out["verdict"]


# ---------------------------------------------------------------- 评审身份与边界


async def test_reviewer_spec_boundaries():
    from app.services.agents.spec import SPECS, can_use

    reviewer = SPECS["reviewer"]
    assert reviewer.name == "评审" and reviewer.can_initiate_tasks is False
    assert can_use("reviewer", "prose.quality_score") is True
    assert can_use("reviewer", "prose.save") is False        # 评审不改正文
    assert can_use("editor", "prose.review_meeting") is True
    assert can_use("editor", "prose.quality_score") is True


async def test_review_meeting_is_registered_as_tool():
    from app.services.agents.registry import get_tool, list_tools

    decl = get_tool("prose.review_meeting")
    assert decl is not None and decl.side_effect == "write" and decl.handler == "prose_review_meeting"
    names = {t["name"] for t in list_tools()}
    assert {"prose.review_meeting", "prose.quality_score"} <= names
