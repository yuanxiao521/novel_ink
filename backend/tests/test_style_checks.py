"""0-token 口吻先验单测（S2 step5 · style_checks.py）。

纯函数、无 LLM、无 DB：直接喂文本断言判定与证据。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.engine.style_checks import (  # noqa: E402
    address_terms,
    ai_tone_scan,
    extract_dialogues,
    homogeneity,
    per_character_metrics,
    prior_block,
    split_sentences,
    voice_prior,
)

CAST = [{"name": "陈默"}, {"name": "李文"}]


def test_split_sentences_keeps_quote_intact():
    """引号内部的句末标点不切句（否则会把一句台词拆成多句，句长统计失真）。"""
    text = "「夹层里。我查过了。」陈默说。李文笑了笑。"
    parts = split_sentences(text)
    assert "「夹层里。我查过了。」" in parts[0]   # 引号内句号不切
    assert len(parts) == 2                        # 引号+归属算一句，再切"李文笑了笑。"


def test_dialogue_speaker_attribution_three_patterns():
    """三种归属写法都要认出来：名：引号 / 名前+说 / 引号后+道。"""
    text = "陈默：「夹层里。」李文笑了笑：「我查了这么久。」「我不信。」陈默说完便转身。"
    ds = extract_dialogues(text, ["陈默", "李文"])
    assert [d["speaker"] for d in ds] == ["陈默", "李文", "陈默"]


def test_per_character_metrics_lengths_and_particles():
    """各角色台词句数/平均句长/语气词集合分账，不混在一起。"""
    text = "陈默：「嗯。」「好吧。」李文：「我帮你查了这么久，你才肯拿出来？」"
    rows = {m["name"]: m for m in per_character_metrics(text, CAST)}
    assert rows["陈默"]["dialogues"] == 2
    assert rows["陈默"]["avg_len"] < rows["李文"]["avg_len"]
    assert "嗯" in rows["陈默"]["particles"] and "吧" in rows["陈默"]["particles"]


def test_homogeneity_flags_when_particles_overlap():
    """两角色语气词集合高度重合 → 判"疑似同质"（≥3 种才判定，样本小不下结论）。"""
    text = ("陈默：「嗯，是啊，好吧。」李文：「嗯，是吧，好啊。」"
            "陈默：「嗯，对啊，行吧。」李文：「嗯，对吧，成啊。」")
    rows = per_character_metrics(text, CAST)
    out = homogeneity(rows)
    assert out["flagged"], out
    assert "疑似同质" in out["flagged"][0]["detail"]


def test_address_terms_flags_mixed_variants_with_card_sanction():
    """称呼表：同一角色出现多种称呼 → 标"待确认"；卡内认可过的单独列出。"""
    text = "林兄，你看这夹层。尘哥，你倒是说句话。林尘站在窗前。"
    chars = [{"name": "林尘", "summary": "李文常称他林兄"}]
    out = address_terms(text, chars)
    assert out["terms"]["林尘"], out
    assert out["findings"], out
    detail = out["findings"][0]["detail"]
    assert "卡内认可：林兄" in detail and "尘哥" in detail


def test_ai_tone_scan_reports_cliches_and_dash():
    """A3 底座：套话计数带证据片段；破折号密度超阈值标红。"""
    text = ("他深吸一口气，眼中闪过一丝犹豫，又深吸一口气——"
            "终于，他深吸一口气，眼中闪过一丝决然——就这样吧——")
    tone = ai_tone_scan(text)
    terms = {c["term"]: c["count"] for c in tone["cliches"]}
    assert terms.get("深吸一口气") == 3
    assert all(c["evidence"] for c in tone["cliches"])       # 每条都带证据片段
    kinds = {f["kind"] for f in tone["flags"]}
    assert "cliche" in kinds and "dash" in kinds


def test_voice_prior_collects_flags_and_renders_prompt_block():
    """总入口汇总 flags；prior_block 渲染成 prompt 可用的紧凑块（含量化事实）。"""
    text = ("陈默：「好。」「行。」「嗯。」「对。」"
            "李文：「我帮你查了这么久，你才肯拿出来？你当真以为我什么都不知道吗，陈默？」")
    prior = voice_prior(text, CAST)
    kinds = {f["kind"] for f in prior["flags"]}
    assert "avg_len" in kinds                                  # 陈默 4 句极短 → 标红
    block = prior_block(prior)
    assert "陈默" in block and "字/句" in block and "确定性信号" in block
