"""前情压缩（v1.58）测试：结构化概要 + 分层渲染 + 冲突检测。

为什么值得钉死：这套字段是"长篇不崩"的地基——
  ① 概要必须**可核对**（状态迁移/知晓范围），不是散文；
  ② 注入必须**分层有预算**（全书→本卷→最近章），超了按层截断；
  ③ 冲突检测宁可漏报不误报（误报会让作者不信）。
"""
from __future__ import annotations

import json

import pytest

from app.services.engine.digest import (
    MAX_EVENTS, detect_conflicts, has_digest, normalize_chapter_digest,
    normalize_volume_digest, render_chapter_digest, render_prior_bundle,
    volume_bounds, volume_of,
)


# ---------------------------------------------------------------- 卷边界
def test_volume_bounds_default_by_size():
    assert volume_bounds(0) == []
    assert volume_bounds(3) == [{"no": 1, "from": 1, "to": 3}]
    b = volume_bounds(18, 8)
    assert [(x["from"], x["to"]) for x in b] == [(1, 8), (9, 16), (17, 18)]


def test_volume_bounds_custom_overrides():
    custom = [{"no": 1, "from": 1, "to": 3}, {"no": 2, "from": 4, "to": 10}]
    b = volume_bounds(10, 8, custom)
    assert [(x["from"], x["to"]) for x in b] == [(1, 3), (4, 10)]
    # 坏数据跳过（from<=0 / to<from）
    assert volume_bounds(10, 8, [{"from": 0, "to": 3}, {"from": 5, "to": 4}]) \
        == volume_bounds(10, 8)


def test_volume_of():
    b = volume_bounds(18, 8)
    assert volume_of(1, b)["no"] == 1
    assert volume_of(9, b)["no"] == 2
    assert volume_of(99, b) is None


# ---------------------------------------------------------------- 概要规范化
def test_normalize_chapter_digest_caps_and_cleans():
    raw = {
        "one_line": "x" * 200,
        "events": [{"who": "霍岸", "did": "收走路引", "where": "界墙", "consequence": "失去身份"}]
                  + [{"who": "a", "did": "b"}] * (MAX_EVENTS + 3),
        "state_changes": [{"target": "路引", "from": "林澈持有", "to": "霍岸持有"},
                          {"target": "", "from": "x", "to": "y"}],
        "knowledge": [{"who": "霍岸", "learned": "涂改出自宁秋鹜", "how": "比对墨迹"}],
        "foreshadow_touched": [{"text": "残图", "action": "ADVANCE"},
                               {"text": "路人伏笔", "action": "??"}],
        "hook": "他叫出了真名。",
        "conflicts": [{"type": "knowledge_leak", "detail": "霍岸本不该知道"}],
    }
    d = normalize_chapter_digest(raw, chapter_no=7)
    assert d["chapter_no"] == 7
    assert len(d["one_line"]) <= 80
    assert len(d["events"]) == MAX_EVENTS
    assert d["state_changes"] == [{"target": "路引", "from": "林澈持有", "to": "霍岸持有"}]
    assert d["foreshadow_touched"][0]["action"] == "advance"
    assert d["foreshadow_touched"][1]["action"] == "advance"    # 非法动作兜底
    assert d["conflicts"][0]["type"] == "knowledge_leak"


def test_normalize_chapter_digest_bad_shape():
    d = normalize_chapter_digest(None, chapter_no=1)
    assert d["events"] == [] and d["one_line"] == ""
    assert normalize_chapter_digest({"events": "x"})["events"] == []


def test_normalize_volume_digest():
    raw = {"arc": "林澈从零里到界墙", "characters": [
        {"name": "林澈", "where": "界墙", "condition": "旧伤未愈",
         "knows": ["自己失忆"], "does_not_know": ["自己的真名"], "holds": [], "stance": "躲避"},
    ], "rules": ["路引决定能否穿里"], "long_foreshadows": [{"text": "涂改", "status": "open"}],
           "turning_points": ["第 7 章 路引被收走"], "ending_state": "失去路引"}
    d = normalize_volume_digest(raw, volume_no=1, range_from=1, range_to=8)
    assert d["range"] == [1, 8] and d["volume_no"] == 1
    assert d["characters"][0]["does_not_know"] == ["自己的真名"]
    assert d["long_foreshadows"][0]["status"] == "open"


# ---------------------------------------------------------------- 渲染注入
def test_render_chapter_digest_has_facts_not_narrative():
    d = normalize_chapter_digest({
        "one_line": "路引被收走",
        "events": [{"who": "霍岸", "did": "收走路引", "where": "界墙", "consequence": "失去身份"}],
        "state_changes": [{"target": "路引", "from": "林澈", "to": "霍岸"}],
        "hook": "他叫出了真名",
        "summary_md": "（人读版长叙事，不应出现在写手素材里）",
    }, chapter_no=7)
    text = render_chapter_digest(d)
    assert "霍岸收走路引" in text and "路引 林澈→霍岸" in text
    assert "钩子：他叫出了真名" in text
    assert "人读版" not in text        # summary_md 不进写手素材（避免复述）


