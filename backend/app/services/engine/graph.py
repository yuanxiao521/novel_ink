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

import asyncio
from typing import Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.config import get_stream_writer

from app.schemas import models
from app.services.engine.character import CharacterEngine
from app.services.engine.director import DirectorEngine, PlanCfg
from app.services.engine.world import WorldEngine
from app.services.llm.client import client as llm_client


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
    """第1层(人设)+第3层(世界事实)+第4步(世界观规则) 护栏 + 熔断。

    拦截则重新演绎；超上限 → 熔断：降级放行带"瑕疵"标记但不写矛盾事实。
    """
    from app.services.engine import world_rules

    rules = sim.scratch.get("world_rules") or []
    for attempt in range(guard_retry + 1):
        ok1, r1 = char_eng.persona_guard(char_id, action)
        new_fact = str(action.get("new_fact", "") or "")
        ok2, r2 = (True, "") if not new_fact else world.check_fact_consistent(new_fact)
        vr = world_rules.check_action(action, rules)  # 世界规则（玄幻硬约束）
        ok3 = not vr
        r3 = f"world_rule:{vr[0].rule}" if vr else ""
        if ok1 and ok2 and ok3:
            sim.guard.last_turn_blocks = 0
            return char_eng.record_action(char_id, action)
        sim.guard.last_turn_blocks += 1
        sim.guard.total_intercepts += 1
        sim.guard.last_intercept_reason = {
            "layer": r1 or r2 or r3,
            "attempt": str(attempt),
            "rules": world_rules.serialize(vr) if vr else [],
        }
    # 熔断
    if action.get("new_fact"):
        action = {k: v for k, v in action.items() if k != "new_fact"}
    ev = char_eng.record_action(char_id, action)
    ev.guard_flags.append("fuse")
    sim.guard.fuse_counts["guard"] = sim.guard.fuse_counts.get("guard", 0) + 1
    return ev


def _default_prose(sim: models.SimulationState, acts: list) -> str:
    """确定性成文：把最近角色行动拼成一段（无 LLM 时回退）。"""
    parts = []
    for e in acts:
        name = sim.characters[e.actor].name if e.actor in sim.characters else e.actor
        t = e.payload.get("text", "")
        parts.append(f"「{t}」——{name}" if e.payload.get("kind") == "dialogue" else f"{t}({name})")
    return " ".join(parts)


