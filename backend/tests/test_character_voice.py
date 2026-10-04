"""E 批回归：角色在场感（只读意见卡）+ 边界不破。

设计依据：docs/正文协作副驾与Agent协作架构.md §11（E 批）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from app.services.agents import character_voice as cv  # noqa: E402

pytestmark = pytest.mark.asyncio


class _Repo:
    def __init__(self, cards):
        self._cards = cards

    async def list_characters_for_scene(self, scene_id: str):
        return self._cards


class _Svc:
    def __init__(self, prose="陈默低声道：\"原来如此。\"\n李文站在门边没有说话。", cards=None):
        self.repo = _Repo(cards if cards is not None else [
            {"id": "chenmo", "name": "陈默", "spec": {
                "summary": "书房主人", "traits": ["敏锐"], "voice": "话不多，句句见血",
                "bottom_lines": ["不施暴"],
            }},
            {"id": "liwen", "name": "李文", "spec": {"summary": "旧识", "voice": "试探", "traits": [], "bottom_lines": []}},
        ])
        self._prose = prose

    async def _scene_and_book(self, scene_id: str):
        return {"id": scene_id, "final_prose": self._prose}, "book-1"


class _VoiceLLM:
    available = True

    def __init__(self):
        self.prompts: list[str] = []

    async def call_cheap(self, prompt, json_schema=None):
        self.prompts.append(prompt)
        return {
            "emotion": "警觉", "monologue": "这话不该由他说出口。",
            "critique": "「原来如此」太软了", "line": "原来如此。",
        }


async def test_character_voices_returns_read_only_opinions(monkeypatch):
    monkeypatch.setattr(cv, "llm_client", _VoiceLLM())
    out = await cv.build_voices(_Svc(), "scene-1")
    assert out["opinions"] and len(out["opinions"]) == 2
    first = out["opinions"][0]
    assert first["name"] == "陈默" and first["emotion"] == "警觉"
    assert first["monologue"] and first["line"] == "原来如此。"
    assert first["avg_len"] is not None          # 0-token 腔调统计一起给
    assert "只读" in out["note"]


async def test_prompt_carries_card_and_voice_metrics(monkeypatch):
    llm = _VoiceLLM()
    monkeypatch.setattr(cv, "llm_client", llm)
    await cv.build_voices(_Svc(), "scene-1")
    prompt = llm.prompts[0]
    assert "陈默" in prompt and "话不多，句句见血" in prompt      # 角色卡腔调进 prompt
    assert "腔调统计" in prompt                                   # 0-token 统计进 prompt
    assert "不得改" in prompt                                     # 红线写在 prompt 里


async def test_character_voices_empty_states_are_honest(monkeypatch):
    monkeypatch.setattr(cv, "llm_client", _VoiceLLM())
    empty = await cv.build_voices(_Svc(prose=""), "scene-1")
    assert empty["opinions"] == [] and "正文为空" in empty["note"]

    no_cast = await cv.build_voices(_Svc(cards=[]), "scene-1")
    assert no_cast["opinions"] == [] and "未配置上场角色" in no_cast["note"]


async def test_character_voices_llm_off_still_gives_metrics(monkeypatch):
    class _Off:
        available = False

        async def call_cheap(self, prompt, json_schema=None):
            return None

    monkeypatch.setattr(cv, "llm_client", _Off())
    out = await cv.build_voices(_Svc(), "scene-1")
    assert len(out["opinions"]) == 2
    assert all(o["monologue"] == "" and "模型未接入" in o["note"] for o in out["opinions"])
    assert all(o["avg_len"] is not None for o in out["opinions"])   # 统计仍然给（0-token）


async def test_character_voices_tool_is_readonly_and_registered():
    from app.services.agents.registry import get_tool
    from app.services.agents.spec import SPECS, can_use

    decl = get_tool("character.voices")
    assert decl is not None and decl.side_effect == "read" and decl.handler == "character_voices"
    assert can_use("editor", "character.voices") is True      # 责编可以请角色发言
    assert can_use("character", "character.voices") is False  # 角色自己不发工具调用
    assert SPECS["character"].can_initiate_tasks is False     # 边界未破：仍不能发起任务
