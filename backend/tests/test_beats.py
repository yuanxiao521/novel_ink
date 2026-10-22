"""场级必然事件 beats + 回合节流（v1.54）测试。

这两件事是"有约束的涌现"的落点：
  beats  → 情节不能被角色自由发挥改掉（hard 未达成会被收束闸门拦下）
  节流   → 每回合最多 3 人行动，主角固定在场，其余静默（治群像开会 + 成本线性涨）
都必须是 0-token 纯函数，才可复现、可解释、可单测。
"""
from __future__ import annotations

from app.services.engine.beats import (
    LEVELS, MIN_ANCHOR_HITS, beats_block, check_beats, hard_beats,
    parse_beats, record_status,
)
from app.services.engine.cast_roles import (
    MAX_ACTORS_PER_TURN, silent_cast, throttle_order,
)


# ---------------------------------------------------------------- beats 解析
def test_parse_beats_normalizes_levels_and_strings():
    raw = [
        "路引被霍岸收走",                                # 字符串 → hard，无锚点（退化为整句）
        {"text": "宁秋鹜承认残图在她手里", "level": "HARD", "anchors": ["宁秋鹜", "残图"]},
        {"text": "两人暂时结盟", "level": "soft"},
        {"text": "气氛变紧张", "level": "weird"},          # 非法档位 → hard
        {"text": "", "level": "hard"},                    # 空文本 → 丢弃
        "garbage-not-dict",
    ]
    got = parse_beats(raw)
    # 字符串项也当 beat 收（hard + 整句当锚点）；非 dict/非 str 的才跳过
    assert len(got) == 5
    assert got[0]["level"] == "hard" and got[0]["anchors"] == ["路引被霍岸收走"]
    assert got[1]["level"] == "hard" and got[1]["anchors"] == ["宁秋鹜", "残图"]
    assert got[2]["level"] == "soft"
    assert got[3]["level"] == "hard"          # 非法档位 → hard（保守：当约束处理）
    assert [b["id"] for b in got][:4] == ["b1", "b2", "b3", "b4"]


def test_parse_beats_bad_inputs():
    assert parse_beats(None) == []
    assert parse_beats({}) == []
    assert parse_beats("x") == []
    assert LEVELS == ("hard", "soft")


def test_hard_beats_filters_soft():
    beats = parse_beats([{"text": "A", "level": "hard"}, {"text": "B", "level": "soft"}])
    assert [b["text"] for b in hard_beats(beats)] == ["A"]


# ---------------------------------------------------------------- prompt 块
def test_beats_block_separates_hard_and_soft():
    beats = parse_beats([{"text": "路引被收走", "level": "hard"},
                         {"text": "两人结盟", "level": "soft"}])
    text = beats_block(beats)
    assert "必然事件" in text and "不可取消" in text
    assert "期望事件" in text
    assert text.index("路引被收走") < text.index("两人结盟")
    assert beats_block([]) == ""


# ---------------------------------------------------------------- 判定
def test_check_beats_requires_enough_anchor_hits():
    beats = parse_beats([{"text": "路引被霍岸收走", "level": "hard",
                          "anchors": ["霍岸", "路引", "收走"]}])
    # 只命中 1 个 → 判未达成（宁可漏报不假报）
    miss = check_beats(beats, ["霍岸走进来，没说话"])
    assert miss["missed"] == ["b1"] and miss["achieved"] == []
    # 命中 >= MIN_ANCHOR_HITS 个 → 达成
    hit = check_beats(beats, ["霍岸一把把路引收走了"])
    assert hit["achieved"] == ["b1"] and hit["missed"] == []
    assert len(hit["evidence"]["b1"]) >= MIN_ANCHOR_HITS


def test_check_beats_single_anchor_needs_one_hit():
    beats = parse_beats([{"text": "残图出现", "level": "hard", "anchors": ["残图"]}])
    assert check_beats(beats, ["她掏出残图"])["achieved"] == ["b1"]


def test_check_beats_ignores_soft_and_tolerates_spaces():
    beats = parse_beats([{"text": "结盟", "level": "soft"},
                         {"text": "宁秋鹜 交出 残图", "level": "hard",
                          "anchors": ["宁秋鹜", "残图"]}])
    res = check_beats(beats, ["宁秋鹜 冷淡 地 交出 了 残图"])
    assert res["judged"] == 1          # soft 不判
    assert res["achieved"] == ["b2"]


def test_record_status_carries_missed_texts():
    beats = parse_beats([{"text": "路引被收走", "level": "hard", "anchors": ["霍岸", "路引"]}])
    status = record_status(beats, check_beats(beats, ["什么都没发生"]))
    assert status["ok"] is False
    assert status["missed"] == ["b1"]
    assert status["missed_texts"] == ["路引被收走"]


# ---------------------------------------------------------------- 回合节流
def test_throttle_keeps_protagonist_and_fills_to_limit():
    roster = ["p", "a", "m1", "m2", "m3"]
    order = throttle_order(roster, protagonists=["p"], priority=["m3", "m1", "a"])
    assert len(order) == MAX_ACTORS_PER_TURN == 3
    assert order[0] == "p"              # 主角固定在场
    assert "m3" in order                # 按导演优先序补位
    # 输出顺序跟随 roster（可复现）
    assert order == [c for c in roster if c in order]


