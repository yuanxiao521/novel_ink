"""导演 Agent 调度（MVP §6）。

原则（关键）：
  - **张力/divergence 不由导演自评**（§6.2 自吹自哨不可信）→ 用**独立启发式代理指标**：
    距上次冲突回合数、动作类型分布、目标权重冲突度。确定性、可复现、无 LLM 也能跑。
  - **三把巧劲都走"隐性手法"**（改世界/改感知/改取舍），不让角色说出"导演让我这么做"。
  - **权重调整的因果律（§6.1 v0.2）**：每次改权重**必须**配一个该角色可感知的剧情内因，
    目标是果、事件是因，不能反着来。
  - **举手机制（§6.2）**：分叉点 / 失控前兆 / 新设定缺口 → 请求作者介入。
  - **收束判据（§6.3）**：只从显式结局状态集合里选，converged 不靠感觉。

真实实现：plan 里"出哪条巧劲、注入什么"由 LLM 生成。此文件先给**确定性回退策略**，
保证无 Key 端到端可跑；接 LLM 时把 `fallback_plan` 换成 `llm_plan`，世界/护栏可复用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from app.schemas import models
from app.schemas.models import BeliefChannel, GoalAdjust, InfoExposure, RaiseRequest
from app.services.engine.world import WorldEngine
from app.services.llm.client import client as llm_client


@dataclass
class PlanCfg:
    """导演确定性回退策略的可配参数（场景文件提供）。接 LLM 后可忽略/覆盖。"""

    secret_fact_ids: list[str] = field(default_factory=list)          # 应被逐步曝光的隐藏事实
    expose_target: dict[str, str] = field(default_factory=dict)       # fact_id -> 该给谁看
    goal_pressure: list[dict] = field(default_factory=list)          # [{char,goal,delta,reason,at_tension}]
    env_pressure_lines: list[str] = field(default_factory=list)      # 张力高时注入的环境压
    raise_after_turn: int = 3                                         # 满该回合数后举手演示
    ending_options: list[str] = field(default_factory=list)          # 显式结局集合(§6.3)


class DirectorEngine:
    def __init__(self, sim: "models.SimulationState", cfg: Optional[PlanCfg] = None):
        self.sim = sim
        self.cfg = cfg or PlanCfg()

    # ----------------------------------------------------------------- 张力启发式（独立评估，不自评）
    def measure_tension(self) -> float:
        d = self.sim.director
        since = max(0, self.sim.world.turn - d.last_conflict_turn)
        # 距上次冲突越远 → 张力爬升（略封顶）
        t = 30.0 + 6.0 * min(since, 10)
        # 冲突事件骤增推张力
        t += min(3 * self._recent_conflicts(), 20.0)
        t = max(0.0, min(100.0, t))

        prev = d.tension
        d.tension = round(t, 1)
        d.tension_trend = "up" if t > prev + 2 else ("down" if t < prev - 2 else "flat")
        return d.tension

    def _recent_conflicts(self) -> int:
        n = 0
        for ev in self.sim.events[-6:]:
            if ev.payload.get("kind") == "conflict":
                n += 1
        return n

    def _mark_conflict(self) -> None:
        self.sim.director.last_conflict_turn = self.sim.world.turn

    # ----------------------------------------------------------------- 主动权顺序（§5.2：导演指定）
    def choose_action_order(self, last_main_actor: Optional[str] = None) -> list[str]:
        order = [c for c in self.sim.characters]
        if last_main_actor in order and len(order) > 1:
            order.remove(last_main_actor)
            order.append(last_main_actor)
        self.sim.action_order = order
        return order

    # ----------------------------------------------------------------- 三把巧劲（确定性回退策略）
    def fallback_plan(self) -> None:
        """决策序：1)信息曝光 → 2)权重调整(带内因) → 3)环境压。产出 DirectorHint + 可能举手。"""
        d = self.sim.director
        hint = models.DirectorHint()
        injected: list[str] = []

        # 1) 信息曝光：找一个还没给到位的 secret fact
        for fid in self.cfg.secret_fact_ids:
            target = self.cfg.expose_target.get(fid)
            if not target or self._has_fact(target, fid):
                continue
            channel = BeliefChannel.inferred if d.tension > 55 else BeliefChannel.perceived
            hint.info_exposures.append(InfoExposure(
                fact_id=fid, target_char_id=target, channel=channel,
            ))
            name = self.sim.characters[target].name if target in self.sim.characters else target
            injected.append(f"信息曝光：{name} 得知「{self._fact_text(fid)}」")
            break

        # 2) 权重调整（带因果律）
        if not hint.info_exposures:
            for p in self.cfg.goal_pressure:
                if d.tension >= p["at_tension"] and self._goal_increase_due(p):
                    hint.goal_adjusts.append(GoalAdjust(
                        char_id=p["char"], goal_id=p["goal"],
                        delta=p["delta"], reason=p["reason"],
                    ))
                    injected.append(f"目标权重：{p['char']}·{p['goal']} {p['delta']:+g}（因：{p['reason']}）")
                    break

        # 3) 环境压
        if not hint.goal_adjusts and d.tension >= 60 and self.cfg.env_pressure_lines:
            idx = min(self.sim.world.turn - 1, len(self.cfg.env_pressure_lines) - 1)
            line = self.cfg.env_pressure_lines[idx]
            injected.append(f"事件注入：{line}")

        hint.stage_prompt = self._pick_stage_prompt(hint)
        self.sim.director.hint = hint
        self.sim.director.injected_events = injected
        self._maybe_raise_hand()

    def _maybe_raise_hand(self) -> None:
        d = self.sim.director
        # 已举手过则不再重复申请（避免每回合反复举手）
        if d.raise_request.pending or d.raise_emitted:
            return
        if self.cfg.raise_after_turn and self.sim.world.turn >= self.cfg.raise_after_turn and len(self.cfg.ending_options) >= 2:
            d.raise_request = RaiseRequest(
                reason_kind="branch",
                reason="分叉点——多种走向都合理，请作者拍板方向",
                pending=True,
            )
            d.raise_emitted = True

    # ----------------------------------------------------------------- LLM 调度入口（graph.py 一行替换）
    def plan(self) -> None:
        """导演计划门面：LLM 可用且产出软引导则用，否则确定性回退。

        graph.py 只需调用 director.plan()；当 LLM 不可用/返回空（无 Key 或调用失败）
        时内部自动回退 fallback_plan()，保证无 Key 端到端可跑、行为与旧路径完全一致。
        """
        if llm_client.available and self.llm_plan():
            return
        self.fallback_plan()

    def llm_plan(self) -> bool:
        """LLM 软引导（导演只给方向，不代写台词）。

        读 sim 导演全局视角（角色全貌 + beliefs 全貌 + world.facts + 张力/回合 +
        ending_options），先生成结构化软引导 JSON，落到 sim.director.hint 与 raise_request。
        成功落盘返回 True；LLM 不可用/返回非法（call_cheap 返回空或非 dict）返回 False，
        由调用方（plan）回退确定性 fallback_plan。
        """
        schema = {
            "info_exposures": [{"fact_id": "str", "target_char_id": "str", "channel": "perceived|told|inferred"}],
            "goal_adjusts": [{"char_id": "str", "goal_id": "str", "delta": 0.0, "reason": "剧情内因"}],
            "injected_event": "str：环境/事件压，可留空",
            "stage_prompt": "str：一句中文，提示让谁先接话",
            "raise_request": {"reason_kind": "branch|out_of_control|world_gap，可空", "reason": "str，可空"},
        }
        raw = llm_client.call_cheap(self._llm_plan_prompt(), schema)
        if not isinstance(raw, dict):
            return False

        hint = models.DirectorHint()
        injected: list[str] = []
        try:
            for e in raw.get("info_exposures") or []:
                fid = e.get("fact_id"); tgt = e.get("target_char_id")
                if not fid or not tgt or tgt not in self.sim.characters or self._has_fact(tgt, fid):
                    continue
                channel = BeliefChannel.inferred if e.get("channel") == "inferred" \
                    else (BeliefChannel.told if e.get("channel") == "told" else BeliefChannel.perceived)
                hint.info_exposures.append(InfoExposure(fact_id=fid, target_char_id=tgt, channel=channel))
                name = self.sim.characters[tgt].name
                injected.append(f"信息曝光：{name} 得知「{self._fact_text(fid)}」")
            for a in raw.get("goal_adjusts") or []:
                # 因果律硬约束：无剧情内因的调权一律丢弃
                if not a.get("reason") or not a.get("char_id") or not a.get("goal_id"):
                    continue
                hint.goal_adjusts.append(GoalAdjust(
                    char_id=a["char_id"], goal_id=a["goal_id"],
                    delta=float(a.get("delta", 0.0)), reason=a["reason"],
                ))
                injected.append(f"目标权重：{a['char_id']}·{a['goal_id']} {float(a.get('delta', 0.0)):+g}（因：{a['reason']}）")
            inj = str(raw.get("injected_event") or "").strip()
            if inj:
                injected.append(f"事件注入：{inj}")
        except Exception:
            # 任何结构异常都视为 LLM 不可靠 → 回退，绝不污染 sim
            return False

        if not (hint.info_exposures or hint.goal_adjusts or inj):
            return False  # 空引导等同失败，交给确定性回退
        hint.stage_prompt = raw.get("stage_prompt") or self._pick_stage_prompt(hint)
        self.sim.director.hint = hint
        self.sim.director.injected_events = injected

        rr = raw.get("raise_request")
        if isinstance(rr, dict) and rr.get("reason_kind") and rr.get("reason") and not self.sim.director.raise_emitted:
            self.sim.director.raise_request = RaiseRequest(
                reason_kind=rr["reason_kind"], reason=rr["reason"], pending=True,
            )
            self.sim.director.raise_emitted = True
        return True

    def llm_converge(self) -> str:
        """用 LLM 从 ending_options 判定收敛结局。返回 ending str 或 ""。

        LLM 不可用/返回空/无法匹配任一显式结局 → 返回 ""（交给场景 converge_fn）。
        回合数护栏：前 3 回合不收束（对齐场景 converge_fn 的 `turn < 3` 默认护栏），
        避免真实 LLM 在回合 1 就提前收束、挤压多回合涌现与前端导演台演示。
        """
        if self.sim.world.turn < 3:
            return ""
        options = self.sim.director.ending_options
        if not options or not llm_client.available:
            return ""
        prompt = (
            "你是小说收束判断器。阅读当前剧情全局视角，从下列显式结局集合中，"
            "选出最贴合当前成局的一个，只原样返回该选项文本，不要解释、不要加词。\n"
            f"可选结局：{'、'.join(options)}\n\n剧情全局视角：\n" + self._global_view()
        )
        raw = llm_client.call_cheap(prompt)
        if not raw:
            return ""
        text = str(raw).strip()
        # 允许 LLM 原样返回某个选项，或把选项包容在其回答里
        for opt in options:
            if opt in text or text in opt:
                return opt
        return ""

    # ----------------------------------------------------------------- 收束判定（§6.3）
    def check_converged(self, converge_fn: Optional[Callable[[models.SimulationState], str]] = None) -> str:
        ending = self.llm_converge()
        if not ending and converge_fn is not None:
            ending = converge_fn(self.sim)
        if ending:
            self.sim.director.converged = True
            self.sim.director.ending_selected = ending
        return ending

    # ----------------------------------------------------------------- LLM 提示词构建
    def _global_view(self) -> str:
        """导演全局视角：角色全貌 + beliefs 全貌 + world.facts + 张力/回合 + ending_options。"""
        s = self.sim
        lines = [
            f"当前回合：{s.world.turn}；张力：{s.director.tension}（趋势 {s.director.tension_trend}）",
            "可选结局：" + "、".join(s.director.ending_options or ["（未定）"]),
            "— 角色全貌 —",
        ]
        for cid, c in s.characters.items():
            goals = "；".join(f"{g.id}（权重{g.weight}）" for g in c.dynamic_goals)
            lines.append(f"[{cid}] {c.name}：{c.summary}；动态目标：{goals or '无'}")
        if any(s.beliefs.values()):
            lines.append("— 各角色已知信念（beliefs 全貌）—")
            for cid, blist in s.beliefs.items():
                if not blist:
                    continue
                name = s.characters[cid].name if cid in s.characters else cid
                bs = "；".join(f"{b.text}（置信{b.confidence}）" for b in blist)
                lines.append(f"{name}：{bs}")
        lines.append("— 世界事实（当前成立）—")
        for f in s.active_facts():
            lines.append(f"{f.text}（{f.kind}）")
        lines.append("— 近期事件 —")
        for ev in s.events[-8:]:
            lines.append(f"<{ev.source.value}·{ev.actor}> {ev.payload.get('text', '') or ev.payload}")
        return "\n".join(lines)

    def _llm_plan_prompt(self) -> str:
        return (
            "请基于以下剧情全局视角，作为导演给出软引导。\n"
            "规则：只给方向、不代写台词；每次权重调整(goal_adjusts)必须附剧情内因(reason)——"
            "目标是果、事件是因，不能反过来；信息曝光(info_exposures)的 target 必须是剧情中"
            "合理能得知该事实的角色；injected_event 用于注入环境/事件压力，可留空；"
            "stage_prompt 用一句中文提示让谁先接话；若出现多走向都合理的分叉点，可给出 "
            "raise_request。\n\n全局视角：\n" + self._global_view()
        )

    # ----------------------------------------------------------------- 内部小工具
    def _has_fact(self, char_id: str, fact_id: str) -> bool:
        world = WorldEngine(self.sim)
        return any(b.fact_id == fact_id for b in self.sim.beliefs.get(char_id, [])) or \
            any(f.id == fact_id for f in world.perceivable_facts(char_id))

    def _fact_text(self, fact_id: str) -> str:
        for f in self.sim.world.facts:
            if f.id == fact_id:
                return f.text
        return fact_id

    def _goal_increase_due(self, p: dict) -> bool:
        card = self.sim.characters.get(p["char"])
        if not card:
            return False
        for g in card.dynamic_goals:
            if g.id == p["goal"] and g.last_adjust_reason != p["reason"]:
                return True
        return False

    def _pick_stage_prompt(self, hint: models.DirectorHint) -> str:
        for e in hint.info_exposures:
            name = self.sim.characters[e.target_char_id].name if e.target_char_id in self.sim.characters else e.target_char_id
            return f"舞台提示：让 {name} 先接话（回应刚得知的事）"
        return "舞台提示：推进冲突、避免空转"