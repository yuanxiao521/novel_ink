"""S3 全局张力派生单测（global_view.py）。

纯函数：喂章/场景/回合归档/伏笔，断言曲线、诊断规则与评分。
对应《写作优化方案-S3全局结构.md》§5 验收五条。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.engine.global_view import (  # noqa: E402
    build_global_view,
    scene_tension,
)


def _book(tensions, prose=True, archives=True):
    """构造：每章 1 场景，场景张力取自 tensions；prose/archives 可关。"""
    chapters, scenes, arch = [], [], {}
    for i, t in enumerate(tensions):
        cid, sid = "ch%d" % (i + 1), "sc%d" % (i + 1)
        chapters.append({"id": cid, "order_no": i + 1, "title": "第%d章" % (i + 1)})
        scenes.append({"id": sid, "chapter_id": cid,
                       "final_prose": ("正文" * 30 if prose else "")})
        if archives:
            arch[sid] = [{"tension": t, "tension_trend": "up"}]
    return chapters, scenes, arch


def _rules(view):
    return [d["rule"] for d in view["diagnostics"]]


def test_curve_and_peak_chapter():
    """派生：3 章张力 20/50/80 → 曲线 [20,50,80]，峰值在第 3 章。"""
    chapters, scenes, arch = _book([20, 50, 80])
    view = build_global_view(chapters, scenes, arch, [])
    assert [c["tension_avg"] for c in view["curve"]] == [20.0, 50.0, 80.0]
    assert view["summary"]["mean"] == 50.0
    assert view["summary"]["peak_chapter"]["order_no"] == 3


def test_r1_flat_book_deducts_and_score():
    """R1 平铺：[36,37,38,50] 波动小且均值低 → 命中 R1，评分 80（唯一扣分项）。"""
    chapters, scenes, arch = _book([36, 37, 38, 50])
    view = build_global_view(chapters, scenes, arch, [])
    assert _rules(view) == ["R1"], _rules(view)
    assert view["structure_score"] == 80
    assert "缺起伏" in view["diagnostics"][0]["detail"]


def test_r3_climax_missing_when_peak_in_front():
    """R3 高潮缺失：峰值在第 1 章（后 1/3 之外）→ 命中。"""
    chapters, scenes, arch = _book([80, 40, 30, 20])
    view = build_global_view(chapters, scenes, arch, [])
    assert "R3" in _rules(view)


def test_r2_consecutive_no_rise():
    """R2 无上升：连续 ≥3 章不升 → 命中，并报出**整段**连续区间（不是只报最近 3 章）。"""
    chapters, scenes, arch = _book([50, 50, 48, 47])
    view = build_global_view(chapters, scenes, arch, [])
    r2 = [d for d in view["diagnostics"] if d["rule"] == "R2"]
    assert r2 and r2[0]["evidence"] == [1, 2, 3, 4]


def test_r4_breath_present_suppresses_flag():
    """R4 无喘息：峰值后有章回落 ≥15% → 不报。"""
    chapters, scenes, arch = _book([30, 60, 40, 45])
    view = build_global_view(chapters, scenes, arch, [])
    assert "R4" not in _rules(view)


def test_r5_overdue_foreshadow_lands_in_network():
    """R5 伏笔逾期：期望回收章已过且未闭环 → 进 overdue 并命中 R5。"""
    chapters, scenes, arch = _book([40, 50, 60])
    fs = [{"id": "f1", "type": "plot", "text": "夹层里的半句剑诀", "status": "buried",
           "buried_scene": "sc1", "expected_close_scene": "sc1", "related_char_ids": []}]
    view = build_global_view(chapters, scenes, arch, fs)
    assert view["foreshadows"]["overdue"][0]["id"] == "f1"
    assert view["foreshadows"]["edges"][0] == {"from_chapter": 1, "to_chapter": 1,
                                               "foreshadow_id": "f1"}
    assert "R5" in _rules(view)


def test_r6_imbalance_and_r7_barren():
    """R6 埋收失衡（6 埋 1 收）；R7 有正文无张力记录（回退模式）。"""
    chapters, scenes, arch = _book([40, 50, 60], archives=False)
    fs = [{"id": "f%d" % i, "status": "buried", "text": "伏笔%d" % i} for i in range(6)]
    fs.append({"id": "done", "status": "closed", "text": "已收"})
    view = build_global_view(chapters, scenes, arch, fs)
    rules = _rules(view)
    assert "R6" in rules and "R7" in rules
    assert view["barren_scenes"] == ["sc1", "sc2", "sc3"]


def test_guardrails_single_chapter_and_no_tension():
    """护栏：单章 / 无张力记录时不报 R1-R4（样本不足不下结论），且不把 0 当平淡。"""
    chapters, scenes, arch = _book([20])
    view = build_global_view(chapters, scenes, arch, [])
    assert _rules(view) == [] and view["structure_score"] == 100

    chapters2, scenes2, _ = _book([10, 10, 10], archives=False)
    view2 = build_global_view(chapters2, scenes2, {}, [])
    assert not {"R1", "R2", "R3", "R4"} & set(_rules(view2))

    assert scene_tension([{"tension": 0, "tension_trend": "flat"}])["has"] is False
    assert scene_tension([])["has"] is False


def test_r7_ignores_scenes_without_prose():
    """R7 只算"有正文且无张力"的场景：没正文的裸场景不冤枉。"""
    chapters = [{"id": "ch1", "order_no": 1, "title": "一"}]
    scenes = [{"id": "s1", "chapter_id": "ch1", "final_prose": "正文"},
              {"id": "s2", "chapter_id": "ch1", "final_prose": ""}]
    view = build_global_view(chapters, scenes, {}, [])
    assert view["barren_scenes"] == ["s1"]


def test_score_floor_never_negative():
    """评分下限 0：多规则同时命中也不会出现负分。"""
    chapters, scenes, arch = _book([80, 40, 35, 30, 32, 31])
    fs = [{"id": "f%d" % i, "status": "buried", "text": "x", "buried_scene": "sc1",
           "expected_close_scene": "sc1"} for i in range(6)]
    view = build_global_view(chapters, scenes, arch, fs)
    assert 0 <= view["structure_score"] <= 100
    assert len(_rules(view)) >= 3

# ---------------------------------------------------------------- 服务装配（step3）
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture
async def svc():
    """内存态服务：1 书 1 章 1 场景 + 1 个带 2 条回合归档的 sim。"""
    from app.db.repo import Repo
    from app.schemas.models import SimulationState, TurnArchive
    from app.services.service import SimulationService

    repo = Repo(use_db=False)
    service = SimulationService(repo)
    await repo.save_book({"id": "book-g", "title": "张力测试书"})
    await repo.save_chapter({"id": "c1", "book_id": "book-g", "title": "第一章", "order_no": 1})
    await repo.save_scene({"id": "s1", "chapter_id": "c1", "title": "开场",
                           "final_prose": "正文" * 20})
    sim = SimulationState(scenario="t", book_id="book-g", chapter_id="c1", scene_id="s1")
    sim.turn_archives = [TurnArchive(turn=1, tension=30, tension_trend="up"),
                         TurnArchive(turn=2, tension=55, tension_trend="up")]
    await repo.save("sim-1", sim)
    return service


@pytest.mark.asyncio
async def test_service_assembles_tension_from_sim_archives(svc):
    """装配：张力从 sim 的 turn_archives 流进曲线（均值/峰值/回合数都对）。

    同时守内存态回归：场景未显式带 cursor_pos 时，list_scenes_by_chapter 也必须看得到
    （旧实现把 cursor_pos 当类型标记 → 整片场景不可见，S3/概览会丢场景）。
    """
    assert [s["id"] for s in await svc.repo.list_scenes_by_chapter("c1")] == ["s1"]
    view = await svc.book_global_view("book-g")
    assert view["book_id"] == "book-g" and view["sims"] == 1
    ch = view["curve"][0]
    assert (ch["tension_avg"], ch["tension_peak"], ch["turns"]) == (42.5, 55.0, 2)
    assert view["summary"]["mean"] == 42.5
    assert view["curve"][0]["status"] == "done"


@pytest.mark.asyncio
async def test_service_marks_barren_scene_when_no_archives(svc):
    """护栏：有正文但 sim 无归档 → 不编造张力，如实进 barren + R7。"""
    from app.schemas.models import SimulationState

    await svc.repo.save("sim-2", SimulationState(scenario="t", book_id="book-g",
                                                 chapter_id="c1", scene_id="s1"))
    view = await svc.book_global_view("book-g")
    assert view["curve"][0]["tension_avg"] is None      # 不按 0 计入
    assert view["barren_scenes"] == ["s1"]
    assert "R7" in [d["rule"] for d in view["diagnostics"]]

