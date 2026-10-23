"""剧本台连拍回归（v1.73d）：归档按 (turn, exchange) 排序、标题带来回号、json 带 exchange。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.engine.emergence import render_script_json, render_script_md  # noqa: E402


def _archives():
    return [
        {"turn": 2, "exchange": 2, "cls": "type-action", "summary": "来回2｜反光锁住暗绿",
         "tension": 48, "tension_trend": "up", "prose": "雨幕收紧。",
         "events": [{"id": "E4", "actor": "char-1", "kind": "action", "text": "贴墙前移"}],
         "thoughts": []},
        {"turn": 2, "exchange": 1, "cls": "type-dialogue", "summary": "来回1｜先问我这一层",
         "tension": 40, "tension_trend": "flat", "prose": "",
         "events": [{"id": "E3", "actor": "char-1", "kind": "dialogue", "text": "先问我这一层"}],
         "thoughts": [{"char": "甲", "monologue": "她不对劲", "reasoning": "", "emotion": ""}]},
        {"turn": 1, "exchange": 1, "cls": "type-action", "summary": "开场",
         "tension": 30, "tension_trend": "flat", "prose": "",
         "events": [{"id": "E1", "actor": "char-2", "kind": "action", "text": "推门"}],
         "thoughts": []},
    ]


def test_json_sorted_by_turn_then_exchange_with_field():
    out = render_script_json({"id": "s", "title": "巷口"}, _archives())
    got = [(t["turn"], t["exchange"]) for t in out["turns"]]
    assert got == [(1, 1), (2, 1), (2, 2)], got


def test_md_head_shows_exchange():
    md = render_script_md({"id": "s", "title": "巷口"}, _archives())
    assert "〔回合 2 · 来回2 · 行动〕" in md
    assert "〔回合 2 · 对话〕" in md          # 来回 1 不带来回号（单来回拍/首来回）
    assert md.index("先问我这一层") < md.index("反光锁住暗绿"), "同回合来回必须按序渲染"
