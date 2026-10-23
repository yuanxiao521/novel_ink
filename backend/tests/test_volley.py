"""volley 对话快速通道回归（v1.73）：提示拼装 / 逐行清洗 / 非法行丢弃 / 摘要兜底。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.schemas import models  # noqa: E402
from app.services.engine.volley import (  # noqa: E402
    parse_volley, volley_prompt, volley_summary,
)


def _sim() -> models.SimulationState:
    return models.SimulationState(
        id="s-v", scenario="test", book_id="b", chapter_id="c", scene_id="s",
        characters={
            "a": models.CharacterCard(id="a", name="甲", summary="主角", bottom_lines=["不动手"]),
            "b": models.CharacterCard(id="b", name="乙", summary="对手"),
        },
    )


def test_prompt_contains_cast_and_boundaries():
    sim = _sim()
    p = volley_prompt(sim, ["a", "b"])
    assert "甲" in p and "乙" in p and "不动手" in p and "lines" in p


def test_parse_volley_drops_illegal_lines():
    sim = _sim()
    raw = {"lines": [
        {"actor": "a", "kind": "dialogue", "text": "你到底想怎样？"},
        {"actor": "ghost", "kind": "dialogue", "text": "不在场的人"},       # 丢弃
        {"actor": "b", "kind": "kindergarden", "text": "kind 越界"},        # kind 归一为 dialogue
        {"actor": "a", "kind": "action", "text": ""},                       # 空文本丢弃
        "不是字典的一行",                                                    # 丢弃
    ]}
    out = parse_volley(raw, sim, ["a", "b"])
    assert len(out) == 2, out
    assert out[0]["actor"] == "a" and out[0]["text"] == "你到底想怎样？"
    assert out[1]["actor"] == "b" and out[1]["kind"] == "dialogue"


def test_parse_volley_bad_payload_returns_empty():
    sim = _sim()
    assert parse_volley(None, sim, ["a"]) == []
    assert parse_volley({"lines": "不是列表"}, sim, ["a"]) == []
    assert parse_volley({"lines": [1, 2]}, sim, ["a"]) == []


def test_volley_summary_fallback():
    assert volley_summary({"summary": "甲逼问，乙避而不答"}, "兜底") == "甲逼问，乙避而不答"
    assert volley_summary(None, "兜底") == "兜底"
    assert volley_summary({}, "") == ""
