"""LangGraph 回合循环（MVP §5.2 节点图，方案 A：外层循环）。

设计决策：
  - **单回合图**：图内只跑 导演计划→写引导→角色→刷新→成文 一条链；
    是否进入下一回合由**外层调用方**（SimulationService/run_demo 的循环）判断。
    好处：图简单、每回合可落库、逐步 step 与回放贴合前端实时推进。
  - 导演引擎使用**场景传入的 PlanCfg**（不再空配置），否则曝光/调权/举手全部空转。

节点流（单回合）：
  ① director_plan（+turn · 定主动权 · 张力 · 三把巧劲 · 举手）
  ② 黑板写引导（曝光→信念 · 调权→写内因 · 注入→新事实）
  ③ character_phase（顺序：感知→决策→人设护栏→世界护栏→事件写入，带熔断）
  ④ refresh_world（记录冲突回合）
  ⑤ render_prose（成文段落）

共享状态：每个节点取 `state["sim"]`（黑板对象）原地修改。
"""
from __future__ import annotations

from typing import Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app.schemas import models
from app.services.engine.character import CharacterEngine
from app.services.engine.director import DirectorEngine, PlanCfg
from app.services.engine.world import WorldEngine


class NovoState(TypedDict, total=False):
    sim: models.SimulationState
    last_main_actor: Optional[str]


DecideFn = Callable[[str], Optional[dict]]
ConvergeFn = Callable[[models.SimulationState], str]


def _apply_guidance(sim: models.SimulationState, world: WorldEngine) -> None:
    """把导演 hint 落到世界/信念（黑板·写引导）。

    三把巧劲 → 世界实际变化 + 归因事件：
      - 信息曝光 → 目标角色记一条 belief（溯源 DIR）
      - 目标权重 → 改权重 + 写回剧情内因（因果律）
      - 事件注入 → 新增环境事实 + 导演归因事件
    """
    hint = sim.director.hint
    for exp in hint.info_exposures:
        fact = next((f for f in sim.world.facts if f.id == exp.fact_id), None)
        if not fact:
            continue
        world.record_belief(
            char_id=exp.target_char_id, fact_id=fact.id,
            source_event_id="DIR", channel=exp.channel, text=fact.text, confidence=0.8,
        )
    for adj in hint.goal_adjusts:
        world.apply_goal_adjust(adj)
    for line in sim.director.injected_events:
        if line.startswith("事件注入"):
            world.add_fact(text=line.split("：", 1)[-1], source_event_id="DIR", kind="env")
    if sim.director.injected_events:
        world.append_event(
            type_=models.EventType.director_hint,
            source=models.EventSource.director,
            actor="导演",
            payload={"hints": sim.director.injected_events},
        )


def _guard_and_record(
    sim: models.SimulationState, char_id: str, action: dict,
    char_eng: CharacterEngine, world: WorldEngine, guard_retry: int,
) -> Optional[models.Event]:
    """第1层(人设)+第3层(世界事实)护栏 + 熔断。

    拦截则重新演绎；超上限 → 熔断：降级放行带"瑕疵"标记但不写矛盾事实。
    """
    for attempt in range(guard_retry + 1):
        ok1, r1 = char_eng.persona_guard(char_id, action)
        new_fact = str(action.get("new_fact", "") or "")
        ok2, r2 = (True, "") if not new_fact else world.check_fact_consistent(new_fact)
        if ok1 and ok2:
            sim.guard.last_turn_blocks = 0
            return char_eng.record_action(char_id, action)
        sim.guard.last_turn_blocks += 1
        sim.guard.total_intercepts += 1
        sim.guard.last_intercept_reason = {"layer": r1 or r2, "attempt": attempt}
    # 熔断
    if action.get("new_fact"):
        action = {k: v for k, v in action.items() if k != "new_fact"}
    ev = char_eng.record_action(char_id, action)
    ev.guard_flags.append("fuse")
    sim.guard.fuse_counts["guard"] = sim.guard.fuse_counts.get("guard", 0) + 1
    return ev


def build_graph(plan_cfg: PlanCfg, decide_fn: DecideFn, converge_fn: ConvergeFn,
                guard_retry: int, max_turns: int):
    """构造单回合 LangGraph。`plan_cfg` 为导演场景配置（禁止空）。"""
    g = StateGraph(NovoState)

    def director_plan(state: NovoState) -> dict:
        sim = state["sim"]
        director = DirectorEngine(sim, plan_cfg)
        world = WorldEngine(sim)
        sim.world.turn += 1
        if not sim.action_order:
            director.choose_action_order(state.get("last_main_actor"))
        director.measure_tension()
        director.plan()  # LLM 软引导；LLM 不可用/返回空时内部回退确定性 fallback_plan
        _apply_guidance(sim, world)
        return {}

    def character_phase(state: NovoState) -> dict:
        sim = state["sim"]
        char_eng = CharacterEngine(sim)
        world = WorldEngine(sim)
        order = list(sim.action_order)
        if last := state.get("last_main_actor"):
            if last in order and len(order) > 1:
                order = [c for c in order if c != last] + [last]
        last_main = None
        for char_id in order:
            char_eng.think(char_id)          # 思考层：内心独白/推理中间产物 -> sim.scratch
            action = char_eng.decide(char_id, decide_fn)  # 决策层：LLM 或 decide_fn 回退
            if not action:
                continue
            ev = _guard_and_record(sim, char_id, action, char_eng, world, guard_retry)
            if ev:
                last_main = char_id
        return {"last_main_actor": last_main}

    def refresh_world(state: NovoState) -> dict:
        sim = state["sim"]
        for ev in sim.events[-len(sim.action_order):]:
            if ev.payload.get("kind") == "conflict":
                sim.director.last_conflict_turn = sim.world.turn
        return {}

    def render_prose(state: NovoState) -> dict:
        sim = state["sim"]
        world = WorldEngine(sim)
        acts = [e for e in sim.events[-6:] if e.source.value == "character"]
        if not acts:
            return {}
        parts = []
        for e in acts:
            name = sim.characters[e.actor].name if e.actor in sim.characters else e.actor
            t = e.payload.get("text", "")
            parts.append(f"「{t}」——{name}" if e.payload.get("kind") == "dialogue" else f"{t}({name})")
        world.append_event(
            type_=models.EventType.text, source=models.EventSource.system,
            actor="成文", payload={"text": " ".join(parts)},
        )
        # 判定终止状态，写入 sim（外层据此决定是否继续）。
        DirectorEngine(sim, plan_cfg).check_converged(converge_fn)
        if sim.director.converged or sim.director.raise_request.pending or sim.world.turn >= max_turns:
            sim.ended = True
        return {}

    g.add_node("director_plan", director_plan)
    g.add_node("character_phase", character_phase)
    g.add_node("refresh_world", refresh_world)
    g.add_node("render_prose", render_prose)

    g.add_edge(START, "director_plan")
    g.add_edge("director_plan", "character_phase")
    g.add_edge("character_phase", "refresh_world")
    g.add_edge("refresh_world", "render_prose")
    g.add_edge("render_prose", END)
    return g.compile()