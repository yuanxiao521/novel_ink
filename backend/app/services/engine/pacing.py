"""导演节奏闸门（v1.73）：拍（turn）内打几个来回、每人至多几条行为 —— 0-token 纯函数。

背景（作者拍板）：一个 turn 只有"每角色一次行动 + 一段旁白"太薄。方案 = **连拍**
（导演一次规划 N 个来回，拍内角色真正你来我往）+ **多行为 decide**（一次决策可出
多条行为）。**默认 1 来回 1 行为（=旧行为），由确定性信号加厚**，LLM 建议只能抬不能
越过闸门 —— 成本与护栏兜底永远在代码里，不在模型嘴里。

为什么做成纯函数：节奏是"情节密度"的唯一强制点，必须可单测（同 beats 闸门一个道理），
否则要么永远不变厚（形同虚设），要么成本失控。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional


MAX_EXCHANGES = 3        # 一拍最多几个来回（超出就拆成多拍，保住 checkpoint 粒度）
MAX_BEHAVIORS = 3        # 单次 decide 最多几条行为
THIN = (1, 1)            # 压薄后的默认值（= v1.72 及之前的行为）


@dataclass
class Pace:
    """一拍的节奏：exchanges 个来回 × 每人每次至多 behaviors_max 条行为。"""

    exchanges: int = 1
    behaviors_max: int = 1
    mode: str = "normal"     # normal（逐角色 decide）| volley（对话快速通道，见 volley.py）
    reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _clamp(n: Any, lo: int, hi: int) -> int:
    try:
        v = int(n)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def normalize_llm_pace(raw: Any) -> Optional[Pace]:
    """导演 LLM 建议的 pace → 合法 Pace；非法/越界一律 None（交给信号闸门定默认）。"""
    if not isinstance(raw, dict):
        return None
    ex = _clamp(raw.get("exchanges"), 1, MAX_EXCHANGES)
    bh = _clamp(raw.get("behaviors_max"), 1, MAX_BEHAVIORS)
    mode = str(raw.get("mode") or "normal").strip()
    mode = mode if mode in ("normal", "volley") else "normal"
    reason = str(raw.get("reason") or "").strip()[:60]
    return Pace(exchanges=ex, behaviors_max=bh, mode=mode, reason=reason)


def decide_pace(
    sim,
    beats: Optional[list[dict]] = None,
    *,
    llm_pace: Any = None,
    max_actors: int = 3,
) -> Pace:
    """拍板本拍节奏（0-token）。LLM 建议只是底稿，信号闸门最终决定。

    信号（按作者拍板的优先级）：
      压薄（一票否决，回到 1×1）：
        - 上回合有护栏拦截/熔断（刚出过戏，冷静一拍）
        - 举手待批 / 已收敛（回合要停了，不再加厚）
      加厚（取最大值，不叠加溢出）：
        - 还有没发生的 hard beat（必然事件没到 → 这拍要有戏）
        - 最近 6 条事件里有 conflict（正在交锋）
        - 张力连升（trend=up）
      volley 快速通道：导演建议 volley 且 当前没有 hard beat 目标、最近无冲突
        （纯对话过场 → 一次生成整段交锋，省调用，见 volley.py）
    """
    from app.services.engine.beats import event_texts, unmet_hard_beats

    p = normalize_llm_pace(llm_pace) or Pace()
    reasons: list[str] = []

    d = getattr(sim, "director", None)
    guard = getattr(sim, "guard", None)
    # —— 压薄信号（一票否决：命中即锁定 1×1，后面的加厚信号不再抬回去）——
    blocks = int(getattr(guard, "last_turn_blocks", 0) or 0)
    if blocks > 0:
        p.exchanges, p.behaviors_max = THIN
        reasons.append(f"上回合护栏拦截 {blocks} 次，冷静一拍")
        p.reason = "；".join(reasons[:3])
        return p
    if getattr(d, "raise_request", None) is not None and getattr(d.raise_request, "pending", False):
        p.exchanges, p.behaviors_max = THIN
        reasons.append("等作者拍板中")
        p.reason = "；".join(reasons[:3])
        return p
    if getattr(sim, "ended", False) or getattr(d, "converged", False):
        p.exchanges, p.behaviors_max = THIN
        reasons.append("本拍已收束")
        p.reason = "；".join(reasons[:3])
        return p

    # —— 加厚信号 ——
    events = list(getattr(sim, "events", []) or [])
    texts = event_texts(events)
    unmet = unmet_hard_beats(list(beats or []), texts)
    if unmet:
        p.exchanges = max(p.exchanges, 2)
        p.behaviors_max = max(p.behaviors_max, 2)
        reasons.append(f"必然事件未达成（{len(unmet)} 条）")
    recent_conflict = any(
        (getattr(e, "payload", None) or {}).get("kind") == "conflict" for e in events[-6:]
    )
    if recent_conflict:
        p.exchanges = max(p.exchanges, 2)
        reasons.append("正在交锋")
    if getattr(d, "tension_trend", "") == "up":
        p.behaviors_max = max(p.behaviors_max, 2)
        reasons.append("张力上行")

    # —— volley 快速通道（方案三）：只在纯对话过场时启用 ——
    if p.mode == "volley":
        if unmet or recent_conflict:
            p.mode = "normal"
            reasons.append("有硬节拍/冲突，走逐角色涌现")
        elif max_actors > 3:
            p.mode = "normal"
            reasons.append("人数超 3，volley 只保 3 人内对话")

    p.exchanges = _clamp(p.exchanges, 1, MAX_EXCHANGES)
    p.behaviors_max = _clamp(p.behaviors_max, 1, MAX_BEHAVIORS)
    if p.mode == "volley":
        p.exchanges, p.behaviors_max = 1, 1
    if not p.reason:
        p.reason = "；".join(reasons[:3]) or "默认节奏（1 来回 × 1 行为）"
    return p