def test_render_prior_bundle_layers_and_budget():
    bundle = {
        "book": {"arc": "全书弧线", "ending_state": "当前状态"},
        "volume": {"arc": "本卷弧线", "characters": [
            {"name": "林澈", "where": "界墙", "knows": ["失忆"], "does_not_know": ["真名"], "holds": []}]},
        "recent": [{"chapter_no": 6, "one_line": "第六章", "hook": "钩一"},
                   {"chapter_no": 7, "one_line": "第七章", "hook": "钩二"}],
    }
    text = render_prior_bundle(bundle)
    assert "【前情 · 全书】" in text and "【前情 · 本卷】" in text and "【前情 · 最近章节】" in text
    assert "**不知道**：真名" in text          # 信息边界必须显式
    assert text.index("全书") < text.index("本卷") < text.index("最近章节")
    assert len(render_prior_bundle(bundle, budget=120)) <= 120
    assert render_prior_bundle({}) == ""


# ---------------------------------------------------------------- 冲突检测
def test_detect_conflicts_knowledge_leak():
    vol = {"characters": [{"name": "林澈", "does_not_know": ["自己的真名"], "holds": []}]}
    ch = {"knowledge": [{"who": "林澈", "learned": "自己的真名是沈砚", "how": "听到"}]}
    got = detect_conflicts(ch, vol)
    assert got and got[0]["type"] == "knowledge_leak"
    assert "沈砚" in got[0]["detail"]


def test_detect_conflicts_item_handover():
    vol = {"characters": [{"name": "林澈", "holds": ["路引"], "does_not_know": []}]}
    ch = {"state_changes": [{"target": "路引", "from": "林澈", "to": "霍岸持有"}]}
    got = detect_conflicts(ch, vol)
    assert got and got[0]["type"] == "item_handover"
    # 迁移记到本人名下不算冲突
    ok = detect_conflicts({"state_changes": [{"target": "路引", "from": "林澈", "to": "林澈持有"}]}, vol)
    assert ok == []


def test_detect_conflicts_quiet_when_nothing_matches():
    assert detect_conflicts({"knowledge": []}, {}) == []
    assert detect_conflicts({}, {"characters": []}) == []


def test_has_digest():
    assert has_digest({"digest_json": json.dumps({"one_line": "x"})}) is True
    assert has_digest({"digest_json": "{}"}) is False
    assert has_digest({"digest_json": "not-json"}) is False
    assert has_digest({}) is False


# ---------------------------------------------------------------- 服务层：前情装配
@pytest.mark.asyncio
async def test_chapter_prior_assembles_layers():
    from app.db.repo import Repo
    from app.services.service import SimulationService

    repo = Repo(use_db=False)
    svc = SimulationService(repo)
    await repo.save_book({"id": "b1", "title": "T", "genre": "g", "status": "planned",
                          "cover_init": "T", "synopsis": "", "volume_size": 2})
    for i in (1, 2, 3):
        await repo.save_chapter({"id": f"c{i}", "book_id": "b1", "title": f"第 {i} 章",
                                 "summary": "", "order_no": i, "tone": "action",
                                 "word_target": 1000,
                                 "digest_json": json.dumps({"chapter_no": i,
                                                            "one_line": f"第{i}章概要"},
                                                           ensure_ascii=False)})
    await repo.save_book_digest({"id": "book-b1", "book_id": "b1", "level": "book",
                                "range_from": 1, "range_to": 3,
                                "digest_json": json.dumps({"arc": "全书弧线"})})
    await repo.save_book_digest({"id": "vol-b1-1-2", "book_id": "b1", "level": "volume",
                                "range_from": 1, "range_to": 2,
                                "digest_json": json.dumps({"arc": "第一卷弧线"})})
    await repo.save_book_digest({"id": "vol-b1-3-3", "book_id": "b1", "level": "volume",
                                "range_from": 3, "range_to": 3,
                                "digest_json": json.dumps({"arc": "第二卷弧线"})})

    bundle = await svc.chapter_prior("c3")
    assert bundle["book"]["arc"] == "全书弧线"
    # 第 3 章属第 2 卷（volume_size=2 → 1-2 / 3-3）
    assert bundle["volume"]["arc"] == "第二卷弧线"
    assert bundle["volume_no"] == 2 and bundle["volume_range"] == [3, 3]
    assert [d["one_line"] for d in bundle["recent"]] == ["第2章概要", "第1章概要"]
    assert "【前情 · 本卷】" in bundle["text"]
    assert bundle["has_any"] is True


@pytest.mark.asyncio
async def test_set_volume_config_validates():
    from app.db.repo import Repo
    from app.errors import InvalidActionError
    from app.services.service import SimulationService

    repo = Repo(use_db=False)
    svc = SimulationService(repo)
    await repo.save_book({"id": "b2", "title": "T", "genre": "g", "status": "planned",
                          "cover_init": "T", "synopsis": ""})
    for i in (1, 2, 3, 4):
        await repo.save_chapter({"id": f"d{i}", "book_id": "b2", "title": f"第 {i} 章",
                                 "summary": "", "order_no": i, "tone": "action",
                                 "word_target": 1000})
    got = await svc.set_volume_config("b2", volume_size=2)
    assert [(b["from"], b["to"]) for b in got["bounds"]] == [(1, 2), (3, 4)]
    got2 = await svc.set_volume_config("b2", volumes=[{"from": 1, "to": 3}, {"from": 4, "to": 4}])
    assert [(b["from"], b["to"]) for b in got2["bounds"]] == [(1, 3), (4, 4)]
    with pytest.raises(InvalidActionError):
        await svc.set_volume_config("b2", volumes=[{"from": 1, "to": 3}, {"from": 3, "to": 4}])
