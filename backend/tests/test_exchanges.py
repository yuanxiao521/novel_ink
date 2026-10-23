"""连拍（exchanges）与多行为回归（v1.73）。

作者拍板：方案一（导演连拍 + 多行为 decide）+ 方案三（对话快速通道），默认 1 来回。
锁死三件事：①本来回独立归档（时间线一个节点）②回退精确到来回末尾 ③decide_multi
的多行为拆分/旧格式兼容/上限截断。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from app.schemas import models  # noqa: E402


def _sim() -> models.SimulationState:
    sim = models.SimulationState(
        id="s-burst", scenario="betrayal_night", book_id="b-b", chapter_id="c-b", scene_id="s-b",
        characters={
            "a": models.CharacterCard(id="a", name="甲", summary="主角"),
            "b": models.CharacterCard(id="b", name="乙", summary="对手"),
        },
        action_order=["a", "b"],
    )
    return sim


def _burst_events(sim: models.SimulationState) -> None:
    """模拟一个 2 来回的拍：来回1 = 甲台词 + 乙回应；来回2 = 甲动作 + 乙冲突。"""
    from app.services.engine.world import WorldEngine

    w = WorldEngine(sim)
    sim.world.turn = 5
    w.append_event(type_=models.EventType.director_hint, source=models.EventSource.director,
                   actor="导演", payload={"hints": ["舞台提示：让甲先开口"]})
    w.append_event(type_=models.EventType.action, source=models.EventSource.character,
                   actor="a", payload={"kind": "dialogue", "text": "你为什么背着我？", "exchange": 1})
    w.append_event(type_=models.EventType.action, source=models.EventSource.character,
                   actor="b", payload={"kind": "dialogue", "text": "我没有选择。", "exchange": 1})
    w.append_event(type_=models.EventType.action, source=models.EventSource.character,
                   actor="a", payload={"kind": "action", "text": "摔门而出", "exchange": 2})
    w.append_event(type_=models.EventType.action, source=models.EventSource.character,
                   actor="b", payload={"kind": "conflict", "text": "追出去拦住他", "exchange": 2})
    w.append_event(type_=models.EventType.text, source=models.EventSource.system,
                   actor="成文", payload={"text": "雨夜的对峙在门口碎裂。"})


# ---------------------------------------------------------------- 归档按来回拆条
def test_archive_splits_by_exchange():
    from app.db.repo import Repo
    from app.services.service import SimulationService

    repo = Repo(use_db=False)
    svc = SimulationService(repo)
    sim = _sim()
    prev = len(sim.events)
    _burst_events(sim)
    svc._archive_turn(sim, prev)

    arcs = sim.turn_archives
    assert len(arcs) == 2, f"2 个来回应归档 2 条，实际 {len(arcs)}"
    assert [a.exchange for a in arcs] == [1, 2]
    assert [len(a.events) for a in arcs] == [2, 2], "每个来回各自的行动"
    # 旁白挂最后一个来回；第一个来回 prose 空（时间线摘要仍有）
    assert arcs[0].prose == "" and arcs[1].prose == "雨夜的对峙在门口碎裂。"
    # 思考挂第一个来回（拍内思考只发生一次）
    sim.scratch["thoughts"] = {"a": {"thought": "他心里有鬼"}}
    prev2 = len(sim.events)
    svc._archive_turn(sim, prev2)   # 无新事件 → 幂等不追加
    # 冲突优先定类型
    assert arcs[1].cls == "type-conflict"


def test_archive_single_exchange_default():
    """无 exchange 标记的旧事件 → 全算第 1 个来回（兼容旧路径）。"""
    from app.db.repo import Repo
    from app.services.engine.world import WorldEngine
    from app.services.service import SimulationService

    repo = Repo(use_db=False)
    svc = SimulationService(repo)
    sim = _sim()
    prev = len(sim.events)
    w = WorldEngine(sim)
    sim.world.turn = 3
    w.append_event(type_=models.EventType.action, source=models.EventSource.character,
                   actor="a", payload={"kind": "action", "text": "他推门"})
    svc._archive_turn(sim, prev)
    assert len(sim.turn_archives) == 1
    assert sim.turn_archives[0].exchange == 1
    assert sim.turn_archives[0].last_event_id == sim.events[-1].id


# ---------------------------------------------------------------- 回退精确到来回
@pytest.mark.asyncio
async def test_rewind_cuts_at_exchange_boundary():
    from app.db.repo import Repo
    from app.services.service import SimulationService

    repo = Repo(use_db=False)
    svc = SimulationService(repo)
    sim = _sim()
    prev = len(sim.events)
    _burst_events(sim)
    svc._archive_turn(sim, prev)

    ex1 = next(a for a in sim.turn_archives if a.exchange == 1)
    await repo.save("s-burst", sim)
    out = await svc.rewind("s-burst", 5)
    # 事件截到**来回 1 末尾**（乙的"我没有选择。"），来回 2 的两条被截掉
    texts = [e.payload.get("text") for e in out.events if e.source.value == "character"]
    assert texts == ["你为什么背着我？", "我没有选择。"], texts
    assert [a.exchange for a in out.turn_archives] == [1]


# ---------------------------------------------------------------- decide_multi
class _LLMMulti:
    """按 schema 记录 prompt，返回 behaviors 数组 / 旧单行为 / 超量。"""

    def __init__(self, payload):
        self.payload = payload
        self.prompts: list[str] = []

    async def call_strong(self, prompt, json_schema=None):
        self.prompts.append(prompt)
        return self.payload

    async def call_cheap(self, prompt, json_schema=None):
        return None


@pytest.mark.asyncio
async def test_decide_multi_splits_behaviors(monkeypatch):
    import app.services.engine.character as char_mod
    from app.services.engine.character import CharacterEngine

    llm = _LLMMulti({"behaviors": [
        {"kind": "dialogue", "text": "你为什么背着我？", "new_fact": ""},
        {"kind": "action", "text": "把照片摔在桌上", "new_fact": "照片被摔在桌上"},
        {"kind": "dialogue", "text": "第三条（应被截断）", "new_fact": ""},
    ]})
    monkeypatch.setattr(char_mod, "llm", llm)
    sim = _sim()
    eng = CharacterEngine(sim)
    acts = await eng.decide_multi("a", lambda ctx: None, behaviors_max=2)
    assert [a["text"] for a in acts] == ["你为什么背着我？", "把照片摔在桌上"], "超出上限截断"
    assert acts[1]["new_fact"] == "照片被摔在桌上"


@pytest.mark.asyncio
async def test_decide_multi_legacy_single_format(monkeypatch):
    """旧格式 {"kind","text"}（无 behaviors）→ 单条行为，行为不变。"""
    import app.services.engine.character as char_mod
    from app.services.engine.character import CharacterEngine

    llm = _LLMMulti({"kind": "dialogue", "text": "旧格式一句话", "new_fact": ""})
    monkeypatch.setattr(char_mod, "llm", llm)
    eng = CharacterEngine(_sim())
    acts = await eng.decide_multi("a", lambda ctx: None, behaviors_max=3)
    assert len(acts) == 1 and acts[0]["text"] == "旧格式一句话"


@pytest.mark.asyncio
async def test_decide_multi_falls_back_when_llm_empty(monkeypatch):
    """LLM 产出空 → 确定性回退脚本（单条），与旧行为一致。"""
    import app.services.engine.character as char_mod
    from app.services.engine.character import CharacterEngine

    llm = _LLMMulti({"behaviors": []})
    monkeypatch.setattr(char_mod, "llm", llm)
    eng = CharacterEngine(_sim())
    acts = await eng.decide_multi("a", lambda ctx: {"kind": "action", "text": "回退行为"},
                                  behaviors_max=2)
    assert len(acts) == 1 and acts[0]["text"] == "回退行为"
