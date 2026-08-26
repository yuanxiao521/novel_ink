"""导演 llm_converge 回合护栏回归测试（修复：真实 LLM 在回合 1 提前收束）。

护栏契约：
  - world.turn < 3 → 必须返回 ""（不收束），挤压多回合涌现与前端导演台演示。
  - world.turn >= 3 → 交给 LLM 判定；LLM 返回的文本命中 ending_options 之一才收束。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from app import scenarios  # noqa: E402
from app.schemas import models  # noqa: E402
from app.services.engine.director import DirectorEngine  # noqa: E402


class _ConvergeLLM:
    """模拟导演收敛判定；call_cheap 原样返回显式结局文本（async）。"""

    available = True

    def __init__(self, ending: str):
        self.ending = ending

    async def call_strong(self, prompt: str, json_schema=None):
        return None

    async def call_cheap(self, prompt: str, json_schema=None):
        return self.ending


class _UnavailableLLM:
    """LLM 不可用：available=False（导演长程汇报走确定性兜底）。"""

    available = False

    async def call_strong(self, prompt: str, json_schema=None):
        return None

    async def call_cheap(self, prompt: str, json_schema=None):
        return None


def _build_sim() -> models.SimulationState:
    spec = scenarios.load_scenario("betrayal_night")
    sim = models.SimulationState(
        scenario="betrayal_night",
        world=models.WorldState(scene_id="betrayal_night", title=spec.get("title", "")),
    )
    sim.characters = spec["characters"]
    sim.world.facts = list(spec["initial_facts"])
    sim.director.ending_options = list(spec["plan_cfg"].ending_options)
    sim.action_order = [c for c in sim.characters]
    return sim


def test_guardrail_blocks_llm_converge_before_turn_6(monkeypatch):
    """回合 < MIN_CONVERGE_TURN(6) 时即使 LLM 想收束也必须被护栏拦截。"""
    import asyncio
    import app.services.engine.director as director_module

    sim = _build_sim()
    sim.world.turn = 1
    director = DirectorEngine(sim, scenarios.load_scenario("betrayal_night")["plan_cfg"])

    async def _run():
        return await director.llm_converge()

    monkeypatch.setattr(director_module, "llm_client", _ConvergeLLM(ending="背叛暴露"))
    # 护栏在 turn<6 直接短路，连 LLM 都不该被调
    assert asyncio.run(_run()) == ""
    assert sim.director.converged is False

    # turn=4（<6）同样被拦截
    sim.world.turn = 4
    assert asyncio.run(_run()) == ""


def test_llm_converge_needs_two_votes_from_turn_6(monkeypatch):
    """回合 >=6 才能交给 LLM；且同一结局需连续两回合判定一致才收束。"""
    import asyncio
    import app.services.engine.director as director_module

    sim = _build_sim()
    sim.world.turn = 6
    director = DirectorEngine(sim, scenarios.load_scenario("betrayal_night")["plan_cfg"])

    async def _run():
        return await director.llm_converge()

    monkeypatch.setattr(director_module, "llm_client", _ConvergeLLM(ending="背叛暴露"))
    # 第一次判定：记票但不收束（防随机抖动）
    assert asyncio.run(_run()) == ""
    # 第二次同结局 → 返回结局（converged 标志由上层 check_converged 落盘）
    assert asyncio.run(_run()) == "背叛暴露"

    # 中途换结局 → 清账重新累计，不立即收束
    sim2 = _build_sim()
    sim2.world.turn = 7
    dir2 = DirectorEngine(sim2, scenarios.load_scenario("betrayal_night")["plan_cfg"])

    async def _run2():
        return await dir2.llm_converge()

    monkeypatch.setattr(director_module, "llm_client", _ConvergeLLM(ending="隐瞒成功"))
    assert asyncio.run(_run2()) == ""
    monkeypatch.setattr(director_module, "llm_client", _ConvergeLLM(ending="背叛暴露"))
    assert asyncio.run(_run2()) == ""

    # 兜底：LLM 返回无法匹配任一显式结局 → 清账、不收束（交给场景 converge_fn）
    monkeypatch.setattr(director_module, "llm_client", _ConvergeLLM(ending="天花板塌了"))
    assert asyncio.run(_run2()) == ""


def test_analyze_scene_close_fallback_no_llm(monkeypatch):
    """场景收束长程汇报：LLM 不可用时走确定性兜底，不抛异常、摘要取自最近归档。"""
    import asyncio
    import app.services.engine.director as director_module

    sim = _build_sim()
    sim.scene_id = "scene-betrayal-night"
    sim.book_id = "book-rain"
    sim.chapter_id = "chapter-01"
    sim.world.turn = 6
    sim.turn_archives.append(models.TurnArchive(
        turn=6, tension=70, cls="type-conflict", summary="雨夜对峙", prose="两人在书房沉默。"))
    sim.events.append(models.Event(
        id="E-001", turn=6, type=models.EventType.action,
        source=models.EventSource.character, actor="chenmo",
        payload={"kind": "dialogue", "text": "今晚的事，你总该说个明白。"}))
    director = DirectorEngine(sim, scenarios.load_scenario("betrayal_night")["plan_cfg"])
    monkeypatch.setattr(director_module, "llm_client", _UnavailableLLM())

    async def _run():
        return await director.analyze_scene_close(foreshadow_items=[], remaining_scenes=["藏宝洞"])

    r = asyncio.run(_run())
    assert isinstance(r, dict)
    assert r["scene_summary"]  # 兜底：取最近归档摘要
    assert isinstance(r["foreshadow_updates"], list)
    assert isinstance(r["causality"], list)