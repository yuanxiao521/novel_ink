"""S4 涌现产物单测（思考落档 + 剧本渲染）。

覆盖：TurnArchive.thoughts 归集与清空（不跨回合覆盖）/ 思考不进 events /
旧档兼容（无该字段仍可读）/ 剧本 Markdown 与 JSON 渲染。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.schemas import models  # noqa: E402
from app.services.engine.emergence import render_script_json, render_script_md  # noqa: E402
from app.services.service import SimulationService  # noqa: E402

SCENE = {"id": "sc-1", "title": "书房夜谈", "stage_desc": "雨夜书房，烛火将尽",
         "goal": "试探妹妹身份", "content_desc": "两人对坐"}


def _sim() -> models.SimulationState:
    card = models.CharacterCard(id="c1", name="陈默", summary="书房主人", voice="话少，短句")
    sim = models.SimulationState(scenario="t", book_id="b1", chapter_id="ch1", scene_id="sc-1")
    sim.characters = {"c1": card}
    sim.world.turn = 3
    sim.events = [
        models.Event(id="e1", turn=3, type=models.EventType.action,
                     source=models.EventSource.character, actor="c1",
                     payload={"kind": "dialogue", "text": "夹层里。"}),
    ]
    return sim


def test_archive_collects_thoughts_and_clears_scratch():
    """思考归入归档后清空 scratch：第二回合不会覆盖第一回合的思考。"""
    sim = _sim()
    sim.scratch["thoughts"] = {"c1": {"thought": "他在试探我。", "emotion": "警觉",
                                      "reasoning": "残页只给半句，说明他不信任我"}}
    SimulationService._archive_turn(sim, 0)
    a3 = next(a for a in sim.turn_archives if a.turn == 3)
    assert a3.thoughts and a3.thoughts[0]["char"] == "陈默"
    assert a3.thoughts[0]["monologue"] == "他在试探我。"
    assert "不信任我" in a3.thoughts[0]["reasoning"]
    assert sim.scratch["thoughts"] == {}          # 已清空

    # 第二回合：新思考 → 新归档；旧归档内容不被覆盖
    sim.world.turn = 4
    sim.scratch["thoughts"] = {"c1": {"thought": "先稳住他。"}}
    sim.events = sim.events + [models.Event(
        id="e2", turn=4, type=models.EventType.action, source=models.EventSource.character,
        actor="c1", payload={"kind": "action", "text": "把残页推过去"})]
    SimulationService._archive_turn(sim, 1)
    a4 = next(a for a in sim.turn_archives if a.turn == 4)
    assert a4.thoughts[0]["monologue"] == "先稳住他。"
    assert a3.thoughts[0]["monologue"] == "他在试探我。"    # 第一回合仍是原内容


def test_thoughts_do_not_pollute_events():
    """思考只进归档，不进 events（不污染角色行动/账本）。"""
    sim = _sim()
    before = len(sim.events)
    sim.scratch["thoughts"] = {"c1": {"thought": "心里话"}}
    SimulationService._archive_turn(sim, 0)
    a3 = sim.turn_archives[-1]
    assert len(sim.events) == before
    assert all("心里话" not in str(e.payload.get("text")) for e in sim.events)
    assert a3.thoughts[0]["monologue"] == "心里话"


def test_legacy_archive_without_thoughts_is_compatible():
    """旧归档（无 thoughts 字段）仍可构造与渲染（并增向后兼容）。"""
    old = models.TurnArchive(turn=1, cls="type-conflict", summary="旧档", events=[])
    assert old.thoughts == []
    md = render_script_md(SCENE, [old.model_dump()])
    assert "回合 1 · 冲突" in md and "旧档" in md


def test_render_md_shapes_and_toggles():
    """剧本 Markdown：角色台词 / 动作 / 可选思考与张力开关。"""
    arch = [{"turn": 1, "cls": "type-conflict", "summary": "试探",
             "tension": 62.0, "tension_trend": "up",
             "events": [{"actor": "陈默", "kind": "dialogue", "text": "夹层里。"},
                        {"actor": "李文", "kind": "action", "text": "端起茶盏"}],
             "thoughts": [{"char": "陈默", "monologue": "他在试探我。",
                           "reasoning": "只给半句 = 不信任"}]}]
    md = render_script_md(SCENE, arch, with_thoughts=True, with_tension=True)
    assert "# 剧本 · 书房夜谈" in md and "【场景】雨夜书房" in md
    assert "陈默：夹层里。" in md and "（李文 端起茶盏）" in md
    assert "〔内心独白·陈默〕他在试探我。" in md and "〔动机·陈默〕只给半句" in md
    assert "［张力 62.0 up］" in md

    md2 = render_script_md(SCENE, arch, with_thoughts=False, with_tension=False)
    assert "内心独白" not in md2 and "张力" not in md2

    j = render_script_json(SCENE, arch)
    assert j["scene"]["title"] == "书房夜谈" and j["turns"][0]["thoughts"][0]["char"] == "陈默"

    assert "尚无推演回合" in render_script_md(SCENE, [])
