"""空壳回合归档的物理清理（v1.72）。

v1.70 只做到两件事：①新回合不再归档空壳 ②GET /history 展示层过滤。
DB 里的存量空壳一直留着 —— 实测 sim-d58495ab：turn_archives 20 条，其中 19 条
是 LLM 挂掉期间的空壳（无角色行动、无成文），作者看到 total_archived=20 但时间线
只有 1 个回合。本组用例锁死"物理清理"的正确性：
①判据与 /history 过滤一致 ②清理幂等 ③step/rewind 自愈 ④清理后落库。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from app.schemas import models  # noqa: E402
from app.services.service import (  # noqa: E402
    SimulationService, _archive_is_empty,
)


def _shell(turn: int) -> models.TurnArchive:
    return models.TurnArchive(turn=turn, tension=48, tension_trend="flat",
                              cls="type-info", summary="空壳", prose="", events=[])


def _real(turn: int) -> models.TurnArchive:
    return models.TurnArchive(
        turn=turn, tension=52, tension_trend="up", cls="type-action", summary="有行动",
        prose="他推门进去。",
        events=[{"id": f"E{turn}", "actor": "林澈", "kind": "action", "text": "推门"}])


def _sim(sim_id: str, archives: list[models.TurnArchive]) -> models.SimulationState:
    sim = models.SimulationState(
        id=sim_id, scenario="test", book_id="b-a", chapter_id="c-a", scene_id="s-a",
        characters={"林澈": models.CharacterCard(id="林澈", name="林澈", summary="主角")},
        action_order=["林澈"],
    )
    sim.turn_archives = list(archives)
    return sim


async def _repo(book_id: str = "b-p"):
    from app.db.repo import Repo

    repo = Repo(use_db=False)
    await repo.save_book({"id": book_id, "title": "空壳清理", "genre": "测试",
                          "status": "planned", "cover_init": "清", "synopsis": ""})
    await repo.save_chapter({"id": "c-p", "book_id": book_id, "title": "第 1 章",
                             "summary": "目标。", "order_no": 1, "tone": "action",
                             "word_target": 1000})
    await repo.save_scene({"id": "s-p", "chapter_id": "c-p", "title": "门口", "cursor_pos": 0})
    return repo


# ---------------------------------------------------------------- 判据
def test_empty_predicate_matches_history_filter():
    """空壳判据：无 events 且无 prose。只有 prose 或只有 events 都算**真实回合**。"""
    assert _archive_is_empty(_shell(1)) is True
    assert _archive_is_empty(_real(2)) is False
    only_prose = models.TurnArchive(turn=3, tension=50, tension_trend="flat",
                                    cls="type-info", summary="只有成文", prose="一句话。",
                                    events=[])
    assert _archive_is_empty(only_prose) is False, "有成文就不算空壳"
    only_ev = models.TurnArchive(turn=4, tension=50, tension_trend="flat",
                                 cls="type-action", summary="只有行动", prose="",
                                 events=[{"id": "E", "actor": "林澈", "kind": "action", "text": "走"}])
    assert _archive_is_empty(only_ev) is False, "有行动就不算空壳"


# ---------------------------------------------------------------- 纯函数清理
def test_prune_removes_only_shells_and_is_idempotent():
    sim = _sim("s-prune", [_shell(1), _real(2), _shell(3), _real(4)])
    removed = SimulationService.prune_empty_archives(sim)
    assert removed == 2
    assert [a.turn for a in sim.turn_archives] == [2, 4]
    # 幂等：再清一次什么都不删
    assert SimulationService.prune_empty_archives(sim) == 0
    assert [a.turn for a in sim.turn_archives] == [2, 4]


def test_prune_on_clean_sim_is_noop():
    sim = _sim("s-clean", [_real(1), _real(2)])
    assert SimulationService.prune_empty_archives(sim) == 0
    assert len(sim.turn_archives) == 2


# ---------------------------------------------------------------- 服务层：落库
@pytest.mark.asyncio
async def test_prune_archives_persists_and_reports_counts():
    repo = await _repo()
    svc = SimulationService(repo)
    sim = _sim("s-persist", [_shell(i) for i in range(1, 20)] + [_real(20)])
    sim.world.turn = 20
    await repo.save("s-persist", sim)

    out = await svc.prune_archives("s-persist")
    assert out["removed"] == 19 and out["kept"] == 1 and out["total_archived"] == 20
    assert out["turn"] == 20
    # 真落库了：重新读回来只剩 1 条
    again = await svc.get_state("s-persist")
    assert [a.turn for a in again.turn_archives] == [20]
    # 再点一次是幂等的（removed=0），不会报错
    out2 = await svc.prune_archives("s-persist")
    assert out2["removed"] == 0 and out2["kept"] == 1


# ---------------------------------------------------------------- 回退自愈
@pytest.mark.asyncio
async def test_rewind_prunes_legacy_shells():
    repo = await _repo()
    svc = SimulationService(repo)
    sim = _sim("s-rewind", [_shell(1), _shell(2), _real(3), _real(4)])
    sim.world.turn = 4
    await repo.save("s-rewind", sim)

    out = await svc.rewind("s-rewind", 3)
    # 回退到 T-3：T-4 被截断，T-1/T-2 的空壳被物理清掉
    assert [a.turn for a in out.turn_archives] == [3]
    assert out.world.turn == 3


# ---------------------------------------------------------------- 推演自愈
@pytest.mark.asyncio
async def test_step_prunes_legacy_shells(monkeypatch):
    """推进一回合就自愈：老空壳被清掉，新回合正常入档（不必等作者手动点清理）。"""
    from app.services import service as svc_mod

    repo = await _repo()
    svc = SimulationService(repo)
    sim = _sim("s-step", [_shell(i) for i in range(1, 5)])
    sim.scenario = "betrayal_night"   # step 要按场景取 plan_cfg / decide_fn
    sim.world.turn = 4
    await repo.save("s-step", sim)

    class _Graph:
        async def ainvoke(self, _input):
            # 模拟一个真实回合：角色行动 + 成文 + turn 前进
            target = sim
            target.world.turn += 1
            from app.services.engine.world import WorldEngine
            w = WorldEngine(target)
            w.append_event(type_=models.EventType.action, source=models.EventSource.character,
                           actor="林澈", payload={"kind": "action", "text": "他推门进去。"})
            w.append_event(type_=models.EventType.text, source=models.EventSource.system,
                           actor="写手", payload={"text": "门轴响了一声，屋里没人。"})
            return {"sim": target}

    monkeypatch.setattr(svc, "_graph", lambda *a, **k: _Graph())
    monkeypatch.setattr(svc, "_sim_plan_cfg", _fake_plan_cfg)
    monkeypatch.setattr(svc, "_sync_beliefs", _noop)

    await svc.step("s-step", 1)
    again = await svc.get_state("s-step")
    assert [a.turn for a in again.turn_archives] == [5], "老空壳应被清掉、只留新回合"
    assert again.turn_archives[0].prose


async def _fake_plan_cfg(*_a, **_k):
    from app.services.engine.director import PlanCfg

    return PlanCfg()


async def _noop(*_a, **_k):
    return None
