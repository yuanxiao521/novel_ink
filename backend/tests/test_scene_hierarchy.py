"""场景四层链路测试（真实 DB）：seed 目录 → 按 scene 建 sim → step → resume（AC-1/2/3）。

依赖 app.db.seed 已执行（book-rain / chapter-01 / scene-betrayal-night）。
DB 不可用时测试跳过（persist=False 或连接失败）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

pytestmark = pytest.mark.asyncio

from app.config import settings  # noqa: E402


async def _db_ready() -> bool:
    if not settings.persist:
        return False
    try:
        from app.db.engine import get_async_engine
        from sqlalchemy import text

        async with get_async_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest_asyncio.fixture
async def repo():
    from app.db.repo import Repo

    return Repo()


@pytest_asyncio.fixture
async def svc(repo):
    from app.services.service import SimulationService

    return SimulationService(repo)


@pytest.mark.skipif(not settings.persist, reason="persist=False，走内存态")
async def test_ac1_books_list(svc):
    if not await _db_ready():
        pytest.skip("DB 不可用，跳过真实库测试")
    books = await svc.list_books()
    ids = [b["id"] for b in books]
    if "book-rain" not in ids:
        pytest.skip("未执行 seed，跳过")
    assert any(b["title"] == "雨夜书房" for b in books)


@pytest.mark.skipif(not settings.persist, reason="persist=False，走内存态")
async def test_ac2_start_new_sim(svc):
    """POST /sims 语义层：scene_id 建新 sim，turn==0，resumed False。"""
    if not await _db_ready():
        pytest.skip("DB 不可用，跳过真实库测试")
    result = await svc.start(scene_id="scene-betrayal-night", resume=False)
    assert result["resumed"] is False
    sid = result["sim_id"]
    sim = await svc.get_state(sid)
    assert sim.world.turn == 0
    assert sim.scene_id == "scene-betrayal-night"
    assert set(sim.characters.keys()) == {"chenmo", "liwen", "zhoushen"}
    assert len(sim.world.facts) >= 1


@pytest.mark.skipif(not settings.persist, reason="persist=False，走内存态")
async def test_ac3_resume_latest(svc):
    """断线恢复：建 sim → step 3（举手暂停）→ 同 scene resume → 返回原 sim_id，turn==3。"""
    if not await _db_ready():
        pytest.skip("DB 不可用，跳过真实库测试")

    result = await svc.start(scene_id="scene-betrayal-night", resume=False)
    sid = result["sim_id"]
    await svc.step(sid, n=1)
    await svc.step(sid, n=1)
    await svc.step(sid, n=1)
    sim = await svc.get_state(sid)
    assert sim.world.turn == 3

    resumed = await svc.start(scene_id="scene-betrayal-night", resume=True)
    assert resumed["resumed"] is True
    assert resumed["sim_id"] == sid
    # 恢复后仍处于举手暂停态（turn 3 未收敛），可继续批复推进
    sim2 = await svc.get_state(sid)
    assert sim2.director.raise_request.pending is True or sim2.director.converged is True

    # 清理：避免污染后续测试
    await svc.repo.delete(sid)