"""A2 质量回环（自评 → 门控 → 重写 → 三闸复检）：纯编排，重写复用 A3 通道。

设计（《写作优化方案-A2质量回环.md》§3，阈值已按真实数据实测校准）：
  · 合分：最终分 = LLM 自评×0.7 + 硬信号（0-token）×0.3
  · 触发：总分 < 78 **或** 任一维度 < 70（实测真实初稿 86-91，阈值 70 会永不触发）
  · 重写：优先复用 A3 spot_fix（定点）；无定点命中 → 全文重写一次
  · 三闸复检：①复评**上升 ≥5**（实测 σ=2.1、极差 5，+3 会被噪声骗过）
             ②任一维度下降 **>8** 不采纳 ③事实逐字不变（专名/数字/术语/台词）
             ④口吻不劣化（复用 A3 _voice_not_worse）
  · 硬上限：**最多重写 1 次**（防循环烧钱）；不达标一律回退初稿
"""
from __future__ import annotations

import logging
from collections import Counter

from app.services.engine.ai_tone import (
    _voice_not_worse,
    fact_gate,
    protected_tokens,
    spot_fix,
)
from app.services.engine.schema_retry import validate_and_retry
from app.services.engine.style_checks import (
    ai_tone_report,
    hit_score,
    sentences_with,
    voice_prior,
)

logger = logging.getLogger(__name__)

# ---- 阈值（实测校准，见方案 §3.3）----
TRIGGER_TOTAL = 78      # 总分低于此值触发重写
TRIGGER_DIM = 70        # 或任一维度低于此值
RISE_MARGIN = 5         # 复评必须上升 ≥5 才采纳（σ≈2.1）
DIM_DROP_TOLERANCE = 8  # 任一维度下降超过此值不采纳
MAX_REWRITES = 1        # 硬上限：只重写一次
LLM_WEIGHT = 0.7        # 合分权重（其余为硬信号）
HARD_PENALTY_TONE = 12  # 每个文风命中词扣分
HARD_PENALTY_VOICE = 10 # 每条口吻先验 flag 扣分
HARD_PENALTY_STATE = 15 # 每条状态冲突扣分

QUALITY_SCHEMA = {
    "scores": {"coherence": "int 0-100", "character": "int 0-100", "pacing": "int 0-100",
               "imagery": "int 0-100", "ending": "int 0-100"},
    "total": "int 0-100 加权总分",
    "weak_points": [{"dim": "str 维度", "evidence": "str 正文原句（逐字）", "why": "str"}],
    "verdict": "str pass|revise",
}
_REWRITE_SCHEMA = {"text": "str 改写后的正文（≥20 字）"}

_QUALITY_PROMPT = """你是小说编辑，为下面这段正文打分（0-100），维度：连贯 coherence / 人物一致 character / 节奏 pacing / 画面 imagery / 收尾 ending。
要求：
1. total 为 0-100 的加权总分；
2. weak_points 每条必须带 evidence（**正文原句逐字摘录**），找不到原句就不要写；
3. 看不出的维度按 70 给中性分，不要凭空抬高压低。
正文：
{text}
只返回 JSON。"""

_REWRITE_PROMPT = """你是小说编辑。下面这段正文被认为质量不达标（弱项：{weak}）。
请重写一版：保持**事实/人物/台词完全不变**，只改写法与节奏，让弱项变好。
硬约束：专名、数字、时间、术语、引号内台词逐字保留。
正文：
{text}
只返回 JSON（text 字段）。"""

_NORM = str.maketrans({c: "" for c in "“”\"‘’ \t\n"})


def _validate_quality(raw) -> str | None:
    if not isinstance(raw, dict):
        return "评分必须是对象"
    if not isinstance(raw.get("total"), int):
        return "缺少 int total"
    if not isinstance(raw.get("scores"), dict) or not raw["scores"]:
        return "缺少 scores 维度分"
    if "weak_points" in raw and not isinstance(raw["weak_points"], list):
        return "weak_points 必须是数组"
    return None


