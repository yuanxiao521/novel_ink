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


# ---------------------------------------------------------------- 审计发现的误报（回归）
CAST_PROSE = ("林尘推门进来。林尘把伞靠在墙边。林尘看了一眼桌上的残页。"
              "李文皱眉问：林尘，你查到了什么？林尘没有回答，只把残页推过去。"
              "林尘说：这半句剑诀，林尘在夹层里见过。林尘又补充道：后山那具遗骸也对得上。")


def test_r_a3_6_excludes_proper_nouns():
    """R-A3-6 必须能排除专名：主角名在一段里出现 8 次是正常叙事，不是"复读"。

    审计实测：不排除时"林尘"被判 violation（96 字里占 10%）——若真去 spot-fix，
    会去"修"主角名；因此 exclude 是**正确性要求**，不是可选优化。
    """
    rep = ai_tone_report(CAST_PROSE, exclude={"林尘", "李文"})
    assert "R-A3-6" not in _rules(rep), rep

    rep2 = ai_tone_report(CAST_PROSE)
    assert "R-A3-6" in _rules(rep2)
    hit = next(r for r in rep2["rules"] if r["rule"] == "R-A3-6")
    assert "未提供专名清单" in hit["detail"]        # 诚实标注可能误判


def test_cliche_threshold_scales_with_text_length():
    """套话阈值按字数归一：同样的 3 次套话在短句里该报、在长章里不该报。

    审计发现原实现是绝对阈值 6 —— 长章必然命中、短句几乎不可能命中，两端都错。
    """
    short = "他深吸一口气，眼中闪过一丝犹豫，又深吸一口气。"
    assert "R-A3-1" in _rules(ai_tone_report(short))

    long_text = short + "正常叙事。" * 150          # ~800 字
    assert "R-A3-1" not in _rules(ai_tone_report(long_text))


def test_r_a3_4_ignores_hard_wrapped_lines():
    """硬折行（每句一行）不能把段中句当段末：单句"段"一律不做拔高判定。

    审计实测：四行硬折行文本 ["雨敲着窗格。/他推开门。/他知道。/李文抬起头。"]
    会让"他知道。"被当成段末 → 误报 R-A3-4。
    """
    hard = "雨敲着窗格。\n他推开门。\n他知道。\n李文抬起头。"
    assert elevation_endings(hard) == []
    assert "R-A3-4" not in _rules(ai_tone_report(hard))

    # 真段落（≥2 句）仍然可判
    real = "夜里风停了。他知道。"
    assert [h["rule"] for h in elevation_endings(real)] == ["R-A3-4"]


def test_dash_and_ellipsis_count_double_wide_marks_once():
    """破折号/省略号计数：中文成对标点必须算 1 处（审计实锤的计数 bug）。

    旧实现 text.count("——") + text.count("—") 把每处破折号算成 3 处，
    导致 S2 先验与 A3 R-A3-2 全线误报：真实文本 1 处/250 字被报成
    "3 处 / 11.8 千字（阈值 6）"，把"破折号狂飙"这个印象凭空造了出来。
    """
    from app.services.engine.style_checks import ai_tone_scan

    assert ai_tone_scan("他顿了顿——然后笑了。")["dash"] == 1
    assert ai_tone_scan("他顿了顿——然后笑了——又停下。")["dash"] == 2
    assert ai_tone_scan("没有破折号的句子。")["dash"] == 0
    assert ai_tone_scan("他说……然后走了。")["ellipsis"] == 1
    assert ai_tone_scan("他说……然后走了……又回头。")["ellipsis"] == 2

    # 真实量级：1 处 / 300 字 ≈ 3.3/千字 → 不该触发阈值 6
    m = ai_tone_scan("x" * 299 + "——")
    assert m["dash"] == 1 and m["dash"] * 1000 / 300 < 6


def test_report_shape_and_metrics():
    """报告契约：rules/counts/cliches/metrics/clean 五件套齐全（前端与 note payload 依赖）。"""
    rep = ai_tone_report(CLEAN)
    assert set(rep) == {"rules", "counts", "cliches", "metrics", "clean"}
    assert set(rep["metrics"]) == {"dash", "ellipsis", "filler_per_100", "chars"}
    assert rep["metrics"]["chars"] == len(CLEAN)
