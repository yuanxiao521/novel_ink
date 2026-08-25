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
    """模拟导演收敛判定；call_cheap 原样返回显式结局文本。"""

    available = True

    def __init__(self, ending: str):
        self.ending = ending

    def call_strong(self, prompt: str, json_schema=None):
        return None

    def call_cheap(self, prompt: str, json_schema=None):
        return self.ending


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


def test_guardrail_blocks_llm_converge_before_turn_3(monkeypatch):
    """回合 1 时即使 LLM 想收束也必须被护栏拦截。"""
    import app.services.engine.director as director_module

    sim = _build_sim()
    sim.world.turn = 1
    director = DirectorEngine(sim, scenarios.load_scenario("betrayal_night")["plan_cfg"])

    monkeypatch.setattr(director_module, "llm_client", _ConvergeLLM(ending="背叛暴露"))
    # 护栏在 turn<3 直接短路，连 LLM 都不该被调
    assert director.llm_converge() == ""
    assert sim.director.converged is False


def test_llm_converge_allowed_from_turn_3(monkeypatch):
    """回合 >=3 才能交给 LLM；命中 ending_options 才收束。"""
    import app.services.engine.director as director_module

    sim = _build_sim()
    sim.world.turn = 4
    director = DirectorEngine(sim, scenarios.load_scenario("betrayal_night")["plan_cfg"])

    monkeypatch.setattr(director_module, "llm_client", _ConvergeLLM(ending="背叛暴露"))
    assert director.llm_converge() == "背叛暴露"

    # 兜底：LLM 返回无法匹配任一显式结局 → 交给场景 converge_fn（返回 ""）
    monkeypatch.setattr(director_module, "llm_client", _ConvergeLLM(ending="天花板塌了"))
    assert director.llm_converge() == ""