def sanitize_weak_points(report: dict, text: str) -> dict:
    """弱项证据闸门（0-token）：evidence 必须能在正文逐字找到，否则丢弃（防空评骗重写）。"""
    if not isinstance(report, dict):
        return report
    hay = (text or "").translate(_NORM)
    kept = []
    for w in report.get("weak_points") or []:
        if not isinstance(w, dict):
            continue
        ev = str(w.get("evidence") or "").strip()
        if not ev or ev.translate(_NORM) not in hay:
            continue
        kept.append({"dim": str(w.get("dim") or ""), "evidence": ev,
                     "why": str(w.get("why") or "")})
    report["weak_points"] = kept
    return report


def hard_signal_score(text: str, characters=None, state_conflicts: int = 0) -> dict:
    """0-token 硬信号分：文风命中 / 口吻先验 / 状态冲突（各 0-100，取均值）。"""
    names = {str((c or {}).get("name") or "") for c in (characters or [])}
    tone = ai_tone_report(text, exclude={n for n in names if n})
    tone_terms = hit_score(text, {n for n in names if n})["terms"]
    voice_flags = len(voice_prior(text, [{"name": n} for n in names if n]).get("flags") or [])
    tone_score = max(0, 100 - HARD_PENALTY_TONE * tone_terms)
    voice_score = max(0, 100 - HARD_PENALTY_VOICE * voice_flags)
    state_score = max(0, 100 - HARD_PENALTY_STATE * max(0, state_conflicts))
    parts = {"tone": tone_score, "voice": voice_score, "state": state_score,
             "tone_rules": [r["rule"] for r in tone.get("rules") or []],
             "tone_terms": tone_terms, "voice_flags": voice_flags}
    return {"score": round((tone_score + voice_score + state_score) / 3), "parts": parts}


def combine_scores(llm_total: int, hard: dict) -> int:
    """最终分 = LLM 自评×0.7 + 硬信号×0.3。"""
    return int(round(LLM_WEIGHT * llm_total + (1 - LLM_WEIGHT) * hard["score"]))


def should_rewrite(total: int, scores: dict) -> tuple[bool, str]:
    """触发条件（实测校准）：总分 < 78 或任一维度 < 70。"""
    if total < TRIGGER_TOTAL:
        return True, "总分 %d < %d" % (total, TRIGGER_TOTAL)
    low = [(k, v) for k, v in (scores or {}).items() if isinstance(v, int) and v < TRIGGER_DIM]
    if low:
        return True, "维度偏低：%s" % "、".join("%s=%d" % kv for kv in low)
    return False, "分数达标（%d）" % total


def judge_improvement(before: dict, after: dict) -> tuple[bool, str]:
    """三闸之一/二：复评上升 ≥5 且任一维度下降不超过 8。"""
    dt = after["total"] - before["total"]
    if dt < RISE_MARGIN:
        return False, "复评未上升够（%d → %d，需 ≥+%d）" % (before["total"], after["total"], RISE_MARGIN)
    drops = [(k, before["scores"].get(k), after["scores"].get(k))
             for k in before["scores"]
             if isinstance(before["scores"].get(k), int) and isinstance(after["scores"].get(k), int)
             and before["scores"][k] - after["scores"][k] > DIM_DROP_TOLERANCE]
    if drops:
        return False, "维度下降过大：%s" % "、".join("%s %s→%s" % d for d in drops)
    return True, "复评上升 %+d" % dt


def whole_fact_gate(before: str, after: str, protected: dict) -> str | None:
    """全文事实闸门：专名/数字/术语/引号内台词的出现**次数必须一致**。"""
    for name in protected.get("names") or set():
        if before.count(name) != after.count(name):
            return "专名「%s」出现次数被改动" % name
    b_num, a_num = Counter(_num_tokens(before)), Counter(_num_tokens(after))
    if b_num != a_num:
        return "数字/时间被改动"
    b_q, a_q = Counter(_quote_tokens(before)), Counter(_quote_tokens(after))
    if b_q != a_q:
        return "引号内台词被改动"
    return None


def _num_tokens(text: str) -> list:
    from app.services.engine.ai_tone import NUM_RE
    return NUM_RE.findall(text or "")


