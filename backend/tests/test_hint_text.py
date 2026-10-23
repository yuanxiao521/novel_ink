"""导演提示文案可读性回归（v1.73d）：作者面板不露内部 id（char-xxx / g-xxx / fact key）。

作者实测截图：目标权重显示 "char-2c7372cb·g-362b6b +0.15"、信息曝光显示
「reviewer_id_adjacent_to_cheng_badge」——内部键直接示人，看不懂。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.schemas import models  # noqa: E402
from app.services.engine.director import DirectorEngine, PlanCfg  # noqa: E402


def _engine() -> DirectorEngine:
    sim = models.SimulationState(
        id="s-f", scenario="test", book_id="b", chapter_id="c", scene_id="s",
        characters={
            "char-abc": models.CharacterCard(
                id="char-abc", name="程未央", summary="配角",
                dynamic_goals=[models.Goal(id="g-362b6b", text="抢先查清档案流向", weight=0.4)],
            ),
        },
    )
    from app.services.engine.world import WorldEngine

    sim.world.facts.append(models.Fact(id="f-001", text="复核员编号与程未央徽章相邻", kind="env"))
    return DirectorEngine(sim, PlanCfg())


def test_fact_text_found_and_fallback():
    eng = _engine()
    assert eng._fact_text("f-001") == "复核员编号与程未央徽章相邻"
    assert eng._fact_text("f-none") == "f-none"


def test_char_and_goal_names():
    eng = _engine()
    assert eng._char_name("char-abc") == "程未央"
    assert eng._char_name("char-none") == "char-none"   # 查不到回落原 id（诚实）
    assert eng._goal_text("char-abc", "g-362b6b") == "抢先查清档案流向"
    assert eng._goal_text("char-abc", "g-none") == "g-none"


def test_friendly_expose_translates_or_prettifies():
    eng = _engine()
    line = eng._friendly_expose("程未央", "f-001")
    assert "复核员编号" in line and "f-001" not in line
    line2 = eng._friendly_expose("程未央", "reviewer_id_adjacent_to_cheng_badge")
    assert "reviewer_id_adjacent_to_cheng_badge" not in line2
    assert "reviewer id adjacent to cheng badge" in line2


def test_friendly_adjust_no_internal_ids():
    eng = _engine()
    line = eng._friendly_adjust("char-abc", "g-362b6b", 0.15, "回避偏差跟踪")
    assert "char-abc" not in line and "g-362b6b" not in line
    assert "程未央" in line and "抢先查清档案流向" in line and "+0.15" in line and "回避偏差跟踪" in line
