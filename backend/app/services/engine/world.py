"""服务层 · 世界/黑板实现（MVP §4.2·§4.3·§4.4 + §7 第3层护栏核心）。

分工（MVP 设计 v0.2 已明确）：
  - **环境事实**        用 `Fact.visible_to` 感知标签做隔离；
  - **人际事实**(谁知道谁) 走角色 `Belief` 账本（带溯源 channel+source_event_id）。
二者并用 → 信息差与涌现空间由此而来（角色只见局部世界）。

第3层护栏（世界事实连贯）在**事件写入前**校验，未通过 → 打回重演（熔断见 graph）。
护栏的语义判定可替换为 LLM（真实实现）；此处先给**确定性启发式**，保证无 Key 可跑、可测试。
"""
from __future__ import annotations

import re
from typing import Optional

from app.schemas import models
from app.schemas.models import Belief, BeliefChannel, Event, EventSource, EventType, Fact

# 中文否定词缀：任何位置出现即视为"否定断言"
_NEG_WORDS = ("不", "没", "没有", "未", "无", "从不", "并未", "不再", "绝非", "并非")
_NEG_PREFIX = re.compile(r"^(?:" + "|".join(_NEG_WORDS) + ")")
_SEP = re.compile(r"[，。！？；、\s]+")
# 二元字种 shingle：把连续 ASCII/中文切出长度为 2 的子串，用于中文短句的稳比对


def _shingles(text: str) -> set[str]:
    chars = _SEP.sub("", text)  # 去标点/空格
    return {chars[i:i + 2] for i in range(len(chars) - 1) if len(chars[i:i + 2]) == 2}


# 语义相反词对（第三层护栏用；真实实现由 LLM 替代）
_OPPOSITE_PAIRS = [
    ("开", "关"), ("半开", "关"), ("敞开", "关"), ("明亮", "黑暗"),
    ("有", "无"), ("在", "不在"), ("死", "活"), ("离开", "留下"),
]


def _has_semantic_conflict(a: str, b: str) -> bool:
    for x, y in _OPPOSITE_PAIRS:
        if (x in a and y in b) or (y in a and x in b):
            return True
    return False


class WorldEngine:
    """对一个 SimulationState 进行世界侧操作。"""

    def __init__(self, sim: "models.SimulationState"):
        self.sim = sim

    # ---- 事实（第3层护栏 + 黑板环境部分） ----
    def core_terms(self, text: str) -> frozenset[str]:
        """剥离否定后取关键词集合，用于启发式矛盾判定。"""
        core = _NEG_PREFIX.sub("", text.strip())
        return frozenset(t for t in _SEP.split(core) if t)

    def negated(self, text: str) -> bool:
        """句中任意位置出现否定词即视为否定断言。"""
        return any(w in text for w in _NEG_WORDS)

    def check_fact_consistent(self, claim_text: str) -> tuple[bool, str]:
        """第3层护栏：新断言与任一现存事实构成"同核相反"即拦截。

        用二元字种 shingle 重叠判定是否"谈论同一对象"：
          - 共享足够 shingle（同对象）+ 语义相反词对 → 冲突；
          - 共享 shingle 且 一为否定一为肯定 → 冲突。
        例：现存"保险柜门半开"，断言"保险柜门没有敞开" → 拦截。
        """
        c_sh = _shingles(claim_text)
        if not c_sh:
            return True, ""
        for f in self.sim.active_facts():
            f_sh = _shingles(f.text)
            overlap = c_sh & f_sh
            if not overlap:
                continue
            if _has_semantic_conflict(f.text, claim_text):
                return False, f"与事实「{f.text}」矛盾"
            if self.negated(claim_text) != self.negated(f.text):
                return False, f"与事实「{f.text}」矛盾"
        return True, ""

    def add_fact(
        self,
        text: str,
        visible_to: Optional[list[str]] = None,
        source_event_id: str = "",
        kind: str = "env",
    ) -> Fact:
        fact = Fact(
            id=self.sim.next_fact_id(),
            text=text,
            kind=kind,
            visible_to=list(visible_to or []),
            created_event_id=source_event_id,
        )
        self.sim.world.facts.append(fact)
        return fact

    def deactivate_fact(self, fact_id: str) -> None:
        for f in self.sim.world.facts:
            if f.id == fact_id:
                f.active = False
                break

    def perceivable_facts(self, char_id: str) -> list[Fact]:
        """角色能"直接看到"的环境事实子集（感知隔离的关键）。"""
        return [
            f for f in self.sim.active_facts()
            if (not f.visible_to) or (char_id in f.visible_to)
        ]

    # ---- 事件日志（append-only，回放/校验/调优共同基础） ----
    def append_event(
        self,
        type_: EventType,
        source: EventSource,
        actor: str = "",
        payload: Optional[dict] = None,
        guard_flags: Optional[list[str]] = None,
    ) -> Event:
        ev = Event(
            id=self.sim.next_event_id(),
            turn=self.sim.world.turn,
            type=type_,
            source=source,
            actor=actor,
            payload=payload or {},
            world_snapshot={"turn": self.sim.world.turn, "len": len(self.sim.events)},
            guard_flags=list(guard_flags or []),
        )
        self.sim.events.append(ev)
        self.sim.world.timeline.append(ev.id)
        return ev

    # ---- 信念账本（人际信息的认知差） ----
    def record_belief(
        self,
        char_id: str,
        fact_id: str,
        source_event_id: str,
        channel: BeliefChannel,
        text: str,
        confidence: float = 0.5,
    ) -> Belief:
        b = Belief(
            char_id=char_id,
            fact_id=fact_id,
            source_event_id=source_event_id,
            channel=channel,
            text=text,
            confidence=confidence,
        )
        self.sim.beliefs.setdefault(char_id, []).append(b)
        return b

    def beliefs_for(self, char_id: str) -> list[Belief]:
        return list(self.sim.beliefs.get(char_id, []))

    def apply_goal_adjust(self, adj: "models.GoalAdjust") -> None:
        """导演调权重：直接改 dynamic_goal.weight，并把必填的剧情内因写回（§6.1因果律）。"""
        card = self.sim.characters.get(adj.char_id)
        if not card:
            return
        for g in card.dynamic_goals:
            if g.id == adj.goal_id:
                g.weight = max(0.0, min(1.0, g.weight + adj.delta))
                g.last_adjust_reason = adj.reason
                break