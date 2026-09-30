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

import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Optional

from app.errors import DirectorChatUnavailableError
from app.schemas import models
from app.schemas.models import BeliefChannel, GoalAdjust, InfoExposure, RaiseRequest
from app.services.engine.world import WorldEngine
from app.services.llm.client import client as llm_client

logger = logging.getLogger(__name__)

# 收束硬护栏：至少这么多回合后才允许判定收敛（对齐场景 converge_fn 的默认护栏，
# 防真实 LLM 在第 3 回合就提前收束、挤压多回合涌现与导演注入）。
MIN_CONVERGE_TURN = 6


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
    async def plan(self) -> None:
        """导演计划门面：LLM 可用且产出软引导则用，否则确定性回退。

        graph.py 只需 await director.plan()；当 LLM 不可用/返回空（无 Key 或调用失败）
        时内部自动回退 fallback_plan()，保证无 Key 端到端可跑、行为与旧路径完全一致。
        """
        if llm_client.available and await self.llm_plan():
            return
        logger.info("[director-plan] LLM 不可用/产出空 → 回退确定性策略")
        self.fallback_plan()

    async def llm_plan(self) -> bool:
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
        raw = await llm_client.call_cheap(self._llm_plan_prompt(), schema)
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
        # 举手护栏：LLM 自主举手同样受 raise_after_turn 约束（与 llm_converge 的 turn<3
        # 一致），避免真实 LLM 在第 1 回合就举手、挤压多回合涌现与导演台实时演示。
        if (isinstance(rr, dict) and rr.get("reason_kind") and rr.get("reason")
                and not self.sim.director.raise_emitted
                and self.sim.world.turn >= (self.cfg.raise_after_turn or 3)):
            self.sim.director.raise_request = RaiseRequest(
                reason_kind=rr["reason_kind"], reason=rr["reason"], pending=True,
            )
            self.sim.director.raise_emitted = True
        return True

    async def llm_converge(self) -> str:
        """用 LLM 从 ending_options 判定收敛结局。返回 ending str 或 ""。

        LLM 不可用/返回空/无法匹配任一显式结局 → 返回 ""（交给场景 converge_fn）。
        三重护栏（防"三回合就收束"）：
          1. 硬回合护栏：至少 MIN_CONVERGE_TURN（默认 6）回合后才允许判定收束；
          2. 不强选：LLM 只有认为剧情**确实达成**某结局才返回它，否则返回空串；
          3. 连续一致：同一结局需**连续两回合**判定一致才收束，防止随机抖动提前收束。
        """
        if self.sim.world.turn < MIN_CONVERGE_TURN:
            return ""
        options = self.sim.director.ending_options
        if not options or not llm_client.available:
            return ""
        prompt = (
            "你是小说收束判断器。阅读当前剧情全局视角，从下列显式结局集合中"
            "判定剧情是否**已经真正达成**某个结局。\n"
            "规则：只有关键事件已发生、冲突已落定、结局事实上走完时，才原样返回该结局；"
            "若故事仍在推进、结局尚未真正发生，请输出一个空串，绝不硬选。\n"
            f"可选结局：{'、'.join(options)}\n\n剧情全局视角：\n" + self._global_view()
        )
        raw = await llm_client.call_cheap(prompt)
        if not raw:
            self.sim.scratch.pop("converge_vote", None)
            return ""
        text = str(raw).strip()
        for opt in options:
            if opt in text or text in opt:
                # 连续两回合判定同一结局才收束
                if opt == self.sim.scratch.get("converge_vote"):
                    self.sim.scratch.pop("converge_vote", None)
                    return opt
                self.sim.scratch["converge_vote"] = opt
                return ""
        # 本轮未选出任何结局 → 清账，重新累计
        self.sim.scratch.pop("converge_vote", None)
        return ""

    # ----------------------------------------------------------------- 收束判定（§6.3）
    async def check_converged(self, converge_fn: Optional[Callable[[models.SimulationState], str]] = None) -> str:
        ending = await self.llm_converge()
        if not ending and converge_fn is not None:
            ending = converge_fn(self.sim)
        if ending:
            self.sim.director.converged = True
            self.sim.director.ending_selected = ending
        return ending

    # ----------------------------------------------------------------- 场景收束长程汇报（S3）
    async def analyze_scene_close(
        self,
        foreshadow_items: Optional[list[dict]] = None,
        remaining_scenes: Optional[list[str]] = None,
    ) -> dict:
        """场景收束后的导演长程汇报（OpenNovel Director 借鉴，只在收束点跑一次）。

        输入（全部现成状态）：本场事件流 / 张力历史 / 信念变化 / 伏笔现状 / 剩余场景清单。
        输出 JSON：场景摘要 / 伏笔三态更新 / 角色弧线变化 / 因果补全 / 下一场提示。
        LLM 不可用/产出非法 → 确定性兜底（摘要取自最近归档），不阻塞换场。
        """
        s = self.sim
        events = [e.model_dump() for e in s.events[-30:]]
        tension_history = [{"turn": a.turn, "tension": a.tension} for a in s.turn_archives][-15:]
        belief_changes = [
            {"char": b.char_id, "fact": b.fact_id, "channel": b.channel.value, "text": b.text}
            for bl in s.beliefs.values() for b in bl[-6:]
        ][-30:]

        prompt = (
            "你是小说【导演】。一场戏刚收束。请以全书长程视角做一次收束汇报：\n"
            "1) scene_summary：本场发生了什么（≤150 字，中文）。\n"
            "2) foreshadow_updates：对照伏笔账本，哪些被推进（in_progress）或已回收（closed）"
            "（只列有明确变化的；id 用账本原 id）。\n"
            "3) character_arc_deltas：每个关键角色本场的弧线变化（一句）。\n"
            "4) causality：为本场关键事件补因果链——event_id 是事件的 id，caused_by 填引发它的前置事件 id"
            "（不确定可空），causal_pressure 0-1 表示该事件对未来剧情的影响强度。\n"
            "5) next_scene_hint：给下一场的一句话导演提示（或换场/插入/跳过建议）。\n"
            "只评估不代写台词，不改剧情事实。\n\n"
            f"场次：scene={s.scene_id} 回合 {s.world.turn} 结束\n"
            f"张力历史：{tension_history}\n"
            f"信念变化（末段）：{belief_changes}\n"
            f"伏笔现状：{foreshadow_items or []}\n"
            f"剩余场景：{remaining_scenes or []}\n"
            "事件流（末 30）：\n" + "\n".join(
                f"<{e['id']}·{e['actor']}> {e['payload'].get('text', '') or e['payload']}"
                for e in events
            )
        )
        schema = {
            "scene_summary": "str",
            "foreshadow_updates": [{"id": "str", "status": "in_progress|closed", "reason": "str"}],
            "character_arc_deltas": {"char_id": "str"},
            "causality": [{"event_id": "str", "caused_by": "str 或空", "causal_pressure": 0.8}],
            "next_scene_hint": "str",
        }
        raw: Optional[dict] = None
        if llm_client.available:
            try:
                raw = await llm_client.call_cheap(prompt, schema)
            except Exception as e:  # noqa: BLE001
                logger.warning("[director-close] LLM 汇报失败，走兜底：%s", e)
        # 确定性兜底
        summary = str(raw.get("scene_summary") or "") if raw else ""
        if not summary and s.turn_archives:
            summary = s.turn_archives[-1].summary or s.turn_archives[-1].prose[:120]
        return {
            "scene_summary": summary or "（本场无摘要）",
            "foreshadow_updates": (raw or {}).get("foreshadow_updates") or [],
            "character_arc_deltas": (raw or {}).get("character_arc_deltas") or {},
            "causality": (raw or {}).get("causality") or [],
            "next_scene_hint": (raw or {}).get("next_scene_hint") or "",
        }

    # ----------------------------------------------------------------- LLM 提示词构建
    async def stream_chat(self, message: str) -> AsyncIterator[str]:
        """作者↔导演 共创对话（逐 token 流式）。

        导演以全局视角 tag 作者消息，给出**创作方向**（何时揭真相/谁的信念该动摇/
        张力怎么推/下一步往哪走），遵守导演铁律：不代写台词、不剧透、不替角色说话。
        全程流式 yield 文本增量；LLM 不可用或产出为空抛 DirectorChatUnavailableError
        （由路由层转 503 或 SSE error 事件，前端可展示"导演离线"兜底）。
        """
        if not llm_client.available:
            logger.warning("[director-chat] LLM 未配置，导演无法回应")
            raise DirectorChatUnavailableError("导演暂离场：模型未接入，无法与你共创")

        prompt = self._chat_prompt(message)
        logger.info("[director-chat] 作者问：%s", message)
        chunks: list[str] = []
        try:
            async for tok in llm_client.chat_stream(prompt, temperature=0.7):
                chunks.append(tok)
                yield tok
        except Exception as e:  # noqa: BLE001 —— 网络抖动等：记日志，向调用方报"导演无法回应"
            logger.exception("[director-chat] 流式中断: %s", e)
            raise DirectorChatUnavailableError("导演回应中断：请稍后再试") from e

        text = "".join(chunks).strip()
        if not text:
            logger.warning("[director-chat] 导演产出为空")
            raise DirectorChatUnavailableError("导演没有回应（模型输出为空）")
        logger.info("[director-chat] 导演答：%s", text)

    def _chat_prompt(self, message: str) -> str:
        return (
            "你是这部小说项目的「导演 agent」，正与作者（编剧/甲方）现场共创剧情。\n"
            "你的铁律：只给创作方向，不代写角色台词；不替角色做决定；不暴露作者现阶段"
            "不适合知道的内幕；回答要具体、可执行（下一步怎么走、该曝光什么、谁该动摇）。\n\n"
            "当前剧本全局视角：\n" + self._global_view()
            + "\n\n作者说：\n" + message + "\n\n请以导演的口吻（简洁、果断、有画面感）回应作者。"
        )

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
            "规则：只给方向、不代写台词。你每回合必须**真正推动剧情往前走**，"
            "因此至少落地一样（不能全空）：\n"
            " - injected_event：注入一个具体的环境/事件变化（如'周婶敲门送来冷茶'、"
            "'窗外雨声更密了'、'保险柜锁孔又添一道新划痕'），给这场戏加压或换场；\n"
            " - info_exposures：把某个隐藏事实暴露给剧情中合理能得知该事实的角色，"
            "制造信息差/筹码；\n"
            " - goal_adjusts：调整某个角色目标权重，必须附剧情内因(reason)——"
            "目标是果、事件是因，不能反过来。\n"
            "stage_prompt 用一句中文提示让谁先接话；若出现多走向都合理的分叉点，"
            "可给出 raise_request。\n\n全局视角：\n" + self._global_view()
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