def build_graph(plan_cfg: PlanCfg, decide_fn: DecideFn, converge_fn: ConvergeFn,
                guard_retry: int, max_turns: int):
    """构造单回合 LangGraph。`plan_cfg` 为导演场景配置（禁止空）。"""
    g = StateGraph(NovoState)

    async def director_plan(state: NovoState) -> dict:
        sim = state["sim"]
        director = DirectorEngine(sim, plan_cfg)
        world = WorldEngine(sim)
        writer = get_stream_writer()
        sim.world.turn += 1
        if not sim.action_order:
            director.choose_action_order(state.get("last_main_actor"))
        director.measure_tension()
        await director.plan()  # LLM 软引导；LLM 不可用/返回空时内部回退确定性 fallback_plan
        _apply_guidance(sim, world)
        # 即时事件：导演决策完成（张力/提示/注入列表/举手）
        writer({
            "kind": "director",
            "turn": sim.world.turn,
            "tension": sim.director.tension,
            "tension_trend": sim.director.tension_trend,
            "hint": sim.director.hint.stage_prompt,
            "injected": sim.director.injected_events,
            "raise_request": sim.director.raise_request.model_dump(),
        })
        return {}

    async def character_phase(state: NovoState) -> dict:
        sim = state["sim"]
        char_eng = CharacterEngine(sim)
        world = WorldEngine(sim)
        writer = get_stream_writer()
        order = list(sim.action_order)
        if last := state.get("last_main_actor"):
            if last in order and len(order) > 1:
                order = [c for c in order if c != last] + [last]

        for char_id in order:
            writer({"kind": "character_perceive", "turn": sim.world.turn, "char_id": char_id})

        # 思考层·并行：三个角色的内心独白互不依赖（perceive_context 各自子集），
        # asyncio.gather 同时发 3 个流式请求，边流边推 character_think_delta；
        # 决策层必须保持串行（每个角色决定前能看到前一个角色刚做的行动，近况感知）。
        async def _stream_think(cid: str) -> tuple[str, str]:
            text = ""
            async for tok in char_eng.stream_think(cid):
                text += tok
                writer({"kind": "character_think_delta", "turn": sim.world.turn,
                        "char_id": cid, "delta": tok})
            return cid, text

        think_results: dict[str, str] = {}
        for cid, text in await asyncio.gather(*[_stream_think(c) for c in order]):
            think_results[cid] = text

        # 兼容：LLM 不可用的角色走非流式 JSON 思考（可能为 None，无思考产物）
        for cid in order:
            if think_results.get(cid):
                continue
            thought = await char_eng.think(cid)
            if isinstance(thought, dict):
                t_val = str(thought.get("thought") or thought.get("reasoning") or "")
                if t_val:
                    writer({"kind": "character_think", "turn": sim.world.turn,
                            "char_id": cid, "thought": t_val})

        # 决策层·串行（顺序固定）：每个角色 decide 前，前一人行动已写入 events →
        # perceive_context 的【近况】能看到上文，保持"角色互相影响"的涌现
        last_main = None
        for char_id in order:
            action = await char_eng.decide(char_id, decide_fn)  # LLM 或 decide_fn 回退
            if not action:
                continue
            ev = _guard_and_record(sim, char_id, action, char_eng, world, guard_retry)
            if ev:
                last_main = char_id
                writer({
                    "kind": "character_act",
                    "turn": sim.world.turn,
                    "char_id": char_id,
                    "events": [ev.model_dump()],
                    "guard_flags": ev.guard_flags,
                })
        # 主戏角色写回 sim（黑板模式；astream 不返回最终 state，故节点内直接落 sim）
        sim.last_main_actor = last_main or sim.last_main_actor
        return {"last_main_actor": last_main}

    def refresh_world(state: NovoState) -> dict:
        sim = state["sim"]
        for ev in sim.events[-len(sim.action_order):]:
            if ev.payload.get("kind") == "conflict":
                sim.director.last_conflict_turn = sim.world.turn
        return {}

    async def render_prose(state: NovoState) -> dict:
        sim = state["sim"]
        world = WorldEngine(sim)
        writer = get_stream_writer()
        acts = [e for e in sim.events[-6:] if e.source.value == "character"]
        if not acts:
            return {}
        full_text = ""
        # 事件级/逐 token：LLM 可用 → 流式成文（旁白凝聚，逐 token 推送）；否则确定性拼装一次推送
        if llm_client.available:
            act_summary = []
            for e in acts:
                name = sim.characters[e.actor].name if e.actor in sim.characters else e.actor
                t = e.payload.get("text", "")
                k = e.payload.get("kind", "")
                act_summary.append(f"[{k}] {name}：{t}")
            stage = (sim.world.env_conds or [""])[0] if sim.world.env_conds else ""
            prompt = (
                "你是小说实时旁白生成器（成文者）。根据本回合角色行为，写一段（180-280字）" 
                "有画面感的中文小说正文，把角色的行动与台词交织成连贯叙述：\n"
                "· 有环境/景物描写（借助舞台布置与天气，让物件说话，不写'气氛紧张'这类直白词）；\n"
                "· 动作有细节、有留白；可有心理线索但不点破全部；\n"
                "· 战斗/冲突场：呈现招式、灵力/攻防状态、距离感，不写'他打了他一拳'式干话；\n"
                "· 第三人称叙述，对话用引号嵌在叙述里，不要每句都以角色名开头；\n"
                "· 不得改变事实与角色原意，不得让角色说事件外的话。\n"
                f"舞台：{stage or '（无）'}\n"
                "行为：\n" + "\n".join(act_summary)
            )
            parts = []
            async for token in llm_client.stream_cheap_text(prompt):
                parts.append(token)
                full_text += token
                writer({"kind": "prose_delta", "turn": sim.world.turn, "delta": token})
            full_text = "".join(parts).strip()
            if not full_text:  # LLM 流式空 → 回退确定性
                full_text = _default_prose(sim, acts)
        else:
            full_text = _default_prose(sim, acts)
            writer({"kind": "prose", "turn": sim.world.turn, "text": full_text})

        if full_text:
            world.append_event(
                type_=models.EventType.text, source=models.EventSource.system,
                actor="成文", payload={"text": full_text},
            )
            writer({"kind": "prose", "turn": sim.world.turn, "text": full_text})
        # 判定终止状态，写入 sim（外层据此决定是否继续）。
        # 注意：举手(raise_pending) 只是暂停，不是完结 → 不置 ended（否则 accept 后无法继续 step）。
        # ended 仅表示真正收束/超上限。
        await DirectorEngine(sim, plan_cfg).check_converged(converge_fn)
        if sim.director.converged or sim.world.turn >= max_turns:
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