"""S1 世界状态：作者编辑 + 手改保护回归。

覆盖：
- B27a：作者手改（edited=True）的行，质检（state_deltas）不得覆盖；
- 作者手写/修改/删除世界状态（create/update/delete + key 生成 + 同 key 更新不插重）；
- B27b：chief_perceive 注入手改行并标注（不再过滤）。
"""
from __future__ import annotations

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

from app.db.repo import Repo
from app.errors import InvalidActionError
from app.services.service import SimulationService


@pytest_asyncio.fixture
async def svc():
    repo = Repo(use_db=False)
    service = SimulationService(repo)
    await repo.save_book({"id": "book-ws", "title": "状态书", "genre": "玄幻"})
    return service


async def test_author_create_marks_edited_and_generates_key(svc):
    """作者手写状态：edited=True、source=AUTHOR、key 按语义模板生成。"""
    r = await svc.create_world_state("book-ws", {
        "kind": "character", "name": "林尘", "attr": "境界", "value": "元婴初期",
    })
    rows = await svc.repo.list_world_states("book-ws")
    assert len(rows) == 1
    w = rows[0]
    assert w["id"] == r["id"]
    assert w["key"] == "char:林尘:境界"
    assert w["edited"] is True
    assert w["source_event_id"] == "AUTHOR"


async def test_author_create_same_key_updates_not_duplicates(svc):
    """同 (kind,key) 再写 → 更新既有行（复用 id、前值=旧值），不插重复。"""
    r1 = await svc.create_world_state("book-ws", {
        "kind": "item", "name": "屠龙刀", "attr": "携带", "value": "在",
    })
    r2 = await svc.create_world_state("book-ws", {
        "kind": "item", "name": "屠龙刀", "attr": "携带", "value": "遗失",
    })
    assert r1["id"] == r2["id"]
    rows = await svc.repo.list_world_states("book-ws")
    assert len(rows) == 1
    assert rows[0]["value"] == "遗失"
    assert rows[0]["previous_value"] == "在"


async def test_author_create_rejects_bad_kind_and_empty(svc):
    with pytest.raises(InvalidActionError):
        await svc.create_world_state("book-ws", {"kind": "vibes", "name": "x", "value": "y"})
    with pytest.raises(InvalidActionError):
        await svc.create_world_state("book-ws", {"kind": "item", "name": "", "value": "y"})
    with pytest.raises(InvalidActionError):
        await svc.create_world_state("book-ws", {"kind": "item", "name": "x", "value": "  "})


async def test_update_and_delete(svc):
    r = await svc.create_world_state("book-ws", {
        "kind": "time", "name": "全书", "attr": "当前日", "value": "第2天",
    })
    await svc.update_world_state(r["id"], {"value": "第3天"})
    rows = await svc.repo.list_world_states("book-ws")
    assert rows[0]["value"] == "第3天"
    assert rows[0]["previous_value"] == "第2天"
    assert rows[0]["edited"] is True

    await svc.delete_world_state(r["id"])
    assert await svc.repo.list_world_states("book-ws") == []

    with pytest.raises(InvalidActionError):
        await svc.update_world_state("ws-nope", {"value": "x"})


async def test_bookkeeping_protects_author_edited(svc):
    """B27a：质检 state_deltas 命中作者手改行 → 跳过不覆盖；未手改行正常更新。"""
    r = await svc.create_world_state("book-ws", {
        "kind": "character", "name": "林尘", "attr": "境界", "value": "元婴初期",
    })
    # 质检同时报两个 key：手改的 char:林尘:境界 应被保护；新的 numeric 应写入
    opinion = {
        "foreshadow_updates": [], "belief_deltas": [], "causal": [], "risks": [],
        "state_deltas": [
            {"kind": "character", "name": "林尘", "key": "char:林尘:境界",
             "value": "金丹期", "previous_value": ""},
            {"kind": "numeric", "name": "林尘", "key": "num:林尘:战力",
             "value": "1200", "previous_value": ""},
        ],
    }
    written = await svc._apply_verify_bookkeeping("book-ws", opinion)
    assert written == 1  # 只有 numeric 写入
    rows = {w["key"]: w for w in await svc.repo.list_world_states("book-ws")}
    assert rows["char:林尘:境界"]["value"] == "元婴初期"   # 手改未被 LLM 值覆盖
    assert rows["char:林尘:境界"]["edited"] is True
    assert rows["num:林尘:战力"]["value"] == "1200"


async def test_bookkeeping_still_updates_non_edited(svc):
    """对照：edited=False 的行质检正常更新（前值取库中当前值）。"""
    await svc.repo.save_world_state({
        "id": "ws-auto", "book_id": "book-ws", "kind": "character", "name": "林尘",
        "key": "char:林尘:境界", "value": "金丹期", "scene_no": 1,
        "source_event_id": "PROSE_VERIFY", "previous_value": "", "edited": False, "ts": 1,
    })
    opinion = {
        "foreshadow_updates": [], "belief_deltas": [], "causal": [], "risks": [],
        "state_deltas": [{"kind": "character", "name": "林尘", "key": "char:林尘:境界",
                          "value": "元婴初期", "previous_value": ""}],
    }
    written = await svc._apply_verify_bookkeeping("book-ws", opinion)
    assert written == 1
    w = (await svc.repo.get_world_state("ws-auto"))
    assert w["value"] == "元婴初期"
    assert w["previous_value"] == "金丹期"


async def test_chief_perceive_includes_author_edited(svc):
    """B27b：作者手改行必须进主笔感知并标注「以此为准」（此前被过滤=语义反了）。"""
    await svc.create_world_state("book-ws", {
        "kind": "character", "name": "林尘", "attr": "境界", "value": "元婴初期",
    })
    await svc.repo.save_world_state({
        "id": "ws-auto2", "book_id": "book-ws", "kind": "term", "name": "通用",
        "key": "term:通用:灵石", "value": "货币单位", "scene_no": 0,
        "source_event_id": "PROSE_VERIFY", "previous_value": "", "edited": False, "ts": 1,
    })
    block = await svc.chief_perceive("book-ws")
    assert "世界状态现状" in block
    assert "元婴初期" in block and "作者手改，以此为准" in block
    assert "货币单位" in block
