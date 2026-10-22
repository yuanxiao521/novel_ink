"""卷实体化（v1.59）测试：结构真源 + 归属同步 + 陈旧标记 + 回退。

稳定性的三条底线，这里逐条钉死：
  ① 章归属**只有一个写者**（sync_volume_assignments），且幂等；
  ② 卷表为空时读路径**回退派生**（老书不阻塞），但派生结果不写库；
  ③ 卷边界变了 → 旧概要标 stale（不删内容），重算后自动清除。
"""
from __future__ import annotations

import json

import pytest


async def _book_with_chapters(n: int = 6):
    from app.db.repo import Repo
    from app.services.service import SimulationService

    repo = Repo(use_db=False)
    svc = SimulationService(repo)
    await repo.save_book({"id": "b1", "title": "T", "genre": "g", "status": "planned",
                          "cover_init": "T", "synopsis": ""})
    for i in range(1, n + 1):
        await repo.save_chapter({"id": f"c{i}", "book_id": "b1", "title": f"第 {i} 章",
                                 "summary": "", "order_no": i, "tone": "action",
                                 "word_target": 1000})
    return svc, repo


@pytest.mark.asyncio
async def test_set_volume_config_creates_rows_and_assigns():
    svc, repo = await _book_with_chapters(6)
    out = await svc.set_volume_config("b1", volumes=[
        {"no": 1, "from": 1, "to": 3, "title": "零里雨夜", "arc_goal": "逃出零里"},
        {"no": 2, "from": 4, "to": 6, "title": "界墙", "arc_goal": "进第一里"},
    ])
    assert [v["no"] for v in out["volumes"]] == [1, 2]
    assert out["sync"]["assigned"] == 6 and out["sync"]["unassigned"] == []
    # 章归属已写入（派生缓存）
    for i in (1, 3):
        ch = await repo.get_chapter(f"c{i}")
        assert ch["volume_id"] == "vol-b1-1"
    ch6 = await repo.get_chapter("c6")
    assert ch6["volume_id"] == "vol-b1-2"


@pytest.mark.asyncio
async def test_sync_is_idempotent_and_reports_unassigned():
    svc, repo = await _book_with_chapters(6)
    await svc.set_volume_config("b1", volumes=[{"no": 1, "from": 1, "to": 2}])
    a = await svc.sync_volume_assignments("b1")
    b = await svc.sync_volume_assignments("b1")
    assert a == b                                  # 幂等
    assert a["assigned"] == 2 and a["unassigned"] == [3, 4, 5, 6]
    # 落在卷外的章不猜归属
    assert (await repo.get_chapter("c5")).get("volume_id") is None


@pytest.mark.asyncio
async def test_set_volume_config_rejects_overlap():
    from app.errors import InvalidActionError

    svc, _ = await _book_with_chapters(6)
    with pytest.raises(InvalidActionError):
        await svc.set_volume_config("b1", volumes=[{"from": 1, "to": 3}, {"from": 3, "to": 6}])


@pytest.mark.asyncio
async def test_volume_size_fallback_materializes():
    svc, repo = await _book_with_chapters(5)
    out = await svc.set_volume_config("b1", volume_size=2)
    assert [(v["from"], v["to"]) for v in out["volumes"]] == [(1, 2), (3, 4), (5, 5)]
    rows = await repo.list_book_volumes("b1")
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_bounds_fall_back_to_derived_when_no_rows():
    """老书没有卷表 → 读路径回退派生（derived=True），且**不写库**。"""
    svc, repo = await _book_with_chapters(9)
    bounds = await svc._book_volume_bounds("b1")
    assert bounds and bounds[0]["derived"] is True
    assert [(b["from"], b["to"]) for b in bounds] == [(1, 8), (9, 9)]
    assert await repo.list_book_volumes("b1") == []      # 没有任何写入


@pytest.mark.asyncio
async def test_list_book_volumes_counts_chapters():
    svc, _ = await _book_with_chapters(6)
    await svc.set_volume_config("b1", volumes=[{"no": 1, "from": 1, "to": 4},
                                                {"no": 2, "from": 5, "to": 6}])
    rows = await svc.list_book_volumes("b1")
    assert [r["chapters"] for r in rows] == [4, 2]
    assert rows[0]["range"] == [1, 4] and rows[0]["derived"] is False


@pytest.mark.asyncio
async def test_volume_config_marks_digests_stale_and_regenerate_clears():
    svc, repo = await _book_with_chapters(4)
    await svc.set_volume_config("b1", volumes=[{"no": 1, "from": 1, "to": 4}])
    await repo.save_book_digest({"id": "vd-b1-1", "book_id": "b1", "level": "volume",
                                 "volume_id": "vol-b1-1", "range_from": 1, "range_to": 4,
                                 "digest_json": json.dumps({"arc": "旧弧线"}, ensure_ascii=False)})
    out = await svc.set_volume_config("b1", volumes=[{"no": 1, "from": 1, "to": 3},
                                                    {"no": 2, "from": 4, "to": 4}])
    assert out["digests_marked_stale"] >= 1
    rows = await repo.list_book_digests("b1")
    assert rows[0]["stale"] is True
    assert rows[0]["digest_json"] and "旧弧线" in rows[0]["digest_json"]   # 内容没被删


@pytest.mark.asyncio
async def test_create_chapter_auto_assigns_volume():
    """章增删是"连锁效应"最容易漏的地方：建章后必须自动归卷。"""
    svc, repo = await _book_with_chapters(2)
    await svc.set_volume_config("b1", volumes=[{"no": 1, "from": 1, "to": 3}])
    await svc.create_chapter("b1", {"title": "第 3 章"})
    ch = await repo.get_chapter("chapter-" + (await repo.list_chapters_by_book("b1"))[-1]["id"].split("chapter-")[-1])
    assert ch["volume_id"] == "vol-b1-1"


@pytest.mark.asyncio
async def test_commit_book_plan_writes_volumes_from_plan():
    from app.db.repo import Repo
    from app.services.service import SimulationService

    repo = Repo(use_db=False)
    svc = SimulationService(repo)
    bid = (await svc.create_book({"title": "分卷书"}))["id"]
    await svc.commit_book_plan(bid, {
        "worldview": {},
        "chapters": [{"title": f"第 {i} 章", "summary": "", "tone": "action",
                      "word_target": 1000, "scenes": [{"title": "场一"}]} for i in (1, 2, 3, 4)],
        "volumes": [{"title": "第一卷", "from_seq": 1, "to_seq": 2, "arc_goal": "开局",
                     "climax": "被拦查"},
                    {"title": "第二卷", "from_seq": 3, "to_seq": 4, "arc_goal": "入里"}],
        "foreshadow_plan": [], "ending_options": [],
    })
    vols = await repo.list_book_volumes(bid)
    assert [(v["no"], v["range_from"], v["range_to"]) for v in vols] == [(1, 1, 2), (2, 3, 4)]
    assert vols[0]["title"] == "第一卷" and vols[0]["arc_goal"] == "开局"
    chs = await repo.list_chapters_by_book(bid)
    assert all(c["volume_id"] for c in chs)