def _quote_tokens(text: str) -> list:
    from app.services.engine.ai_tone import QUOTE_RE
    return [m.group(1).strip() for m in QUOTE_RE.finditer(text or "")]


async def score_text(llm_client, text: str, characters=None, state_conflicts: int = 0) -> dict:
    """评分：LLM 自评（带证据闸门）+ 硬信号 → 最终分。"""
    raw = await validate_and_retry(llm_client, _QUALITY_PROMPT.format(text=text or "（空）"),
                                   QUALITY_SCHEMA, _validate_quality)
    raw = sanitize_weak_points(raw, text)
    hard = hard_signal_score(text, characters, state_conflicts)
    return {"total": combine_scores(int(raw["total"]), hard),
            "llm_total": int(raw["total"]), "scores": dict(raw.get("scores") or {}),
            "weak_points": raw.get("weak_points") or [], "hard": hard}


async def quality_loop(llm_client, text: str, characters=None, world_states=None,
                       state_conflicts: int = 0) -> dict:
    """质量回环主入口：达标不动；不达标最多重写一次；三闸不过则回退初稿。"""
    text = text or ""
    if not text.strip():
        return {"after": text, "accepted": False, "skipped": "empty-text"}
    if not getattr(llm_client, "available", False):
        return {"after": text, "accepted": False, "error": "LLM 未接入，未做评分"}

    before = await score_text(llm_client, text, characters, state_conflicts)
    need, why = should_rewrite(before["total"], before["scores"])
    if not need:
        return {"after": text, "accepted": False, "skipped": "score-ok", "reason": why,
                "before": before, "rewrites": 0}

    # 重写：优先 A3 定点通道；无定点命中 → 全文重写一次
    protected = protected_tokens(text, characters, world_states)
    names = {str((c or {}).get("name") or "") for c in (characters or [])}
    from app.services.engine.style_checks import rule_policy

    # 注意：hint-only 规则（如破折号 R-A3-2）不算"可定点"，否则会路由到 spot_fix
    # 却因白名单为空而空转（→ "无有效改写"），错过全文重写兜底。
    fixable = [h for h in sentences_with(text, {n for n in names if n})
               if any(rule_policy(r)["fixable"] for r in (h.get("rules") or []))]
    route = "spot" if fixable else "full"
    if route == "spot":
        out = await spot_fix(llm_client, text, characters=characters, world_states=world_states)
        candidate = str(out.get("after") or text)
        if not out.get("accepted"):
            candidate = text      # 定点未采纳 → 视为没改
        changes = out.get("changes") or []
    else:
        weak = "、".join(str(w.get("why") or w.get("dim") or "") for w in before["weak_points"][:3]) or "编辑判定"
        raw = await validate_and_retry(llm_client, _REWRITE_PROMPT.format(weak=weak, text=text),
                                       _REWRITE_SCHEMA,
                                       lambda r: None if isinstance(r, dict) and len(str(r.get("text") or "").strip()) >= 20
                                       else "需要 text 字段（≥20 字）")
        candidate = str(raw.get("text") or text).strip()
        changes = [{"before": text[:60], "after": candidate[:60], "rule": "A2-full", "applied": True}]

    if candidate == text:
        return {"after": text, "accepted": False, "reverted": True, "route": route,
                "reason": "无有效改写", "before": before, "rewrites": 1}

    bad = whole_fact_gate(text, candidate, protected)
    if bad:
        return {"after": text, "accepted": False, "reverted": True, "route": route,
                "reason": bad, "before": before, "changes": changes, "rewrites": 1}

    after = await score_text(llm_client, candidate, characters, state_conflicts)
    ok, why2 = judge_improvement(before, after)
    voice_ok, voice_detail = _voice_not_worse(text, candidate,
                                              [str((c or {}).get("name") or "") for c in (characters or [])])
    if not ok or not voice_ok:
        return {"after": text, "accepted": False, "reverted": True, "route": route,
                "reason": why2 if not ok else voice_detail,
                "before": before, "after_score": after, "changes": changes, "rewrites": 1}

    return {"after": candidate, "accepted": True, "route": route, "before": before,
            "after_score": after, "changes": changes, "rewrites": 1}
