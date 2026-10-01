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

# ---------------------------------------------------------------- S4 step2：确定性高光
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.services.engine.emergence import extract_hits  # noqa: E402


def _arch(turn, cls, tension, events, summary="看" ):
    return {"turn": turn, "cls": cls, "tension": tension, "tension_trend": "up",
            "summary": summary, "events": events, "thoughts": []}


def test_extract_hits_merges_kinds():
    """同一回合命中多条 → 合并进 kinds（不重复出条目）；收束回合只认最后一回合。"""
    archives = [
        _arch(1, "type-dialogue", 20, [{"actor": "陈默", "kind": "dialogue", "text": "夹层里。"}]),
        _arch(2, "type-conflict", 80,
              [{"actor": "李文", "kind": "dialogue",
                "text": "你书房门没锁，我进来讨本书看，犯法？"}]),
    ]
    hits = extract_hits({"id": "sc-1"}, archives)
    assert [h["turn"] for h in hits] == [2]          # 回合 1 平淡，不出条目
    kinds = hits[0]["kinds"]
    assert "conflict" in kinds and "peak" in kinds
    assert "long_dialogue" in kinds and "closing" in kinds   # 长台词 + 末回合
    assert len(kinds) == len(set(kinds))             # 不重复
    assert "犯法？" in hits[0]["quote"] and hits[0]["chars"] == ["李文"]


def test_extract_hits_edge_cases():
    """边界：空归档 → []；单回合不算"收束"；张力全 0 不报峰值。"""
    assert extract_hits({"id": "s"}, []) == []
    one = extract_hits({"id": "s"}, [_arch(1, "type-info", 0,
                                           [{"actor": "A", "kind": "action", "text": "走"}])])
    assert one == []
    two = extract_hits({"id": "s"}, [
        _arch(1, "type-info", 0, [{"actor": "A", "kind": "action", "text": "走"}]),
        _arch(2, "type-info", 0, [{"actor": "A", "kind": "action", "text": "停"}]),
    ])
    assert [h["kinds"] for h in two] == [["closing"]]   # 只有收束，没有假峰值


@pytest_asyncio.fixture
async def svc():
    """内存态：1 书 1 章 1 场景 + 1 个含 2 回合归档的 sim（含思考）。"""
    from app.db.repo import Repo
    from app.services.service import SimulationService

    repo = Repo(use_db=False)
    service = SimulationService(repo)
    await repo.save_book({"id": "b1", "title": "涌现测试书"})
    await repo.save_chapter({"id": "ch1", "book_id": "b1", "title": "第一章", "order_no": 1})
    await repo.save_scene({"id": "sc-1", "chapter_id": "ch1", "title": "书房夜谈",
                           "stage_desc": "雨夜书房", "goal": "试探身份", "final_prose": ""})
    sim = models.SimulationState(scenario="t", book_id="b1", chapter_id="ch1", scene_id="sc-1")
    sim.turn_archives = [
        models.TurnArchive(turn=1, cls="type-dialogue", tension=20, tension_trend="up",
                           summary="试探",
                           events=[{"actor": "陈默", "kind": "dialogue", "text": "夹层里。"}],
                           thoughts=[{"char": "陈默", "monologue": "他在试探我。",
                                      "reasoning": "只给半句 = 不信任"}]),
        models.TurnArchive(turn=2, cls="type-conflict", tension=80, tension_trend="up",
                           summary="翻脸",
                           events=[{"actor": "李文", "kind": "dialogue",
                                    "text": "你书房门没锁，我进来讨本书看，犯法？"}]),
    ]
    await repo.save("sim-1", sim)
    return service


@pytest.mark.asyncio
async def test_scene_script_includes_hits_and_adopt_creates_cards(svc):
    """剧本接口带高光；采纳后进灵感池（source=emergence、adopted=false）。"""
    out = await svc.scene_script("sc-1", with_thoughts=True)
    assert out["turns"] == 2 and out["hits"]
    assert "内心独白·陈默" in out["markdown"]

    res = await svc.adopt_emergence_hits("sc-1")
    assert res["adopted"] == len(out["hits"]) >= 1
    assert res["cards"] and all(c["source"] == "emergence" for c in res["cards"])
    assert all(c["adopted"] is False for c in res["cards"])
    assert "涌现高光" in res["cards"][0]["desc"]
    assert len(await svc.repo.list_inspirations("b1")) >= res["adopted"]   # 确实落库

    only_turn2 = await svc.adopt_emergence_hits("sc-1", turns=[2])
    assert only_turn2["picked"] == 1

