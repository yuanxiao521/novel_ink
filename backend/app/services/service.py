"""服务层 · SimulationService：面向外部（API/脚本）的业务编排。

能力：启动叙事 / step 回合 / 查看状态 / 作者介入（举手/注入积压）。
数据经 Repo 走 DB；DB 未就绪时内存态兜底。
"""
from __future__ import annotations

import uuid

from langgraph.graph.state import CompiledStateGraph

from app import scenarios
from app.config import settings
from app.data.repo import Repo
from app.schemas import models


class SimulationService:
    def __init__(self, repo: Repo):
        self.repo = repo

    _graphs: dict = {}

    def _graph(self, plan_cfg, decide_fn, converge_fn) -> CompiledStateGraph:
        from app.services.engine.graph import build_graph

        key = (settings.guard_retry_max, settings.max_turns)
        g = self._graphs.get(key)
        if g is None:
            g = build_graph(plan_cfg, decide_fn, converge_fn,
                            settings.guard_retry_max, settings.max_turns)
            self._graphs[key] = g
        return g

    # ---------------------------------------------------------------- 启动叙事
    def start(self, scenario_name: str, sim_id: Optional[str] = None) -> str:
        spec = scenarios.load_scenario(scenario_name)
        sim = models.SimulationState(
            scenario=scenario_name,
            world=models.WorldState(scene_id=scenario_name, title=spec.get("title", "")),
        )
        sim.characters = spec["characters"]
        sim.world.facts = list(spec["initial_facts"])
        sim.director.ending_options = list(spec["plan_cfg"].ending_options)
        sim.action_order = [c for c in sim.characters]

        sid = sim_id or f"sim-{uuid.uuid4().hex[:8]}"
        self.repo.init_schema()
        self.repo.save(sid, sim)
        return sid

    # ---------------------------------------------------------------- step 回合
    def step(self, sim_id: str, n: int = 1) -> models.SimulationState:
        sim = self.repo.load(sim_id)
        if sim is None:
            raise KeyError(f"未找到模拟: {sim_id}")
        spec = scenarios.load_scenario(sim.scenario)  # 按 sim 的场景，而非全局默认
        graph = self._graph(spec["plan_cfg"], spec["decide_fn"], spec["converge_fn"])
        for _ in range(n):
            if sim.ended or sim.director.converged or sim.director.raise_request.pending:
                break
            graph.invoke({"sim": sim})
            self.repo.save(sim_id, sim)
        return sim

    # ---------------------------------------------------------------- 查看状态
    def get_state(self, sim_id: str) -> models.SimulationState:
        sim = self.repo.load(sim_id)
        if sim is None:
            raise KeyError(f"未找到模拟: {sim_id}")
        return sim

    # ---------------------------------------------------------------- 作者介入
    def intervene(self, sim_id: str, action: str, payload: dict) -> models.SimulationState:
        sim = self.repo.load(sim_id)
        if sim is None:
            raise KeyError(f"未找到模拟: {sim_id}")
        from app.services.engine.world import WorldEngine

        world = WorldEngine(sim)

        a = action.strip().lower()
        if a in {"accept", "agree", "同意"}:
            sim.director.raise_request.pending = False
            text = payload.get("text", "")
            if text:
                world.append_event(
                    type_=models.EventType.director_hint, source=models.EventSource.director,
                    actor="导演", payload={"hints": [world.add_fact(text=text, source_event_id="USER").text]},
                )
        elif a in {"reject", "驳回"}:
            sim.director.raise_request.pending = False
        elif a == "inject_event":
            text = payload.get("text", "")
            if text:
                fact = world.add_fact(text=text, source_event_id="USER")
                world.append_event(
                    type_=models.EventType.director_hint, source=models.EventSource.director,
                    actor="导演", payload={"hints": [fact.text]},
                )
        elif a == "adjust_weight":
            world.apply_goal_adjust(models.GoalAdjust(
                char_id=payload.get("char_id", ""), goal_id=payload.get("goal_id", ""),
                delta=float(payload.get("delta", 0)), reason=payload.get("reason", ""),
            ))
        elif a == "expose":
            world.record_belief(
                char_id=payload["target_char_id"], fact_id=payload["fact_id"],
                source_event_id="USER",
                channel=models.BeliefChannel(payload.get("channel", "perceived")),
                text=payload.get("text", payload["fact_id"]),
                confidence=float(payload.get("confidence", 0.8)),
            )
        else:
            raise ValueError(f"未知介入动作: {action}")
        self.repo.save(sim_id, sim)
        return sim