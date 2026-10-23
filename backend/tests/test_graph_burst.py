"""graph 级连拍集成（v1.73）：pace.exchanges=2 时一拍真实跑 2 个来回 → 时间线 2 节点。

不走 LLM：CharacterEngine.decide_multi 的回退脚本出单条，但 exchanges=2 会跑两轮 →
每角色 2 条行为（同脚本内容），足以验证"拍内循环 + exchange 标记 + 分组归档"的链路。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from app.schemas import models  # noqa: E402


@pytest.mark.asyncio
async def test_graph_two_exchanges_two_archives(monkeypatch):
    from app.services.engine.director import PlanCfg
    from app.services.engine.graph import build_graph

    sim = models.SimulationState(
        id="s-g", scenario="betrayal_night", book_id="b", chapter_id="c", scene_id="s",
        characters={
            "a": models.CharacterCard(id="a", name="甲", summary="主角"),
            "b": models.CharacterCard(id="b", name="乙", summary="对手"),
        },
        action_order=["a", "b"],
    )
    # 走**真实闸门路径**：hard beat 未达成 → pacing 加厚 exchanges>=2（LLM 不可用也一样）
    plan_cfg = PlanCfg(beats=[{"id": "b1", "level": "hard", "text": "保险柜被打开"}])

    # LLM 不可用 → 全部走确定性回退脚本（decide_fn 是同步函数，与场景文件一致）
    def decide_fn(ctx):
        return {"kind": "dialogue", "text": "回退台词", "new_fact": ""}

    async def converge_fn(sim):
        return ""

    g = build_graph(plan_cfg, decide_fn, converge_fn, guard_retry=1, max_turns=10)
    await g.ainvoke({"sim": sim, "last_main_actor": None})

    # 一拍 2 来回 × 2 角色 = 4 条角色行为（回退脚本每轮各一条）
    char_texts = [e.payload.get("text") for e in sim.events if e.source.value == "character"]
    assert len(char_texts) == 4, f"2 来回应有 4 条角色行为，实际 {len(char_texts)}: {char_texts}"
    # 事件带 exchange 标记 1,2
    exs = {e.payload.get("exchange") for e in sim.events if e.source.value == "character"}
    assert exs == {1, 2}

    # 归档按来回拆 → 2 条
    from app.services.service import SimulationService

    svc = SimulationService.__new__(SimulationService)   # 只要静态方法
    svc._archive_turn(sim, 0)
    assert [a.exchange for a in sim.turn_archives] == [1, 2], \
        f"连拍 2 来回应归档 2 条（时间线 2 节点），实际 {[a.exchange for a in sim.turn_archives]}"


@pytest.mark.asyncio
async def test_graph_pace_gate_default_thin(monkeypatch):
    """无 pace（旧路径/无信号）→ 行为与 v1.72 完全一致：1 来回。"""
    from app.services.engine.director import PlanCfg
    from app.services.engine.graph import build_graph

    sim = models.SimulationState(
        id="s-g2", scenario="betrayal_night", book_id="b", chapter_id="c", scene_id="s",
        characters={"a": models.CharacterCard(id="a", name="甲", summary="主角")},
        action_order=["a"],
    )
    def decide_fn(ctx):
        return {"kind": "dialogue", "text": "一句", "new_fact": ""}

    async def converge_fn(sim):
        return ""

    g = build_graph(PlanCfg(), decide_fn, converge_fn, guard_retry=1, max_turns=10)
    await g.ainvoke({"sim": sim, "last_main_actor": None})
    char_texts = [e for e in sim.events if e.source.value == "character"]
    assert len(char_texts) == 1, "默认节奏必须与旧行为一致（1 来回 × 1 行为）"
