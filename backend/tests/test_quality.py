"""A2 质量回环单测（评分/门控/三闸复检/路由）。假 LLM 驱动，确定性可复跑。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.engine.quality import (  # noqa: E402
    RISE_MARGIN, combine_scores, hard_signal_score, judge_improvement, quality_loop,
    sanitize_weak_points, score_text, should_rewrite, whole_fact_gate, _validate_quality,
)

DIMS = {"coherence": 85, "character": 85, "pacing": 82, "imagery": 88, "ending": 84}
CLEAN = ("雨敲着窗格，烛火矮了一截。陈默把残页按在桌面，指节泛白。"
         "李文端起茶盏，没有喝。")


def _rep(total, dims=None, weak=None):
    return {"scores": dict(dims or DIMS), "total": total,
            "weak_points": weak or [], "verdict": "pass" if total >= 78 else "revise"}


class _SeqLLM:
    def __init__(self, responses, available=True):
        self.available = available
        self.responses = list(responses)
        self.calls: list[str] = []

    async def call_cheap(self, prompt, json_schema=None):
        self.calls.append(prompt)
        return self.responses.pop(0) if self.responses else None


def test_validator_and_evidence_gate():
    """校验：必须有 total/scores；弱项证据必须能在正文逐字找到（防空评）。"""
    assert _validate_quality(_rep(80)) is None
    assert _validate_quality({"scores": DIMS}) is not None
    assert _validate_quality({"total": 80}) is not None

    text = "他把残页推过去。李文没有接。"
    rep = {"weak_points": [{"dim": "pacing", "evidence": "李文没有接。", "why": "太顺"},
                           {"dim": "imagery", "evidence": "正文里没这句", "why": "幻觉"}]}
    out = sanitize_weak_points(rep, text)
    assert [w["dim"] for w in out["weak_points"]] == ["pacing"]


def test_hard_signal_penalizes_ai_tone():
    """硬信号：AI 味文本应显著低于干净文本（文风分被扣）。"""
    clean = hard_signal_score(CLEAN)
    cliche = hard_signal_score("他深吸一口气，眼中闪过一丝犹豫，缓缓开口。")
    assert clean["parts"]["tone"] == 100
    assert cliche["parts"]["tone"] < 100 and cliche["score"] <= clean["score"]
    assert combine_scores(100, clean) > combine_scores(100, cliche)


def test_trigger_rules_match_measured_calibration():
    """触发条件：总分 <78 或任一维度 <70（实测真实初稿 86-91，70 会永不触发）。"""
    assert should_rewrite(70, DIMS)[0] is True
    assert should_rewrite(90, dict(DIMS, pacing=62))[0] is True     # 单维度塌
    assert should_rewrite(85, DIMS)[0] is False                     # 达标 → 不重写


def test_judge_improvement_margin_and_dim_drop():
    """复评门槛：上升 ≥5（σ≈2.1）；任一维度降 >8 不采纳。"""
    before = _rep(70)
    assert judge_improvement(before, _rep(70 + RISE_MARGIN))[0] is True
    ok, why = judge_improvement(before, _rep(72))
    assert ok is False and "未上升够" in why
    ok2, why2 = judge_improvement(before, _rep(90, dict(DIMS, pacing=60)))
    assert ok2 is False and "维度下降过大" in why2


def test_whole_fact_gate_blocks_changes():
    """全文事实闸门：专名/数字/台词次数必须一致。"""
    prot = {"names": {"陈默"}, "numbers": set(), "terms": set(), "quotes": {"夹层里。"}}
    assert whole_fact_gate("陈默说：「夹层里。」", "陈默低声道：「夹层里。」", prot) is None
    assert "专名" in (whole_fact_gate("陈默说。", "林凡说。", prot) or "")
    assert "台词" in (whole_fact_gate("他说：「夹层里。」", "他说：「懂了。」", prot) or "")
    assert whole_fact_gate("他等了三天。", "他等了三天。", prot) is None


@pytest.mark.asyncio
async def test_loop_skips_when_score_ok_without_rewrite():
    """达标 → **不重写**（只花 1 次评分调用，省钱）。"""
    llm = _SeqLLM([_rep(88)])
    out = await quality_loop(llm, CLEAN)
    assert out["accepted"] is False and out["skipped"] == "score-ok"
    assert out["after"] == CLEAN and len(llm.calls) == 1


@pytest.mark.asyncio
async def test_loop_full_rewrite_accepts_when_improved():
    """低分 → 全文重写（无定点命中）→ 复评上升够 → 采纳。"""
    rewritten = "雨敲着窗格，烛火矮了一截。陈默把残页按在桌面，指节泛白，像按着一块冰。李文端起茶盏，终究没有喝。"
    llm = _SeqLLM([_rep(60), {"text": rewritten}, _rep(80)])
    out = await quality_loop(llm, CLEAN)
    assert out["accepted"] is True and out["route"] == "full" and out["rewrites"] == 1
    assert out["after"] == rewritten
    assert out["after_score"]["total"] > out["before"]["total"]


@pytest.mark.asyncio
async def test_loop_reverts_when_not_improved():
    """复评没涨够 → **回退初稿**（不采纳噪声）。"""
    rewritten = "雨敲着窗格。陈默把残页按在桌面。李文端起茶盏。"
    llm = _SeqLLM([_rep(60), {"text": rewritten}, _rep(62)])
    out = await quality_loop(llm, CLEAN)
    assert out["accepted"] is False and out["reverted"] is True
    assert out["after"] == CLEAN and "未上升够" in out["reason"]


@pytest.mark.asyncio
async def test_loop_llm_unavailable_keeps_text():
    out = await quality_loop(_SeqLLM([], available=False), CLEAN)
    assert out["accepted"] is False and out["after"] == CLEAN and out["error"]
