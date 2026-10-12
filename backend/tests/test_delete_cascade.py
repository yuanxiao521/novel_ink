"""B36 回归：删书/删章/删场景必须真的删掉（含挂在它们上的推演快照）。

背景（实测）：`simulation.book_id/chapter_id/scene_id` 是 NO ACTION 外键，而"四层级联"
只配在 ORM relationship 上 → 只要场景推演过，删书就会被 `simulation_scene_id_fkey` 挡下；
旧 `_delete_row` 把 IntegrityError 吞成一条 WARNING 并正常返回 → `DELETE /books/{id}`
回 204 但数据还在（书架残留 5 本冒烟书，两条日志坐实）。
修复：删书/章/场景前先 `delete_sims_for()`，且 `_delete_row` 失败改抛 RepoError。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from app.config import settings  # noqa: E402

pytestmark = pytest.mark.asyncio


def _mk_state(bid: str, cid: str, sid: str):
    from app.schemas.models import SimulationState, WorldState

    return SimulationState(scenario="betrayal_night", book_id=bid, chapter_id=cid, scene_id=sid,
                           world=WorldState(scene_id=sid, title="场景一"))


async def _seed(repo, bid: str, cid: str, sid: str):
    await repo.save_book({"id": bid, "title": "删除回归", "genre": "悬疑"})
    await repo.save_chapter({"id": cid, "book_id": bid, "title": "第一章", "order_no": 1})
    await repo.save_scene({"id": sid, "chapter_id": cid, "title": "场景一", "cursor_pos": 1})
    await repo.save("sim-" + bid, _mk_state(bid, cid, sid))


# ---------------------------------------------------------------- 内存态
async def test_delete_book_memory_cascades_and_clears_sims():
    from app.db.repo import Repo

    repo = Repo(use_db=False)
    bid, cid, sid = "book-delm", "chapter-delm", "scene-delm"
    await _seed(repo, bid, cid, sid)
    # 场景子表（批注）也挂一个，验证级联子表一起清
    await repo.save_prose_note({"id": "note-delm", "scene_id": sid, "kind": "verifier",
                                "status": "pending", "suggestion": "改", "before": "", "after": ""})

    await repo.delete_book(bid)

    assert await repo.get_book(bid) is None
    assert await repo.list_chapters_by_book(bid) == []
    assert await repo.load("sim-" + bid) is None          # 快照随书清掉
    assert await repo.get_prose_note("note-delm") is None  # 场景子表一起清


async def test_delete_scene_memory_clears_its_sim():
    from app.db.repo import Repo

    repo = Repo(use_db=False)
    bid, cid, sid = "book-dels", "chapter-dels", "scene-dels"
    await _seed(repo, bid, cid, sid)
    await repo.delete_scene(sid)
    assert await repo.get_scene(sid) is None
    assert await repo.load("sim-" + bid) is None


# ---------------------------------------------------------------- DB 态（真库）
async def _db_ready() -> bool:
    if not settings.persist:
        return False
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(settings.dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        await eng.dispose()


@pytest.mark.skipif(not settings.persist, reason="persist=False，走内存态")
async def test_delete_book_db_really_deletes_with_sim():
    """B36 主用例：有 sim 的书必须能真删掉（修前抛 ForeignKeyViolation 且静默）。"""
    if not await _db_ready():
        pytest.skip("DB 不可用，跳过真实库测试")

    from app.db.repo import Repo

    repo = Repo()
    bid, cid, sid = "book-delreg", "chapter-delreg", "scene-delreg"
    for old in ("book-delreg",):  # 幂等：上一轮残留先清
        try:
            await repo.delete_book(old)
        except Exception:  # noqa: BLE001
            pass
    await _seed(repo, bid, cid, sid)

    await repo.delete_book(bid)  # 修前：这里被 simulation_scene_id_fkey 挡下

    assert await repo.get_book(bid) is None
    assert await repo.list_chapters_by_book(bid) == []
    assert await repo.load("sim-" + bid) is None
