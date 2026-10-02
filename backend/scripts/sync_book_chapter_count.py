"""一次性数据修复：按实际章数重算 books.chapter_count（B23）。

背景：chapter_count 此前只在建书时同步，建章 / 删章 / plan commit 都不更新，
导致侧栏书目章节数长期失真（实测 4 本书 3 本错，含"显示 15 章 / 实际 0 章"）。

用法（在仓库根目录）：
    .venv\\Scripts\\python.exe backend/scripts/sync_book_chapter_count.py
幂等：只按实际数据重算，重复执行结果一致。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.repo import Repo  # noqa: E402

repo = Repo()


async def main() -> int:
    books = await repo.list_books()
    print(f"共 {len(books)} 本书")
    fixed = 0
    for b in books:
        before = b.get("chapter_count") or 0
        await repo._sync_book_chapter_count(b["id"])
        after_row = await repo.get_book(b["id"])
        after = (after_row or {}).get("chapter_count") or 0
        flag = "FIX " if before != after else "ok  "
        if before != after:
            fixed += 1
        print(f"{flag}{b['id']} {b['title']!r}: {before} -> {after}")
    print(f"修正 {fixed} 本")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
