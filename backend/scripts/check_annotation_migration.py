"""一次性核对：迁移 0014 是否生效（表 + alembic 版本）。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.engine import get_async_engine  # noqa: E402


async def main() -> int:
    eng = get_async_engine()
    async with eng.connect() as c:
        rows = (await c.execute(text(
            "select table_name from information_schema.tables "
            "where table_schema='public' and table_name in ('prose_annotations','prose_notes') order by table_name"
        ))).all()
        cols = (await c.execute(text(
            "select column_name from information_schema.columns where table_name='prose_annotations' order by ordinal_position"
        ))).all()
        ver = (await c.execute(text("select version_num from alembic_version"))).scalar()
    print("tables:", [r[0] for r in rows])
    print("alembic:", ver)
    print("annotation cols:", [r[0] for r in cols])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
