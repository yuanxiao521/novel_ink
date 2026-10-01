"""A3 step3 定点重写单测（ai_tone.py）。

覆盖：规则白名单 / 句子级定位 / 事实闸门四类 / 口吻闸门 / 幂等空操作 /
LLM 不可用 / 落地校验 / 事实变更丢弃 / 命中数不降则回退。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.engine.ai_tone import (  # noqa: E402
    _validate_spot_fix,
    _voice_not_worse,
    fact_gate,
    protected_tokens,
    spot_fix,
)
from app.services.engine.style_checks import (  # noqa: E402
    rule_policy,
    sentences_with,
)

CLICHE_TEXT = "他深吸一口气。雨敲着窗格。"


class _FakeLLM:
    def __init__(self, response=None, available=True):
        self.available = available
        self.response = response
        self.calls: list[str] = []

    async def call_cheap(self, prompt, json_schema=None):
        self.calls.append(prompt)
        return self.response


def _rw(index, before, after, rule="R-A3-1", reason="套话换动作"):
    return {"index": index, "before": before, "after": after, "rule": rule, "reason": reason}


# ---------------------------------------------------------------- 定位与策略
def test_rule_policy_whitelist():
    """白名单：R-A3-2 只提示不改；R-A3-5 保守（限句数）；其余可改。"""
    assert rule_policy("R-A3-2")["fixable"] is False
    assert rule_policy("R-A3-2")["mode"] == "hint"
    assert rule_policy("R-A3-5")["mode"] == "conservative" and rule_policy("R-A3-5")["fixable"]
    for r in ("R-A3-1", "R-A3-3", "R-A3-4", "R-A3-6"):
        assert rule_policy(r)["mode"] == "fix", r
    assert rule_policy("R-A3-99")["fixable"] is False


def test_sentences_with_locates_sentence_rules():
    """句子级定位：给出"哪句命中哪条"，含只提示的 R-A3-2 与段末 R-A3-4。"""
    hits = {h["sentence"]: h["rules"] for h in sentences_with("他顿了顿——然后笑了。雨停了。")}
    assert hits["他顿了顿——然后笑了。"] == ["R-A3-2"]

    hits2 = {h["sentence"]: h["rules"] for h in sentences_with("夜里风停了。他知道。")}
    assert "R-A3-4" in hits2["他知道。"]

    hits3 = {h["sentence"]: h["rules"] for h in sentences_with(CLICHE_TEXT)}
    assert "R-A3-1" in hits3["他深吸一口气。"]


# ---------------------------------------------------------------- 事实闸门
def test_fact_gate_blocks_fact_changes_and_allows_equivalent():
    """四类事实逐字保护：专名/数字/术语/台词；等价改写放行。"""
    text = "陈默深吸一口气，第三日动身，突破了凝气境。「我明白了。」"
    chars = [{"name": "陈默"}]
    ws = [{"kind": "term", "name": "凝气境", "value": "凝气境"}]
    prot = protected_tokens(text, chars, ws)
    assert "凝气境" in prot["terms"] and "第三日" in prot["numbers"]
    assert "我明白了。" in prot["quotes"]

    assert fact_gate("陈默深吸一口气。", "陈默吸了口气。", prot) is None          # 等价改写
    assert "专名" in (fact_gate("陈默深吸一口气。", "林凡深吸一口气。", prot) or "")
    assert "数字" in (fact_gate("第三日动身。", "次日动身。", prot) or "")
    assert "术语" in (fact_gate("突破了凝气境。", "突破了更高境界。", prot) or "")
    assert "台词" in (fact_gate("他低声道：「我明白了。」", "他低声道：「懂了。」", prot) or "")


def test_voice_gate_detects_length_shift_and_new_particles():
    """口吻闸门：角色平均句长剧变 或 新增语气词 → 判"劣化"。"""
    cast = ["陈默"]
    before = "陈默：「好。」「行。」「嗯。」"
    ok, _ = _voice_not_worse(before, before, cast)
    assert ok
    longer = "陈默：「好。」「行。」「嗯，我知道了，这事儿我记下了，你放心。」"
    ok2, detail = _voice_not_worse(before, longer, cast)
    assert ok2 is False and "平均句长" in detail

    # 句长几乎不变（3→4 字，33% < 40%）但新增语气词 → 命中语气词闸
    ok3, d3 = _voice_not_worse("陈默：「不知。」「不行。」", "陈默：「不知呢。」「不行吧。」", cast)
    assert ok3 is False and "新增语气词" in d3


# ---------------------------------------------------------------- spot-fix
@pytest.mark.asyncio
async def test_spot_fix_skips_hint_only_rule_without_llm_call():
    """只有"只提示"规则命中（破折号）→ 不做改写、不调 LLM（幂等空操作）。"""
    llm = _FakeLLM(response={"rewrites": []})
    out = await spot_fix(llm, "他顿了顿——然后笑了。")
    assert out["accepted"] is True and out["skipped"] == "no-fixable-hit"
    assert out["after"] == "他顿了顿——然后笑了。"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_spot_fix_skips_pure_dialogue_sentence():
    """纯台词句跳过（台词逐字受保护，改了必然回退 → 不浪费 LLM 调用）。"""
    llm = _FakeLLM(response={"rewrites": []})
    out = await spot_fix(llm, "「他深吸一口气。」", characters=[{"name": "陈默"}])
    assert out["skipped"] == "no-fixable-hit" and llm.calls == []


@pytest.mark.asyncio
async def test_spot_fix_llm_unavailable_keeps_text():
    """LLM 不可用：不改文 + 明确报错（不抛 500）。"""
    out = await spot_fix(_FakeLLM(available=False), CLICHE_TEXT)
    assert out["accepted"] is False and out["error"] and out["after"] == CLICHE_TEXT


@pytest.mark.asyncio
async def test_spot_fix_applies_grounded_rewrite_and_accepts():
    """正常路径：有据改写被应用，句子级命中数下降 → accepted，且 before 逐字进入 prompt。"""
    llm = _FakeLLM({"rewrites": [_rw(0, "他深吸一口气。", "他把伞靠在门边。")]})
    out = await spot_fix(llm, CLICHE_TEXT, characters=[{"name": "陈默"}])
    assert out["accepted"] is True
    assert out["after"] == "他把伞靠在门边。雨敲着窗格。"
    assert out["hits_before"] == 1 and out["hits_after"] == 0
    assert "他深吸一口气。" in llm.calls[0]        # 原句逐字进 prompt（便于模型对齐）
    assert "需处理：深吸一口气" in llm.calls[0]     # 命中词写进 prompt（否则模型做表面改写）
    assert out["changes"][0]["applied"] is True


@pytest.mark.asyncio
async def test_spot_fix_drops_ungrounded_and_fact_changing_rewrites():
    """落地校验 + 事实闸门：原句找不到 / 改了数字 → 该条丢弃并记原因。"""
    llm = _FakeLLM({"rewrites": [
        _rw(0, "正文里根本没有这句。", "随便改改。"),
        _rw(1, "他深吸一口气。", "他吸了口气，等了三天。"),
    ]})
    out = await spot_fix(llm, CLICHE_TEXT)
    assert out["after"] == CLICHE_TEXT and out["accepted"] is False
    reasons = [c.get("blocked_reason") or "" for c in out["changes"]]
    assert any("逐字找到" in r for r in reasons)
    assert any("数字" in r for r in reasons)
    assert out["changes"][0]["applied"] is False


@pytest.mark.asyncio
async def test_spot_fix_reverts_when_hits_not_reduced():
    """双闸复检：改写后仍命中（套话没删干净）→ 回退原文并说明原因。"""
    llm = _FakeLLM({"rewrites": [_rw(0, "他深吸一口气。", "他深吸一口气，缓缓起身。")]})
    out = await spot_fix(llm, CLICHE_TEXT)
    assert out["accepted"] is False and out["reverted"] is True
    assert out["after"] == CLICHE_TEXT
    assert "未下降" in out["reason"]
    assert out["score_after"]["terms"] > out["score_before"]["terms"]   # 改后更糟 → 回退


@pytest.mark.asyncio
async def test_spot_fix_accepts_partial_improvement():
    """复检口径：允许"部分改善"—— 一句里多个套话降到更少即成（不必清零）。

    真 LLM 实测教训：按"命中句数"判定会把"8 个套话→1 个"整段回退。
    """
    text = "他深吸一口气，眼中闪过一丝犹豫，缓缓开口。"
    llm = _FakeLLM({"rewrites": [_rw(0, "他深吸一口气，眼中闪过一丝犹豫，缓缓开口。",
                                     "他攥了攥拳，缓缓开口。")]})
    out = await spot_fix(llm, text)
    assert out["accepted"] is True
    assert out["score_after"]["terms"] < out["score_before"]["terms"]
    assert out["after"] == "他攥了攥拳，缓缓开口。"


def test_spot_fix_validator_requires_before_and_after():
    assert _validate_spot_fix({"rewrites": [_rw(0, "a", "b")]}) is None
    assert _validate_spot_fix({}) is not None
    assert _validate_spot_fix({"rewrites": [{"index": 0, "after": "b"}]}) is not None
    assert _validate_spot_fix({"rewrites": [{"index": 0, "before": "a"}]}) is not None