def test_throttle_without_protagonist_takes_priority_order():
    order = throttle_order(["x", "y", "z", "w"], protagonists=[], priority=["w", "y"])
    # 优先序决定"谁进名单"，输出顺序仍跟随 roster（可复现）
    assert order == ["x", "y", "w"]


def test_throttle_edge_cases():
    assert throttle_order([]) == []
    assert throttle_order(["a"]) == ["a"]
    assert throttle_order(["a", "b"], limit=5) == ["a", "b"]


def test_silent_cast_is_complement():
    assert silent_cast(["a", "b", "c"], ["a", "c"]) == ["b"]


# ---------------------------------------------------------------- 与场景配置的接线
def test_scene_plan_cfg_parses_beats():
    """plan_cfg_json.beats 必须能被读成 PlanCfg.beats（否则导演看不到约束）。"""
    import json

    from app.services.engine.director import PlanCfg
    from app.services.service import _scene_plan_cfg

    cfg = _scene_plan_cfg(json.dumps({"beats": [{"text": "路引被收走", "level": "hard",
                                                  "anchors": ["霍岸", "路引"]}]}), PlanCfg())
    assert len(cfg.beats) == 1 and cfg.beats[0]["level"] == "hard"
    assert _scene_plan_cfg("{}", PlanCfg()).beats == []

# ---------------------------------------------------------------- 收束闸门（可测的那一半）
def _sim_with(texts: list[str]):
    """造一个只带 events 的最小 sim（够闸门判定用）。"""
    from app.schemas.models import Event, EventSource, EventType, SimulationState

    sim = SimulationState(scenario="t", world={"scene_id": "s1", "title": "t"})
    for i, t in enumerate(texts):
        sim.events.append(Event(id=f"e{i}", turn=1, type=EventType.action,
                                source=EventSource.character, actor="霍岸",
                                payload={"text": t}))
    return sim


BEATS_ONE = [{"id": "b1", "text": "路引被霍岸收走", "level": "hard",
              "anchors": ["霍岸", "路引"]}]


def test_gate_passes_when_beats_achieved():
    from app.services.engine.beats import apply_beats_gate

    sim = _sim_with(["霍岸把路引收进袖里"])
    sim.director.converged = True
    status = apply_beats_gate(sim, BEATS_ONE)
    assert status["ok"] is True and status["missed"] == []
    assert sim.director.converged is True          # 不拦
    assert sim.scratch.get("beats_retry") is None   # 不计数


def test_gate_blocks_up_to_two_retries_then_allows_imperfect_converge():
    from app.services.engine.beats import apply_beats_gate

    sim = _sim_with(["什么都没发生"])
    # 第 1 次：拦下，retry=1
    sim.director.converged = True
    s1 = apply_beats_gate(sim, BEATS_ONE)
    assert s1["ok"] is False and sim.director.converged is False
    assert sim.scratch["beats_retry"] == 1
    assert "必然事件还没发生" in sim.director.hint.stage_prompt
    # 第 2 次：再拦一次，retry=2
    sim.director.converged = True
    apply_beats_gate(sim, BEATS_ONE)
    assert sim.scratch["beats_retry"] == 2
    # 第 3 次：补救用完 → 允许带缺陷收束（converged 保持 True，标记 exhausted）
    sim.director.converged = True
    s3 = apply_beats_gate(sim, BEATS_ONE)
    assert s3.get("exhausted") is True and s3["ok"] is False
    assert sim.director.converged is True
    assert sim.scratch["beats_status"]["missed_texts"] == ["路引被霍岸收走"]


def test_gate_noop_without_beats():
    from app.services.engine.beats import apply_beats_gate

    sim = _sim_with(["随便"])
    assert apply_beats_gate(sim, []) is None
    assert sim.scratch.get("beats_status") is None

# ---------------------------------------------------------------- LLM 围栏清洗（v1.54 顺手修）
def test_strip_fence_handles_markdown_and_prose():
    """模型爱用 ```json 围栏：不清洗就会被判 JSON 解析失败 → 导演软引导整条回退。"""
    from app.services.llm.client import _strip_fence

    fenced = chr(96) * 3 + "json" + chr(10) + chr(123) + chr(34) + "a" + chr(34) + ": 1" + chr(125) + chr(10) + chr(96) * 3
    assert _strip_fence(fenced) == chr(123) + chr(34) + "a" + chr(34) + ": 1" + chr(125)
    assert _strip_fence("先说明一句" + chr(10) + chr(123) + chr(34) + "a" + chr(34) + ": 1" + chr(125)) == chr(123) + chr(34) + "a" + chr(34) + ": 1" + chr(125)
    assert _strip_fence(chr(123) + chr(34) + "a" + chr(34) + ": 1" + chr(125)) == chr(123) + chr(34) + "a" + chr(34) + ": 1" + chr(125)
    assert _strip_fence("") == ""


