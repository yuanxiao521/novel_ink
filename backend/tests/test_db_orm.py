"""DB ORM 集成测试（有真实 DB 时验证；无 DB/ persist=False 时跳过）。

验证：
  1) 基线迁移后五张 ORM 表存在（books/chapters/scenes/characters/simulation）
  2) 四层级联：seed 数据 → 书 → 章 → 场景 → 角色
  3) pgvector 列（characters.embedding）可创建类型
  4) 旧 simulation 行（无新列）仍可 load（AC-9 兼容）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402


pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def repo():
    from app.db.repo import Repo

    return Repo()


async def _db_ready() -> bool:
    """DB 可用性探针。

    用**独立临时引擎**探测，而非 app 的全局单例引擎：单例引擎绑定在"第一个用到它的
    用例"的事件循环上，pytest-asyncio 每个用例新开循环 → 后续用例 connect 抛
    "attached to a different loop"，被 except 吞掉后误报"DB 不可用"→ 静默 skip
    （实测：单跑 test_db_orm 4 passed，跟在 test_api 后面跑就 1 skipped）。
    """
    if not settings.persist:
        return False
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(settings.dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async def _probe():
            async with eng.connect() as conn:
                await conn.execute(text("SELECT 1"))

        await asyncio.wait_for(_probe(), timeout=5)  # 快速失败：DB 未开不至于拖慢 skip
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        await eng.dispose()


@pytest.mark.skipif(not settings.persist, reason="persist=False，走内存态")
async def test_seed_data_present(repo):
    if not await _db_ready():
        pytest.skip("DB 不可用，跳过真实库测试")
    # seed 已通过 app.db.seed 灌入；本测试依赖它已执行
    books = await repo.list_books()
    ids = [b["id"] for b in books]
    if "book-rain" not in ids:
        pytest.skip("未执行 seed，跳过")
    chapters = await repo.list_chapters_by_book("book-rain")
    assert len(chapters) == 1
    scenes = await repo.list_scenes_by_chapter(chapters[0]["id"])
    assert len(scenes) == 1
    chars = await repo.list_characters_by_scene(scenes[0]["id"])
    assert len(chars) == 3
    names = {c["name"] for c in chars}
    assert {"陈默", "李文", "周婶"} <= names


@pytest.mark.skipif(not settings.persist, reason="persist=False，走内存态")
async def test_book_tree_aggregated(repo):
    if not await _db_ready():
        pytest.skip("DB 不可用，跳过真实库测试")
    tree = await repo.get_book_tree("book-rain")
    if tree is None:
        pytest.skip("未执行 seed，跳过")
    assert tree["title"] == "雨夜书房"
    assert len(tree["chapters"]) == 1
    ch0 = tree["chapters"][0]
    assert len(ch0["scenes"]) == 1
    sc0 = ch0["scenes"][0]
    assert sc0["scenario_def"] == "betrayal_night"
    assert len(sc0["characters"]) == 0  # 书级角色不内联进场景（避免每场景重复）
    book_chars = await repo.list_characters_by_book("book-rain")
    assert len(book_chars) == 3  # 书级角色库一次拉全，无 N+1


@pytest.mark.skipif(not settings.persist, reason="persist=False，走内存态")
async def test_pgvector_column_exists(repo):
    if not await _db_ready():
        pytest.skip("DB 不可用，跳过真实库测试")
    from sqlalchemy import inspect, text

    from app.db.engine import get_async_engine

    async with get_async_engine().connect() as conn:
        cols = {
            row.name: str(row.type)
            for row in (await conn.execute(
                text("SELECT column_name AS name, data_type AS type FROM information_schema.columns WHERE table_name='characters'")
            ))
        }
    assert cols.get("embedding", "").startswith("USER-DEFINED") or "embedding" in cols, cols


@pytest.mark.skipif(not settings.persist, reason="persist=False，走内存态")
async def test_old_simulation_row_compatible(repo):
    """旧快照（无顶层 book/chapter/scene 字段）仍能 save/load，不报错（AC-9）。"""
    from app.schemas.models import SimulationState, WorldState

    # 模拟旧格式：只有 world.scene_id，无顶层三级 id / last_main_actor
    old = SimulationState(scenario="old", world=WorldState(scene_id="s", title="t"))
    await repo.save("sim-legacy-check", old)
    loaded = await repo.load("sim-legacy-check")
    assert loaded is not None
    # 顶层新字段默认空，world.scene_id 保留旧值（兼容旧快照语义）
    assert loaded.book_id == ""
    assert loaded.last_main_actor == ""
    assert loaded.world.scene_id == "s"
    await repo.delete("sim-legacy-check")


async def test_schema_matches_orm_no_drift():
    """结构对齐守卫（B18 回归）：DB 实际列必须与 ORM 元数据一致。

    0001 用 `Base.metadata.create_all` 建表、0006/0011 用 `op.create_table`（表已存在即跳过）
    → 老库会出现「迁移跑过、结构没改」的静默漂移：beliefs 仍为 sim 级旧结构、缺
    id/book_id → `list_beliefs` 恒抛 UndefinedColumn → `_query_or_mem` 静默落内存态，
    v1.5 信念账本在 DB 模式实际失效（0 报错、0 落库）。
    本用例把漂移变成红灯（迁移 0013 已对齐 0012 期间的两处漂移）。
    """
    import app.db.models  # noqa: F401  确保全部 ORM 注册
    from app.db.base import Base
    from sqlalchemy.ext.asyncio import create_async_engine

    if not await _db_ready():
        pytest.skip("DB 不可用")
    url = settings.dsn.replace("postgresql://", "postgresql+asyncpg://")
    eng = create_async_engine(url)
    try:
        async with eng.connect() as conn:
            rows = (await conn.execute(text(
                "select table_name, column_name from information_schema.columns "
                "where table_schema='public'"))).all()
    finally:
        await eng.dispose()
    db: dict = {}
    for tbl, col in rows:
        db.setdefault(tbl, set()).add(col)

    problems = []
    for name, table in sorted(Base.metadata.tables.items()):
        orm = {c.name for c in table.columns}
        actual = db.get(name)
        if actual is None:
            problems.append(f"{name}: 表缺失")
            continue
        miss, extra = sorted(orm - actual), sorted(actual - orm)
        if miss or extra:
            problems.append(f"{name}: DB 缺 {miss} / DB 多 {extra}")
    assert not problems, "ORM 与 DB 结构漂移（需补对齐迁移）：" + "；".join(problems)