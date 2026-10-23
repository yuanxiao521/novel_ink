"""节奏闸门回归（v1.73）：默认 1×1、信号加厚、护栏压薄、LLM 建议不得越过闸门。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.schemas import models  # noqa: E402
from app.services.engine.pacing import (  # noqa: E402
    MAX_BEHAVIORS, MAX_EXCHANGES, Pace, decide_pace, normalize_llm_pace,
)


def _sim(*, blocks: int = 0, trend: str = "flat", conflict: bool = False,
         pending: bool = False, ended: bool = False, converged: bool = False):
    sim = models.SimulationState(
        id="s-pace", scenario="test", book_id="b", chapter_id="c", scene_id="s",
        characters={"a": models.CharacterCard(id="a", name="甲", summary="主角")},
    )
    sim.guard.last_turn_blocks = blocks
    sim.director.tension_trend = trend
    sim.director.raise_request.pending = pending
    sim.ended = ended
    sim.director.converged = converged
    if conflict:
        from app.services.engine.world import WorldEngine
        WorldEngine(sim).append_event(
            type_=models.EventType.action, source=models.EventSource.character,
            actor="a", payload={"kind": "conflict", "text": "拍案而起"})
    return sim


# ---- LLM 建议规范化 -------------------------------------------------------
def test_normalize_llm_pace_clamps_and_rejects():
    p = normalize_llm_pace({"exchanges": 99, "behaviors_max": -3, "mode": "weird"})
    assert p == Pace(exchanges=MAX_EXCHANGES, behaviors_max=1, mode="normal")
    assert normalize_llm_pace("乱来的") is None
    assert normalize_llm_pace(None) is None
    assert normalize_llm_pace({"exchanges": 2, "behaviors_max": 2}) == Pace(2, 2, "normal")


# ---- 默认：无任何信号 = 1×1（与 v1.72 行为一致） --------------------------
def test_default_is_thin():
    p = decide_pace(_sim(), [])
    assert (p.exchanges, p.behaviors_max) == (1, 1)
    assert p.mode == "normal"


# ---- 加厚信号：hard beat 未达成 / 冲突 / 张力上行 -------------------------
def test_hard_beat_thickens():
    beats = [{"id": "b1", "level": "hard", "text": "保险柜被打开", "done": False}]
    p = decide_pace(_sim(), beats)
    assert p.exchanges >= 2 and p.behaviors_max >= 2, p.reason


def test_conflict_and_tension_thicken():
    p = decide_pace(_sim(conflict=True, trend="up"), [])
    assert p.exchanges >= 2 and p.behaviors_max >= 2
    # 不越上限
    p2 = decide_pace(_sim(conflict=True, trend="up"), [{"id": "b1", "level": "hard", "text": "x"}])
    assert p2.exchanges <= MAX_EXCHANGES and p2.behaviors_max <= MAX_BEHAVIORS


# ---- 压薄信号一票否决：护栏拦截 / 举手 / 收束 ------------------------------
def test_guard_blocks_thin_despite_llm_request():
    p = decide_pace(_sim(blocks=2), [{"id": "b1", "level": "hard", "text": "x"}],
                    llm_pace={"exchanges": 3, "behaviors_max": 3})
    assert (p.exchanges, p.behaviors_max) == (1, 1), "护栏拦截必须压薄（一票否决）"


def test_raise_pending_thins():
    p = decide_pace(_sim(pending=True), [{"id": "b1", "level": "hard", "text": "x"}],
                    llm_pace={"exchanges": 3, "behaviors_max": 3})
    assert (p.exchanges, p.behaviors_max) == (1, 1)


def test_converged_or_ended_thins():
    assert decide_pace(_sim(ended=True), []).exchanges == 1
    assert decide_pace(_sim(converged=True), []).exchanges == 1


# ---- volley 快速通道：只给纯对话过场 ---------------------------------------
def test_volley_only_for_quiet_passage():
    # 无节拍无冲突 → 保留 volley
    p = decide_pace(_sim(), [], llm_pace={"mode": "volley"})
    assert p.mode == "volley" and (p.exchanges, p.behaviors_max) == (1, 1)
    # 有冲突/硬节拍 → 打回 normal（硬戏必须走逐角色涌现）
    p2 = decide_pace(_sim(conflict=True), [], llm_pace={"mode": "volley"})
    assert p2.mode == "normal"
    p3 = decide_pace(_sim(), [{"id": "b1", "level": "hard", "text": "x"}],
                     llm_pace={"mode": "volley"})
    assert p3.mode == "normal"
    # 人数 >3 → volley 只保 3 人内对话，退回 normal
    p4 = decide_pace(_sim(), [], llm_pace={"mode": "volley"}, max_actors=5)
    assert p4.mode == "normal"
