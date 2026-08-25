"""run_demo：无 LLM Key 跑最小导演台闭环。

用法（在 backend/ 下）：
  uv run python -m app.run_demo            # 完整回合循环
  uv run python -m app.run_demo --turns 6  # 只跑 6 回合
"""
from __future__ import annotations

import argparse

from app.scenarios.betrayal_night import betrayal_night
from app.services.engine.graph import build_graph
from app.schemas import models
from app.config import settings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=0, help="跑固定回合数(0=至收束/举手/上限)")
    args = ap.parse_args()

    spec = betrayal_night()
    sim = models.SimulationState(
        scenario="betrayal_night",
        world=models.WorldState(scene_id="betrayal_night", title="背叛之夜 · 书房夜雨"),
    )
    sim.characters = spec["characters"]
    sim.world.facts = list(spec["initial_facts"])
    sim.director.ending_options = list(spec["plan_cfg"].ending_options)
    sim.action_order = [c for c in sim.characters]

    graph = build_graph(spec["plan_cfg"], spec["decide_fn"], spec["converge_fn"],
                        settings.guard_retry_max, settings.max_turns)

    target = args.turns or settings.max_turns
    print(f"=== 涌现式小说 Agent · 最小导演台闭环 ===")
    print(f"场景：背叛之夜 · 角色：{', '.join(sim.characters)}")

    for i in range(1, target + 1):
        graph.invoke({"sim": sim})
        t = sim.world.turn
        d = sim.director
        n_events = len(sim.events)
        flags = [f for f in sim.events if f.guard_flags]
        print(f"T{t:02d} 张力{d.tension:5.1f}({d.tension_trend:4}) 事件{n_events:3d} "
              f"拦截{sim.guard.total_intercepts:2d} 熔断{len(flags)} "
              f"{'举手!' if d.raise_request.pending else ''} "
              f"{'收束->'+d.ending_selected if d.converged else ''}")
        if d.raise_request.pending:
            print(f"      └─ 导演举手：{d.raise_request.reason}（demo 模拟作者『同意』继续）")
            d.raise_request.pending = False
        if d.converged:
            break

    # 收尾断言（供脚本判断）
    print("\n--- 校验摘要 ---")
    print(f"回合: {sim.world.turn}")
    print(f"事件日志: {len(sim.events)} 条 (append-only)")
    print(f"信念账本: {sum(len(v) for v in sim.beliefs.values())} 条")
    for cid, bl in sim.beliefs.items():
        for b in bl:
            print(f"  {cid}: 「{b.text}」 via {b.channel.value} <- {b.source_event_id}")
    print(f"张力趋势: {sim.director.tension_trend}")
    print(f"护栏拦截: {sim.guard.total_intercepts} 次, 熔断: {sum(sim.guard.fuse_counts.values())} 次")
    print(f"收束: {sim.director.converged} -> {sim.director.ending_selected}")
    print(f"举手: {sim.director.raise_request.reason if sim.director.raise_request.pending else '无'}")

    # 断言涌现+护栏具备
    assert sim.world.turn >= 1, "至少推进一回合"
    assert len(sim.events) > 0, "事件日志非空"
    assert any(b.source_event_id for v in sim.beliefs.values() for b in v), "信念需带溯源"
    print("\n✓ 涌现+护栏闭环 OK")


if __name__ == "__main__":
    main()