"""世界规则校验器单测（S4.1）：0 token 拦玄幻硬约束。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.engine import world_rules  # noqa: E402

_RULES = [
    {
        "concept": "筑基境不能空间挪移",
        "constraint": "筑基境修士不能动用空间挪移",
        "rule_type": "negation",
        "keywords": ["筑基", "挪移", "空间跳跃"],
        "constraint_keywords": ["不能"],
    },
    {
        "concept": "核心传承只能宗主开启",
        "constraint": "核心传承只能由宗主一脉开启",
        "rule_type": "exclusive",
        "keywords": ["核心传承", "传承"],
        "constraint_keywords": ["只能", "宗主"],
    },
]


def test_negation_violation_detected():
    """筑基境修士动用空间挪移 → 拦截（violation）。"""
    vs = world_rules.check_violations("他手捏法诀，竟动用空间挪移遁走", _RULES)
    assert any(v.rule == "筑基境不能空间挪移" and v.severity == "violation" for v in vs)


def test_negation_pass_when_concept_absent():
    """正文没提筑基/挪移 → 不触发。"""
    vs = world_rules.check_violations("他拔出长剑，剑气纵横三丈", _RULES)
    assert vs == []


def test_exclusive_violation_detected():
    """非许可者擅自触碰核心传承 → 拦截。"""
    vs = world_rules.check_violations("他暗自伸手，竟要独自开启核心传承", _RULES)
    assert any(v.rule == "核心传承只能宗主开启" for v in vs)


def test_exclusive_pass_when_permitted():
    """许可者（宗主）在场 → 不拦截。"""
    vs = world_rules.check_violations("宗主以印信开启核心传承", _RULES)
    assert vs == []


def test_check_action_combines_text_and_new_fact():
    """护栏入口：同时检测 action.text 与 new_fact。"""
    act = {"actor": "chenmo", "kind": "action",
           "text": "他尝试动用空间挪移", "new_fact": "筑基境修士施展空间挪移成功"}
    vs = world_rules.check_action(act, _RULES)
    assert len(vs) >= 1


def test_no_rules_no_op():
    assert world_rules.check_violations("随便写点啥", []) == []