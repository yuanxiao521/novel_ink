"""A3 反 AI 味规则单测（style_checks.ai_tone_report）。

纯函数：喂文本，断言规则命中/不误伤/证据可定位。
对应《写作优化方案-A3反AI味校验.md》§5 验收（规则可复现 + 干净文本 0 命中）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.engine.style_checks import (  # noqa: E402
    ai_tone_report,
    elevation_endings,
    homogeneous_runs,
    paragraphs,
    top_content_words,
)

CLEAN = "雨敲着窗格，烛火矮了一截。陈默把那张残页按在桌面，指节泛白。李文端起茶盏，没有喝。"


def _rules(rep):
    return [r["rule"] for r in rep["rules"]]


def test_existing_signals_map_to_rules():
    """既有信号升级为规则：套话 → R-A3-1；破折号/省略号滥用 → R-A3-2。"""
    text = ("他深吸一口气，眼中闪过一丝犹豫——又深吸一口气，眼中闪过一丝决然——"
            "终于，他深吸一口气，眼中闪过一丝疲惫——就这样吧——")
    rep = ai_tone_report(text)
    rules = _rules(rep)
    assert "R-A3-1" in rules and "R-A3-2" in rules
    assert rep["counts"]["by_rule"]["R-A3-1"] == 1


def test_filler_density_rule_is_reachable():
    """R-A3-3 必须可达：ai_tone_scan 只把填充词当指标返回，report 需按阈值升级成规则。"""
    rep = ai_tone_report("「这事儿，我是知道的，他的心思也就那样了，是的，都明白了的了。」")
    assert "R-A3-3" in _rules(rep), rep
    assert rep["metrics"]["filler_per_100"] > 16
    assert ai_tone_report(CLEAN)["metrics"]["filler_per_100"] <= 16


def test_elevation_ending_flagged_and_guarded():
    """R-A3-4：段末短句含"拔高/顿悟"词 → 命中；长句或普通收尾不误伤。"""
    text = "夜里风停了。他知道。\n\n桌上还留着半盏茶。"
    hits = elevation_endings(text)
    assert [h["rule"] for h in hits] == ["R-A3-4"]
    assert "他知道" in hits[0]["evidence"]          # 证据可在原文定位
    assert paragraphs(text) == ["夜里风停了。他知道。", "桌上还留着半盏茶。"]

    long_plain = "夜里风停了。他终于说出一句很长很长的、把前因后果都交代清楚的解释。"
    assert "R-A3-4" not in _rules(ai_tone_report(long_plain))


def test_homogeneous_runs_flagged_and_guarded():
    """R-A3-5：连续 3 句同首字 + 句长接近 → 命中；首字不同不算（防误伤）。"""
    homo = "他深吸一口气。他眼中闪过一丝慌。他嘴角抽了一下。"
    hits = homogeneous_runs(homo)
    assert [h["rule"] for h in hits] == ["R-A3-5"]

    diff_first = "他深吸一口气。窗外雨声渐密。她低头没有说话。"
    assert "R-A3-5" not in _rules(ai_tone_report(diff_first))


def test_top_content_word_is_violation():
    """R-A3-6：高频实词集中 → violation 级（比套话更伤文本）。"""
    hits = top_content_words("林尘看着林尘，林尘问林尘，林尘答林尘。")
    assert [h["rule"] for h in hits] == ["R-A3-6"]
    assert hits[0]["severity"] == "violation"
    rep = ai_tone_report("林尘看着林尘，林尘问林尘，林尘答林尘。")
    assert rep["counts"]["violation"] >= 1


def test_clean_text_has_no_rules():
    """不误伤：干净叙述文本 0 命中（clean=True，counts.total=0）。"""
    rep = ai_tone_report(CLEAN)
    assert rep["rules"] == [] and rep["clean"] is True
    assert rep["counts"] == {"total": 0, "violation": 0, "by_rule": {}}


def test_report_shape_and_metrics():
    """报告契约：rules/counts/cliches/metrics/clean 五件套齐全（前端与 note payload 依赖）。"""
    rep = ai_tone_report(CLEAN)
    assert set(rep) == {"rules", "counts", "cliches", "metrics", "clean"}
    assert set(rep["metrics"]) == {"dash", "ellipsis", "filler_per_100", "chars"}
    assert rep["metrics"]["chars"] == len(CLEAN)
